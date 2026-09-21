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


def _scene(project_root: Path, asset: Asset, title: str, subtitle: str, output: Path, scene_index: int, style_index: int) -> None:
    background_dir = project_root / "assets" / "backrounds"
    music_dir = project_root / "assets" / "back_musics"
    background = _choose_file(background_dir, style_index * 11 + scene_index * 7, {".png", ".jpg", ".jpeg", ".webp"})
    music = _choose_file(music_dir, style_index * 5 + scene_index, {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"})
    if background is None or music is None or asset.teacher_voice is None:
        raise RuntimeError(f"Missing background/music/teacher voice for {asset.name}")
    student = asset.student_voice or asset.teacher_voice
    teacher_duration = _duration(asset.teacher_voice, 1.8)
    student_duration = _duration(student, 1.4)
    student_start = teacher_duration + 0.30
    length = min(8.0, max(4.8, student_start + student_duration + 0.25))
    x = 160 + (scene_index % 2) * 40
    font = _font()
    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=2:1[bg];"
        "[1:v]format=rgba,scale=780:780:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=x={x}+18*sin(2*PI*t/{length:.3f}):y=445+12*cos(2*PI*t/{length:.3f})[base];"
        "[base]drawbox=x=45:y=70:w=990:h=185:color=0x18233bEE:t=fill,"
        f"drawtext=fontfile='{font}':text='{_quote(title)}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=115,"
        "drawbox=x=90:y=1330:w=900:h=300:color=0xFFF4C4DD:t=fill,"
        f"drawtext=fontfile='{font}':text='{_quote(subtitle)}':fontcolor=0x17213B:fontsize=58:x=(w-text_w)/2:y=1435[v];"
        f"[2:a]aresample=48000,atrim=duration={teacher_duration:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0.0, teacher_duration-0.20):.3f}:d=0.20[teacher];"
        f"[3:a]aresample=48000,atrim=duration={student_duration:.3f},asetpts=PTS-STARTPTS,adelay={round(student_start * 1000)}:all=1[student];"
        f"[4:a]aresample=48000,volume=0.24,atrim=duration={length:.3f},asetpts=PTS-STARTPTS[music];"
        f"[teacher][student]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,volume=0.82,apad,atrim=duration={length:.3f}[voicebus];"
        f"[music]apad,atrim=duration={length:.3f}[musicpad];"
        "[voicebus][musicpad]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
    )
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(background),
        "-loop", "1", "-i", str(asset.image), "-i", str(asset.teacher_voice), "-i", str(student), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", vf, "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", "-r", "30", "-s", "1080x1920",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output),
    ])


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
    manifest = {"signature": plan.signature, "title": plan.title, "theme": plan.theme, "output": str(output), "assets": [asset.name for asset in plan.assets]}
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not keep_temporary:
        shutil.rmtree(workdir, ignore_errors=True)
    return manifest
