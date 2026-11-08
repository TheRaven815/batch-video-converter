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
    _maybe_await,
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


@router.get("/health/live", response_model=HealthResponse)
async def health_live() -> HealthResponse:
    redis_status = "ok"
    try:
        await _maybe_await(storage_client.ping())
    except (redis.RedisError, StorageError):
        redis_status = "error"
    return HealthResponse(status="ok", redis=redis_status)


@router.get("/health/ready", response_model=HealthResponse)
async def health_ready() -> HealthResponse:
    try:
        await _maybe_await(storage_client.ping())
    except (redis.RedisError, StorageError) as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    return HealthResponse(status="ready", redis="ok")


@router.get(
    "/api/v1/worker/health",
    response_model=WorkerHealthResponse,
    dependencies=[Depends(get_current_user)],
)
async def worker_health() -> WorkerHealthResponse:
    redis_status = "ok"
    try:
        await _maybe_await(storage_client.ping())
        queue_depth = sum(
            int(await _maybe_await(storage_client.llen(queue)) or 0)
            for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME)
        )
        running_jobs = await _maybe_await(job_repository.count_running_jobs())
    except Exception:  # redis.RedisError or sqlite errors from the local backend
        redis_status = "error"
        queue_depth = 0
        running_jobs = 0

    heartbeat_age: float | None = None
    hardware_encoders: list[str] = []
    concurrency_limit = max(1, min(8, settings.worker_concurrency))
    effective_concurrency = concurrency_limit
    try:
        raw_heartbeat = await _maybe_await(storage_client.get(WORKER_HEARTBEAT_KEY))
        heartbeat = json.loads(str(raw_heartbeat)) if raw_heartbeat else {}
        heartbeat_at = float(heartbeat.get("timestamp", 0))
        heartbeat_age = max(0.0, time.time() - heartbeat_at) if heartbeat_at else None
        hardware_encoders = [
            str(value) for value in heartbeat.get("hardware_encoders", []) if value
        ]
        concurrency_limit = max(1, min(8, int(heartbeat.get("worker_concurrency_limit", concurrency_limit))))
        effective_concurrency = max(1, min(concurrency_limit, int(heartbeat.get("effective_worker_concurrency", concurrency_limit))))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    worker_online = heartbeat_age is not None and heartbeat_age < 30
    disk = shutil.disk_usage(settings.data_root)
    return WorkerHealthResponse(
        status="ok" if redis_status == "ok" and worker_online else "error",
        redis=redis_status,
        queue_depth=queue_depth,
        running_jobs=running_jobs,
        cpu_percent=psutil.cpu_percent(interval=None),
        checked_at=now_iso(),
        worker_online=worker_online,
        heartbeat_age_seconds=heartbeat_age,
        disk_total_bytes=disk.total,
        disk_used_bytes=disk.used,
        disk_free_bytes=disk.free,
        disk_used_percent=(disk.used / disk.total * 100) if disk.total else 0,
        hardware_encoders=hardware_encoders,
        worker_concurrency_limit=concurrency_limit,
        effective_worker_concurrency=effective_concurrency,
    )
