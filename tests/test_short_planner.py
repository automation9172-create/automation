from datetime import date

from shorts_engine.catalog import scan_assets
from shorts_engine.planner import plan_batch


def test_batch_is_five_and_unique(tmp_path):
    root = tmp_path
    for letter, word in [("a", "apple"), ("b", "ball"), ("c", "cat"), ("d", "dog"), ("e", "elephant"), ("f", "fish"), ("g", "goat"), ("h", "hat")]:
        image_dir = root / "assets" / "real_png" / letter
        teacher_dir = root / "assets" / "voices" / "teacher" / letter
        student_dir = root / "assets" / "voices" / "student_voice" / letter
        image_dir.mkdir(parents=True)
        teacher_dir.mkdir(parents=True)
        student_dir.mkdir(parents=True)
        (image_dir / f"{word}.png").touch()
        (teacher_dir / f"{word}.mp3").touch()
        (student_dir / f"{word}.mp3").touch()
    plans = plan_batch(scan_assets(root), root / ".shorts_work" / "history.json", count=5, on_date=date(2026, 1, 1), seed=2)
    assert len(plans) == 5
    assert len({plan.signature for plan in plans}) == 5

