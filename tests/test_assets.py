"""Contract tests for the on-disk PNG/voice asset matcher."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from phonics_engine.assets import AssetScanner, normalize_asset_name
from phonics_engine.config import EngineConfig


@pytest.fixture
def asset_project(tmp_path: Path) -> Path:
    """Create the minimum structurally valid project tree for scanner tests.

    Files are deliberately empty: AssetScanner only discovers filenames and
    extensions, so tests remain fast and do not depend on media codecs.
    """

    for relative_path in (
        "real_png",
        "voices/teacher",
        "voices/student_voice",
        "backgrounds",
        "back_musics",
    ):
        (tmp_path / relative_path).mkdir(parents=True)

    (tmp_path / "backgrounds" / "theme.png").touch()
    (tmp_path / "back_musics" / "music.mp3").touch()
    return tmp_path


def _touch(project_root: Path, relative_path: str) -> Path:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _scanner(project_root: Path) -> AssetScanner:
    # The intro is outside the matching concern and is disabled so the fixture
    # need not create a valid video file.
    return AssetScanner(project_root, config=EngineConfig(intro_enabled=False))


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("APPLE.PNG", "apple"),
        ("ice cream.png", "icecream"),
        ("ice_cream2.wav", "icecream"),
        ("ice-cream-003.mp3", "icecream"),
        ("Apple_2.MP3", "apple"),
        # Numeric characters are removed only when they are a suffix.
        ("3d-ball.png", "3dball"),
    ],
)
def test_normalize_asset_name_ignores_extension_case_separators_and_suffixes(
    filename: str, expected: str
) -> None:
    assert normalize_asset_name(filename) == expected


def test_scan_matches_audio_by_exact_normalized_base_name(asset_project: Path) -> None:
    """Apple must never absorb similarly prefixed ``airplane`` recordings."""

    _touch(asset_project, "real_png/A/Apple.png")
    _touch(asset_project, "real_png/A/air-plane.png")

    _touch(asset_project, "voices/teacher/A/apple1.wav")
    _touch(asset_project, "voices/teacher/A/APPLE_2.MP3")
    _touch(asset_project, "voices/teacher/A/airplane1.wav")
    _touch(asset_project, "voices/student_voice/A/apple1.wav")
    _touch(asset_project, "voices/student_voice/A/airplane_2.wav")

    matches = _scanner(asset_project).scan()
    by_name = {match.name: match for match in matches["A"]}

    assert set(by_name) == {"apple", "airplane"}
    assert {voice.name for voice in by_name["apple"].teacher_options} == {
        "apple1.wav",
        "APPLE_2.MP3",
    }
    assert {voice.name for voice in by_name["apple"].student_options} == {"apple1.wav"}
    assert {voice.name for voice in by_name["airplane"].teacher_options} == {
        "airplane1.wav"
    }
    assert {voice.name for voice in by_name["airplane"].student_options} == {
        "airplane_2.wav"
    }


def test_scan_skips_objects_with_missing_voice_and_logs_why(
    asset_project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ``cat`` is complete; apple has no teacher and banana has no student.
    _touch(asset_project, "real_png/A/apple.png")
    _touch(asset_project, "real_png/A/banana.png")
    _touch(asset_project, "real_png/A/cat.png")
    _touch(asset_project, "voices/student_voice/A/apple1.wav")
    _touch(asset_project, "voices/teacher/A/banana1.wav")
    _touch(asset_project, "voices/teacher/A/cat1.wav")
    _touch(asset_project, "voices/student_voice/A/cat2.wav")

    with caplog.at_level(logging.WARNING, logger="phonics_engine.assets"):
        matches = _scanner(asset_project).scan()

    assert [match.name for match in matches["A"]] == ["cat"]
    warnings = "\n".join(record.getMessage() for record in caplog.records)
    assert "missing_teacher_voice letter=A object=apple" in warnings
    assert "missing_student_voice letter=A object=banana" in warnings
