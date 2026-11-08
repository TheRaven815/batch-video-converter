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
    _audit,
    _batch_summary_from_records,
    _maybe_await,
    _records_for_batch,
)
from video_converter.api.routers.jobs import (  # noqa: E402
    archive_jobs_bulk,
    cancel_jobs_bulk,
    delete_jobs_bulk,
    start_jobs_bulk,
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
    "/api/v1/batches", response_model=BatchListResponse, dependencies=[Depends(get_current_user)]
)
async def list_batches(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> BatchListResponse:
    list_batch_ids_method = getattr(job_repository, "list_batch_ids", None)
    list_batch_records_method = getattr(job_repository, "list_batch_records", None)
    if list_batch_ids_method is not None and list_batch_records_method is not None:
        batch_ids = await _maybe_await(list_batch_ids_method())
        if batch_ids:
            indexed_batches: list[BatchSummaryDto] = []
            scan_index = cursor
            while scan_index < len(batch_ids) and len(indexed_batches) < limit:
                batch_id = batch_ids[scan_index]
                scan_index += 1
                records = await _maybe_await(list_batch_records_method(batch_id))
                visible = [record for record in records if not record.archived]
                if visible:
                    indexed_batches.append(_batch_summary_from_records(batch_id, visible))
            next_cursor = scan_index if scan_index < len(batch_ids) else None
            if response is not None and next_cursor is not None:
                response.headers["X-Next-Cursor"] = str(next_cursor)
            return BatchListResponse(
                batches=indexed_batches,
                next_cursor=str(next_cursor) if next_cursor is not None else None,
            )

    grouped: dict[str, list[JobRecord]] = {}
    scan_cursor: int | None = 0
    while scan_cursor is not None:
        records, scan_cursor = await _maybe_await(
            job_repository.list_records_page(
                cursor=scan_cursor, limit=max(limit, 100), newest_first=True
            )
        )
        for record in records:
            if not record.batch_id or record.archived:
                continue
            grouped.setdefault(record.batch_id, []).append(record)

    batches = [
        _batch_summary_from_records(batch_id, records) for batch_id, records in grouped.items()
    ]
    batches.sort(key=lambda batch: (batch.updated_at or "", batch.batch_id), reverse=True)
    selected = batches[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < len(batches) else None
    if response is not None and next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(next_cursor)
    return BatchListResponse(
        batches=selected, next_cursor=str(next_cursor) if next_cursor is not None else None
    )


@router.post(
    "/api/v1/batches/{batch_id}/{action}",
    response_model=BatchActionResponse,
)
async def act_on_batch(
    batch_id: str,
    action: str,
    actor: Annotated[str, Depends(get_current_user)],
) -> BatchActionResponse:
    if action not in {"cancel", "retry", "archive", "delete"}:
        raise HTTPException(status_code=422, detail="Unsupported batch action")
    records = await _records_for_batch(batch_id)
    if not records:
        raise HTTPException(status_code=404, detail="Batch not found")
    payload = JobIdsRequest(job_ids=[record.id for record in records])
    if action == "cancel":
        result = await cancel_jobs_bulk(payload)
    elif action == "retry":
        retryable = [
            record.id
            for record in records
            if record.status in {JobStatus.failed, JobStatus.cancelled, JobStatus.completed}
        ]
        result = await start_jobs_bulk(JobIdsRequest(job_ids=retryable))
    elif action == "archive":
        result = await archive_jobs_bulk(payload)
    else:
        result = await delete_jobs_bulk(payload)
    await _audit(
        actor,
        f"batch.{action}",
        batch_id,
        updated=len(result.updated),
        skipped=len(result.skipped),
    )
    return BatchActionResponse(batch_id=batch_id, action=action, result=result)
