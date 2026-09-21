from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from .catalog import scan_assets
from .history import record, write_json
from .planner import plan_batch
from .renderer import render_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and optionally upload daily vertical Shorts")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--date", type=lambda value: date.fromisoformat(value), default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--privacy-status", choices=["private", "unlisted", "public"], default="public")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-temporary-files", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    history_dir = root / ".shorts_work"
    plan_history = history_dir / "plan_history.json"
    upload_history = history_dir / "upload_history.json"
    output_dir = root / "shorts_outputs" / (args.date or date.today()).isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    plans = plan_batch(scan_assets(root), plan_history, count=args.count, on_date=args.date, seed=args.seed)
    if args.dry_run:
        print(json.dumps([plan.manifest() for plan in plans], indent=2))
        return 0
    failures: list[str] = []
    for index, plan in enumerate(plans, start=1):
        output = output_dir / f"short-{index:02d}.mp4"
        try:
            render_plan(root, plan, output, keep_temporary=args.keep_temporary_files)
            record(plan_history, {"signature": plan.signature, "date": str(args.date or date.today()), "title": plan.title, "theme": plan.theme})
            receipt = {"signature": plan.signature, "date": str(args.date or date.today()), "title": plan.title, "file": str(output), "status": "rendered"}
            if args.upload:
                from .youtube import upload
                receipt["youtube_video_id"] = upload(output, plan.title, plan.description, args.privacy_status)
                receipt["status"] = "uploaded"
            record(upload_history, receipt)
            print(f"[{index}/{len(plans)}] {receipt['status']}: {plan.title}")
        except Exception as exc:
            failures.append(f"{plan.title}: {exc}")
            print(f"[{index}/{len(plans)}] FAILED: {plan.title}: {exc}", file=sys.stderr)
    write_json(history_dir / "last_batch.json", {"date": str(args.date or date.today()), "count": len(plans), "failures": failures})
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

