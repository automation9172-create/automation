"""Discover image and voice assets using their actual filenames."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .config import EngineConfig
from .models import ObjectAsset

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def normalize_asset_name(filename: str | Path) -> str:
    """Return the shared object key, stripping a final take number only.

    ``ice_cream2.mp3`` and ``ice-cream-003.wav`` therefore match
    ``ice cream.png``.  The exact-key approach deliberately does *not* make
    ``apple`` match ``airplane``.
    """

    stem = Path(filename).stem.casefold().strip()
    stem = re.sub(r"(?:[ _-]*\d+)$", "", stem)
    return re.sub(r"[^a-z0-9]+", "", stem)


def voice_take_key(filename: str | Path) -> str:
    """Keep the take suffix so teacher and student use the same numbered take."""

    return re.sub(r"[^a-z0-9]+", "", Path(filename).stem.casefold())


def display_name_from_key(key: str) -> str:
    return re.sub(r"(?<=\w)([A-Z])", r" \1", key.replace("_", " ")).title()


@dataclass(frozen=True)
class AssetPaths:
    root: Path
    images: Path
    teacher: Path
    student: Path
    backgrounds: Path
    music: Path
    intros: Path
    videos: Path
    downloads: Path
    thumbnails: Path


class AssetScanner:
    """Read the project layout, including the existing ``backrounds`` spelling."""

    def __init__(self, root: Path, config: EngineConfig | None = None, logger: logging.Logger | None = None) -> None:
        self.root = Path(root)
        self.config = config or EngineConfig()
        self.logger = logger or logging.getLogger("phonics_engine.assets")
        asset_root = self.root / self.config.asset_root if self.config.asset_root else self.root / "assets"
        if not asset_root.exists():
            asset_root = self.root
        self.paths = AssetPaths(
            root=asset_root,
            images=asset_root / "real_png",
            teacher=asset_root / "voices" / "teacher",
            student=asset_root / "voices" / "student_voice",
            backgrounds=self._first_existing(asset_root / "backrounds", asset_root / "backgrounds"),
            music=asset_root / "back_musics",
            intros=asset_root / "intros",
            videos=asset_root / "videos",
            downloads=self.root / "downloaded_videos",
            thumbnails=asset_root / "thumbnails",
        )

    @staticmethod
    def _first_existing(*choices: Path) -> Path:
        return next((path for path in choices if path.exists()), choices[0])

    @staticmethod
    def media_files(folder: Path, extensions: set[str]) -> list[Path]:
        if not folder.exists():
            return []
        return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() in extensions)

    def validate_inputs(self) -> None:
        required = (self.paths.images, self.paths.teacher, self.paths.student, self.paths.backgrounds, self.paths.music)
        missing = [str(path) for path in required if not path.is_dir()]
        if missing:
            raise FileNotFoundError("Missing required asset folder(s): " + ", ".join(missing))
        if not self.background_files():
            raise FileNotFoundError(f"No background images in {self.paths.backgrounds}")

    def background_files(self) -> list[Path]:
        return self.media_files(self.paths.backgrounds, IMAGE_EXTENSIONS)

    def intro_files(self) -> list[Path]:
        return self.media_files(self.paths.intros, VIDEO_EXTENSIONS)

    def thumbnail_files(self) -> list[Path]:
        return self.media_files(self.paths.thumbnails, IMAGE_EXTENSIONS)

    def music_files(self) -> list[Path]:
        return self.media_files(self.paths.music, AUDIO_EXTENSIONS)

    def scan(self) -> dict[str, list[ObjectAsset]]:
        matches: dict[str, list[ObjectAsset]] = {chr(code): [] for code in range(ord("A"), ord("Z") + 1)}
        for letter in matches:
            image_files = self.media_files(self.paths.images / letter, IMAGE_EXTENSIONS)
            teachers = self.media_files(self.paths.teacher / letter, AUDIO_EXTENSIONS)
            students = self.media_files(self.paths.student / letter, AUDIO_EXTENSIONS)
            teacher_by_name: dict[str, list[Path]] = defaultdict(list)
            student_by_name: dict[str, list[Path]] = defaultdict(list)
            for path in teachers:
                teacher_by_name[normalize_asset_name(path)].append(path)
            for path in students:
                student_by_name[normalize_asset_name(path)].append(path)
            for image in image_files:
                name = normalize_asset_name(image)
                teacher_options = tuple(teacher_by_name.get(name, []))
                student_options = tuple(student_by_name.get(name, []))
                if not teacher_options:
                    self.logger.warning("missing_teacher_voice letter=%s object=%s png=%s", letter, name, image)
                if not student_options:
                    self.logger.warning("missing_student_voice letter=%s object=%s png=%s", letter, name, image)
                if teacher_options and student_options:
                    matches[letter].append(ObjectAsset(letter, name, display_name_from_key(image.stem), image, teacher_options, student_options))
        return matches
