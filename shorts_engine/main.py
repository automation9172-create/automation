from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from .catalog import scan_assets
from .history import read_json, record, write_json
from .planner import ShortPlan, plan_batch
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
    run_date = args.date or date.today()
    batch_path = history_dir / f"daily_batch_{run_date.isoformat()}.json"
    output_dir = root / "shorts_outputs" / run_date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = scan_assets(root)
    asset_by_key = {asset.key: asset for asset in assets}
    state = read_json(batch_path, {})
    plans: list[ShortPlan] = []
    if state.get("date") == run_date.isoformat() and state.get("plans"):
        try:
            for row in state["plans"]:
                plans.append(ShortPlan(row["signature"], row["theme"], row["title"], row["description"], [asset_by_key[key] for key in row["asset_keys"]], int(row["style_index"])))
        except (KeyError, TypeError, ValueError):
            plans = []
    if not plans:
        plans = plan_batch(assets, plan_history, count=args.count, on_date=run_date, seed=args.seed)
        state = {
            "date": run_date.isoformat(),
            "plans": [{"signature": plan.signature, "theme": plan.theme, "title": plan.title, "description": plan.description, "style_index": plan.style_index, "asset_keys": [asset.key for asset in plan.assets]} for plan in plans],
            "status": {},
        }
        write_json(batch_path, state)
    if args.dry_run:
        print(json.dumps([plan.manifest() for plan in plans], indent=2))
        return 0
    uploaded_data = read_json(upload_history, {"shorts": []})
    uploaded_signatures = {item.get("signature") for item in uploaded_data.get("shorts", []) if item.get("status") == "uploaded"}
    failures: list[str] = []
    for index, plan in enumerate(plans, start=1):
        if plan.signature in uploaded_signatures or state.get("status", {}).get(plan.signature, {}).get("status") == "uploaded":
            print(f"[{index}/{len(plans)}] already uploaded: {plan.title}")
            continue
        output = output_dir / f"short-{index:02d}.mp4"
        try:
            render_plan(root, plan, output, keep_temporary=args.keep_temporary_files)
            receipt = {"signature": plan.signature, "date": run_date.isoformat(), "title": plan.title, "file": str(output), "status": "rendered"}
            if args.upload:
                from .youtube import upload
                receipt["youtube_video_id"] = upload(output, plan.title, plan.description, args.privacy_status)
                receipt["status"] = "uploaded"
                record(plan_history, {"signature": plan.signature, "date": run_date.isoformat(), "title": plan.title, "theme": plan.theme})
                uploaded_signatures.add(plan.signature)
            state.setdefault("status", {})[plan.signature] = {"status": receipt["status"], "youtube_video_id": receipt.get("youtube_video_id"), "file": str(output)}
            write_json(batch_path, state)
            record(upload_history, receipt)
            print(f"[{index}/{len(plans)}] {receipt['status']}: {plan.title}")
        except Exception as exc:
            failures.append(f"{plan.title}: {exc}")
            print(f"[{index}/{len(plans)}] FAILED: {plan.title}: {exc}", file=sys.stderr)
    write_json(history_dir / "last_batch.json", {"date": run_date.isoformat(), "count": len(plans), "failures": failures})
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
