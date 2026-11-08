from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import psutil
import redis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from video_converter.api.audit import read_recent_audit_lines, write_audit_event
from video_converter.api.auth import get_current_user, get_stream_user
from video_converter.api.common import (
    VIDEO_EXTENSIONS as VIDEO_EXTENSIONS,
    build_media_roots_map,
    maybe_await,
)
from video_converter.api.errors import error_code, path_validation_error
from video_converter.core.config import (
    HIGH_PRIORITY_QUEUE_NAME,
    JOB_EVENTS_CHANNEL,
    LOW_PRIORITY_QUEUE_NAME,
    QUEUE_NAME,
    WORKER_HEARTBEAT_KEY,
    Settings,
)
from video_converter.core.models import (
    AuditEventDto,
    BatchActionResponse,
    BatchCreateError,
    BatchListResponse,
    BatchSummaryDto,
    HealthResponse,
    JobActionSkip,
    JobBatchCreateRequest,
    JobBatchCreateResponse,
    JobBulkActionResponse,
    JobCreateRequest,
    JobIdsRequest,
    JobRecord,
    JobStatus,
    JobValidationItem,
    JobValidationResponse,
    MediaBrowseEntryDto,
    MediaBrowseResponse,
    MediaRootDto,
    MediaSubtitleProbeResponse,
    MediaSubtitleTrackDto,
    OutputFileDto,
    OutputListResponse,
    StructuredErrorResponse,
    SystemSettings,
    UploadResponse,
    WorkerHealthResponse,
    now_iso,
)
from video_converter.core.path_validation import SourcePathTraversalError, validate_source_path
from video_converter.core.storage import StorageError
from video_converter.api import routes as _routes
from video_converter.api.runtime import (  # noqa: E402
    job_repository,
    settings,
    storage_client,
)
from video_converter.api.routes import (  # noqa: E402
    _delete_output_files,
    _iter_file_range,
    _safe_output_path,
    _scan_output_entries,
    _thumbnail_path,
)

logger = logging.getLogger("api")
router = APIRouter(
    responses={
        400: {"model": StructuredErrorResponse, "description": "Bad request"},
        401: {"model": StructuredErrorResponse, "description": "Authentication failed"},
        404: {"model": StructuredErrorResponse, "description": "Resource not found"},
        422: {"model": StructuredErrorResponse, "description": "Validation failed"},
        500: {"model": StructuredErrorResponse, "description": "Internal error"},
        503: {"model": StructuredErrorResponse, "description": "Service unavailable"},
    }
)


@router.get(
    "/api/v1/outputs", response_model=OutputListResponse, dependencies=[Depends(get_current_user)]
)
async def list_outputs(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> OutputListResponse:
    outputs_dir = settings.outputs_dir.resolve()
    if not outputs_dir.exists():
        return OutputListResponse(outputs=[])

    entries = await asyncio.to_thread(_scan_output_entries, outputs_dir, q)
    selected = entries[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < len(entries) else None
    outputs = [
        OutputFileDto(
            filename=path.name,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            download_url=f"/api/v1/outputs/{path.name}/download",
            preview_url=f"/api/v1/outputs/{path.name}/preview",
            thumbnail_url=f"/api/v1/outputs/{path.name}/thumbnail",
        )
        for path, stat in selected
    ]
    if response is not None and next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(next_cursor)
    return OutputListResponse(
        outputs=outputs, next_cursor=str(next_cursor) if next_cursor is not None else None
    )


@router.get("/api/v1/outputs/{filename}/download", dependencies=[Depends(get_current_user)])
async def download_output(filename: str) -> FileResponse:
    output_path = _safe_output_path(filename)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    return FileResponse(output_path, filename=output_path.name)


@router.get("/api/v1/outputs/{filename}/preview", dependencies=[Depends(get_stream_user)])
async def preview_output(filename: str, request: Request) -> StreamingResponse:
    output_path = _safe_output_path(filename)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    size = output_path.stat().st_size
    start, end = 0, size - 1
    status_code = 200
    range_header = request.headers.get("range")
    if range_header:
        try:
            unit, raw_range = range_header.strip().split("=", 1)
            if unit.lower() != "bytes" or "," in raw_range:
                raise ValueError
            raw_start, raw_end = raw_range.split("-", 1)
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else end
            elif raw_end:
                suffix_length = int(raw_end)
                start = max(0, size - suffix_length)
            if size == 0 or start < 0 or start >= size or end < start:
                raise ValueError
            end = min(end, size - 1)
            status_code = 206
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=416,
                detail="Invalid byte range",
                headers={"Content-Range": f"bytes */{size}"},
            ) from None
    media_type = mimetypes.guess_type(output_path.name)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(max(0, end - start + 1)),
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _iter_file_range(output_path, start, end),
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )


@router.get("/api/v1/outputs/{filename}/thumbnail", dependencies=[Depends(get_stream_user)])
async def output_thumbnail(filename: str) -> FileResponse:
    output_path = _safe_output_path(filename)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    thumbnail = _thumbnail_path(filename)
    if not thumbnail.exists() or thumbnail.stat().st_mtime < output_path.stat().st_mtime:
        temp_thumbnail = thumbnail.with_name(f"{thumbnail.name}.{uuid.uuid4().hex}.tmp.jpg")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            str(output_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=480:-2",
            str(temp_thumbnail),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return_code = await asyncio.wait_for(process.wait(), timeout=30)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            temp_thumbnail.unlink(missing_ok=True)
            raise HTTPException(status_code=504, detail="Thumbnail generation timed out") from exc
        except BaseException:
            temp_thumbnail.unlink(missing_ok=True)
            raise
        if return_code != 0 or not temp_thumbnail.exists():
            temp_thumbnail.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Thumbnail generation failed")
        # Publish atomically so concurrent readers never see a half-written JPEG.
        os.replace(temp_thumbnail, thumbnail)
    return FileResponse(thumbnail, media_type="image/jpeg")


@router.delete("/api/v1/outputs/{filename}", dependencies=[Depends(get_current_user)])
async def delete_output(filename: str) -> dict[str, str]:
    output_path = _safe_output_path(filename)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    output_path.unlink()
    # Sidecars belong to the same output and must not be orphaned.
    for sidecar in (
        *settings.outputs_dir.glob(f"{output_path.stem}.srt"),
        *settings.outputs_dir.glob(f"{output_path.stem}.*.srt"),
    ):
        sidecar.unlink(missing_ok=True)
    _thumbnail_path(filename).unlink(missing_ok=True)
    (settings.temp_dir / "thumbnails" / f"{output_path.stem}.jpg").unlink(missing_ok=True)
    return {"deleted": filename}


@router.delete("/api/v1/outputs", dependencies=[Depends(get_current_user)])
async def clear_outputs() -> dict[str, int]:
    outputs_dir = settings.outputs_dir.resolve()
    if not outputs_dir.exists():
        return {"deleted": 0}

    # Remove cached thumbnails generated for previews; otherwise they accumulate
    # indefinitely (one 480p JPEG per output).
    thumbnails_dir = (settings.temp_dir / "thumbnails").resolve()
    try:
        thumbnails_dir.relative_to(settings.temp_dir.resolve())
    except ValueError:
        thumbnails_dir = settings.temp_dir / "thumbnails"

    count = await asyncio.to_thread(_delete_output_files, outputs_dir, thumbnails_dir)
    return {"deleted": count}
