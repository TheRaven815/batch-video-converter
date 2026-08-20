from __future__ import annotations

import json
import subprocess
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from video_converter.api.auth import get_current_user
from video_converter.api.routers._select import select_routes
from video_converter.core.models import MediaStreamEntryDto, MediaStreamsProbeResponse
from video_converter.core.path_validation import SourcePathTraversalError
from video_converter.core.path_validation import validate_source_path as _validate_source_path

router = select_routes(lambda path: path.startswith("/api/v1/media"))

# ---------------------------------------------------------------------------
# New: GET /api/v1/media/streams — ffprobe all streams (video/audio/subtitle)
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
}

_STREAMS_CACHE_TTL_SECONDS = 60
_streams_probe_cache: dict[tuple[str, str], tuple[float, MediaStreamsProbeResponse]] = {}


def _build_media_roots_map() -> dict[str, object]:
    # Lazy import to avoid circular import at module load time.
    from video_converter.api import routes as routes_module

    cfg = getattr(routes_module, "settings", None)
    if cfg is None or not hasattr(cfg, "media_roots"):
        from video_converter.core.config import get_settings

        cfg = get_settings()
    return {root.key: root for root in cfg.media_roots}  # type: ignore[attr-defined]


@router.get(
    "/api/v1/media/streams",
    response_model=MediaStreamsProbeResponse,
    dependencies=[Depends(get_current_user)],
)
async def probe_media_streams(
    root_key: Annotated[str, Query(min_length=1, max_length=64)],
    path: Annotated[str, Query(min_length=1, max_length=2048)],
) -> MediaStreamsProbeResponse:
    roots_map = _build_media_roots_map()

    root_obj = roots_map.get(root_key)
    if root_obj is None:
        raise HTTPException(status_code=404, detail="Media root not found")

    # Validate source path (checks traversal + existence + extension) without
    # leaking absolute filesystem paths to the caller.
    try:
        resolved = _validate_source_path(
            roots_map,
            root_key,
            path,
            supported_extensions=VIDEO_EXTENSIONS,
        )
    except SourcePathTraversalError as exc:
        from video_converter.api.errors import path_validation_error

        raise path_validation_error(exc, status_code=400) from exc
    except ValueError as exc:
        from video_converter.api.errors import path_validation_error

        raise path_validation_error(exc) from exc

    # Determine the canonical rel_path for cache key and response (no absolute leak).
    # Re-derive from the validated file to ensure consistent normalisation.
    root_path = getattr(root_obj, "path", root_obj)  # type: ignore[arg-type]
    from pathlib import Path

    root_path = Path(str(root_path)).resolve()  # type: ignore[arg-type]
    normalized_path = resolved.relative_to(root_path).as_posix()
    cache_key = (str(root_key), normalized_path)
    cached = _streams_probe_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    if cached:
        del _streams_probe_cache[cache_key]

    # Run ffprobe (single invocation for all stream types)
    ffprobe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,channels:stream_tags=language,title",
        "-of",
        "json",
        str(resolved),
    ]
    try:
        proc = subprocess.run(
            ffprobe_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="ffprobe timed out") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to run ffprobe") from exc

    if proc.returncode != 0:
        tail = (proc.stderr or "ffprobe failed").strip()[-700:]
        raise HTTPException(status_code=500, detail=f"ffprobe error: {tail}")

    try:
        raw: object = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Failed to parse ffprobe output") from exc

    streams = raw.get("streams", []) if isinstance(raw, dict) else []  # type: ignore[union-attr]
    video: list[MediaStreamEntryDto] = []
    audio: list[MediaStreamEntryDto] = []
    subtitle: list[MediaStreamEntryDto] = []

    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            raw_index = stream.get("index")
            if not isinstance(raw_index, int):
                continue
            codec_type = str(stream.get("codec_type") or "").strip().lower()
            codec_name = str(stream.get("codec_name") or "").strip().lower()
            channels_raw = stream.get("channels")
            channels: int | None = None
            if isinstance(channels_raw, int):
                channels = channels_raw
            else:
                try:
                    channels = int(str(channels_raw)) if channels_raw is not None else None
                except (TypeError, ValueError):
                    channels = None
            tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
            language_raw = (tags or {}).get("language")  # type: ignore[union-attr]
            language = str(language_raw).strip().lower() if language_raw else None
            language = language or None
            title_raw = (tags or {}).get("title")  # type: ignore[union-attr]
            title = str(title_raw).strip() if title_raw not in (None, "") else None
            entry = MediaStreamEntryDto(
                index=raw_index,
                codec=codec_name,
                language=language,
                channels=channels,
                title=title or None,
            )
            if codec_type == "video":
                video.append(entry)
            elif codec_type == "audio":
                audio.append(entry)
            elif codec_type == "subtitle":
                subtitle.append(entry)

    result = MediaStreamsProbeResponse(
        root_key=str(root_key),
        path=normalized_path,
        video=video,
        audio=audio,
        subtitle=subtitle,
    )
    _streams_probe_cache[cache_key] = (time.monotonic() + _STREAMS_CACHE_TTL_SECONDS, result)
    return result
