from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


def normalize_name(value: str) -> str:
    stem = Path(value).stem.lower().strip()
    stem = re.sub(r"(?:[ _-]*\d+)$", "", stem)
    return re.sub(r"[^a-z0-9]+", "", stem)


def display_name(value: str) -> str:
    return re.sub(r"[_-]+", " ", value).strip().title()


@dataclass(frozen=True)
class Asset:
    letter: str
    key: str
    name: str
    image: Path
    teacher_voice: Path | None
    student_voice: Path | None


def _files_by_key(folder: Path) -> dict[str, Path]:
    if not folder.exists():
        return {}
    result: dict[str, Path] = {}
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a"}:
            result.setdefault(normalize_name(path.stem), path)
    return result


def scan_assets(project_root: Path) -> list[Asset]:
    root = project_root / "assets"
    image_root = root / "real_png"
    teacher_root = root / "voices" / "teacher"
    student_root = root / "voices" / "student_voice"
    assets: list[Asset] = []
    for letter_dir in sorted(image_root.glob("[a-z]")):
        letter = letter_dir.name.lower()
        teachers = _files_by_key(teacher_root / letter)
        students = _files_by_key(student_root / letter)
        for image in sorted(letter_dir.iterdir()):
            if not image.is_file() or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            key = normalize_name(image.stem)
            assets.append(Asset(letter, key, display_name(image.stem), image, teachers.get(key), students.get(key)))
    return assets
