from __future__ import annotations

from pathlib import Path

from PIL import Image

from phonics_engine.models import EducationalScenePlan, ObjectAsset, TimelinePlan
from phonics_engine.thumbnail import THUMBNAIL_SIZE, _featured_scenes, compose_discovery_thumbnail


def _plan(tmp_path: Path) -> TimelinePlan:
    source_thumbnail = tmp_path / "base.png"
    Image.new("RGB", THUMBNAIL_SIZE, (85, 170, 225)).save(source_thumbnail)
    scenes = []
    for index, (letter, name, color) in enumerate(
        (("A", "apple", "red"), ("B", "butterfly", "orange"), ("C", "camera", "blue"), ("D", "drum", "green"))
    ):
        png = tmp_path / f"{name}.png"
        Image.new("RGBA", (260, 220), color).save(png)
        asset = ObjectAsset(letter, name, name.title(), png, (), ())
        scenes.append(EducationalScenePlan(index, 1, letter, asset, png, png, 1, 1, 0, 1, 3))
    return TimelinePlan(None, 0, source_thumbnail, (), tuple(scenes), (), 0, 10, source_thumbnail)


def test_composed_thumbnail_is_distinct_mobile_ready_jpeg(tmp_path) -> None:
    plan = _plan(tmp_path)
    output = compose_discovery_thumbnail(plan, tmp_path / "result.jpg", "plan-one", tmp_path)

    assert output.is_file()
    assert output.stat().st_size < 2 * 1024 * 1024
    with Image.open(output) as image:
        assert image.size == THUMBNAIL_SIZE
        assert image.format == "JPEG"


def test_featured_thumbnail_objects_are_plan_matched_and_exclude_fixed_apple(tmp_path) -> None:
    plan = _plan(tmp_path)
    featured = _featured_scenes(plan, "plan-two")

    assert len(featured) == 3
    assert {scene.letter for scene in featured} == {"B", "C", "D"}
