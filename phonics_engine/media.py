"""Narrow, checked wrappers around FFmpeg and FFprobe."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


class MediaError(RuntimeError):
    pass


class FFmpeg:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("phonics_engine.media")

    def validate_available(self) -> None:
        missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
        if missing:
            raise MediaError("Install FFmpeg and add it to PATH. Missing: " + ", ".join(missing))

    def run(self, command: list[str], purpose: str, *, timeout_seconds: float | None = None) -> None:
        self.logger.info("%s", purpose)
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise MediaError(f"FFmpeg was not found while {purpose}") from exc
        except subprocess.TimeoutExpired as exc:
            # subprocess.run terminates and reaps the FFmpeg child before it
            # raises.  That prevents one unhealthy PNG/filter from leaving a
            # renderer permanently stuck or from accumulating orphan encoders.
            limit = f" after {timeout_seconds:.0f} seconds" if timeout_seconds else ""
            raise MediaError(f"{purpose} timed out{limit}; FFmpeg was stopped") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "FFmpeg exited with an error").strip()
            raise MediaError(f"{purpose} failed (exit {exc.returncode}):\n{detail[-3000:]}") from exc
        if completed.stderr:
            self.logger.debug("ffmpeg: %s", completed.stderr)

    def probe_duration(self, path: Path) -> float:
        command = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ]
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
            duration = float(completed.stdout.strip())
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or ""
            raise MediaError(f"Reading duration: {path.name} failed: {detail}".strip()) from exc
        if duration <= 0:
            raise MediaError(f"Reading duration: {path.name} returned a non-positive duration")
        return duration

    def is_readable_video(self, path: Path) -> bool:
        try:
            self.probe_duration(path)
        except MediaError:
            return False
        return True


def write_concat_list(paths: Iterable[Path], destination: Path) -> None:
    """Create an FFmpeg concat demuxer list, escaping apostrophes safely."""

    destination.write_text(
        "".join("file '" + str(path.resolve()).replace("'", "'\\\\''") + "'\n" for path in paths),
        encoding="utf-8",
    )
