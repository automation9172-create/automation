from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .history import read_json


# Daily upload schedule (IST hours, cumulative quota):
#   Morning   05-10 → up to 2 uploads  (total 0→2)
#   Best time 12-17 → up to 1 upload   (total 2→3)
#   Night     19-23 → up to 2 uploads  (total 3→5)
_SLOTS = [
    (range(5, 11),  2),   # morning
    (range(12, 18), 3),   # best time (noon-6 PM)
    (range(19, 24), 5),   # night
]


def _slot_name(ist_hour: int) -> str:
    if ist_hour in range(5, 11):
        return "morning"
    if ist_hour in range(12, 18):
        return "best"
    if ist_hour in range(19, 24):
        return "night"
    return "off"


def _slot_cumulative_quota(ist_hour: int) -> int:
    for hours, quota in _SLOTS:
        if ist_hour in hours:
            return quota
    return 0


def _uploaded_count(history: dict, day: str) -> int:
    return sum(1 for item in history.get("shorts", []) if item.get("date") == day and item.get("status") == "uploaded")


def _uploaded_count_from_batch(root: Path, day: str) -> int:
    for path in (root / ".shorts_work").glob("daily_batch_*.json"):
        data = read_json(path, {})
        if data.get("date") == day:
            return sum(1 for status in data.get("status", {}).values() if status.get("status") == "uploaded")
    return 0


def _pending_batch_days(root: Path, today: date) -> list[str]:
    pending: list[str] = []
    for path in sorted((root / ".shorts_work").glob("daily_batch_*.json")):
        data = read_json(path, {})
        value = data.get("date")
        if not value:
            continue
        try:
            day = date.fromisoformat(value)
        except ValueError:
            continue
        total_uploaded = sum(1 for status in data.get("status", {}).values() if status.get("status") == "uploaded")
        if today - timedelta(days=2) <= day < today and total_uploaded < 5:
            pending.append(value)
    return pending


def main() -> int:
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    today = now.date()
    today_key = today.isoformat()
    ist_hour = now.hour

    history = read_json(Path(".shorts_work") / "upload_history.json", {"shorts": []})
    uploaded_today = max(
        _uploaded_count(history, today_key),
        _uploaded_count_from_batch(Path("."), today_key),
    )

    slot = _slot_name(ist_hour)
    cumulative_quota = _slot_cumulative_quota(ist_hour)

    pending = _pending_batch_days(Path("."), today)

    if pending:
        # Incomplete batch from a previous day — finish it in current slot
        run_date = min(pending)
        max_upload = max(1, cumulative_quota - uploaded_today) if cumulative_quota > 0 else 2
        should_run = True
    else:
        run_date = today_key
        max_upload = max(0, cumulative_quota - uploaded_today)
        should_run = max_upload > 0

    print(f"run={'true' if should_run else 'false'}")
    print(f"run_date={run_date}")
    print(f"slot={slot}")
    print(f"max_upload={max_upload}")
    print(f"uploaded_today={uploaded_today}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
