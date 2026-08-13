"""Remove only disposable render files after a CI upload attempt."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def clean(root: Path, *, remove_outputs: bool = False, remove_logs: bool = False) -> list[Path]:
    root = root.resolve()
    if not (root / "generate.py").is_file():
        raise RuntimeError(f"Refusing cleanup because this is not a phonics-engine root: {root}")
    removed: list[Path] = []
    directory_targets = [path for path in root.glob("render_*") if path.is_dir()]
    directory_targets.extend(path for path in (root / "tmp", root / ".pytest_cache") if path.is_dir())
    if remove_logs and (root / "logs").is_dir():
        directory_targets.append(root / "logs")
    for path in directory_targets:
        if path.resolve().parent != root:
            raise RuntimeError(f"Refusing cleanup outside project root: {path}")
        shutil.rmtree(path, ignore_errors=False)
        removed.append(path)
    if remove_outputs and (root / "outputs").is_dir():
        outputs = (root / "outputs").resolve()
        if outputs.parent != root:
            raise RuntimeError(f"Refusing output cleanup outside project root: {outputs}")
        shutil.rmtree(outputs, ignore_errors=False)
        removed.append(outputs)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--ci", action="store_true", help="Also remove generated outputs and logs.")
    arguments = parser.parse_args(argv)
    try:
        removed = clean(Path(arguments.project_root), remove_outputs=arguments.ci, remove_logs=arguments.ci)
    except (OSError, RuntimeError) as exc:
        print(f"Cleanup failed: {exc}")
        return 1
    print(f"Cleanup complete: {len(removed)} disposable paths removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
