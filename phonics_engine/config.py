"""Configuration for the phonics renderer."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EngineConfig:
    # A video never falls below eight minutes unless the caller explicitly
    # changes the floor.  The planner repeats A--Z rounds when necessary.
    target_duration_seconds: float = 500.0
    minimum_video_duration_seconds: float = 480.0
    # Scenes stay at HD for stable, memory-efficient rendering. The completed
    # lesson is upscaled once to YouTube 4K landscape during final export.
    resolution: tuple[int, int] = (1920, 1080)
    export_resolution: tuple[int, int] = (3840, 2160)
    fps: int = 30
    pixabay_clip_duration: float = 5.5
    teacher_student_gap: float = 0.30
    scene_lead_in_seconds: float = 0.55
    post_speech_hold_seconds: float = 0.45
    transition_seconds: float = 0.25
    visual_fade_seconds: float = 0.18
    voice_fade_seconds: float = 0.03
    # A small number of teacher lines receive the echo effect. Student lines
    # always remain clean for repetition.
    teacher_echo_scene_fraction: float = 0.18

    normal_music_volume: float = 0.20
    speech_music_volume: float = 0.070
    music_crossfade_seconds: float = 2.0
    music_start_fade_seconds: float = 0.7
    music_end_fade_seconds: float = 1.0
    minimum_music_tracks: int = 2
    maximum_music_tracks: int = 4

    intro_enabled: bool = True
    intro_max_duration_seconds: float = 15.0
    outro_enabled: bool = False
    ending_duration_seconds: float = 3.0
    random_seed: int | None = None

    asset_root: str | None = None
    output_prefix: str = "phonics_A_to_Z"
    font_path: str | None = None
    font_letter_size: int = 460
    font_word_size: int = 120
    safe_margin_x: int = 70
    safe_margin_y: int = 58
    object_max_width: int = 810
    object_max_height: int = 720

    # Exact provider footage is allowed for the current render only. It is
    # written beneath the render's temporary directory and then deleted.
    pixabay_enabled: bool = True
    pexels_enabled: bool = True
    # Provider metadata must name every word in the selected object. Anything
    # less exact falls through to that object's reviewed manual folder.
    provider_minimum_match_confidence: float = 1.0
    pixabay_per_page: int = 20
    pixabay_min_width: int = 1280
    pixabay_min_height: int = 720
    pixabay_connect_timeout_seconds: float = 8.0
    pixabay_read_timeout_seconds: float = 60.0
    pixabay_max_download_megabytes: int = 500

    video_crf: int = 20
    export_video_crf: int = 18
    ffmpeg_preset: str = "veryfast"
    # Intel Quick Sync is attempted only for the simple final 1080p-to-4K
    # pass. If unavailable, libx264 automatically uses all CPU cores.
    hardware_4k_export_enabled: bool = True
    export_scale_flags: str = "bicubic"
    export_cpu_threads: int = 0
    # A segment normally takes seconds.  This hard stop turns an FFmpeg stall
    # into a recoverable per-scene fallback instead of halting the whole video.
    ffmpeg_scene_timeout_seconds: float = 180.0
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000
    master_audio_volume: float = 1.25
    keep_temporary_files: bool = False
    maximum_rounds: int = 12
    # The ledger stops at this many successful videos. Within that capacity,
    # every first A--Z round must remain meaningfully separated from history.
    originality_history_capacity: int = 10000
    minimum_object_route_distance: int = 12
    minimum_content_route_distance: int = 21
    minimum_visual_route_distance: int = 18
    teaching_variant_count: int = 12
    font_variant_count: int = 6
    text_color_count: int = 10
    border_variant_count: int = 10
    overlay_variant_count: int = 6
    overlay_color_count: int = 10
    layout_variant_count: int = 8
    motion_variant_count: int = 6

    def effective_target_duration(self) -> float:
        return max(self.target_duration_seconds, self.minimum_video_duration_seconds)

    def as_json_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["resolution"] = list(self.resolution)
        value["export_resolution"] = list(self.export_resolution)
        return value


def write_default_config(path: Path) -> None:
    """Write an editable starter config only if one does not exist."""

    if not path.exists():
        path.write_text(json.dumps(EngineConfig().as_json_dict(), indent=2) + "\n", encoding="utf-8")


def load_config(path: Path) -> EngineConfig:
    write_default_config(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")

    allowed = {field.name for field in fields(EngineConfig)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown config setting(s): {', '.join(unknown)}")
    values = EngineConfig().as_json_dict()
    values.update(raw)
    for setting in ("resolution", "export_resolution"):
        resolution = values[setting]
        if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
            raise ValueError(f"{setting} must be a [width, height] array")
        values[setting] = (int(resolution[0]), int(resolution[1]))
    config = EngineConfig(**values)
    _validate(config)
    return config


def _validate(config: EngineConfig) -> None:
    for setting, (width, height) in (("resolution", config.resolution), ("export_resolution", config.export_resolution)):
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise ValueError(f"{setting} must use positive, even dimensions")
        if width * 9 != height * 16:
            raise ValueError(f"{setting} must be a 16:9 YouTube landscape size")
    if config.fps <= 0 or config.pixabay_clip_duration <= 0:
        raise ValueError("fps and pixabay_clip_duration must be positive")
    if config.target_duration_seconds <= 0 or config.minimum_video_duration_seconds <= 0:
        raise ValueError("video duration settings must be positive")
    if not 0 <= config.speech_music_volume <= config.normal_music_volume <= 1:
        raise ValueError("music volumes must satisfy 0 <= speech <= normal <= 1")
    if config.master_audio_volume <= 0:
        raise ValueError("master_audio_volume must be positive")
    if not 0 <= config.teacher_echo_scene_fraction <= 1:
        raise ValueError("teacher_echo_scene_fraction must be in [0, 1]")
    if config.ffmpeg_scene_timeout_seconds <= 0:
        raise ValueError("ffmpeg_scene_timeout_seconds must be positive")
    if config.export_scale_flags not in {"fast_bilinear", "bilinear", "bicubic", "lanczos"}:
        raise ValueError("export_scale_flags must be fast_bilinear, bilinear, bicubic, or lanczos")
    if config.export_cpu_threads < 0:
        raise ValueError("export_cpu_threads cannot be negative")
    if not 0 < config.provider_minimum_match_confidence <= 1:
        raise ValueError("provider_minimum_match_confidence must be in (0, 1]")
    if config.minimum_music_tracks < 1 or config.maximum_music_tracks < config.minimum_music_tracks:
        raise ValueError("invalid music track limits")
    if config.originality_history_capacity < 1:
        raise ValueError("originality_history_capacity must be positive")
    if not 1 <= config.minimum_object_route_distance <= 26:
        raise ValueError("minimum_object_route_distance must be in [1, 26]")
    if not config.minimum_object_route_distance <= config.minimum_content_route_distance <= 26:
        raise ValueError("minimum_content_route_distance must be between object distance and 26")
    if not 1 <= config.minimum_visual_route_distance <= 26:
        raise ValueError("minimum_visual_route_distance must be in [1, 26]")
    for setting in (
        "teaching_variant_count",
        "font_variant_count",
        "text_color_count",
        "border_variant_count",
        "overlay_variant_count",
        "overlay_color_count",
        "layout_variant_count",
        "motion_variant_count",
    ):
        if getattr(config, setting) < 2:
            raise ValueError(f"{setting} must be at least 2")
