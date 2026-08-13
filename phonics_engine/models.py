"""Small, explicit records shared by scanning, planning, and rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ObjectAsset:
    letter: str
    name: str
    display_name: str
    png_path: Path
    teacher_options: tuple[Path, ...]
    student_options: tuple[Path, ...]


@dataclass(frozen=True)
class EducationalScenePlan:
    index: int
    round_number: int
    letter: str
    asset: ObjectAsset
    teacher_audio: Path
    student_audio: Path
    teacher_duration: float
    student_duration: float
    teacher_start: float
    student_start: float
    duration: float
    teacher_echo: bool = False
    visual_seed: int = 0
    letter_has_eyes: bool = False
    font_variant: int = 0
    text_color_variant: int = 0
    border_variant: int = 0
    teaching_variant: int = 0
    overlay_variant: int = 0
    overlay_color_variant: int = 0
    layout_variant: int = 0
    motion_variant: int = 0


@dataclass(frozen=True)
class ClipScenePlan:
    index: int
    letter: str
    asset: ObjectAsset
    duration: float


@dataclass(frozen=True)
class TimelinePlan:
    intro_path: Path | None
    intro_duration: float
    background_path: Path
    selected_music: tuple[Path, ...]
    educational_scenes: tuple[EducationalScenePlan, ...]
    clip_scenes: tuple[ClipScenePlan, ...]
    outro_duration: float
    estimated_duration: float
    thumbnail_path: Path | None = None
