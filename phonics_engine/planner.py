"""Build one deterministic lesson plan from the discovered local assets."""

from __future__ import annotations

import logging
import random
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from .assets import AssetScanner, voice_take_key
from .config import EngineConfig
from .media import FFmpeg, MediaError
from .models import ClipScenePlan, EducationalScenePlan, ObjectAsset, TimelinePlan


class TimelinePlanner:
    def __init__(self, scanner: AssetScanner, media: FFmpeg, config: EngineConfig, rng: random.Random, logger: logging.Logger | None = None, usage_counts: dict[str, Counter[str]] | None = None) -> None:
        self.scanner = scanner
        self.media = media
        self.config = config
        self.rng = rng
        self.logger = logger or logging.getLogger("phonics_engine.planner")
        self._duration_cache: dict[Path, float] = {}
        self._invalid_duration_cache: dict[Path, MediaError] = {}
        self.usage_counts = usage_counts or {}

    def _path_key(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(Path(self.scanner.root).resolve()).as_posix().casefold()
        except ValueError:
            return resolved.name.casefold()

    def _balanced_choice(self, values: list, category: str, key) -> object:
        """Prefer less-used assets while keeping every candidate selectable.

        A strict minimum-only choice periodically collapsed a letter to one
        possible object and made the originality gate impossible to satisfy.
        Inverse-square weighting preserves balance without removing variety.
        """

        counts = self.usage_counts.get(category, Counter())
        minimum = min(counts[key(value)] for value in values)
        weights = [1.0 / (1 + counts[key(value)] - minimum) ** 2 for value in values]
        return self.rng.choices(values, weights=weights, k=1)[0]

    def _style_choice(self, category: str, count: int) -> int:
        return int(self._balanced_choice(list(range(count)), category, lambda value: str(value)))

    def _duration(self, path: Path) -> float:
        if path in self._invalid_duration_cache:
            raise self._invalid_duration_cache[path]
        if path not in self._duration_cache:
            try:
                self._duration_cache[path] = self.media.probe_duration(path)
            except MediaError as exc:
                self._invalid_duration_cache[path] = exc
                raise
        return self._duration_cache[path]

    def _choose_pair(self, asset: ObjectAsset) -> tuple[Path, Path]:
        """Prefer teacher/student recordings with exactly the same numbered take."""

        teachers = defaultdict(list)
        students = defaultdict(list)
        for path in asset.teacher_options:
            teachers[voice_take_key(path)].append(path)
        for path in asset.student_options:
            students[voice_take_key(path)].append(path)
        common = sorted(set(teachers) & set(students))
        if common:
            pairs = [(teacher, student) for take in common for teacher in teachers[take] for student in students[take]]
            teacher_counts = self.usage_counts.get("teacher_voices", Counter())
            student_counts = self.usage_counts.get("student_voices", Counter())
            pair_counts = [teacher_counts[self._path_key(pair[0])] + student_counts[self._path_key(pair[1])] for pair in pairs]
            minimum = min(pair_counts)
            weights = [1.0 / (1 + count - minimum) ** 2 for count in pair_counts]
            return self.rng.choices(pairs, weights=weights, k=1)[0]
        # The scanner has already established object-level identity.  This
        # fallback supports legacy libraries where teacher/student names have
        # the same object but differently numbered takes.
        self.logger.warning("no_matching_take_number letter=%s object=%s", asset.letter, asset.name)
        teacher = self._balanced_choice(list(asset.teacher_options), "teacher_voices", self._path_key)
        student = self._balanced_choice(list(asset.student_options), "student_voices", self._path_key)
        return teacher, student

    def _music_choices(self) -> tuple[Path, ...]:
        valid: list[Path] = []
        for path in self.scanner.music_files():
            try:
                self._duration(path)
            except MediaError as exc:
                self.logger.warning("skipping_invalid_music path=%s error=%s", path, exc)
            else:
                valid.append(path)
        if not valid:
            self.logger.warning("no_valid_background_music video will render with narration only")
            return ()
        count = min(len(valid), self.rng.randint(self.config.minimum_music_tracks, self.config.maximum_music_tracks))
        selected: list[Path] = []
        available = valid.copy()
        while available and len(selected) < count:
            chosen = self._balanced_choice(available, "music", self._path_key)
            selected.append(chosen)
            available.remove(chosen)
        return tuple(selected)

    def _intro(self) -> tuple[Path | None, float]:
        if not self.config.intro_enabled:
            return None, 0.0
        valid: list[tuple[Path, float]] = []
        for path in self.scanner.intro_files():
            try:
                valid.append((path, min(self._duration(path), self.config.intro_max_duration_seconds)))
            except MediaError as exc:
                self.logger.warning("skipping_invalid_intro path=%s error=%s", path, exc)
        if not valid:
            return None, 0.0
        return self._balanced_choice(valid, "intros", lambda item: self._path_key(item[0]))

    def _thumbnail(self) -> Path | None:
        choices = self.scanner.thumbnail_files()
        if not choices:
            return None
        return self._balanced_choice(choices, "thumbnails", self._path_key)

    def build(self, matches: dict[str, list[ObjectAsset]]) -> TimelinePlan:
        missing = [letter for letter in sorted(matches) if not matches[letter]]
        if missing:
            raise RuntimeError("Every alphabet letter needs one PNG plus teacher/student audio. Missing: " + ", ".join(missing))
        backgrounds = self.scanner.background_files()
        if not backgrounds:
            raise RuntimeError("No usable background images were found")
        background = self._balanced_choice(backgrounds, "backgrounds", self._path_key)  # exactly one per complete video
        intro_path, intro_duration = self._intro()
        music = self._music_choices()
        thumbnail = self._thumbnail()
        target = self.config.effective_target_duration()
        education: list[EducationalScenePlan] = []
        clips: list[ClipScenePlan] = []
        used_by_letter: dict[str, list[ObjectAsset]] = defaultdict(list)
        total = intro_duration
        index = 1
        round_number = 1
        letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

        while total < target and round_number <= self.config.maximum_rounds:
            for letter in letters:
                pool = [asset for asset in matches[letter] if asset not in used_by_letter[letter]]
                if not pool:
                    used_by_letter[letter].clear()
                    pool = matches[letter]
                asset = self._balanced_choice(
                    pool,
                    "objects",
                    lambda candidate: f"{candidate.letter}:{candidate.name}".casefold(),
                )
                used_by_letter[letter].append(asset)
                teacher, student = self._choose_pair(asset)
                teacher_duration = self._duration(teacher)
                student_duration = self._duration(student)
                teacher_start = self.config.scene_lead_in_seconds
                student_start = teacher_start + teacher_duration + self.config.teacher_student_gap
                duration = student_start + student_duration + self.config.post_speech_hold_seconds
                education.append(EducationalScenePlan(
                    index,
                    round_number,
                    letter,
                    asset,
                    teacher,
                    student,
                    teacher_duration,
                    student_duration,
                    teacher_start,
                    student_start,
                    duration,
                    visual_seed=self.rng.getrandbits(32),
                    letter_has_eyes=bool(self._style_choice("letter_eyes", 2)),
                    font_variant=self._style_choice("font_variants", self.config.font_variant_count),
                    text_color_variant=self._style_choice("text_colors", self.config.text_color_count),
                    border_variant=self._style_choice("border_variants", self.config.border_variant_count),
                    teaching_variant=self._style_choice("teaching_variants", self.config.teaching_variant_count),
                    overlay_variant=self._style_choice("overlay_variants", self.config.overlay_variant_count),
                    overlay_color_variant=self._style_choice("overlay_colors", self.config.overlay_color_count),
                    layout_variant=self._style_choice("layout_variants", self.config.layout_variant_count),
                    motion_variant=self._style_choice("motion_variants", self.config.motion_variant_count),
                ))
                clips.append(ClipScenePlan(index, letter, asset, self.config.pixabay_clip_duration))
                total += duration + self.config.pixabay_clip_duration
                index += 1
                if total >= target:
                    break
            round_number += 1
        if total < target:
            raise RuntimeError("maximum_rounds was reached before the target duration")
        # Echo is a short accent, not an effect on every teacher recording.
        # Choose a small, reproducible random subset for this finished video.
        echo_count = min(len(education), round(len(education) * self.config.teacher_echo_scene_fraction))
        echo_indexes = set(self.rng.sample(range(len(education)), echo_count)) if echo_count else set()
        education = [replace(scene, teacher_echo=position in echo_indexes) for position, scene in enumerate(education)]
        outro = self.config.ending_duration_seconds if self.config.outro_enabled else 0.0
        return TimelinePlan(intro_path, intro_duration, background, music, tuple(education), tuple(clips), outro, total + outro, thumbnail)
