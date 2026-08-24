"""Compose a distinct, readable thumbnail from the completed lesson plan."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import EducationalScenePlan, TimelinePlan


THUMBNAIL_SIZE = (1280, 720)
_PALETTES = (
    ((27, 48, 148), (0, 191, 255), (255, 226, 64)),
    ((129, 31, 140), (255, 69, 143), (255, 226, 74)),
    ((0, 112, 92), (34, 197, 154), (255, 211, 65)),
    ((180, 49, 42), (255, 111, 58), (255, 225, 79)),
    ((69, 53, 173), (148, 89, 255), (75, 221, 255)),
    ((0, 91, 163), (0, 177, 225), (255, 203, 66)),
    ((142, 48, 99), (242, 76, 126), (116, 231, 197)),
    ((39, 101, 45), (88, 187, 92), (255, 214, 61)),
)


def _font_path(root: Path) -> Path | None:
    choices = (
        root / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
        Path(r"C:\Windows\Fonts\comicbd.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    )
    return next((path for path in choices if path.is_file()), None)


def _font(root: Path, size: int):
    from PIL import ImageFont

    path = _font_path(root)
    try:
        return ImageFont.truetype(str(path) if path else "DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _cover(image, size: tuple[int, int]):
    from PIL import Image, ImageOps

    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def _primary_scenes(plan: TimelinePlan) -> list[EducationalScenePlan]:
    by_letter: dict[str, EducationalScenePlan] = {}
    for scene in plan.educational_scenes:
        by_letter.setdefault(scene.letter.upper(), scene)
    return [by_letter[letter] for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if letter in by_letter]


def _featured_scenes(plan: TimelinePlan, identity: str) -> tuple[EducationalScenePlan, ...]:
    scenes = _primary_scenes(plan)
    non_apple = [scene for scene in scenes if scene.letter != "A"] or scenes
    if not non_apple:
        return ()
    ranked = sorted(
        non_apple,
        key=lambda scene: hashlib.sha256(
            f"{identity}|{scene.letter}|{scene.asset.name}".encode("utf-8")
        ).digest(),
    )
    return tuple(ranked[:3])


def _fit_text(draw, text: str, root: Path, maximum_size: int, maximum_width: int):
    size = maximum_size
    while size > 28:
        font = _font(root, size)
        if draw.textbbox((0, 0), text, font=font, stroke_width=2)[2] <= maximum_width:
            return font
        size -= 4
    return _font(root, size)


def compose_discovery_thumbnail(plan: TimelinePlan, output_path: Path, identity: str, root: Path) -> Path:
    """Write a mobile-readable 16:9 thumbnail whose featured object changes per plan."""

    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

    if not plan.thumbnail_path:
        raise ValueError("A source thumbnail is required")
    featured = _featured_scenes(plan, identity)
    if not featured:
        raise ValueError("The lesson has no educational scenes")

    width, height = THUMBNAIL_SIZE
    with Image.open(plan.thumbnail_path) as source:
        background = _cover(ImageOps.exif_transpose(source), THUMBNAIL_SIZE)
    background = ImageEnhance.Color(background).enhance(0.75).filter(ImageFilter.GaussianBlur(7))
    canvas = background.convert("RGBA")
    digest = hashlib.sha256((identity + "thumbnail").encode("utf-8")).digest()
    dark, bright, accent = _PALETTES[digest[0] % len(_PALETTES)]
    wash = Image.new("RGBA", THUMBNAIL_SIZE, (*dark, 218))
    canvas = Image.alpha_composite(canvas, wash)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Quiet geometric accents make each result distinct without hiding the lesson subject.
    for index in range(8):
        x = (digest[(index + 2) % len(digest)] * 17 + index * 149) % width
        y = (digest[(index + 10) % len(digest)] * 11 + index * 83) % height
        radius = 24 + digest[(index + 18) % len(digest)] % 54
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*bright, 30))

    title_font = _font(root, 104)
    subtitle_font = _font(root, 52)
    small_font = _font(root, 31)
    draw.rounded_rectangle((42, 40, 522, 105), radius=32, fill=(255, 255, 255, 235))
    draw.text((74, 56), "LEARN • LOOK • REPEAT", font=small_font, fill=(*dark, 255))
    draw.text((48, 130), "ABC", font=title_font, fill=(255, 255, 255, 255), stroke_width=7, stroke_fill=(*bright, 255))
    draw.text((52, 250), "PHONICS", font=subtitle_font, fill=(*accent, 255), stroke_width=4, stroke_fill=(29, 29, 45, 230))
    draw.rounded_rectangle((50, 342, 500, 620), radius=38, fill=(255, 255, 255, 230), outline=(*accent, 255), width=8)
    draw.text((90, 374), "A–Z", font=_font(root, 116), fill=(*dark, 255))
    draw.text((91, 516), "FULL LESSON", font=_font(root, 38), fill=(35, 35, 48, 255))

    hero = featured[0]
    card = (548, 38, 1238, 680)
    draw.rounded_rectangle(card, radius=50, fill=(255, 255, 255, 242), outline=(*bright, 255), width=10)
    draw.ellipse((584, 68, 752, 236), fill=(*bright, 255), outline=(255, 255, 255, 255), width=5)
    letter_font = _font(root, 116)
    letter_box = draw.textbbox((0, 0), hero.letter, font=letter_font)
    draw.text((668 - (letter_box[2] - letter_box[0]) / 2, 80), hero.letter, font=letter_font, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(*dark, 255))

    with Image.open(hero.asset.png_path) as source:
        object_image = ImageOps.exif_transpose(source).convert("RGBA")
    object_image.thumbnail((530, 390), Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", object_image.size, (0, 0, 0, 0))
    if object_image.getchannel("A").getbbox():
        shadow.putalpha(object_image.getchannel("A").filter(ImageFilter.GaussianBlur(14)))
    shadow_color = Image.new("RGBA", object_image.size, (0, 0, 0, 105))
    shadow_color.putalpha(shadow.getchannel("A"))
    object_x = 885 - object_image.width // 2
    object_y = 155 + max(0, (380 - object_image.height) // 2)
    canvas.alpha_composite(shadow_color, (object_x + 13, object_y + 17))
    canvas.alpha_composite(object_image, (object_x, object_y))

    name = f"{hero.letter} FOR {hero.asset.display_name.upper()}"
    name_font = _fit_text(draw, name, root, 58, 590)
    draw.rounded_rectangle((590, 548, 1196, 646), radius=34, fill=(*bright, 255))
    name_box = draw.textbbox((0, 0), name, font=name_font, stroke_width=2)
    draw.text((893 - (name_box[2] - name_box[0]) / 2, 565 - name_box[1]), name, font=name_font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(*dark, 255))

    # Two small, plan-matched object badges add more variation while remaining readable on mobile.
    for index, scene in enumerate(featured[1:]):
        cx = 810 + index * 250
        draw.ellipse((cx - 62, 70, cx + 62, 194), fill=(*accent, 245), outline=(255, 255, 255, 255), width=5)
        with Image.open(scene.asset.png_path) as source:
            badge = ImageOps.exif_transpose(source).convert("RGBA")
        badge.thumbnail((92, 92), Image.Resampling.LANCZOS)
        canvas.alpha_composite(badge, (cx - badge.width // 2, 86 + (92 - badge.height) // 2))
        badge_text = f"{scene.letter} {scene.asset.display_name.upper()}"
        badge_font = _fit_text(draw, badge_text, root, 24, 190)
        box = draw.textbbox((0, 0), badge_text, font=badge_font)
        draw.text((cx - (box[2] - box[0]) / 2, 199), badge_text, font=badge_font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(*dark, 255))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "JPEG", quality=91, optimize=True, progressive=True)
    return output_path
