from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from .catalog import Asset
from .planner import ShortPlan


def _run(args: list[str]) -> None:
    result = subprocess.run(args, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-8:]
        raise RuntimeError("FFmpeg failed: " + " | ".join(detail))


def _duration(path: Path, fallback: float = 1.8) -> float:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return fallback


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%").replace("[", "\\[").replace("]", "\\]")


def _font() -> str:
    candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")]
    return str(next((item for item in candidates if item.exists()), candidates[0])).replace("\\", "/").replace(":", "\\:")


def _choose_file(folder: Path, index: int, suffixes: set[str]) -> Path | None:
    files = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in suffixes) if folder.exists() else []
    return files[index % len(files)] if files else None


def _choose_valid_music(music_dir: Path, letter_index: int) -> Path | None:
    suffixes = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    files = sorted(p for p in music_dir.iterdir() if p.is_file() and p.suffix.lower() in suffixes) if music_dir.exists() else []
    if not files:
        return None
    for offset in range(len(files)):
        candidate = files[(letter_index + offset) % len(files)]
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-i", str(candidate)],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        if probe.returncode == 0:
            return candidate
    return None


_ACCENT_COLORS = ["FFD700", "FF6B35", "7BC67E", "4FC3F7", "FF8A80", "CE93D8"]


def _scene(project_root: Path, asset: Asset, title: str, subtitle: str, output: Path, scene_index: int, style_index: int) -> None:
    background_dir = project_root / "assets" / "backrounds"
    music_dir = project_root / "assets" / "back_musics"
    background = _choose_file(background_dir, style_index * 11 + scene_index * 7, {".png", ".jpg", ".jpeg", ".webp"})
    # Each letter gets its own consistent background music track; skip corrupt files automatically
    letter_index = ord(asset.letter.lower()) - ord('a') if asset.letter else 0
    music = _choose_valid_music(music_dir, letter_index)
    if background is None or music is None or asset.teacher_voice is None:
        raise RuntimeError(f"Missing background/music/teacher voice for {asset.name}")
    student = asset.student_voice or asset.teacher_voice
    teacher_duration = _duration(asset.teacher_voice, 1.8)
    student_duration = _duration(student, 1.4)
    student_start = teacher_duration + 0.30
    length = min(8.0, max(4.8, student_start + student_duration + 0.25))
    font = _font()
    accent = _ACCENT_COLORS[letter_index % len(_ACCENT_COLORS)]
    letter_text = _quote(asset.letter.upper())
    vf = (
        # Clean vivid background — no blur
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"
        # Large PNG fills lower portion of screen
        "[1:v]format=rgba,scale=1000:1000:force_original_aspect_ratio=decrease[fg];"
        # Fast independent x/y bounce periods (1.2s and 1.5s) for lively animation
        "[bg][fg]overlay=x=40+18*sin(2*PI*t/1.2):y=700+14*cos(2*PI*t/1.5)[base];"
        # Huge letter at top ONLY — bright accent + thick white outline, NO bottom text
        f"[base]drawtext=fontfile='{font}':text='{letter_text}':fontcolor=0x{accent}:fontsize=900:x=(w-text_w)/2:y=25:borderw=22:bordercolor=white[v];"
        f"[2:a]aresample=48000,atrim=duration={teacher_duration:.3f},asetpts=PTS-STARTPTS,aecho=0.8:0.9:60:0.40,volume=1.25,afade=t=out:st={max(0.0, teacher_duration-0.20):.3f}:d=0.20[teacher];"
        f"[3:a]aresample=48000,atrim=duration={student_duration:.3f},asetpts=PTS-STARTPTS,aecho=0.8:0.9:60:0.40,volume=1.25,adelay={round(student_start * 1000)}:all=1[student];"
        f"[4:a]aresample=48000,volume=0.22,atrim=duration={length:.3f},asetpts=PTS-STARTPTS[music];"
        f"[teacher][student]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,volume=1.0,apad,atrim=duration={length:.3f}[voicebus];"
        f"[music]apad,atrim=duration={length:.3f}[musicpad];"
        "[voicebus][musicpad]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
    )
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(background),
        "-loop", "1", "-i", str(asset.image), "-i", str(asset.teacher_voice), "-i", str(student), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", vf, "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", "-r", "30", "-s", "1080x1920",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output),
    ])


def render_thumbnail(project_root: Path, plan: ShortPlan, output_dir: Path) -> Path:
    asset = plan.assets[0]
    background_dir = project_root / "assets" / "backrounds"
    background = _choose_file(background_dir, plan.style_index * 11, {".png", ".jpg", ".jpeg", ".webp"})
    if background is None or not asset.image.exists():
        raise RuntimeError(f"Missing background or image for thumbnail: {asset.name}")
    font = _font()
    letter_index = ord(asset.letter.lower()) - ord('a') if asset.letter else 0
    accent = _ACCENT_COLORS[letter_index % len(_ACCENT_COLORS)]
    letter_text = _quote(asset.letter.upper())
    thumb = output_dir / "thumbnail.jpg"
    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"
        "[1:v]format=rgba,scale=1000:1000:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=x=40:y=700[base];"
        f"[base]drawtext=fontfile='{font}':text='{letter_text}':fontcolor=0x{accent}:fontsize=900:x=(w-text_w)/2:y=25:borderw=22:bordercolor=white[v]"
    )
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", "1", "-i", str(background),
        "-loop", "1", "-i", str(asset.image),
        "-filter_complex", vf,
        "-map", "[v]", "-vframes", "1", "-s", "1080x1920",
        "-q:v", "2", str(thumb),
    ])
    return thumb


def render_plan(project_root: Path, plan: ShortPlan, output: Path, keep_temporary: bool = False) -> dict:
    workdir = output.parent / f".{output.stem}_scenes"
    workdir.mkdir(parents=True, exist_ok=True)
    scenes: list[Path] = []
    for index, asset in enumerate(plan.assets):
        if plan.theme == "abc":
            subtitle, title = f"{asset.letter.upper()} for {asset.name}", "LEARN ABC"
        elif plan.theme == "count":
            subtitle, title = f"{index + 1} {asset.name}", "COUNT WITH ME!"
        elif plan.theme == "vehicles":
            subtitle, title = f"VROOM! {asset.name}", "VEHICLE SONG"
        elif plan.theme == "animals":
            subtitle, title = f"THIS IS A {asset.name.upper()}!", "GUESS THE ANIMAL"
        elif plan.theme == "colors":
            subtitle, title = f"COLOR THE {asset.name.upper()}", "LEARN COLORS"
        else:
            subtitle, title = f"SAY {asset.name.upper()}!", "GUESS IT!"
        scene = workdir / f"scene-{index:02d}.mp4"
        _scene(project_root, asset, title, subtitle, scene, index, plan.style_index)
        scenes.append(scene)
    concat = workdir / "concat.txt"
    concat.write_text("".join(f"file '{scene.as_posix()}'\n" for scene in scenes), encoding="utf-8")
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(output)])
    thumbnail = render_thumbnail(project_root, plan, output.parent)
    manifest = {"signature": plan.signature, "title": plan.title, "theme": plan.theme, "output": str(output), "thumbnail": str(thumbnail), "assets": [asset.name for asset in plan.assets]}
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not keep_temporary:
        shutil.rmtree(workdir, ignore_errors=True)
    return manifest
