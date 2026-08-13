"""Use confident temporary stock footage, then exact reviewed local footage."""

from __future__ import annotations

import logging
import os
import random
import re
import hashlib
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    # Requests' bundled CA file can miss the Windows-managed certificate
    # chain used on some networks. The project already depends on truststore
    # for Windows, so provider calls use the same trusted system store as the
    # browser instead of failing and silently dropping to manual footage.
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # Non-Windows installs do not require this dependency.
    pass

from .assets import AssetPaths, normalize_asset_name
from .config import EngineConfig
from .media import FFmpeg
from .models import ObjectAsset


class PixabayVideoCache:
    PIXABAY_API_URL = "https://pixabay.com/api/videos/"
    PEXELS_API_URL = "https://api.pexels.com/v1/videos/search"
    VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}

    def __init__(self, paths: AssetPaths, config: EngineConfig, media: FFmpeg, rng: random.Random, logger: logging.Logger | None = None, video_usage_counts: Counter[str] | None = None) -> None:
        self.paths, self.config, self.media, self.rng = paths, config, media, rng
        self.logger = logger or logging.getLogger("phonics_engine.pixabay")
        self._temporary_directory: Path | None = None
        self.video_usage_counts = video_usage_counts or Counter()
        self._manual_hash_by_path: dict[Path, str] = {}
        self._cross_object_duplicate_hashes: set[str] | None = None

    def _balanced_video(self, values: list, name) -> object:
        minimum = min(self.video_usage_counts[str(name(value)).casefold()] for value in values)
        weights = [
            1.0 / (1 + self.video_usage_counts[str(name(value)).casefold()] - minimum) ** 2
            for value in values
        ]
        return self.rng.choices(values, weights=weights, k=1)[0]

    def set_temporary_directory(self, directory: Path) -> None:
        """Set the per-render location for disposable provider clips."""

        self._temporary_directory = Path(directory) / "provider_videos"
        self._temporary_directory.mkdir(parents=True, exist_ok=True)

    def clear_temporary_downloads(self) -> None:
        """Remove provider media even when the caller keeps scene files."""

        if not self._temporary_directory:
            return
        if self._temporary_directory.is_dir():
            for path in self._temporary_directory.glob("*"):
                if path.is_file():
                    path.unlink(missing_ok=True)
            self._temporary_directory.rmdir()
        self._temporary_directory = None

    def release_temporary_clip(self, path: Path | None) -> None:
        """Delete a provider clip immediately after its scene has been made."""

        if path and self._temporary_directory and path.parent == self._temporary_directory:
            path.unlink(missing_ok=True)

    def _is_exactly_named_for_asset(self, path: Path, asset: ObjectAsset) -> bool:
        """Allow only files explicitly labelled for this exact object.

        Valid examples are ``apple_pixabay_123_large.mp4``,
        ``apple_pexels_456_hd.mp4``, and a manually approved
        ``apple_example_video_01.mp4``. A plain ``pixabay_123.mp4`` is never
        usable because it does not identify what it contains.
        """

        stem = path.stem.casefold().replace("-", "_").replace(" ", "_")
        target = asset.name.casefold()
        return any(stem.startswith(f"{target}_{provider}_") for provider in ("pixabay", "pexels", "example", "approved"))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _unsafe_cross_object_hashes(self) -> set[str]:
        """Identify byte-identical manual clips assigned to different objects."""

        if self._cross_object_duplicate_hashes is not None:
            return self._cross_object_duplicate_hashes
        owners_by_hash: dict[str, set[str]] = {}
        paths_by_hash: dict[str, list[Path]] = {}
        if self.paths.downloads.is_dir():
            for path in self.paths.downloads.rglob("*"):
                if not path.is_file() or path.suffix.casefold() not in self.VIDEO_EXTENSIONS:
                    continue
                try:
                    relative = path.relative_to(self.paths.downloads)
                    if len(relative.parts) < 3:
                        continue
                    owner = f"{relative.parts[0].upper()}:{normalize_asset_name(relative.parts[1])}"
                    file_hash = self._sha256(path)
                except OSError as exc:
                    self.logger.warning("manual_video_hash_failed path=%s error=%s", path, exc)
                    continue
                self._manual_hash_by_path[path] = file_hash
                owners_by_hash.setdefault(file_hash, set()).add(owner)
                paths_by_hash.setdefault(file_hash, []).append(path)
        unsafe = {file_hash for file_hash, owners in owners_by_hash.items() if len(owners) > 1}
        for file_hash in sorted(unsafe):
            self.logger.warning(
                "cross_object_duplicate_videos_rejected hash=%s paths=%s",
                file_hash[:12],
                " | ".join(str(path) for path in paths_by_hash[file_hash]),
            )
        self._cross_object_duplicate_hashes = unsafe
        return unsafe

    def _cached_exact_candidates(self, asset: ObjectAsset) -> list[Path]:
        letter_folder = self.paths.downloads / asset.letter
        candidates: list[Path] = []
        unsafe_hashes = self._unsafe_cross_object_hashes()

        # Your manually reviewed library uses this preferred layout:
        # downloaded_videos/A/apple/apple_1.mp4.  Requiring both the folder
        # and filename to match prevents an Apple clip ever being used for a
        # different A-word such as airplane.
        if letter_folder.is_dir():
            object_folders = [folder for folder in letter_folder.iterdir() if folder.is_dir() and normalize_asset_name(folder.name) == asset.name]
            for object_folder in object_folders:
                for path in sorted(object_folder.iterdir()):
                    if (
                        path.is_file()
                        and path.suffix.casefold() in self.VIDEO_EXTENSIONS
                        and normalize_asset_name(path) == asset.name
                        and self._manual_hash_by_path.get(path) not in unsafe_hashes
                        and self.media.is_readable_video(path)
                    ):
                        candidates.append(path)

        return candidates

    @staticmethod
    def _provider_match_confidence(text: str, asset: ObjectAsset) -> float:
        """Score provider metadata by complete object words, never substrings.

        The score is the fraction of requested object words found in the
        provider tags or public video URL. A single word like ``Apple`` must
        match completely; multiword objects like ``Yellow Bus`` must match
        both words to meet the default 80% threshold. This avoids Apple being
        accepted from a Pineapple result.
        """

        source_words = [normalize_asset_name(token) for token in re.findall(r"[A-Za-z0-9]+", text)]
        object_words = [normalize_asset_name(token) for token in re.findall(r"[A-Za-z0-9]+", asset.display_name)]
        if not source_words or not object_words:
            return 0.0

        def has_word(word: str) -> bool:
            return word in source_words or any(
                "".join(source_words[start:end]) == word
                for start in range(len(source_words))
                for end in range(start + 2, len(source_words) + 1)
            )

        # A joined exact phrase supports ``x-ray-hand`` for ``Xray Hand`` and
        # is stronger than matching the individual words in arbitrary places.
        if any(
            "".join(source_words[start:end]) == asset.name
            for start in range(len(source_words))
            for end in range(start + 1, len(source_words) + 1)
        ):
            return 1.0
        # Non-contiguous metadata can still be useful for diagnostics, but it
        # deliberately cannot reach 1.0. With the production threshold at
        # 1.0, only an exact complete object phrase is downloadable.
        return 0.95 * sum(has_word(word) for word in object_words) / len(object_words)

    def _download(self, url: str, destination: Path, *, provider: str, asset: ObjectAsset) -> Path | None:
        if self._temporary_directory is None or destination.parent != self._temporary_directory:
            raise RuntimeError("Provider clips may only be downloaded to the active temporary render directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and self.media.is_readable_video(destination):
            self.logger.info("using_existing_provider_download provider=%s object=%s path=%s", provider, asset.name, destination)
            return destination
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            with requests.get(url, stream=True, timeout=(self.config.pixabay_connect_timeout_seconds, self.config.pixabay_read_timeout_seconds)) as response:
                response.raise_for_status()
                received = 0
                with temporary.open("wb") as file:
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > self.config.pixabay_max_download_megabytes * 1024 * 1024:
                            raise ValueError("download exceeds configured size limit")
                        file.write(chunk)
            temporary.replace(destination)
            if not self.media.is_readable_video(destination):
                destination.unlink(missing_ok=True)
                self.logger.warning("provider_download_invalid provider=%s object=%s", provider, asset.name)
                return None
        except (OSError, ValueError, requests.RequestException) as exc:
            temporary.unlink(missing_ok=True)
            self.logger.warning("provider_download_failed provider=%s object=%s error=%s", provider, asset.name, exc)
            return None
        self.logger.info("provider_downloaded provider=%s object=%s path=%s", provider, asset.name, destination)
        return destination

    def _try_pixabay(self, asset: ObjectAsset) -> Path | None:
        api_key = os.environ.get("PIXABAY_API_KEY", "").strip()
        if not self.config.pixabay_enabled or not api_key:
            return None
        try:
            response = requests.get(
                self.PIXABAY_API_URL,
                params={"key": api_key, "q": asset.display_name, "safesearch": "true", "per_page": self.config.pixabay_per_page},
                timeout=(self.config.pixabay_connect_timeout_seconds, self.config.pixabay_read_timeout_seconds),
            )
            response.raise_for_status()
            hits = response.json().get("hits", [])
        except (requests.RequestException, ValueError) as exc:
            self.logger.warning("pixabay_search_failed object=%s error=%s", asset.name, exc)
            return None
        choices: list[tuple[str, str, str, float]] = []
        for hit in hits:
            tags = str(hit.get("tags", ""))
            confidence = self._provider_match_confidence(tags, asset)
            if confidence < self.config.provider_minimum_match_confidence:
                self.logger.info("pixabay_rejected_low_match object=%s confidence=%.2f tags=%s", asset.name, confidence, tags)
                continue
            if "people" in tags.casefold() or "person" in tags.casefold():
                continue
            for quality in ("large", "medium", "small"):
                rendition = hit.get("videos", {}).get(quality, {})
                if rendition.get("url") and rendition.get("width", 0) >= self.config.pixabay_min_width and rendition.get("height", 0) >= self.config.pixabay_min_height:
                    choices.append((str(hit.get("id")), quality, rendition["url"], confidence))
                    break
        if not choices:
            self.logger.info("pixabay_no_exact_video object=%s", asset.name)
            return None
        if self._temporary_directory is None:
            return None
        video_id, quality, url, confidence = self._balanced_video(
            choices,
            lambda choice: f"{asset.name}_pixabay_{choice[0]}_{choice[1]}.mp4",
        )
        destination = self._temporary_directory / f"{asset.name}_pixabay_{video_id}_{quality}.mp4"
        self.logger.info("pixabay_match_accepted object=%s confidence=%.2f", asset.name, confidence)
        return self._download(url, destination, provider="pixabay", asset=asset)

    def _try_pexels(self, asset: ObjectAsset) -> Path | None:
        api_key = os.environ.get("PEXELS_API_KEY", "").strip()
        if not self.config.pexels_enabled or not api_key:
            return None
        try:
            response = requests.get(
                self.PEXELS_API_URL,
                headers={"Authorization": api_key},
                params={"query": asset.display_name, "orientation": "landscape", "size": "medium", "per_page": self.config.pixabay_per_page},
                timeout=(self.config.pixabay_connect_timeout_seconds, self.config.pixabay_read_timeout_seconds),
            )
            response.raise_for_status()
            videos = response.json().get("videos", [])
        except (requests.RequestException, ValueError) as exc:
            self.logger.warning("pexels_search_failed object=%s error=%s", asset.name, exc)
            return None
        choices: list[tuple[str, str, str, float]] = []
        for video in videos:
            page_url = str(video.get("url", ""))
            confidence = self._provider_match_confidence(urlparse(page_url).path, asset)
            if confidence < self.config.provider_minimum_match_confidence:
                self.logger.info("pexels_rejected_low_match object=%s confidence=%.2f url=%s", asset.name, confidence, page_url)
                continue
            for file in video.get("video_files", []):
                if file.get("file_type") != "video/mp4" or not file.get("link"):
                    continue
                if file.get("width", 0) >= self.config.pixabay_min_width and file.get("height", 0) >= self.config.pixabay_min_height:
                    choices.append((str(video.get("id")), str(file.get("id", "hd")), file["link"], confidence))
        if not choices:
            self.logger.info("pexels_no_exact_video object=%s", asset.name)
            return None
        if self._temporary_directory is None:
            return None
        video_id, file_id, url, confidence = self._balanced_video(
            choices,
            lambda choice: f"{asset.name}_pexels_{choice[0]}_{choice[1]}.mp4",
        )
        destination = self._temporary_directory / f"{asset.name}_pexels_{video_id}_{file_id}.mp4"
        self.logger.info("pexels_match_accepted object=%s confidence=%.2f", asset.name, confidence)
        return self._download(url, destination, provider="pexels", asset=asset)

    def get_clip(self, asset: ObjectAsset) -> Path | None:
        """Use Pixabay, then Pexels, then only this object's reviewed folder."""

        for lookup in (self._try_pixabay, self._try_pexels):
            provider_clip = lookup(asset)
            if provider_clip:
                return provider_clip

        cached = self._cached_exact_candidates(asset)
        if cached:
            selected = self._balanced_video(cached, lambda path: path.name)
            self.logger.info("using_random_reviewed_video object=%s path=%s", asset.name, selected)
            return selected
        self.logger.warning("no_reviewed_video_available object=%s", asset.name)
        return None
