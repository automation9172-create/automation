from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from phonics_engine.youtube_upload import (
    YOUTUBE_UPLOAD_SCOPE,
    _metadata,
    _normalise_publish_at,
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
    assert any(word in title for word in ("Apple", "Butterfly", "Mango", "Zebra", "A–Z"))
    assert "original teacher and child voice recordings" in description
    assert "phonics song" in tags
    assert YOUTUBE_UPLOAD_SCOPE == "https://www.googleapis.com/auth/youtube.upload"


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
