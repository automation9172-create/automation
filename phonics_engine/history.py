"""Persistent cross-channel history for non-repeating lesson plans."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import TimelinePlan


class ContentHistory:
    """Record successful videos and expose least-used asset counts.

    One database is shared by every channel name passed to the CLI.  A plan is
    recorded only after its final MP4 is successfully created, so failed and
    dry runs do not consume combinations.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.path = Path(root) / ".phonics_work" / "content_history.json"
        self.backup_path = Path(root) / ".phonics_work" / "content_history.backup.json"
        self._entries, self._usage = self._load()

    @staticmethod
    def _read_copy(path: Path) -> tuple[list[dict], dict[str, dict[str, int]]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot safely read content history {path}: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise RuntimeError(f"Invalid content history format: {path}")
        entries = [entry for entry in payload["entries"] if isinstance(entry, dict)]
        raw_usage = payload.get("usage_counts")
        if isinstance(raw_usage, dict):
            usage = {
                str(category): {str(key): int(count) for key, count in values.items()}
                for category, values in raw_usage.items()
                if isinstance(values, dict)
            }
        else:
            # One-time compatibility with the original verbose ledger format.
            rebuilt: dict[str, Counter[str]] = {}
            for entry in entries:
                for category, values in entry.get("assets", {}).items():
                    rebuilt.setdefault(category, Counter()).update(str(value).casefold() for value in values)
            usage = {category: dict(counts) for category, counts in rebuilt.items()}
        return entries, usage

    def _load(self) -> tuple[list[dict], dict[str, dict[str, int]]]:
        existing = [path for path in (self.path, self.backup_path) if path.is_file()]
        if not existing:
            return [], {}

        valid: list[tuple[int, int, list[dict], dict[str, dict[str, int]]]] = []
        errors: list[str] = []
        for path in existing:
            try:
                entries, usage = self._read_copy(path)
                # Prefer the copy with the most completed videos. Modification
                # time breaks a tie if a process stopped between mirror writes.
                valid.append((len(entries), path.stat().st_mtime_ns, entries, usage))
            except (OSError, RuntimeError) as exc:
                errors.append(str(exc))
        if not valid:
            details = "; ".join(errors)
            raise RuntimeError(f"All content-history copies are unreadable; refusing to reset history: {details}")
        _, _, entries, usage = max(valid, key=lambda item: (item[0], item[1]))
        return entries, self._portable_usage_keys(usage)

    def _path_key(self, path: Path | None) -> str:
        """Use a stable key that survives Windows/local and Linux/CI runs."""

        if not path:
            return ""
        resolved = Path(path).resolve()
        try:
            return resolved.relative_to(self.root).as_posix().casefold()
        except ValueError:
            return resolved.name.casefold()

    def _portable_usage_keys(self, usage: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        path_categories = {
            "backgrounds", "intros", "thumbnails", "music",
            "teacher_voices", "student_voices",
        }
        result: dict[str, dict[str, int]] = {}
        for category, values in usage.items():
            counts: Counter[str] = Counter()
            for raw_key, count in values.items():
                key = str(raw_key).replace("\\", "/").casefold()
                if category in path_categories:
                    for marker in ("/assets/", "/downloaded_videos/"):
                        if marker in key:
                            key = marker.strip("/") + "/" + key.split(marker, 1)[1]
                            break
                counts[key] += int(count)
            result[category] = dict(counts)
        return result

    def usage_counts(self, category: str) -> Counter[str]:
        return Counter(self._usage.get(category, {}))

    def signature(self, plan: TimelinePlan) -> str:
        payload = {
            "intro": self._path_key(plan.intro_path),
            "background": self._path_key(plan.background_path),
            "thumbnail": self._path_key(plan.thumbnail_path),
            "music": [self._path_key(path) for path in plan.selected_music],
            "scenes": [
                {
                    "letter": scene.letter,
                    "object": scene.asset.name,
                    "teacher": self._path_key(scene.teacher_audio),
                    "student": self._path_key(scene.student_audio),
                    "teaching_variant": scene.teaching_variant,
                }
                for scene in plan.educational_scenes
            ],
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def contains(self, signature: str) -> bool:
        return any(entry.get("signature") == signature for entry in self._entries)

    @staticmethod
    def _primary_scenes(plan: TimelinePlan) -> list:
        first_by_letter: dict[str, object] = {}
        for scene in plan.educational_scenes:
            first_by_letter.setdefault(scene.letter, scene)
        return [first_by_letter[letter] for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if letter in first_by_letter]

    def object_route(self, plan: TimelinePlan) -> list[str]:
        return [f"{scene.letter}:{scene.asset.name}".casefold() for scene in self._primary_scenes(plan)]

    def content_route(self, plan: TimelinePlan) -> list[str]:
        return [
            f"{scene.letter}:{scene.asset.name}:{Path(scene.teacher_audio).name}:{Path(scene.student_audio).name}:lesson{scene.teaching_variant}".casefold()
            for scene in self._primary_scenes(plan)
        ]

    def visual_route(self, plan: TimelinePlan) -> list[str]:
        return [
            (
                f"{scene.letter}:font{scene.font_variant}:"
                f"color{scene.text_color_variant}:border{scene.border_variant}:"
                f"eyes{int(scene.letter_has_eyes)}:overlay{scene.overlay_variant}:"
                f"overlaycolor{scene.overlay_color_variant}:layout{scene.layout_variant}:"
                f"motion{scene.motion_variant}"
            ).casefold()
            for scene in self._primary_scenes(plan)
        ]

    @staticmethod
    def _distance(first: list[str], second: list[str]) -> int:
        if len(first) != len(second):
            return max(len(first), len(second))
        return sum(left != right for left, right in zip(first, second))

    def originality_check(
        self,
        plan: TimelinePlan,
        minimum_object_distance: int,
        minimum_content_distance: int,
        minimum_visual_distance: int = 0,
    ) -> tuple[bool, dict[str, int]]:
        object_route = self.object_route(plan)
        content_route = self.content_route(plan)
        visual_route = self.visual_route(plan)
        closest_object = len(object_route)
        closest_content = len(content_route)
        closest_visual = len(visual_route)
        compared = 0
        for entry in self._entries:
            previous_objects = entry.get("object_route")
            previous_content = entry.get("content_route")
            if not isinstance(previous_objects, list) or not isinstance(previous_content, list):
                continue
            compared += 1
            closest_object = min(closest_object, self._distance(object_route, previous_objects))
            closest_content = min(closest_content, self._distance(content_route, previous_content))
            previous_visual = entry.get("visual_route")
            if minimum_visual_distance and isinstance(previous_visual, list):
                closest_visual = min(closest_visual, self._distance(visual_route, previous_visual))
            if (
                closest_object < minimum_object_distance
                or closest_content < minimum_content_distance
                or (minimum_visual_distance and isinstance(previous_visual, list) and closest_visual < minimum_visual_distance)
            ):
                return False, {
                    "compared_videos": compared,
                    "closest_object_distance": closest_object,
                    "closest_content_distance": closest_content,
                    "closest_visual_distance": closest_visual,
                }
        return True, {
            "compared_videos": compared,
            "closest_object_distance": closest_object,
            "closest_content_distance": closest_content,
            "closest_visual_distance": closest_visual,
        }

    def route_signature(self, plan: TimelinePlan) -> str:
        route = "|".join(self.object_route(plan))
        return hashlib.sha256(route.encode("utf-8")).hexdigest()

    def contains_route(self, route_signature: str) -> bool:
        return any(entry.get("route_signature") == route_signature for entry in self._entries)

    def record(
        self,
        plan: TimelinePlan,
        channel: str,
        output_path: Path,
        video_sources: Iterable[str] = (),
        *,
        minimum_object_distance: int = 1,
        minimum_content_distance: int = 1,
        minimum_visual_distance: int = 0,
    ) -> str:
        # Reload immediately before committing so another channel process that
        # completed while this video rendered is included in the final check.
        self._entries, self._usage = self._load()
        signature = self.signature(plan)
        if self.contains(signature):
            raise RuntimeError("Refusing to record a duplicate A-Z lesson plan")
        route_signature = self.route_signature(plan)
        if self.contains_route(route_signature):
            raise RuntimeError("Refusing to record a repeated A-Z object route")
        original, distances = self.originality_check(
            plan,
            minimum_object_distance,
            minimum_content_distance,
            minimum_visual_distance,
        )
        if not original:
            raise RuntimeError(f"Refusing to record a weakly differentiated A-Z plan: {distances}")
        sources = list(video_sources)
        assets = {
            "backgrounds": [self._path_key(plan.background_path)],
            "intros": [self._path_key(plan.intro_path)] if plan.intro_path else [],
            "thumbnails": [self._path_key(plan.thumbnail_path)] if plan.thumbnail_path else [],
            "music": [self._path_key(path) for path in plan.selected_music],
            "objects": [f"{scene.letter}:{scene.asset.name}".casefold() for scene in plan.educational_scenes],
            "teacher_voices": [self._path_key(scene.teacher_audio) for scene in plan.educational_scenes],
            "student_voices": [self._path_key(scene.student_audio) for scene in plan.educational_scenes],
            "video_sources": [str(source).casefold() for source in sources],
            "font_variants": [str(scene.font_variant) for scene in plan.educational_scenes],
            "text_colors": [str(scene.text_color_variant) for scene in plan.educational_scenes],
            "border_variants": [str(scene.border_variant) for scene in plan.educational_scenes],
            "letter_eyes": [str(int(scene.letter_has_eyes)) for scene in plan.educational_scenes],
            "teaching_variants": [str(scene.teaching_variant) for scene in plan.educational_scenes],
            "overlay_variants": [str(scene.overlay_variant) for scene in plan.educational_scenes],
            "overlay_colors": [str(scene.overlay_color_variant) for scene in plan.educational_scenes],
            "layout_variants": [str(scene.layout_variant) for scene in plan.educational_scenes],
            "motion_variants": [str(scene.motion_variant) for scene in plan.educational_scenes],
        }
        for category, values in assets.items():
            counts = Counter(self._usage.get(category, {}))
            counts.update(values)
            self._usage[category] = dict(counts)
        self._entries.append(
            {
                "signature": signature,
                "route_signature": route_signature,
                "object_route": self.object_route(plan),
                "content_route": self.content_route(plan),
                "visual_route": self.visual_route(plan),
                "originality_distances": distances,
                "channel": channel,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "output": str(output_path.resolve()),
                "selection": {
                    "background": plan.background_path.name,
                    "intro": plan.intro_path.name if plan.intro_path else None,
                    "thumbnail": plan.thumbnail_path.name if plan.thumbnail_path else None,
                    "music": [path.name for path in plan.selected_music],
                    "video_sources": sources,
                },
            }
        )
        payload = json.dumps(
            {"version": 3, "usage_counts": self._usage, "entries": self._entries},
            separators=(",", ":"),
        )
        for destination in (self.path, self.backup_path):
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(destination)
        return signature

    @property
    def video_count(self) -> int:
        return len(self._entries)
