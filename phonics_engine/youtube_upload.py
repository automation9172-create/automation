"""One-time YouTube authorization and safe resumable scheduled uploads."""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import ssl
import tempfile
import time
from datetime import datetime, time as datetime_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
RETRIABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 10
UPLOAD_SOCKET_TIMEOUT_SECONDS = 180
UPLOAD_CHUNK_MIB = 2
YOUTUBE_THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024
YOUTUBE_THUMBNAIL_TARGET_BYTES = 1_900_000
PUBLISH_SLOTS = {
    "morning": datetime_time(hour=8, minute=17),
    "afternoon": datetime_time(hour=14, minute=47),
    "evening": datetime_time(hour=19, minute=38),
}
_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _prefer_ipv4_for_uploads() -> None:
    """Avoid long streaming stalls on otherwise-valid but broken IPv6 routes."""
    if os.environ.get("YOUTUBE_PREFER_IPV4", "1").strip().lower() in {"0", "false", "no"}:
        return
    if getattr(socket.getaddrinfo, "_phonics_ipv4_preferred", False):
        return

    def ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
        results = _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
        ipv4 = [result for result in results if result[0] == socket.AF_INET]
        return ipv4 or results

    ipv4_first._phonics_ipv4_preferred = True  # type: ignore[attr-defined]
    socket.getaddrinfo = ipv4_first


def _configure_secure_network() -> None:
    _prefer_ipv4_for_uploads()
    if os.name != "nt":
        return
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt to enable secure Windows certificate validation") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authorize or upload a generated phonics video.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    authorize = subparsers.add_parser("authorize", help="Create a refresh-token file using a local browser.")
    authorize.add_argument("--client-secrets", default="client_secret.json")
    authorize.add_argument("--token-output", default="youtube_token.json")

    check = subparsers.add_parser("check-auth", help="Verify that a stored refresh token reaches the intended channel.")
    check.add_argument("--token", default="youtube_token.json")

    upload = subparsers.add_parser("upload", help="Upload one completed render and its selected thumbnail.")
    upload.add_argument("--project-root", default=".")
    upload.add_argument("--manifest", help="Completed video manifest; latest is used when omitted.")
    upload.add_argument("--token", default="youtube_token.json")
    upload.add_argument("--privacy-status", choices=("private", "unlisted", "public"), default=os.environ.get("YOUTUBE_PRIVACY_STATUS", "public"))
    upload.add_argument("--category-id", default=os.environ.get("YOUTUBE_CATEGORY_ID", "27"))
    upload.add_argument(
        "--publish-at",
        help="Future ISO-8601 time. The video uploads privately and YouTube publishes it at this exact time.",
    )

    slot = subparsers.add_parser("slot", help="Calculate and check one scheduled India publishing slot.")
    slot.add_argument("--project-root", default=".")
    slot.add_argument("--name", choices=tuple(PUBLISH_SLOTS), required=True)
    return parser


def _google_modules():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before using YouTube upload automation") from exc
    return Request, Credentials, InstalledAppFlow, build, HttpError, MediaFileUpload


def _prepare_thumbnail_for_upload(thumbnail_path: Path, temporary_directory: Path) -> Path:
    """Return a YouTube-safe thumbnail without modifying the selected source asset."""
    if thumbnail_path.stat().st_size <= YOUTUBE_THUMBNAIL_MAX_BYTES:
        return thumbnail_path

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Install Pillow from requirements.txt to compress oversized thumbnails") from exc

    temporary_directory.mkdir(parents=True, exist_ok=True)
    output = temporary_directory / "youtube_thumbnail.jpg"
    with Image.open(thumbnail_path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, "white")
        flattened.paste(rgba, mask=rgba.getchannel("A"))

        for quality in (92, 88, 84, 80, 75, 70, 65, 60):
            flattened.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
            if output.stat().st_size <= YOUTUBE_THUMBNAIL_TARGET_BYTES:
                print(
                    f"Compressed oversized thumbnail from {thumbnail_path.stat().st_size} "
                    f"to {output.stat().st_size} bytes for YouTube.",
                    flush=True,
                )
                return output

    raise RuntimeError(
        f"Could not compress thumbnail below YouTube's {YOUTUBE_THUMBNAIL_MAX_BYTES}-byte limit: "
        f"{thumbnail_path}"
    )


def authorize(client_secrets: Path, token_output: Path) -> int:
    _, _, InstalledAppFlow, _, _, _ = _google_modules()
    if not client_secrets.is_file():
        raise FileNotFoundError(f"OAuth client file not found: {client_secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), [YOUTUBE_UPLOAD_SCOPE])
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token; revoke the old grant and authorize again")
    token_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = token_output.with_suffix(token_output.suffix + ".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    temporary.replace(token_output)
    print(f"Authorization complete. Keep this file private: {token_output.resolve()}")
    print("Add its complete JSON content to the GitHub Actions secret YOUTUBE_TOKEN_JSON.")
    return 0


def _credentials(token_path: Path):
    _configure_secure_network()
    Request, Credentials, _, _, _, _ = _google_modules()
    inline = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()
    if inline:
        try:
            info = json.loads(inline)
        except json.JSONDecodeError as exc:
            raise RuntimeError("YOUTUBE_TOKEN_JSON is not valid JSON") from exc
    elif token_path.is_file():
        try:
            info = json.loads(token_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid YouTube token JSON: {token_path}") from exc
    else:
        raise FileNotFoundError(
            "No YouTube refresh token found. Run the authorize command locally, then add YOUTUBE_TOKEN_JSON in GitHub."
        )
    credentials = Credentials.from_authorized_user_info(info, [YOUTUBE_UPLOAD_SCOPE])
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            raise RuntimeError("YouTube credentials are not refreshable; authorize again")
    return credentials


def _service(token_path: Path):
    _, _, _, build, _, _ = _google_modules()
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before using YouTube upload automation") from exc
    _configure_secure_network()
    timeout = int(os.environ.get("YOUTUBE_SOCKET_TIMEOUT_SECONDS", UPLOAD_SOCKET_TIMEOUT_SECONDS))
    raw_http = httplib2.Http(timeout=timeout)
    # YouTube uses 308 as "Resume Incomplete" after each uploaded chunk. Newer
    # httplib2 releases also classify 308 as a redirect and otherwise reject
    # YouTube's valid response because it intentionally has no Location header.
    raw_http.redirect_codes = frozenset(code for code in raw_http.redirect_codes if code != 308)
    transport = AuthorizedHttp(_credentials(token_path), http=raw_http)
    return build("youtube", "v3", http=transport, cache_discovery=False)


def check_auth(token_path: Path) -> int:
    credentials = _credentials(token_path)
    if not credentials.refresh_token:
        raise RuntimeError("The token has no refresh token for unattended uploads")
    if not credentials.has_scopes([YOUTUBE_UPLOAD_SCOPE]):
        raise RuntimeError("The token does not grant the required youtube.upload scope")
    print("YouTube upload authorization is valid.")
    print("Granted scope: https://www.googleapis.com/auth/youtube.upload")
    print("Uploads will go to the YouTube channel selected during Google consent.")
    print("Channel metadata is intentionally not read because this token uses the least-privilege upload-only scope.")
    return 0


def _latest_manifest(root: Path) -> Path:
    manifests = sorted((root / "logs").glob("video_manifest_*.json"), key=lambda path: path.stat().st_mtime_ns)
    if not manifests:
        raise FileNotFoundError("No completed video manifest was found")
    return manifests[-1]


def _primary_scene_names(manifest: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for scene in manifest.get("scenes", []):
        letter = str(scene.get("letter", "")).upper()
        if letter and letter not in result:
            result[letter] = str(scene.get("object", letter.title()))
    return result


def _metadata(manifest: dict) -> tuple[str, str, list[str]]:
    objects = _primary_scene_names(manifest)
    signature = str(manifest.get("plan_signature", "0"))
    seed = int((signature or "0")[:12], 16)
    templates = (
        "A for {a} to Z for {z} | ABC Phonics Repeat-Along for Kids",
        "Learn ABC A–Z | {a}, {m} and {z} Phonics Words for Children",
        "ABC Phonics Lesson | Teacher and Child Repeat A for {a}",
        "A–Z Alphabet Learning | Say and Repeat {a}, {b}, {z}",
        "English ABC for Kids | A for {a} to Z for {z}",
        "Alphabet Sounds and Words | A–Z Preschool Repeat-Along",
        "A for {a}, B for {b} | Complete ABC Learning Video",
        "Learn Letters A to Z | Colorful Phonics Words for Preschool",
        "ABC Repeat After Me | A for {a}, M for {m}, Z for {z}",
        "Phonics A–Z for Toddlers | Listen, Look and Repeat",
        "Capital and Small Letters A–Z | ABC Words for Kids",
        "Complete Alphabet Lesson | A for {a} and Z for {z}",
    )
    values = {
        "a": objects.get("A", "Apple"),
        "b": objects.get("B", "Ball"),
        "m": objects.get("M", "Mango"),
        "z": objects.get("Z", "Zebra"),
    }
    title = templates[seed % len(templates)].format(**values)[:100].strip()
    sample_letters = ("A", "B", "F", "M", "S", "Z")
    examples = ", ".join(f"{letter} for {objects[letter]}" for letter in sample_letters if letter in objects)
    description = (
        "A complete A–Z phonics lesson using original teacher and child voice recordings. "
        "Children can look, listen, and repeat each alphabet word.\n\n"
        f"Examples in this lesson: {examples}.\n\n"
        "Each lesson is newly assembled with a different educational route, visual composition, "
        "background, music selection, and exact-object example footage.\n\n"
        "Made for toddlers, preschool, nursery, kindergarten, and early English learners.\n\n"
        "#ABCSong #PhonicsSong #AlphabetForKids #PreschoolLearning #LearnABC"
    )
    tags = [
        "abc song", "phonics song", "alphabet for kids", "a for apple", "preschool learning",
        "learn abc", "kindergarten", "nursery rhymes", "teacher and student voice", "a to z",
    ]
    return title, description, tags


def _history_paths(root: Path) -> tuple[Path, Path]:
    directory = root / ".phonics_work"
    return directory / "youtube_upload_history.json", directory / "youtube_upload_history.backup.json"


def _read_upload_history(root: Path) -> list[dict]:
    candidates: list[list[dict]] = []
    existing = 0
    for path in _history_paths(root):
        if not path.is_file():
            continue
        existing += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, list):
            candidates.append([entry for entry in entries if isinstance(entry, dict)])
    if existing and not candidates:
        raise RuntimeError("All YouTube upload-history copies are unreadable; refusing to risk a duplicate upload")
    return max(candidates, key=len) if candidates else []


def _record_upload(root: Path, entry: dict) -> None:
    entries = _read_upload_history(root)
    if any(
        item.get("plan_signature") == entry.get("plan_signature")
        or (
            entry.get("scheduled_publish_at")
            and item.get("scheduled_publish_at") == entry.get("scheduled_publish_at")
        )
        for item in entries
    ):
        return
    entries.append(entry)
    payload = json.dumps({"version": 1, "entries": entries}, separators=(",", ":"))
    for path in _history_paths(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)


def _is_retriable_error(exc: Exception) -> bool:
    _, _, _, _, HttpError, _ = _google_modules()
    status = getattr(getattr(exc, "resp", None), "status", None)
    if isinstance(exc, HttpError):
        return status in RETRIABLE_HTTP_STATUSES
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    try:
        import httplib2

        transport_errors = (httplib2.HttpLib2Error,)
    except ImportError:
        transport_errors = ()
    return isinstance(exc, (OSError, TimeoutError, ConnectionError, socket.timeout, *transport_errors))


def _execute_with_retry(callable_request, *, label: str):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return callable_request()
        except Exception as exc:
            if not _is_retriable_error(exc):
                raise
            if attempt >= MAX_RETRIES:
                raise RuntimeError(f"{label} failed after {MAX_RETRIES + 1} attempts") from exc
            delay = random.uniform(0, min(60, 2 ** (attempt + 1)))
            print(f"{label} retry {attempt + 1}/{MAX_RETRIES} in {delay:.1f}s", flush=True)
            time.sleep(delay)


def _authorized_headers(token_path: Path) -> dict[str, str]:
    credentials = _credentials(token_path)
    if not credentials.token:
        raise RuntimeError("YouTube credentials did not provide an access token")
    return {"Authorization": f"Bearer {credentials.token}"}


def _request_timeout() -> tuple[int, int]:
    connect = int(os.environ.get("YOUTUBE_CONNECT_TIMEOUT_SECONDS", "30"))
    read = int(os.environ.get("YOUTUBE_READ_TIMEOUT_SECONDS", "120"))
    return connect, read


def _start_resumable_upload(
    token_path: Path,
    body: dict,
    *,
    video_size: int,
) -> tuple[object, str]:
    import requests

    _configure_secure_network()
    session = requests.Session()
    session.headers.update(_authorized_headers(token_path))
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(video_size),
    }
    response = session.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"part": "snippet,status", "uploadType": "resumable"},
        headers=headers,
        json=body,
        timeout=_request_timeout(),
        allow_redirects=False,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(
            f"YouTube could not start the resumable upload: HTTP {response.status_code} {response.text[:500]}"
        )
    upload_url = response.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube did not return a resumable upload URL")
    return session, upload_url


def _confirmed_upload_position(
    session,
    upload_url: str,
    video_size: int,
) -> tuple[int, dict | None]:
    response = session.put(
        upload_url,
        headers={
            "Content-Length": "0",
            "Content-Range": f"bytes */{video_size}",
        },
        timeout=_request_timeout(),
        allow_redirects=False,
    )
    if response.status_code in {200, 201}:
        return video_size, response.json()
    if response.status_code == 308:
        acknowledged = response.headers.get("Range", "")
        if "-" not in acknowledged:
            return 0, None
        return int(acknowledged.rsplit("-", 1)[1]) + 1, None
    raise RuntimeError(
        f"YouTube could not confirm resumable progress: HTTP {response.status_code} {response.text[:500]}"
    )


def _upload_video_resumably(
    video_path: Path,
    token_path: Path,
    body: dict,
    *,
    chunk_size: int,
) -> dict:
    import requests

    video_size = video_path.stat().st_size
    session, upload_url = _start_resumable_upload(token_path, body, video_size=video_size)
    position = 0
    failures = 0
    with video_path.open("rb") as source:
        while position < video_size:
            source.seek(position)
            chunk = source.read(min(chunk_size, video_size - position))
            end = position + len(chunk) - 1
            try:
                response = session.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {position}-{end}/{video_size}",
                    },
                    data=chunk,
                    timeout=_request_timeout(),
                    allow_redirects=False,
                )
                if response.status_code in {200, 201}:
                    return response.json()
                if response.status_code == 308:
                    acknowledged = response.headers.get("Range", "")
                    position = int(acknowledged.rsplit("-", 1)[1]) + 1 if "-" in acknowledged else 0
                    failures = 0
                    print(f"Upload progress: {round(position / video_size * 100)}%", flush=True)
                    continue
                if response.status_code not in RETRIABLE_HTTP_STATUSES:
                    raise RuntimeError(
                        f"YouTube upload rejected a chunk: HTTP {response.status_code} {response.text[:500]}"
                    )
                raise requests.HTTPError(f"temporary YouTube HTTP {response.status_code}", response=response)
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                failures += 1
                if failures > MAX_RETRIES:
                    raise RuntimeError(f"Video upload failed after {MAX_RETRIES} consecutive retries") from exc
                delay = random.uniform(0, min(60, 2**failures))
                print(
                    f"Video chunk retry {failures}/{MAX_RETRIES} after {type(exc).__name__}; "
                    f"checking confirmed progress in {delay:.1f}s",
                    flush=True,
                )
                time.sleep(delay)
                position, completed = _confirmed_upload_position(session, upload_url, video_size)
                if completed:
                    return completed
    raise RuntimeError("YouTube upload ended without a completed video response")


def _normalise_publish_at(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--publish-at must include a timezone offset or Z")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def scheduled_slot(name: str, *, now: datetime | None = None) -> str:
    india = ZoneInfo("Asia/Kolkata")
    current = (now or datetime.now(timezone.utc)).astimezone(india)
    slot_time = PUBLISH_SLOTS[name]
    target = datetime.combine(current.date(), slot_time, tzinfo=india)
    return target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def slot_status(root: Path, name: str) -> int:
    publish_at = scheduled_slot(name)
    uploaded = any(entry.get("scheduled_publish_at") == publish_at for entry in _read_upload_history(root.resolve()))
    print(f"publish_at={publish_at}")
    print(f"needed={'false' if uploaded else 'true'}")
    return 0


def upload(
    root: Path,
    manifest_path: Path | None,
    token_path: Path,
    privacy_status: str,
    category_id: str,
    publish_at: str | None = None,
) -> int:
    _, _, _, _, _, MediaFileUpload = _google_modules()
    root = root.resolve()
    manifest_path = manifest_path.resolve() if manifest_path else _latest_manifest(root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    signature = str(manifest.get("plan_signature", "")).strip()
    if not signature:
        raise RuntimeError("Completed manifest does not contain a plan signature")
    scheduled_publish_at = _normalise_publish_at(publish_at)
    history = _read_upload_history(root)
    previous = next((entry for entry in history if entry.get("plan_signature") == signature), None)
    if previous:
        print(f"Plan already uploaded as https://youtu.be/{previous['video_id']}; duplicate upload skipped.")
        return 0
    scheduled = next(
        (
            entry
            for entry in history
            if scheduled_publish_at and entry.get("scheduled_publish_at") == scheduled_publish_at
        ),
        None,
    )
    if scheduled:
        print(f"Publishing slot already filled by https://youtu.be/{scheduled['video_id']}; upload skipped.")
        return 0

    video_path = Path(str(manifest.get("output_path", "")))
    if not video_path.is_absolute():
        video_path = root / video_path
    if not video_path.is_file():
        raise FileNotFoundError(f"Rendered video not found: {video_path}")
    thumbnail_value = manifest.get("thumbnail_output")
    thumbnail_path = Path(str(thumbnail_value)) if thumbnail_value else None
    if thumbnail_path and not thumbnail_path.is_absolute():
        thumbnail_path = root / thumbnail_path

    title, description, tags = _metadata(manifest)
    youtube = _service(token_path)
    api_privacy_status = privacy_status
    status_body: dict[str, object] = {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": True}
    api_publish_at = scheduled_publish_at
    if api_publish_at:
        if privacy_status != "public":
            raise ValueError("Scheduled publishing requires --privacy-status public")
        target = datetime.fromisoformat(api_publish_at.replace("Z", "+00:00"))
        if target <= datetime.now(timezone.utc):
            print(f"Scheduled time {api_publish_at} has passed; uploading publicly as a late recovery.", flush=True)
            api_publish_at = None
        else:
            api_privacy_status = "private"
            status_body["privacyStatus"] = "private"
            status_body["publishAt"] = api_publish_at
    body = {
        "snippet": {"title": title, "description": description, "tags": tags, "categoryId": str(category_id)},
        "status": status_body,
    }
    chunk_mib = int(os.environ.get("YOUTUBE_UPLOAD_CHUNK_MIB", UPLOAD_CHUNK_MIB))
    if chunk_mib < 1:
        raise ValueError("YOUTUBE_UPLOAD_CHUNK_MIB must be at least 1")
    response = _upload_video_resumably(
        video_path,
        token_path,
        body,
        chunk_size=chunk_mib * 1024 * 1024,
    )
    video_id = response.get("id") if isinstance(response, dict) else None
    if not video_id:
        raise RuntimeError(f"YouTube returned an unexpected upload response: {response!r}")

    actual_privacy = str(response.get("status", {}).get("privacyStatus", api_privacy_status))
    if actual_privacy != api_privacy_status:
        print(
            f"Warning: requested API privacy={api_privacy_status}, but YouTube returned privacy={actual_privacy}. "
            "Check whether the API project has completed the YouTube upload audit."
        )
    receipt = {
        "plan_signature": signature,
        "video_id": video_id,
        "title": title,
        "requested_privacy_status": privacy_status,
        "actual_privacy_status": actual_privacy,
        "scheduled_publish_at": scheduled_publish_at,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    _record_upload(root, receipt)
    if thumbnail_path and thumbnail_path.is_file():
        with tempfile.TemporaryDirectory(prefix="phonics_youtube_thumbnail_") as temporary:
            thumbnail_media = None
            try:
                upload_thumbnail = _prepare_thumbnail_for_upload(thumbnail_path, Path(temporary))
                thumbnail_media = MediaFileUpload(str(upload_thumbnail), resumable=False)
                _execute_with_retry(
                    lambda: youtube.thumbnails().set(videoId=video_id, media_body=thumbnail_media).execute(),
                    label="Thumbnail upload",
                )
                print(f"Thumbnail uploaded successfully for video {video_id}.", flush=True)
            except Exception as exc:
                print(f"Warning: video uploaded, but thumbnail upload failed: {exc}")
            finally:
                media_handle = getattr(thumbnail_media, "_fd", None)
                if media_handle is not None:
                    media_handle.close()
    if api_publish_at:
        print(f"Uploaded successfully and scheduled for {api_publish_at}: https://youtu.be/{video_id}")
    else:
        print(f"Uploaded successfully: https://youtu.be/{video_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "authorize":
            return authorize(Path(arguments.client_secrets), Path(arguments.token_output))
        if arguments.command == "check-auth":
            return check_auth(Path(arguments.token))
        if arguments.command == "slot":
            return slot_status(Path(arguments.project_root), arguments.name)
        return upload(
            Path(arguments.project_root),
            Path(arguments.manifest) if arguments.manifest else None,
            Path(arguments.token),
            arguments.privacy_status,
            arguments.category_id,
            arguments.publish_at,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"YouTube automation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
