from __future__ import annotations

from pathlib import Path
import json

from phonics_engine.history import ContentHistory
from phonics_engine.models import EducationalScenePlan, ObjectAsset, TimelinePlan


def _plan(tmp_path: Path, *, visual_seed: int) -> TimelinePlan:
    asset = ObjectAsset("A", "apple", "Apple", tmp_path / "apple.png", (tmp_path / "apple1.mp3",), (tmp_path / "apple1.mp3",))
    scene = EducationalScenePlan(
        1, 1, "A", asset, asset.teacher_options[0], asset.student_options[0],
        1.0, 1.0, 0.2, 1.4, 2.6, visual_seed=visual_seed,
    )
    return TimelinePlan(None, 0.0, tmp_path / "background.png", (), (scene,), (), 0.0, 2.6, tmp_path / "thumb.png")


def test_history_rejects_cosmetic_repackaging_of_same_route(tmp_path: Path) -> None:
    history = ContentHistory(tmp_path)
    first = _plan(tmp_path, visual_seed=1)
    cosmetic_variant = _plan(tmp_path, visual_seed=999)

    history.record(first, "channel_1", tmp_path / "first.mp4")

    assert history.contains(history.signature(cosmetic_variant))
    assert history.contains_route(history.route_signature(cosmetic_variant))
    assert history.usage_counts("objects")["a:apple"] == 1


def test_history_is_mirrored_and_recovers_from_a_damaged_primary(tmp_path: Path) -> None:
    history = ContentHistory(tmp_path)
    history.record(_plan(tmp_path, visual_seed=1), "channel_1", tmp_path / "first.mp4")

    assert history.path.is_file()
    assert history.backup_path.is_file()
    history.path.write_text("damaged", encoding="utf-8")

    recovered = ContentHistory(tmp_path)
    assert recovered.video_count == 1
    assert recovered.usage_counts("objects")["a:apple"] == 1
    assert json.loads(recovered.backup_path.read_text(encoding="utf-8"))["entries"]


def _alphabet_plan(tmp_path: Path, changed_objects: int, changed_lessons: int, changed_visuals: int = 0) -> TimelinePlan:
    scenes = []
    for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        object_name = f"object_{index}_{'new' if index < changed_objects else 'base'}"
        asset = ObjectAsset(
            letter,
            object_name,
            object_name.title(),
            tmp_path / f"{object_name}.png",
            (tmp_path / f"{object_name}_teacher.mp3",),
            (tmp_path / f"{object_name}_student.mp3",),
        )
        scenes.append(EducationalScenePlan(
            index + 1, 1, letter, asset, asset.teacher_options[0], asset.student_options[0],
            1.0, 1.0, 0.2, 1.4, 2.6,
            teaching_variant=1 if index < changed_lessons else 0,
            overlay_variant=1 if index < changed_visuals else 0,
        ))
    return TimelinePlan(None, 0.0, tmp_path / "background.png", (), tuple(scenes), (), 0.0, 67.6)


def test_20k_gate_requires_object_and_content_distance(tmp_path: Path) -> None:
    history = ContentHistory(tmp_path)
    base = _alphabet_plan(tmp_path, changed_objects=0, changed_lessons=0)
    history.record(base, "channel_1", tmp_path / "base.mp4")

    too_close = _alphabet_plan(tmp_path, changed_objects=11, changed_lessons=21)
    accepted = _alphabet_plan(tmp_path, changed_objects=12, changed_lessons=21)

    assert history.originality_check(too_close, 12, 21)[0] is False
    passes, metrics = history.originality_check(accepted, 12, 21)
    assert passes is True
    assert metrics["closest_object_distance"] == 12
    assert metrics["closest_content_distance"] == 21


def test_visual_gate_is_additional_to_substantive_gates(tmp_path: Path) -> None:
    history = ContentHistory(tmp_path)
    history.record(_alphabet_plan(tmp_path, 0, 0, 0), "channel_1", tmp_path / "base.mp4")

    too_close = _alphabet_plan(tmp_path, 12, 21, 17)
    accepted = _alphabet_plan(tmp_path, 12, 21, 18)

    passes, metrics = history.originality_check(too_close, 12, 21, 18)
    assert passes is False
    assert metrics["closest_visual_distance"] == 17
    passes, metrics = history.originality_check(accepted, 12, 21, 18)
    assert passes is True
    assert metrics["closest_visual_distance"] == 18


def test_history_uses_portable_relative_asset_keys(tmp_path: Path) -> None:
    history = ContentHistory(tmp_path)
    plan = _alphabet_plan(tmp_path, 0, 0)
    history.record(plan, "channel_1", tmp_path / "output.mp4")

    assert history.usage_counts("backgrounds")["background.png"] == 1
    assert all("\\" not in key for key in history.usage_counts("teacher_voices"))
