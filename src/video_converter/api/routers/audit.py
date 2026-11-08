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
    "/api/v1/audit",
    response_model=list[AuditEventDto],
    dependencies=[Depends(get_current_user)],
)
async def list_audit_events(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEventDto]:
    audit_path = settings.logs_dir / "audit.jsonl"
    if not audit_path.exists():
        return []
    lines = await asyncio.to_thread(read_recent_audit_lines, settings.logs_dir, limit)
    events: list[AuditEventDto] = []
    for line in reversed(lines):
        try:
            events.append(AuditEventDto.model_validate_json(line))
        except ValueError:
            continue
        if len(events) >= limit:
            break
    return events
