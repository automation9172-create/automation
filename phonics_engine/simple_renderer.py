"""FFmpeg renderer for the reusable A--Z phonics lesson layout."""

from __future__ import annotations

import logging
import math
import random
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import EngineConfig
from .media import FFmpeg, MediaError, write_concat_list
from .models import ClipScenePlan, EducationalScenePlan, TimelinePlan


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    duration: float
    log_temporary_directory: Path | None
    video_sources: tuple[str, ...] = ()


class SimpleVideoAssembler:
    """Render one fixed-background lesson without loading the timeline into RAM."""

    def __init__(self, config: EngineConfig, media: FFmpeg, logger: logging.Logger, root: Path, *, fast_render: bool = False) -> None:
        self.config, self.media, self.logger, self.root = config, media, logger, Path(root)
        self.width, self.height = config.resolution
        self.fast_render = fast_render
        self.temporary_directory: Path | None = None

    def _find_font(self, variant: int = 0) -> Path | None:
        choices: list[Path] = []
        if self.config.font_path:
            choices.append(Path(self.config.font_path))
        choices.extend((
            Path(r"C:\Windows\Fonts\comicbd.ttf"),
            Path(r"C:\Windows\Fonts\ARLRDBD.TTF"),
            Path(r"C:\Windows\Fonts\trebucbd.ttf"),
            Path(r"C:\Windows\Fonts\impact.ttf"),
            Path(r"C:\Windows\Fonts\calibrib.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf"),
        ))
        available = [path for path in choices if path.is_file()]
        return available[variant % len(available)] if available else None

    def _build_text_overlay(self, letter_text: str, word_text: str, output_path: Path, scene: EducationalScenePlan | None = None) -> None:
        """Make the large left-hand letter and word layer from the reference style."""

        from PIL import Image, ImageDraw, ImageFont

        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        visual_rng = random.Random(scene.visual_seed if scene else 0)
        palettes = (
            ((255, 255, 255, 255), (242, 42, 131, 255), (244, 37, 194, 255)),
            ((255, 248, 105, 255), (245, 74, 37, 255), (255, 82, 82, 255)),
            ((236, 255, 255, 255), (25, 151, 255, 255), (35, 93, 230, 255)),
            ((255, 255, 255, 255), (112, 48, 220, 255), (244, 72, 170, 255)),
            ((255, 252, 230, 255), (24, 180, 112, 255), (8, 128, 105, 255)),
            ((255, 255, 255, 255), (255, 132, 24, 255), (228, 53, 84, 255)),
            ((255, 247, 252, 255), (225, 42, 75, 255), (94, 48, 220, 255)),
            ((246, 255, 236, 255), (63, 156, 48, 255), (255, 119, 38, 255)),
            ((255, 255, 255, 255), (0, 154, 166, 255), (232, 72, 112, 255)),
            ((255, 250, 225, 255), (78, 83, 230, 255), (0, 166, 121, 255)),
        )
        palette_index = scene.text_color_variant if scene else visual_rng.randrange(len(palettes))
        fill_color, letter_stroke, word_stroke = palettes[palette_index % len(palettes)]
        font_path = self._find_font(scene.font_variant if scene else 0)
        try:
            letter_font = ImageFont.truetype(str(font_path) if font_path else "DejaVuSans-Bold.ttf", self.config.font_letter_size)
            word_font = ImageFont.truetype(str(font_path) if font_path else "DejaVuSans-Bold.ttf", self.config.font_word_size)
        except OSError:
            letter_font = ImageFont.load_default()
            word_font = ImageFont.load_default()
        # The left column stays clearly left, but moves in from the edge so the
        # whole alphabet shape is visible on every screen.
        layout_offsets = ((0, 0), (18, -8), (30, 8), (10, 16), (35, -14), (22, 12), (6, -4), (28, 2))
        offset_x, offset_y = layout_offsets[(scene.layout_variant if scene else 0) % len(layout_offsets)]
        margin = self.config.safe_margin_x + int(self.width * 0.04) + offset_x
        letter_position = (margin, int(self.height * 0.15) + offset_y)
        word_position = (margin + 8, int(self.height * 0.66) + offset_y // 2)
        # Pink outline + white fill makes the letter legible over every one of
        # the user-provided cartoon backgrounds.  The word deliberately sits
        # directly beneath the letter, as in the supplied reference frame.
        # A deliberately soft, short shadow makes the alphabet readable but
        # does not compete with the object shadow on the right.
        draw.text((letter_position[0] + 4, letter_position[1] + 5), letter_text.upper(), font=letter_font, fill=(0, 0, 0, 90), stroke_width=2, stroke_fill=(0, 0, 0, 55))
        draw.text(letter_position, letter_text.upper(), font=letter_font, fill=fill_color, stroke_width=max(3, self.width // 230), stroke_fill=letter_stroke)
        draw.text((word_position[0] + 3, word_position[1] + 4), word_text.upper(), font=word_font, fill=(0, 0, 0, 90), stroke_width=1, stroke_fill=(0, 0, 0, 45))
        draw.text(word_position, word_text.upper(), font=word_font, fill=fill_color, stroke_width=max(2, self.width // 350), stroke_fill=word_stroke)
        if scene:
            border_palette = (
                (255, 255, 255, 205), (255, 76, 163, 210), (50, 196, 255, 210),
                (255, 205, 45, 210), (101, 224, 133, 210), (132, 91, 255, 210),
                (255, 122, 58, 210), (28, 188, 173, 210), (238, 82, 99, 210),
                (72, 92, 218, 210),
            )
            border_index = scene.border_variant % len(border_palette)
            border_color = border_palette[border_index]
            # The frame is deliberately different from the letter outline,
            # so random styling never looks like one flat matching template.
            while sum((border_color[channel] - letter_stroke[channel]) ** 2 for channel in range(3)) < 12000:
                border_index = (border_index + 1) % len(border_palette)
                border_color = border_palette[border_index]
            border_width = max(5, self.width // (250 - 15 * (scene.border_variant % 4)))
            inset = max(8, border_width + 3)
            draw.rounded_rectangle(
                (inset, inset, self.width - inset - 1, self.height - inset - 1),
                radius=max(18, self.width // 70),
                outline=border_color,
                width=border_width,
            )
            if scene.border_variant >= 5:
                inner = inset + border_width + max(5, self.width // 300)
                draw.rounded_rectangle(
                    (inner, inner, self.width - inner - 1, self.height - inner - 1),
                    radius=max(14, self.width // 82),
                    outline=(*border_color[:3], 85),
                    width=max(2, border_width // 2),
                )
        if scene and scene.letter_has_eyes:
            bbox = draw.textbbox(letter_position, letter_text.upper(), font=letter_font, stroke_width=max(3, self.width // 230))
            letter_width = max(1, bbox[2] - bbox[0])
            eye_radius = max(10, round(letter_font.size * 0.040))
            eye_y = bbox[1] + round((bbox[3] - bbox[1]) * 0.28)
            for ratio in (0.38, 0.62):
                eye_x = bbox[0] + round(letter_width * ratio)
                draw.ellipse((eye_x - eye_radius, eye_y - eye_radius, eye_x + eye_radius, eye_y + eye_radius), fill=(255, 255, 255, 255), outline=(35, 35, 35, 255), width=max(2, eye_radius // 6))
                pupil = max(4, eye_radius // 2)
                pupil_x = eye_x + visual_rng.randint(-max(1, eye_radius // 5), max(1, eye_radius // 5))
                draw.ellipse((pupil_x - pupil, eye_y - pupil, pupil_x + pupil, eye_y + pupil), fill=(15, 15, 15, 255))
        if scene and scene.teaching_variant:
            lesson_texts = (
                "",
                f"CAPITAL {scene.letter}  |  SMALL {scene.letter.lower()}",
                f"STARTS WITH {scene.letter}",
                f"WORD: {scene.asset.display_name.upper()}",
                f"{len(scene.asset.display_name.replace(' ', ''))} LETTER WORD",
                f"{scene.letter}  |  {scene.asset.display_name.upper()}",
                f"CAN YOU SAY {scene.asset.display_name.upper()}?",
                f"FIND THE LETTER {scene.letter}",
                f"REPEAT: {scene.asset.display_name.upper()}",
                f"LETTER {scene.letter} WORD",
                f"LOOK, LISTEN & REPEAT",
                f"{scene.asset.display_name.upper()} BEGINS WITH {scene.letter}",
            )
            lesson_text = lesson_texts[scene.teaching_variant % len(lesson_texts)]
            badge_font_size = max(22, round(self.config.font_word_size * 0.34))
            try:
                badge_font = ImageFont.truetype(str(font_path) if font_path else "DejaVuSans-Bold.ttf", badge_font_size)
            except OSError:
                badge_font = ImageFont.load_default()
            badge_box = draw.textbbox((0, 0), lesson_text, font=badge_font)
            badge_width = badge_box[2] - badge_box[0] + 34
            badge_height = badge_box[3] - badge_box[1] + 24
            badge_x = margin + 8
            badge_y = min(self.height - badge_height - self.config.safe_margin_y, int(self.height * 0.83))
            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_width, badge_y + badge_height),
                radius=max(8, badge_height // 3),
                fill=(255, 255, 255, 205),
                outline=word_stroke,
                width=max(2, self.width // 650),
            )
            draw.text((badge_x + 17, badge_y + 10 - badge_box[1]), lesson_text, font=badge_font, fill=(30, 30, 45, 255))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, "PNG")

    def _object_placement(self, scene: EducationalScenePlan, object_image) -> tuple[int, int]:
        from PIL import Image

        placements = (
            (0.70, 0.50, 1.00), (0.72, 0.49, 0.94), (0.68, 0.52, 0.98),
            (0.71, 0.53, 0.90), (0.69, 0.47, 1.00), (0.73, 0.51, 0.92),
            (0.67, 0.49, 0.96), (0.71, 0.48, 0.88),
        )
        x_ratio, y_ratio, scale = placements[scene.layout_variant % len(placements)]
        object_image.thumbnail(
            (round(self.config.object_max_width * scale), round(self.config.object_max_height * scale)),
            Image.Resampling.LANCZOS,
        )
        return int(self.width * x_ratio - object_image.width / 2), int(self.height * y_ratio - object_image.height / 2)

    def _build_fountain_overlay(self, object_path: Path, output_path: Path) -> None:
        """Pre-compose the faint background-object fountain into one PNG.

        Keeping four full-resolution FFmpeg branches alive consumed excessive
        memory. A single transparent overlay provides the same visual layer
        while the renderer holds just one extra video frame in memory.
        """

        from PIL import Image

        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        positions = (
            (0.52, 0.72, 0.105),
            (0.61, 0.56, 0.115),
            (0.73, 0.46, 0.125),
            (0.85, 0.60, 0.110),
        )
        with Image.open(object_path).convert("RGBA") as source:
            for x_ratio, y_ratio, size_ratio in positions:
                copy = source.copy()
                side = max(48, int(self.width * size_ratio))
                copy.thumbnail((side, side), Image.Resampling.LANCZOS)
                alpha = copy.getchannel("A").point(lambda value: round(value * 0.20))
                copy.putalpha(alpha)
                x = int(self.width * x_ratio - copy.width / 2)
                y = int(self.height * y_ratio - copy.height / 2)
                canvas.alpha_composite(copy, (x, y))
        canvas.save(output_path, "PNG")

    def _draw_subtle_overlay_marks(self, canvas, scene: EducationalScenePlan, rng: random.Random, count: int) -> None:
        """Draw low-opacity colored marks that become a moving overlay sheet."""

        from PIL import ImageDraw

        palettes = (
            (255, 255, 255), (255, 122, 190), (93, 210, 255), (255, 218, 94),
            (126, 238, 169), (177, 132, 255), (255, 148, 84), (77, 217, 202),
            (255, 112, 126), (114, 139, 255),
        )
        red, green, blue = palettes[scene.overlay_color_variant % len(palettes)]
        draw = ImageDraw.Draw(canvas, "RGBA")
        variant = scene.overlay_variant % 6
        for _ in range(count):
            x = rng.randint(-round(self.width * 0.10), round(self.width * 0.92))
            y = rng.randint(0, canvas.height - 1)
            length = rng.randint(round(self.width * 0.09), round(self.width * 0.25))
            rise = rng.randint(round(self.height * 0.02), round(self.height * 0.08))
            alpha = rng.randint(18, 42)
            width = rng.randint(2, 4)
            color = (red, green, blue, alpha)
            if variant == 0:
                draw.line(((x, y), (x + length, y - rise)), fill=color, width=width)
            elif variant == 1:
                draw.line(((x, y), (x + length // 2, y + rise), (x + length, y)), fill=color, width=width, joint="curve")
            elif variant == 2:
                draw.line(((x, y), (x + length // 3, y - rise), (x + 2 * length // 3, y + rise), (x + length, y)), fill=color, width=width, joint="curve")
            elif variant == 3:
                radius = max(12, length // 7)
                draw.ellipse((x, y - radius, x + radius * 2, y + radius), outline=color, width=width)
            elif variant == 4:
                arm = max(12, length // 8)
                draw.line(((x - arm, y), (x + arm, y)), fill=color, width=width)
                draw.line(((x, y - arm), (x, y + arm)), fill=color, width=width)
            else:
                segment = max(10, length // 6)
                for start in range(0, length, segment * 2):
                    draw.line(((x + start, y), (x + min(length, start + segment), y - rise)), fill=color, width=width)

    def _build_ambient_overlay(self, scene: EducationalScenePlan, output_path: Path) -> None:
        """Create a very faint colored overlay for the full-screen object clip."""

        from PIL import Image

        canvas = Image.new("RGBA", (self.width, round(self.height * 1.45)), (0, 0, 0, 0))
        rng = random.Random(f"ambient:{scene.visual_seed}:{scene.overlay_variant}:{scene.overlay_color_variant}")
        self._draw_subtle_overlay_marks(canvas, scene, rng, rng.randint(5, 8))
        canvas.save(output_path, "PNG")

    def _build_falling_rain_sheet(self, scene: EducationalScenePlan, output_path: Path) -> None:
        """Make one moving sheet of object rain and subtle colored marks.

        FFmpeg moves this single sheet down twice.  It gives a small object-rain
        effect while avoiding multiple expensive transparent overlay branches.
        The subtle line patterns move with the same sheet, so they add visual
        variation without another full-resolution FFmpeg input.
        """

        from PIL import Image

        rng = random.Random(f"falling-rain:{scene.visual_seed}:{scene.motion_variant}:{scene.asset.name}")
        sheet_height = round(self.height * 1.85)
        canvas = Image.new("RGBA", (self.width, sheet_height), (0, 0, 0, 0))
        line_rng = random.Random(f"motion-lines:{scene.visual_seed}:{scene.letter}:{scene.overlay_variant}")
        # Most scenes receive a different low-opacity colored pattern.
        # These are decorative overlays only; the selected background image
        # itself remains unchanged for the complete A-Z video.
        if line_rng.random() < 0.82:
            self._draw_subtle_overlay_marks(canvas, scene, line_rng, line_rng.randint(5, 8))
        with Image.open(scene.asset.png_path).convert("RGBA") as source:
            object_count = 3 + scene.motion_variant % 2
            for number in range(object_count):
                duplicate = source.copy()
                side = max(48, round(self.width * rng.uniform(0.075, 0.135)))
                duplicate.thumbnail((side, side), Image.Resampling.LANCZOS)
                duplicate = duplicate.rotate(rng.uniform(-16, 16), resample=Image.Resampling.BICUBIC, expand=True)
                opacity = rng.uniform(0.25, 0.38)
                alpha = duplicate.getchannel("A").point(lambda value, amount=opacity: round(value * amount))
                duplicate.putalpha(alpha)
                x = rng.randint(-duplicate.width // 3, self.width - (duplicate.width * 2 // 3))
                y = int((0.08 + number * 0.29 + rng.uniform(-0.06, 0.06)) * self.height)
                canvas.alpha_composite(duplicate, (x, y))
        canvas.save(output_path, "PNG")

    def _build_scene_background_card(self, background_path: Path, output_path: Path) -> None:
        """Fit the single selected lesson background to the output frame."""

        from PIL import Image, ImageOps

        with Image.open(background_path).convert("RGBA") as raw_background:
            background = ImageOps.fit(
                raw_background,
                (self.width, self.height),
                Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        background.save(output_path, "PNG")

    def _build_scene_foreground(self, scene: EducationalScenePlan, label_path: Path, output_path: Path) -> None:
        """Create the fixed front layer: label plus the shadowed main object."""

        from PIL import Image, ImageFilter

        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        with Image.open(label_path).convert("RGBA") as label:
            canvas.alpha_composite(label, (0, 0))
        with Image.open(scene.asset.png_path).convert("RGBA") as object_image:
            x, y = self._object_placement(scene, object_image)
            shadow = Image.new("RGBA", object_image.size, (0, 0, 0, 0))
            shadow.putalpha(object_image.getchannel("A").point(lambda value: round(value * 0.48)))
            shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, self.width // 240)))
            canvas.alpha_composite(shadow, (x + 18, y + 24))
            canvas.alpha_composite(object_image, (x, y))
        canvas.save(output_path, "PNG")

    def _teacher_filter(self, input_index: int, scene: EducationalScenePlan) -> str:
        """Use a gentle three-tap echo only on selected teacher lines."""

        echo = ",aecho=0.8:0.65:80|160|240:0.25|0.16|0.10" if scene.teacher_echo else ""
        return (
            f"[{input_index}:a]atrim=duration={scene.teacher_duration:.3f},"
            f"aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo,"
            f"afade=t=in:st=0:d={self.config.voice_fade_seconds:.3f}{echo},"
            f"adelay={round(scene.teacher_start * 1000)}:all=1[teacher]"
        )

    def _build_scene_card(self, scene: EducationalScenePlan, background_path: Path, label_path: Path, fountain_path: Path, output_path: Path) -> None:
        """Pre-compose the complete visual scene into one reliable PNG frame.

        FFmpeg's multiple transparent 1080p overlays intermittently stalled on
        some otherwise valid PNGs (notably Train). Pillow builds the exact same
        visual layers once on disk, so FFmpeg only needs to encode one flat
        image with audio. This is far more stable and uses much less memory.
        """

        from PIL import Image, ImageFilter, ImageOps

        with Image.open(background_path).convert("RGBA") as raw_background:
            canvas = ImageOps.fit(raw_background, (self.width, self.height), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        with Image.open(fountain_path).convert("RGBA") as fountain:
            canvas.alpha_composite(fountain, (0, 0))
        with Image.open(label_path).convert("RGBA") as label:
            canvas.alpha_composite(label, (0, 0))
        with Image.open(scene.asset.png_path).convert("RGBA") as object_image:
            x, y = self._object_placement(scene, object_image)
            shadow = Image.new("RGBA", object_image.size, (0, 0, 0, 0))
            shadow.putalpha(object_image.getchannel("A").point(lambda value: round(value * 0.48)))
            shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, self.width // 240)))
            canvas.alpha_composite(shadow, (x + 18, y + 24))
            canvas.alpha_composite(object_image, (x, y))
        canvas.save(output_path, "PNG")

    def _encode_args(self) -> list[str]:
        # A small fixed thread count is fast enough without the runaway memory
        # use caused by FFmpeg's automatic all-core thread selection.
        return ["-c:v", "libx264", "-preset", self.config.ffmpeg_preset, "-crf", str(self.config.video_crf), "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "aac", "-b:a", self.config.audio_bitrate, "-ar", str(self.config.audio_sample_rate), "-movflags", "+faststart"]

    def _image_input(self, path: Path) -> list[str]:
        return ["-loop", "1", "-framerate", str(self.config.fps), "-i", str(path)]

    def _silent_input(self) -> list[str]:
        return ["-f", "lavfi", "-i", f"anullsrc=r={self.config.audio_sample_rate}:cl=stereo"]

    def _render_educational_segment(self, scene: EducationalScenePlan, background_path: Path, output_path: Path) -> None:
        assert self.temporary_directory is not None
        text = self.temporary_directory / f"label_{scene.index:03d}.png"
        background_card = self.temporary_directory / f"background_{scene.index:03d}.png"
        rain_sheet = self.temporary_directory / f"falling_rain_{scene.index:03d}.png"
        foreground = self.temporary_directory / f"foreground_{scene.index:03d}.png"
        self._build_text_overlay(scene.letter, scene.asset.display_name, text, scene)
        self._build_scene_background_card(background_path, background_card)
        self._build_falling_rain_sheet(scene, rain_sheet)
        self._build_scene_foreground(scene, text, foreground)
        duration = scene.duration
        # This complete 3--4 object rain sheet starts above the screen and
        # falls through twice. Every object is in a different random lane.
        fall_duration = min(3.3, max(1.2, (duration - 0.45) / 2))
        first_start = 0.10
        first_end = first_start + fall_duration
        second_start = min(duration - fall_duration - 0.10, first_end + 0.20)
        second_end = second_start + fall_duration
        fall_y = (
            f"if(between(t\\,{first_start:.3f}\\,{first_end:.3f})\\,"
            f"-overlay_h+(main_h+overlay_h)*(t-{first_start:.3f})/{fall_duration:.3f}\\,"
            f"if(between(t\\,{second_start:.3f}\\,{second_end:.3f})\\,"
            f"-overlay_h+(main_h+overlay_h)*(t-{second_start:.3f})/{fall_duration:.3f}\\,-overlay_h))"
        )
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2"]
        command += self._image_input(background_card)
        command += self._image_input(rain_sheet)
        command += self._image_input(foreground)
        command += self._silent_input()
        command += ["-i", str(scene.teacher_audio), "-i", str(scene.student_audio)]
        drift_amplitudes = (0.012, 0.018, 0.024, 0.030, 0.020, 0.035)
        drift_periods = (1.55, 1.85, 2.15, 1.70, 2.40, 2.05)
        amplitude = drift_amplitudes[scene.motion_variant % len(drift_amplitudes)]
        period = drift_periods[scene.motion_variant % len(drift_periods)]
        phase = (scene.motion_variant % 4) * 0.7
        filters = [
            f"[0:v]fps={self.config.fps},format=rgba[background]",
            f"[1:v]format=rgba[falling_rain]",
            f"[2:v]format=rgba[foreground]",
            f"[background][falling_rain]overlay=x='{amplitude:.3f}*main_w*sin(2*PI*t/{period:.3f}+{phase:.3f})':y='{fall_y}':eval=frame:format=auto[with_falling_rain]",
            f"[with_falling_rain][foreground]overlay=0:0:format=auto,fade=t=in:st=0:d={self.config.visual_fade_seconds:.3f},fade=t=out:st={max(0, duration-self.config.visual_fade_seconds):.3f}:d={self.config.visual_fade_seconds:.3f},format=yuv420p[video]",
            f"[3:a]atrim=duration={duration:.3f},aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo[silence]",
            self._teacher_filter(4, scene),
            f"[5:a]atrim=duration={scene.student_duration:.3f},aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo,afade=t=in:st=0:d={self.config.voice_fade_seconds:.3f},adelay={round(scene.student_start * 1000)}:all=1[student]",
            "[teacher][student][silence]amix=inputs=3:duration=longest:normalize=0[audio]",
        ]
        command += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]", "-t", f"{duration:.3f}"] + self._encode_args() + [str(output_path)]
        self.media.run(
            command,
            f"Rendering education scene {scene.index}: {scene.letter} for {scene.asset.display_name}",
            timeout_seconds=self.config.ffmpeg_scene_timeout_seconds,
        )

    def _render_educational_safety_fallback(self, scene: EducationalScenePlan, background_path: Path, output_path: Path) -> None:
        """Render an education scene with the absolute minimum video work.

        This is deliberately separate from the normal path.  It has no video
        filter graph at all: FFmpeg encodes one pre-composed PNG and mixes the
        two voices.  If a platform-specific FFmpeg problem remains, the lesson
        continues with this safe version instead of abandoning the full render.
        """

        assert self.temporary_directory is not None
        text = self.temporary_directory / f"label_safe_{scene.index:03d}.png"
        fountain = self.temporary_directory / f"fountain_safe_{scene.index:03d}.png"
        scene_card = self.temporary_directory / f"scene_card_safe_{scene.index:03d}.png"
        self._build_text_overlay(scene.letter, scene.asset.display_name, text, scene)
        self._build_fountain_overlay(scene.asset.png_path, fountain)
        self._build_scene_card(scene, background_path, text, fountain, scene_card)
        duration = scene.duration
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2"]
        command += self._image_input(scene_card)
        command += self._silent_input()
        command += ["-i", str(scene.teacher_audio), "-i", str(scene.student_audio)]
        filters = [
            f"[1:a]atrim=duration={duration:.3f},aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo[silence]",
            self._teacher_filter(2, scene),
            f"[3:a]atrim=duration={scene.student_duration:.3f},aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo,afade=t=in:st=0:d={self.config.voice_fade_seconds:.3f},adelay={round(scene.student_start * 1000)}:all=1[student]",
            "[teacher][student][silence]amix=inputs=3:duration=longest:normalize=0[audio]",
        ]
        command += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[audio]", "-t", f"{duration:.3f}", "-r", str(self.config.fps)] + self._encode_args() + [str(output_path)]
        self.media.run(
            command,
            f"Rendering safety fallback {scene.index}: {scene.letter} for {scene.asset.display_name}",
            timeout_seconds=self.config.ffmpeg_scene_timeout_seconds,
        )

    def _render_real_video_segment(self, source: Path, scene: ClipScenePlan, visual: EducationalScenePlan, output_path: Path) -> None:
        # The object film intentionally fills the whole frame.  No previous
        # background is shown during this phase.
        assert self.temporary_directory is not None
        duration = scene.duration
        ambient = self.temporary_directory / f"ambient_{scene.index:03d}.png"
        self._build_ambient_overlay(visual, ambient)
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2", "-stream_loop", "-1", "-i", str(source)]
        command += self._image_input(ambient)
        command += self._silent_input()
        amplitude = (0.008, 0.012, 0.016, 0.020, 0.010, 0.018)[visual.motion_variant % 6]
        period = (1.9, 2.3, 2.7, 2.1, 3.0, 2.5)[visual.motion_variant % 6]
        filters = [
            f"[0:v]setpts=PTS-STARTPTS,scale={self.width}:{self.height}:force_original_aspect_ratio=increase,crop={self.width}:{self.height},fps={self.config.fps},format=rgba[base]",
            f"[1:v]format=rgba[ambient]",
            f"[base][ambient]overlay=x='{amplitude:.3f}*main_w*sin(2*PI*t/{period:.3f})':y='-overlay_h+(main_h+overlay_h)*t/{duration:.3f}':eval=frame:format=auto,fade=t=in:st=0:d={self.config.visual_fade_seconds:.3f},fade=t=out:st={max(0, duration-self.config.visual_fade_seconds):.3f}:d={self.config.visual_fade_seconds:.3f},format=yuv420p[video]",
            f"[2:a]atrim=duration={duration:.3f},aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo[audio]",
        ]
        command += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]", "-t", f"{duration:.3f}"] + self._encode_args() + [str(output_path)]
        self.media.run(
            command,
            f"Rendering full-screen video {scene.index}: {scene.asset.display_name}",
            timeout_seconds=self.config.ffmpeg_scene_timeout_seconds,
        )

    def _render_png_fallback(self, scene: ClipScenePlan, background: Path, output_path: Path) -> None:
        """Keep the lesson complete if Pixabay footage is not available yet."""

        duration = scene.duration
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2"] + self._image_input(background) + self._image_input(scene.asset.png_path) + self._silent_input()
        filters = [
            f"[0:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,crop={self.width}:{self.height},fps={self.config.fps},format=rgba[bg]",
            f"[1:v]format=rgba,scale=w='min({self.width}\\,iw*(0.72+t*0.06))':h='min({self.height}\\,ih*(0.72+t*0.06))':force_original_aspect_ratio=decrease:eval=frame[object]",
            "[bg][object]overlay=x='main_w/2-overlay_w/2':y='main_h/2-overlay_h/2+10*sin(2*PI*0.7*t)',format=yuv420p[video]",
            f"[2:a]atrim=duration={duration:.3f},aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo[audio]",
        ]
        command += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]", "-t", f"{duration:.3f}"] + self._encode_args() + [str(output_path)]
        self.media.run(
            command,
            f"Rendering animated image fallback {scene.index}: {scene.asset.display_name}",
            timeout_seconds=self.config.ffmpeg_scene_timeout_seconds,
        )

    def _render_intro(self, source: Path, duration: float, output_path: Path) -> None:
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2", "-i", str(source)]
        filters = [
            f"[0:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,crop={self.width}:{self.height},fps={self.config.fps},format=yuv420p[video]",
            f"[0:a]aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo,atrim=duration={duration:.3f}[audio]",
        ]
        command += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]", "-t", f"{duration:.3f}"] + self._encode_args() + [str(output_path)]
        self.media.run(command, f"Rendering intro: {source.name}")

    def _render_outro(self, background: Path, duration: float, output_path: Path) -> None:
        # Reuse the fixed lesson background for a simple closing card.
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2"] + self._image_input(background) + self._silent_input()
        filters = [
            f"[0:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,crop={self.width}:{self.height},fps={self.config.fps},format=yuv420p[video]",
            f"[1:a]atrim=duration={duration:.3f},aresample={self.config.audio_sample_rate},aformat=channel_layouts=stereo[audio]",
        ]
        command += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]", "-t", f"{duration:.3f}"] + self._encode_args() + [str(output_path)]
        self.media.run(command, "Rendering outro")

    def _speech_ranges(self, plan: TimelinePlan) -> list[tuple[float, float]]:
        offset = plan.intro_duration
        ranges: list[tuple[float, float]] = []
        for scene, clip in zip(plan.educational_scenes, plan.clip_scenes):
            ranges.append((offset + scene.teacher_start, offset + scene.teacher_start + scene.teacher_duration))
            ranges.append((offset + scene.student_start, offset + scene.student_start + scene.student_duration))
            offset += scene.duration + clip.duration
        return ranges

    def _build_music_bed(self, tracks: tuple[Path, ...], duration: float, speech_ranges: list[tuple[float, float]], destination: Path) -> bool:
        if not tracks:
            return False
        try:
            from pydub import AudioSegment
            loaded = [AudioSegment.from_file(path) for path in tracks]
        except Exception as exc:
            self.logger.warning("background_music_unavailable error=%s", exc)
            return False
        loaded = [piece for piece in loaded if len(piece)]
        if not loaded:
            return False
        target_ms = round(duration * 1000)
        bed = AudioSegment.silent(duration=0, frame_rate=self.config.audio_sample_rate)
        index = 0
        while len(bed) < target_ms:
            next_track = loaded[index % len(loaded)]
            index += 1
            overlap = min(round(self.config.music_crossfade_seconds * 1000), len(bed), len(next_track), 2000)
            bed = bed.append(next_track, crossfade=overlap) if overlap else bed + next_track
        bed = bed[:target_ms]
        normal_gain = 20 * math.log10(max(self.config.normal_music_volume, 0.001))
        duck_gain = 20 * math.log10(max(self.config.speech_music_volume / self.config.normal_music_volume, 0.001))
        bed = bed.apply_gain(normal_gain)
        for start, end in speech_ranges:
            start_ms, end_ms = max(0, round(start * 1000)), min(target_ms, round(end * 1000))
            if end_ms > start_ms:
                bed = bed[:start_ms] + bed[start_ms:end_ms].apply_gain(duck_gain) + bed[end_ms:]
        bed = bed.fade_in(round(self.config.music_start_fade_seconds * 1000)).fade_out(round(self.config.music_end_fade_seconds * 1000))
        bed.export(destination, format="wav")
        return True

    def _add_music(self, silent_video: Path, music: Path, output: Path) -> None:
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-filter_complex_threads", "2", "-threads", "2", "-i", str(silent_video), "-stream_loop", "-1", "-i", str(music), "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,volume=" + str(self.config.master_audio_volume) + "[audio]", "-map", "0:v", "-map", "[audio]", "-c:v", "copy", "-c:a", "aac", "-b:a", self.config.audio_bitrate, "-shortest", str(output)]
        self.media.run(command, "Adding ducked background music")

    def _apply_master_volume(self, source: Path, output: Path) -> None:
        """Raise volume and perform one fast, reliable 4K export pass."""

        export_width, export_height = self.config.export_resolution
        common = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-filter_threads", "0", "-filter_complex_threads", "0",
            "-i", str(source),
            "-vf", f"scale={export_width}:{export_height}:flags={self.config.export_scale_flags},format=nv12",
            "-filter:a", f"volume={self.config.master_audio_volume}",
            "-map", "0:v", "-map", "0:a",
        ]
        ending = [
            "-c:a", "aac", "-b:a", self.config.audio_bitrate,
            "-movflags", "+faststart", str(output),
        ]
        if self.config.hardware_4k_export_enabled:
            hardware = common + [
                "-c:v", "h264_qsv", "-preset", "veryfast",
                "-global_quality", str(self.config.export_video_crf),
                "-look_ahead", "0", "-profile:v", "high",
            ] + ending
            try:
                self.media.run(
                    hardware,
                    f"Exporting final YouTube 4K video with Intel Quick Sync {export_width}x{export_height}",
                )
                return
            except MediaError as exc:
                self.logger.warning("quick_sync_4k_export_unavailable_using_cpu error=%s", exc)
                if output.is_file():
                    # Renaming first avoids slow media-file delete hooks on
                    # some Windows antivirus installations.
                    failed = output.with_suffix(".failed_export")
                    output.replace(failed)
                    failed.unlink(missing_ok=True)

        cpu = common + [
            "-c:v", "libx264", "-preset", self.config.ffmpeg_preset,
            "-crf", str(self.config.export_video_crf), "-pix_fmt", "yuv420p",
            "-threads", str(self.config.export_cpu_threads),
        ] + ending
        self.media.run(cpu, f"Exporting final YouTube 4K video with CPU {export_width}x{export_height}")

    def render(self, plan: TimelinePlan, pixabay) -> RenderResult:
        temporary = Path(tempfile.mkdtemp(prefix="render_", dir=self.root))
        self.temporary_directory = temporary
        segment_paths: list[Path] = []
        video_sources: list[str] = []
        output: Path | None = None
        try:
            if pixabay:
                pixabay.set_temporary_directory(temporary)
            if plan.intro_path:
                intro = temporary / "intro.mp4"
                self._render_intro(plan.intro_path, plan.intro_duration, intro)
                segment_paths.append(intro)
            for number, (education, clip) in enumerate(zip(plan.educational_scenes, plan.clip_scenes), 1):
                education_file = temporary / f"education_{number:03d}.mp4"
                try:
                    self._render_educational_segment(education, plan.background_path, education_file)
                except MediaError as exc:
                    self.logger.warning(
                        "education_scene_primary_failed_using_safety_fallback index=%s object=%s error=%s",
                        education.index,
                        education.asset.display_name,
                        exc,
                    )
                    self._render_educational_safety_fallback(education, plan.background_path, education_file)
                segment_paths.append(education_file)
                clip_file = temporary / f"object_video_{number:03d}.mp4"
                source = pixabay.get_clip(clip.asset) if pixabay else None
                if source:
                    try:
                        self._render_real_video_segment(source, clip, education, clip_file)
                        video_sources.append(source.name)
                    except MediaError as exc:
                        # Never replace an exact object clip with an unrelated
                        # video.  If the selected provider/manual media is
                        # unexpectedly unreadable while rendering, finish this
                        # slot with the object's own animated PNG instead.
                        self.logger.warning(
                            "object_video_failed_using_exact_png_fallback index=%s object=%s source=%s error=%s",
                            clip.index,
                            clip.asset.display_name,
                            source,
                            exc,
                        )
                        self._render_png_fallback(clip, plan.background_path, clip_file)
                        video_sources.append(f"png-fallback:{clip.letter}:{clip.asset.name}")
                    finally:
                        if pixabay:
                            pixabay.release_temporary_clip(source)
                else:
                    self._render_png_fallback(clip, plan.background_path, clip_file)
                    video_sources.append(f"png-fallback:{clip.letter}:{clip.asset.name}")
                segment_paths.append(clip_file)
            if plan.outro_duration:
                outro = temporary / "outro.mp4"
                self._render_outro(plan.background_path, plan.outro_duration, outro)
                segment_paths.append(outro)
            concat = temporary / "segments.txt"
            write_concat_list(segment_paths, concat)
            narration_video = temporary / "narration.mp4"
            self.media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(narration_video)], "Joining lesson segments")
            outputs = self.root / "outputs"
            outputs.mkdir(parents=True, exist_ok=True)
            output = outputs / f"{self.config.output_prefix}_{datetime.now():%Y%m%d_%H%M%S}.mp4"
            duration = self.media.probe_duration(narration_video)
            music = temporary / "music.wav"
            mixed_video = temporary / "mixed.mp4"
            if self._build_music_bed(plan.selected_music, duration, self._speech_ranges(plan), music):
                self._add_music(narration_video, music, mixed_video)
            else:
                shutil.copy2(narration_video, mixed_video)
            self._apply_master_volume(mixed_video, output)
            final_duration = self.media.probe_duration(output)
            retained = temporary if self.config.keep_temporary_files else None
            if not retained:
                shutil.rmtree(temporary, ignore_errors=True)
            return RenderResult(output, final_duration, retained, tuple(video_sources))
        except Exception as exc:
            self.logger.exception("render_failed temporary_directory=%s", temporary)
            if output and output.is_file():
                output.unlink(missing_ok=True)
            if not self.config.keep_temporary_files:
                shutil.rmtree(temporary, ignore_errors=True)
            raise MediaError(f"Video render failed; temporary render cache was removed: {exc}") from exc
        finally:
            if pixabay:
                pixabay.clear_temporary_downloads()
