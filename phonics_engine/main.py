"""Command-line entry point for the phonics-video engine."""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .assets import AssetScanner
from .config import EngineConfig, load_config
from .history import ContentHistory
from .media import FFmpeg, MediaError
from .pixabay import PixabayVideoCache
from .planner import TimelinePlanner
from .renderer import VideoAssembler
from .thumbnail import compose_discovery_thumbnail


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an animated A--Z phonics lesson from local assets.")
    parser.add_argument("--project-root", default=".", help="Folder containing assets/ (default: current folder).")
    parser.add_argument("--config", default="config.json", help="JSON config, relative to project root by default.")
    parser.add_argument("--seed", type=int, help="Make all random choices reproducible.")
    parser.add_argument("--channel", default="channel_1", help="Channel name used by the shared non-repetition history.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and write the render plan without rendering.")
    parser.add_argument("--validate", action="store_true", help="Check FFmpeg and the local image/voice matching only.")
    parser.add_argument("--disable-pixabay", action="store_true", help="Disable all provider lookup and use reviewed local videos or PNG fallbacks only.")
    parser.add_argument("--keep-temporary-files", action="store_true", help="Keep individual scene clips after a successful render.")
    return parser


def _logger(root: Path) -> tuple[logging.Logger, Path]:
    directory = root / "logs"
    directory.mkdir(exist_ok=True)
    path = directory / f"phonics_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger = logging.getLogger("phonics_engine")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    for handler in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger, path


def _manifest(plan) -> dict:
    return {
        "estimated_duration_seconds": round(plan.estimated_duration, 3),
        "intro": str(plan.intro_path) if plan.intro_path else None,
        "background": str(plan.background_path),
        "thumbnail": str(plan.thumbnail_path) if plan.thumbnail_path else None,
        "music_tracks": [str(path) for path in plan.selected_music],
        "scenes": [
            {
                "index": education.index,
                "round": education.round_number,
                "letter": education.letter,
                "object": education.asset.display_name,
                "png": str(education.asset.png_path),
                "teacher_voice": str(education.teacher_audio),
                "student_voice": str(education.student_audio),
                "teacher_echo": education.teacher_echo,
                "visual_seed": education.visual_seed,
                "letter_has_eyes": education.letter_has_eyes,
                "font_variant": education.font_variant,
                "text_color_variant": education.text_color_variant,
                "border_variant": education.border_variant,
                "teaching_variant": education.teaching_variant,
                "overlay_variant": education.overlay_variant,
                "overlay_color_variant": education.overlay_color_variant,
                "layout_variant": education.layout_variant,
                "motion_variant": education.motion_variant,
                "teacher_duration_seconds": round(education.teacher_duration, 3),
                "student_duration_seconds": round(education.student_duration, 3),
                "education_duration_seconds": round(education.duration, 3),
                "full_screen_video_duration_seconds": round(clip.duration, 3),
            }
            for education, clip in zip(plan.educational_scenes, plan.clip_scenes)
        ],
    }


def run(arguments: argparse.Namespace) -> int:
    root = Path(arguments.project_root).resolve()
    # Keep credentials out of source and manifests while supporting the
    # project's documented ``.env`` setup.
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except ImportError:
        pass
    config_path = Path(arguments.config)
    if not config_path.is_absolute():
        config_path = root / config_path
    config: EngineConfig = load_config(config_path)
    if arguments.seed is not None:
        config = replace(config, random_seed=arguments.seed)
    if arguments.disable_pixabay:
        config = replace(config, pixabay_enabled=False, pexels_enabled=False)
    if arguments.keep_temporary_files:
        config = replace(config, keep_temporary_files=True)
    logger, log_path = _logger(root)
    media = FFmpeg(logger)
    media.validate_available()
    scanner = AssetScanner(root, config, logger)
    scanner.validate_inputs()
    matches = scanner.scan()
    valid = sum(map(len, matches.values()))
    if not valid:
        raise RuntimeError("No PNG object has both teacher and student narration")
    logger.info("found_valid_objects count=%s", valid)
    if arguments.validate:
        print(f"Validation passed: {valid} matched objects. Log: {log_path}")
        return 0
    history = ContentHistory(root)
    if history.video_count >= config.originality_history_capacity:
        raise RuntimeError(
            f"Originality history reached its configured {config.originality_history_capacity}-video capacity"
        )
    categories = (
        "backgrounds", "intros", "thumbnails", "music", "objects",
        "teacher_voices", "student_voices", "video_sources", "font_variants",
        "text_colors", "border_variants", "letter_eyes", "teaching_variants",
        "overlay_variants", "overlay_colors", "layout_variants", "motion_variants",
    )
    usage_counts = {category: history.usage_counts(category) for category in categories}
    planner = TimelinePlanner(scanner, media, config, random.Random(config.random_seed), logger, usage_counts)
    plan = None
    plan_signature = ""
    route_signature = ""
    originality_metrics: dict[str, int] = {}
    for attempt in range(1, 501):
        candidate = planner.build(matches)
        signature = history.signature(candidate)
        candidate_route = history.route_signature(candidate)
        original, distances = history.originality_check(
            candidate,
            config.minimum_object_route_distance,
            config.minimum_content_route_distance,
            config.minimum_visual_route_distance,
        )
        if not history.contains(signature) and not history.contains_route(candidate_route) and original:
            plan, plan_signature, route_signature, originality_metrics = candidate, signature, candidate_route, distances
            break
        logger.warning(
            "originality_candidate_rejected attempt=%s signature=%s route=%s distances=%s",
            attempt,
            signature,
            candidate_route,
            distances,
        )
    if plan is None:
        raise RuntimeError("Could not create a sufficiently different A-Z plan after 500 attempts; add more objects or voice takes")
    plan_path = log_path.with_suffix(".plan.json")
    plan_path.write_text(json.dumps({
        **_manifest(plan),
        "plan_signature": plan_signature,
        "route_signature": route_signature,
        "originality_gate": originality_metrics,
        "history_count_before_render": history.video_count,
    }, indent=2), encoding="utf-8")
    logger.info("plan_ready scenes=%s duration=%.1fs background=%s music_tracks=%s signature=%s route=%s originality=%s", len(plan.educational_scenes), plan.estimated_duration, plan.background_path.name, len(plan.selected_music), plan_signature, route_signature, originality_metrics)
    if arguments.dry_run:
        print(f"Dry run passed: {len(plan.educational_scenes)} scenes, {plan.estimated_duration:.1f}s, unique signature {plan_signature[:12]}, originality gate {originality_metrics}. Plan: {plan_path}")
        return 0
    renderer = VideoAssembler(config, media, logger, root)
    pixabay = PixabayVideoCache(
        scanner.paths,
        config,
        media,
        random.Random(config.random_seed),
        logger,
        usage_counts["video_sources"],
    )
    result = renderer.render(plan, pixabay)
    thumbnail_output = None
    if plan.thumbnail_path:
        thumbnail_output = result.output_path.with_name(result.output_path.stem + "_thumbnail.jpg")
        compose_discovery_thumbnail(plan, thumbnail_output, plan_signature, root)
        logger.info("Composed distinct plan-matched thumbnail: %s", thumbnail_output)
    history.record(
        plan,
        arguments.channel.strip() or "channel_1",
        result.output_path,
        result.video_sources,
        minimum_object_distance=config.minimum_object_route_distance,
        minimum_content_distance=config.minimum_content_route_distance,
        minimum_visual_distance=config.minimum_visual_route_distance,
    )
    completed = {
        **_manifest(plan),
        "channel": arguments.channel.strip() or "channel_1",
        "plan_signature": plan_signature,
        "route_signature": route_signature,
        "originality_gate": originality_metrics,
        "output_path": str(result.output_path),
        "thumbnail_output": str(thumbnail_output) if thumbnail_output else None,
        "final_duration_seconds": round(result.duration, 3),
        "export_resolution": list(config.export_resolution),
    }
    output_manifest = log_path.with_name(f"video_manifest_{datetime.now():%Y%m%d_%H%M%S}.json")
    output_manifest.write_text(json.dumps(completed, indent=2), encoding="utf-8")
    print(f"Video created: {result.output_path}")
    print(f"Duration: {result.duration:.1f} seconds")
    print(f"Plan: {plan_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(_parser().parse_args(argv))
    except (FileNotFoundError, ValueError, RuntimeError, MediaError) as exc:
        print(f"Generation failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Generation cancelled. Scene files were retained for inspection.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
