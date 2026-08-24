from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from phonics_engine.youtube_upload import (
    APPROVED_VIDEO_TITLES,
    YOUTUBE_MANAGE_SCOPE,
    YOUTUBE_SCOPES,
    YOUTUBE_THUMBNAIL_MAX_BYTES,
    YOUTUBE_UPLOAD_SCOPE,
    _metadata,
    _normalise_publish_at,
    _prepare_thumbnail_for_upload,
    _read_upload_history,
    _record_upload,
    scheduled_slot,
)


def test_upload_metadata_is_derived_from_the_completed_plan() -> None:
    manifest = {
        "plan_signature": "abcdef0123456789",
        "scenes": [
            {"letter": "A", "object": "Apple"},
            {"letter": "B", "object": "Butterfly"},
            {"letter": "M", "object": "Mango"},
            {"letter": "Z", "object": "Zebra"},
        ],
    }

    title, description, tags = _metadata(manifest)

    assert len(title) <= 100
    assert title == APPROVED_VIDEO_TITLES[0]
    assert "original teacher and child voice recordings" in description
    assert "phonics song" in tags
    assert YOUTUBE_UPLOAD_SCOPE == "https://www.googleapis.com/auth/youtube.upload"
    assert YOUTUBE_MANAGE_SCOPE == "https://www.googleapis.com/auth/youtube.force-ssl"
    assert YOUTUBE_SCOPES == (YOUTUBE_UPLOAD_SCOPE, YOUTUBE_MANAGE_SCOPE)


def test_upload_titles_rotate_deterministically_through_only_approved_titles() -> None:
    manifest = {"scenes": []}

    selected = [_metadata(manifest, title_index=index)[0] for index in range(12)]

    assert selected[:6] == list(APPROVED_VIDEO_TITLES)
    assert selected[6:] == list(APPROVED_VIDEO_TITLES)


def test_specific_approved_title_is_replaced_when_its_object_does_not_match() -> None:
    manifest = {
        "scenes": [
            {"letter": "B", "object": "Butterfly"},
            {"letter": "Z", "object": "Zinnia"},
        ]
    }

    assert _metadata(manifest, title_index=1)[0] == APPROVED_VIDEO_TITLES[2]
    assert _metadata(manifest, title_index=5)[0] == APPROVED_VIDEO_TITLES[3]


def test_upload_receipt_is_mirrored_and_idempotent(tmp_path) -> None:
    receipt = {"plan_signature": "abc123", "video_id": "video123"}
    _record_upload(tmp_path, receipt)
    _record_upload(tmp_path, receipt)

    entries = _read_upload_history(tmp_path)
    assert entries == [receipt]
    assert json.loads((tmp_path / ".phonics_work" / "youtube_upload_history.backup.json").read_text())["entries"]


def test_upload_history_never_silently_resets_when_both_copies_are_bad(tmp_path) -> None:
    work = tmp_path / ".phonics_work"
    work.mkdir()
    (work / "youtube_upload_history.json").write_text("bad")
    (work / "youtube_upload_history.backup.json").write_text("bad")

    with pytest.raises(RuntimeError, match="refusing"):
        _read_upload_history(tmp_path)


def test_scheduled_slots_are_exact_india_times_in_utc() -> None:
    now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)

    assert scheduled_slot("morning", now=now) == "2026-08-11T02:47:00Z"
    assert scheduled_slot("afternoon", now=now) == "2026-08-11T09:17:00Z"
    assert scheduled_slot("evening", now=now) == "2026-08-11T14:08:00Z"
    assert _normalise_publish_at("2026-08-11T08:17:00+05:30") == "2026-08-11T02:47:00Z"


def test_github_workflow_has_three_attempts_for_each_publication_slot() -> None:
    workflow = Path(".github/workflows/generate-and-upload.yml").read_text(encoding="utf-8")
    expected_crons = (
        "17 4 * * *", "17 5 * * *", "17 6 * * *",
        "47 10 * * *", "47 11 * * *", "47 12 * * *",
        "38 15 * * *", "38 16 * * *", "38 17 * * *",
    )

    for cron in expected_crons:
        assert f'- cron: "{cron}"' in workflow
    assert workflow.count('timezone: "Asia/Kolkata"') == 9
    assert "Verify unattended YouTube authorization before rendering" in workflow


def test_upload_receipt_rejects_two_videos_for_one_scheduled_slot(tmp_path) -> None:
    first = {
        "plan_signature": "first",
        "video_id": "video-one",
        "scheduled_publish_at": "2026-08-12T02:47:00Z",
    }
    second = {
        "plan_signature": "second",
        "video_id": "video-two",
        "scheduled_publish_at": "2026-08-12T02:47:00Z",
    }

    _record_upload(tmp_path, first)
    _record_upload(tmp_path, second)

    assert _read_upload_history(tmp_path) == [first]


def test_oversized_thumbnail_is_compressed_without_changing_source(tmp_path) -> None:
    source = tmp_path / "large.png"
    image = Image.effect_noise((2400, 1350), 100).convert("RGB")
    image.save(source, "PNG")
    original_size = source.stat().st_size
    assert original_size > YOUTUBE_THUMBNAIL_MAX_BYTES

    prepared = _prepare_thumbnail_for_upload(source, tmp_path / "prepared")

    assert prepared != source
    assert prepared.suffix == ".jpg"
    assert prepared.stat().st_size < YOUTUBE_THUMBNAIL_MAX_BYTES
    assert source.stat().st_size == original_size
