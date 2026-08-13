from __future__ import annotations

import logging
import random
from pathlib import Path

from PIL import Image

from phonics_engine.config import EngineConfig
from phonics_engine.models import EducationalScenePlan, ObjectAsset
from phonics_engine.simple_renderer import SimpleVideoAssembler


def test_build_text_overlay_writes_png_with_text(tmp_path: Path) -> None:
    assembler = SimpleVideoAssembler(
        EngineConfig(resolution=(1920, 1080)),
        media=object(),
        logger=logging.getLogger("test"),
        root=tmp_path,
    )

    overlay_path = tmp_path / "overlay.png"
    assembler._build_text_overlay("A", "APPLE", overlay_path)

    assert overlay_path.exists()
    with Image.open(overlay_path) as image:
        assert image.size == (1920, 1080)
        assert image.mode == "RGBA"
        assert image.getbbox() is not None


def test_falling_sheet_includes_subtle_colored_motion_marks(tmp_path: Path) -> None:
    object_path = tmp_path / "apple.png"
    Image.new("RGBA", (100, 100), (220, 30, 30, 255)).save(object_path)
    asset = ObjectAsset(
        "A", "apple", "Apple", object_path,
        (tmp_path / "teacher.mp3",), (tmp_path / "student.mp3",),
    )
    seed = next(
        value
        for value in range(100)
        if random.Random(f"motion-lines:{value}:A:3").random() < 0.82
    )
    scene = EducationalScenePlan(
        1, 1, "A", asset, asset.teacher_options[0], asset.student_options[0],
        1.0, 1.0, 0.2, 1.4, 2.6, visual_seed=seed,
        overlay_variant=3, overlay_color_variant=2,
    )
    assembler = SimpleVideoAssembler(
        EngineConfig(resolution=(640, 360)), object(), logging.getLogger("test"), tmp_path
    )
    output = tmp_path / "falling_sheet.png"

    assembler._build_falling_rain_sheet(scene, output)

    with Image.open(output).convert("RGBA") as sheet:
        subtle_blue = sum(
            1
            for red, green, blue, alpha in sheet.getdata()
            if blue > red and blue >= green and 15 <= alpha <= 55
        )
    assert subtle_blue > 100
