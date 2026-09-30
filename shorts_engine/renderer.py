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


def _music_candidates(music_dir: Path, letter_index: int) -> list[Path]:
    suffixes = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    files = sorted(p for p in music_dir.iterdir() if p.is_file() and p.suffix.lower() in suffixes) if music_dir.exists() else []
    if not files:
        return []
    n = len(files)
    return [files[(letter_index + offset) % n] for offset in range(n)]


_ACCENT_COLORS = ["FFD700", "FF6B35", "7BC67E", "4FC3F7", "FF8A80", "CE93D8"]


_ECHO_PARAMS = [
    (60, 0.40, 1.25),  # style 0 — standard echo
    (45, 0.32, 1.28),  # style 1 — tight short echo, louder
    (75, 0.45, 1.22),  # style 2 — longer reverb feel
    (55, 0.36, 1.30),  # style 3 — punchy mid echo
]


def _scene(project_root: Path, asset: Asset, title: str, subtitle: str, output: Path, scene_index: int, style_index: int) -> None:
    background_dir = project_root / "assets" / "backrounds"
    music_dir = project_root / "assets" / "back_musics"
    letter_index = ord(asset.letter.lower()) - ord('a') if asset.letter else 0
    # Unique background per letter+style+scene — avoids reused-content detection
    bg_index = letter_index * 7 + style_index * 13 + scene_index * 3
    background = _choose_file(background_dir, bg_index, {".png", ".jpg", ".jpeg", ".webp"})
    music_list = _music_candidates(music_dir, letter_index)
    if background is None or not music_list or asset.teacher_voice is None:
        raise RuntimeError(f"Missing background/music/teacher voice for {asset.name}")
    student = asset.student_voice or asset.teacher_voice
    teacher_duration = _duration(asset.teacher_voice, 1.8)
    student_duration = _duration(student, 1.4)
    student_start = teacher_duration + 0.30
    length = min(8.0, max(4.8, student_start + student_duration + 0.25))
    font = _font()
    # Accent color varies by both letter and style — same letter looks different across videos
    accent = _ACCENT_COLORS[(letter_index + style_index * 2) % len(_ACCENT_COLORS)]
    echo_delay, echo_decay, voice_vol = _ECHO_PARAMS[style_index % len(_ECHO_PARAMS)]
    letter_text = _quote(asset.letter.upper())
    # 4 distinct PNG animations cycling per style_index
    anim = style_index % 4
    if anim == 0:
        ox, oy = "80+12*sin(2*PI*t/1.2)", "720+14*cos(2*PI*t/1.5)"
    elif anim == 1:
        ox, oy = "80+40*sin(2*PI*t/1.0)", "720+6*cos(2*PI*t/2.0)"
    elif anim == 2:
        ox, oy = "80+22*sin(2*PI*t/1.4)", "720+22*sin(2*PI*t/2.8)"
    else:
        ox, oy = "80+8*sin(2*PI*t/1.5)", "720+20*abs(sin(PI*t/0.55))"
    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"
        "[1:v]format=rgba,scale=980:980:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=x={ox}:y={oy}[base];"
        # Letter moved down to y=100 — not clipped at very top edge
        f"[base]drawtext=fontfile='{font}':text='{letter_text}':fontcolor=0x{accent}:fontsize=900:x=(w-text_w)/2:y=100:borderw=22:bordercolor=white[v];"
        f"[2:a]aresample=48000,atrim=duration={teacher_duration:.3f},asetpts=PTS-STARTPTS,aecho=0.8:0.9:{echo_delay}:{echo_decay:.2f},volume={voice_vol:.2f},afade=t=out:st={max(0.0, teacher_duration-0.20):.3f}:d=0.20[teacher];"
        f"[3:a]aresample=48000,atrim=duration={student_duration:.3f},asetpts=PTS-STARTPTS,aecho=0.8:0.9:{echo_delay}:{echo_decay:.2f},volume={voice_vol:.2f},adelay={round(student_start * 1000)}:all=1[student];"
        f"[4:a]aresample=48000,volume=0.22,atrim=duration={length:.3f},asetpts=PTS-STARTPTS[music];"
        f"[teacher][student]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,volume=1.0,apad,atrim=duration={length:.3f}[voicebus];"
        f"[music]apad,atrim=duration={length:.3f}[musicpad];"
        "[voicebus][musicpad]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
    )
    last_err: Exception = RuntimeError("No music candidates")
    for music in music_list:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(background),
            "-loop", "1", "-i", str(asset.image), "-i", str(asset.teacher_voice), "-i", str(student), "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", vf, "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", "-r", "30", "-s", "1080x1920",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output),
        ]
        try:
            _run(cmd)
            return
        except RuntimeError as err:
            # Corrupt/invalid music file — try next candidate
            if "Invalid data" in str(err) or "Error opening input" in str(err) or "MPEG audio" in str(err):
                last_err = err
                continue
            raise
    raise last_err


def render_thumbnail(project_root: Path, plan: ShortPlan, output_dir: Path) -> Path:
    asset = plan.assets[0]
    background_dir = project_root / "assets" / "backrounds"
    letter_index = ord(asset.letter.lower()) - ord('a') if asset.letter else 0
    # Same bg formula as _scene so thumbnail background matches first scene
    bg_index = letter_index * 7 + plan.style_index * 13
    background = _choose_file(background_dir, bg_index, {".png", ".jpg", ".jpeg", ".webp"})
    if background is None or not asset.image.exists():
        raise RuntimeError(f"Missing background or image for thumbnail: {asset.name}")
    thumb = output_dir / "thumbnail.jpg"
    # PNG fills most of the frame — centered, large — NO letter text (just the image like reference channel)
    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"
        "[1:v]format=rgba,scale=1060:1060:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=x=10:y=430[v]"
    )
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", "1", "-i", str(background),
        "-loop", "1", "-i", str(asset.image),
        "-filter_complex", vf,
        "-map", "[v]", "-vframes", "1", "-s", "1080x1920",
        "-qscale:v", "1", str(thumb),
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
