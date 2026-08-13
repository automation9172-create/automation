from __future__ import annotations

import logging
import random
from collections import Counter
from pathlib import Path

import pytest

from phonics_engine.assets import AssetPaths
from phonics_engine.config import EngineConfig
from phonics_engine.models import ObjectAsset
from phonics_engine.pixabay import PixabayVideoCache
from phonics_engine.planner import TimelinePlanner


class _ChoiceRecorder:
    def __init__(self) -> None:
        self.weights: list[float] = []

    def choices(self, values, *, weights, k):
        self.weights = list(weights)
        return [values[-1]]


class _ReadableMedia:
    @staticmethod
    def is_readable_video(path: Path) -> bool:
        return True


def _paths(root: Path) -> AssetPaths:
    return AssetPaths(
        root=root / "assets",
        images=root / "assets" / "real_png",
        teacher=root / "assets" / "voices" / "teacher",
        student=root / "assets" / "voices" / "student_voice",
        backgrounds=root / "assets" / "backgrounds",
        music=root / "assets" / "back_musics",
        intros=root / "assets" / "intros",
        videos=root / "assets" / "videos",
        downloads=root / "downloaded_videos",
        thumbnails=root / "assets" / "thumbnails",
    )


def test_balanced_selection_keeps_more_used_candidates_selectable(tmp_path: Path) -> None:
    chooser = _ChoiceRecorder()
    planner = TimelinePlanner(
        scanner=object(),
        media=object(),
        config=EngineConfig(),
        rng=chooser,
        usage_counts={"objects": Counter({"a:apple": 0, "a:ant": 3})},
    )

    selected = planner._balanced_choice(
        ["a:apple", "a:ant"], "objects", lambda value: value
    )

    assert selected == "a:ant"
    assert chooser.weights == pytest.approx([1.0, 1.0 / 16.0])


def test_cross_object_byte_duplicates_are_rejected_without_deletion(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    apple_folder = paths.downloads / "A" / "apple"
    ball_folder = paths.downloads / "B" / "ball"
    apple_folder.mkdir(parents=True)
    ball_folder.mkdir(parents=True)
    duplicate_apple = apple_folder / "apple_1.mp4"
    safe_apple = apple_folder / "apple_2.mp4"
    duplicate_ball = ball_folder / "ball_1.mp4"
    duplicate_apple.write_bytes(b"cross-object duplicate")
    duplicate_ball.write_bytes(b"cross-object duplicate")
    safe_apple.write_bytes(b"reviewed apple clip")

    cache = PixabayVideoCache(
        paths,
        EngineConfig(),
        _ReadableMedia(),
        random.Random(1),
        logging.getLogger("test.video_safety"),
    )
    asset = ObjectAsset(
        "A", "apple", "Apple", paths.images / "A" / "apple.png",
        (paths.teacher / "A" / "apple1.mp3",),
        (paths.student / "A" / "apple1.mp3",),
    )

    assert cache._cached_exact_candidates(asset) == [safe_apple]
    assert duplicate_apple.is_file()
    assert duplicate_ball.is_file()


def test_provider_match_requires_complete_contiguous_object_phrase(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    asset = ObjectAsset(
        "F", "firetruck", "Fire Truck", paths.images / "F" / "fire_truck.png",
        (paths.teacher / "F" / "fire_truck1.mp3",),
        (paths.student / "F" / "fire_truck1.mp3",),
    )

    assert PixabayVideoCache._provider_match_confidence("red fire truck emergency vehicle", asset) == 1.0
    assert PixabayVideoCache._provider_match_confidence("fire scene with a delivery truck", asset) < 1.0
