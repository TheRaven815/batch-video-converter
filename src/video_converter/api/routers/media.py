from __future__ import annotations

import asyncio
import json
import subprocess
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from video_converter.api.auth import get_current_user
from video_converter.api.common import VIDEO_EXTENSIONS, runtime_media_roots_map
from video_converter.api.errors import path_validation_error
from video_converter.api.runtime import settings
from video_converter.api.routes import _collect_browse_entries, _store_subtitle_probe
from video_converter.core.models import (
    MediaBrowseResponse,
    MediaRootDto,
    MediaStreamEntryDto,
    MediaStreamsProbeResponse,
    MediaSubtitleProbeResponse,
    MediaSubtitleTrackDto,
)
from video_converter.core.path_validation import SourcePathTraversalError
from video_converter.core.path_validation import validate_source_path as _validate_source_path

router = APIRouter()

# ---------------------------------------------------------------------------
# New: GET /api/v1/media/streams — ffprobe all streams (video/audio/subtitle)
# ---------------------------------------------------------------------------

_STREAMS_CACHE_TTL_SECONDS = 60
_STREAMS_CACHE_MAX_ENTRIES = 512
# (expiry, source mtime_ns, source size, response)
_streams_probe_cache: dict[
    tuple[str, str], tuple[float, int, int, MediaStreamsProbeResponse]
] = {}


def _cached_streams_probe(
    cache_key: tuple[str, str], mtime_ns: int, size: int
) -> MediaStreamsProbeResponse | None:
    cached = _streams_probe_cache.get(cache_key)
    if cached is None:
        return None
    expiry, cached_mtime, cached_size, result = cached
    if (
        expiry <= time.monotonic()
        or cached_mtime != mtime_ns
        or cached_size != size
    ):
        _streams_probe_cache.pop(cache_key, None)
        return None
    return result


def _store_streams_probe(
    cache_key: tuple[str, str],
    mtime_ns: int,
    size: int,
    result: MediaStreamsProbeResponse,
) -> None:
    now = time.monotonic()
    if len(_streams_probe_cache) >= _STREAMS_CACHE_MAX_ENTRIES:
        for key in [key for key, value in _streams_probe_cache.items() if value[0] <= now]:
            _streams_probe_cache.pop(key, None)
        while len(_streams_probe_cache) >= _STREAMS_CACHE_MAX_ENTRIES:
            _streams_probe_cache.pop(next(iter(_streams_probe_cache)))
    _streams_probe_cache[cache_key] = (
        now + _STREAMS_CACHE_TTL_SECONDS,
        mtime_ns,
        size,
        result,
    )


@router.get(
    "/api/v1/media/streams",
    response_model=MediaStreamsProbeResponse,
    dependencies=[Depends(get_current_user)],
)
async def probe_media_streams(
    root_key: Annotated[str, Query(min_length=1, max_length=64)],
    path: Annotated[str, Query(min_length=1, max_length=2048)],
) -> MediaStreamsProbeResponse:
    roots_map = runtime_media_roots_map()

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
    try:
        source_stat = resolved.stat()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    mtime_ns = source_stat.st_mtime_ns
    source_size = source_stat.st_size
    cache_key = (str(root_key), normalized_path)
    cached_result = _cached_streams_probe(cache_key, mtime_ns, source_size)
    if cached_result is not None:
        return cached_result

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
        proc = await asyncio.to_thread(
            subprocess.run,
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
    _store_streams_probe(cache_key, mtime_ns, source_size, result)
    return result


@router.get(
    "/api/v1/media/roots",
    response_model=list[MediaRootDto],
    dependencies=[Depends(get_current_user)],
)
async def list_media_roots() -> list[MediaRootDto]:
    return [MediaRootDto(key=root.key, label=root.label) for root in settings.media_roots]


@router.get(
    "/api/v1/media/browse",
    response_model=MediaBrowseResponse,
    dependencies=[Depends(get_current_user)],
)
async def browse_media_root(
    root_key: Annotated[str, Query(min_length=1, max_length=64)],
    path: Annotated[str, Query(max_length=2048)] = "",
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    cursor: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> MediaBrowseResponse:
    root_map = runtime_media_roots_map()
    root = root_map.get(root_key)
    if root is None:
        raise HTTPException(status_code=404, detail="Media root not found")

    target = (root.path / path).resolve()
    try:
        target.relative_to(root.path)
    except ValueError as exc:
        raise path_validation_error(
            SourcePathTraversalError("Invalid path"), status_code=400
        ) from exc

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    query = q.lower() if q else None
    all_entries, normalized_current = await asyncio.to_thread(
        _collect_browse_entries, root, target, query
    )

    entries = all_entries[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < len(all_entries) else None
    return MediaBrowseResponse(
        root_key=root.key,
        current_path=normalized_current,
        entries=entries,
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )


@router.get(
    "/api/v1/media/subtitles",
    response_model=MediaSubtitleProbeResponse,
    dependencies=[Depends(get_current_user)],
)
async def probe_media_subtitles(
    root_key: Annotated[str, Query(min_length=1, max_length=64)],
    path: Annotated[str, Query(min_length=1, max_length=2048)],
) -> MediaSubtitleProbeResponse:
    # One shared ffprobe implementation serves both media probe endpoints;
    # the subtitles view is just a projection of the full stream list.
    probe = await probe_media_streams(root_key=root_key, path=path)
    result = MediaSubtitleProbeResponse(
        root_key=probe.root_key,
        path=probe.path,
        tracks=[
            MediaSubtitleTrackDto(
                index=entry.index,
                language=(entry.language or "und"),
                title=entry.title,
                codec_name=entry.codec or None,
            )
            for entry in probe.subtitle
        ],
    )
    root = runtime_media_roots_map().get(probe.root_key)
    if root is not None:
        try:
            source_stat = (root.path / probe.path).stat()
        except OSError:
            return result
        _store_subtitle_probe(
            (probe.root_key, probe.path),
            source_stat.st_mtime_ns,
            source_stat.st_size,
            result,
        )
    return result
