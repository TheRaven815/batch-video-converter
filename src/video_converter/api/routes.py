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
from inspect import isawaitable
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

from video_converter.api.auth import get_current_user, get_stream_user
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

settings: Settings
storage_client: Any
job_repository: Any
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


def _write_audit_event(event: AuditEventDto) -> None:
    if settings is None or not hasattr(settings, "logs_dir"):
        return
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    with (settings.logs_dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(event.model_dump_json() + "\n")


async def _audit(actor: object, action: str, target: str, **details: Any) -> None:
    username = actor if isinstance(actor, str) and actor else "system"
    event = AuditEventDto(
        at=now_iso(), actor=username, action=action, target=target, details=details
    )
    try:
        await asyncio.to_thread(_write_audit_event, event)
    except OSError:
        logger.warning("could not persist audit event", exc_info=True)


def configure_runtime(
    configured_settings: Settings,
    configured_storage: Any,
    configured_repository: Any,
) -> None:
    """Bind lifespan-owned resources used by route handlers."""
    global settings, storage_client, job_repository
    settings = configured_settings
    storage_client = configured_storage
    job_repository = configured_repository


async def _maybe_await(value: Any) -> Any:
    return await value if isawaitable(value) else value


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
SUBTITLE_CACHE_TTL_SECONDS = 300
IDEMPOTENCY_KEY_PREFIX = "idempotency:batch:"
_subtitle_probe_cache: dict[tuple[str, str], tuple[float, MediaSubtitleProbeResponse]] = {}


def _build_media_roots_map() -> dict[str, Any]:
    return {root.key: root for root in settings.media_roots}


def _validate_source_payload(payload: JobCreateRequest) -> None:
    has_root = bool(payload.source_root_key)
    has_path = bool(payload.source_path)
    if has_root != has_path:
        raise HTTPException(
            status_code=422, detail="source_root_key and source_path must be provided together"
        )

    if not has_root:
        return

    try:
        validate_source_path(
            _build_media_roots_map(),
            payload.source_root_key or "",
            payload.source_path or "",
            supported_extensions=VIDEO_EXTENSIONS,
        )
    except ValueError as exc:
        raise path_validation_error(exc) from exc


def _build_job_record(payload: JobCreateRequest, *, batch_id: str | None = None) -> JobRecord:
    _validate_source_payload(payload)

    inferred_name = payload.input_filename
    if not inferred_name and payload.source_path:
        inferred_name = Path(payload.source_path).name

    job_id = str(uuid.uuid4())
    now = now_iso()
    return JobRecord(
        id=job_id,
        status=JobStatus.queued,
        profile=payload.profile,
        video_export=payload.video_export,
        audio_export=payload.audio_export,
        subtitle_export=payload.subtitle_export,
        input_filename=inferred_name,
        source_root_key=payload.source_root_key,
        source_path=payload.source_path,
        subtitle_language=payload.subtitle_language,
        progress_percent=0,
        progress_phase="queued",
        progress_message="Job queued",
        progress_updated_at=now,
        created_at=now,
        updated_at=now,
        batch_id=batch_id,
        attempt_count=0,
        max_attempts=payload.max_attempts,
        quality_crf=payload.quality_crf,
        target_video_bitrate=payload.target_video_bitrate,
        audio_bitrate_kbps=payload.audio_bitrate_kbps,
        resolution=payload.resolution,
        encoder_preset=payload.encoder_preset,
        hardware_acceleration=payload.hardware_acceleration,
        priority=payload.priority,
        audio_stream_indexes=payload.audio_stream_indexes,
        subtitle_stream_indexes=payload.subtitle_stream_indexes,
        audio_channel_mode=payload.audio_channel_mode,
        skip_existing_output=payload.skip_existing_output,
        log_download_url=f"/api/v1/jobs/{job_id}/log",
    )


async def _enqueue_job(record: JobRecord) -> None:
    await _maybe_await(job_repository.enqueue(record))


async def _enqueue_jobs(records: list[JobRecord]) -> None:
    await _maybe_await(job_repository.enqueue_many(records))


def _parse_job_record(raw: str) -> JobRecord | None:
    return job_repository.parse_record(raw)


async def _get_job_record(job_id: str) -> JobRecord | None:
    return await _maybe_await(job_repository.get(job_id))


@router.post(
    "/api/v1/jobs",
    response_model=JobRecord,
    status_code=201,
    dependencies=[Depends(get_current_user)],
)
async def create_job(payload: JobCreateRequest) -> JobRecord:
    record = _build_job_record(payload)
    await _enqueue_job(record)
    return record


@router.post(
    "/api/v1/jobs/validate",
    response_model=JobValidationResponse,
    dependencies=[Depends(get_current_user)],
)
async def validate_jobs(payload: JobBatchCreateRequest) -> JobValidationResponse:
    items: list[JobValidationItem] = []
    for index, item in enumerate(payload.jobs):
        try:
            _validate_source_payload(item)
        except HTTPException as exc:
            message = str(exc.detail)
            items.append(
                JobValidationItem(
                    index=index,
                    valid=False,
                    input_filename=item.input_filename,
                    source_root_key=item.source_root_key,
                    source_path=item.source_path,
                    error_code=error_code(exc),
                    message=message,
                    recoverable=exc.status_code == 422,
                )
            )
        else:
            items.append(
                JobValidationItem(
                    index=index,
                    valid=True,
                    input_filename=item.input_filename,
                    source_root_key=item.source_root_key,
                    source_path=item.source_path,
                )
            )

    valid_count = sum(1 for item in items if item.valid)
    return JobValidationResponse(
        items=items, valid_count=valid_count, invalid_count=len(items) - valid_count
    )


def _idempotency_cache_key(idempotency_key: str, actor: str) -> str:
    actor_digest = hashlib.sha256(actor.encode()).hexdigest()[:16]
    return f"{IDEMPOTENCY_KEY_PREFIX}{actor_digest}:{idempotency_key}"


def _batch_payload_hash(payload: JobBatchCreateRequest) -> str:
    canonical = payload.model_dump_json(exclude_none=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _claim_idempotency_key(
    cache_key: str,
    payload_hash: str,
) -> tuple[bool, JobBatchCreateResponse | None]:
    claim = json.dumps({"payload_hash": payload_hash, "state": "pending"})
    created = await _maybe_await(storage_client.set(cache_key, claim, ex=60, nx=True))
    if created:
        return True, None
    raw = await _maybe_await(storage_client.get(cache_key))
    if not raw:
        return await _claim_idempotency_key(cache_key, payload_hash)
    try:
        stored = json.loads(str(raw))
    except json.JSONDecodeError:
        stored = None
    if not isinstance(stored, dict):
        raise HTTPException(status_code=409, detail="Invalid idempotency state")
    if stored.get("payload_hash") != payload_hash:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key was already used with a different payload",
        )
    response_payload = stored.get("response")
    if response_payload is not None:
        return False, JobBatchCreateResponse.model_validate(response_payload)
    raise HTTPException(status_code=409, detail="An identical batch request is still processing")


async def _store_idempotent_batch_response(
    cache_key: str | None,
    payload_hash: str,
    response: JobBatchCreateResponse,
) -> None:
    if not cache_key:
        return
    await _maybe_await(
        storage_client.set(
            cache_key,
            json.dumps(
                {
                    "payload_hash": payload_hash,
                    "state": "completed",
                    "response": response.model_dump(mode="json"),
                }
            ),
            ex=24 * 60 * 60,
        )
    )


def _validate_and_build_batch(
    items: list[JobCreateRequest],
    batch_id: str,
) -> tuple[list[JobRecord], list[BatchCreateError]]:
    """Validate all items first, then build records for valid ones.

    Returns a tuple of (valid_records, errors). Every item is validated
    before any record is built so that a single failure does not leave
    the batch in a partially-enqueued state.
    """
    errors: list[BatchCreateError] = []
    valid_items: list[tuple[int, JobCreateRequest]] = []

    for index, item in enumerate(items):
        try:
            _validate_source_payload(item)
        except HTTPException as exc:
            message = str(exc.detail) if exc.detail else "Validation failed"
            errors.append(
                BatchCreateError(
                    index=index,
                    input_filename=item.input_filename,
                    source_root_key=item.source_root_key,
                    source_path=item.source_path,
                    error_code=error_code(exc),
                    message=message,
                    recoverable=exc.status_code == 422,
                )
            )
        else:
            valid_items.append((index, item))

    records: list[JobRecord] = []
    for index, item in valid_items:
        try:
            records.append(_build_job_record(item, batch_id=batch_id))
        except HTTPException as exc:
            errors.append(
                BatchCreateError(
                    index=index,
                    input_filename=item.input_filename,
                    source_root_key=item.source_root_key,
                    source_path=item.source_path,
                    error_code=error_code(exc),
                    message=str(exc.detail or "Source disappeared during validation"),
                    recoverable=True,
                )
            )

    return records, errors


@router.post(
    "/api/v1/jobs/batch",
    response_model=JobBatchCreateResponse,
    status_code=201,
    dependencies=[Depends(get_current_user)],
)
async def create_jobs_batch(
    payload: JobBatchCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
    actor: Annotated[str, Depends(get_current_user)] = "system",
) -> JobBatchCreateResponse:
    normalized_idempotency_key = idempotency_key.strip() if idempotency_key else None
    payload_hash = _batch_payload_hash(payload)
    cache_key = (
        _idempotency_cache_key(normalized_idempotency_key, actor)
        if normalized_idempotency_key
        else None
    )
    if cache_key:
        _claimed, cached = await _claim_idempotency_key(cache_key, payload_hash)
        if cached is not None:
            return cached

    batch_id = str(uuid.uuid4())
    enqueued = False
    try:
        records, errors = _validate_and_build_batch(payload.jobs, batch_id)

        if not records and errors:
            raise HTTPException(
                status_code=422,
                detail=f"All {len(errors)} job(s) failed validation",
            )

        if not records:
            raise HTTPException(status_code=422, detail="No jobs provided")

        await _enqueue_jobs(records)
        enqueued = True
        response = JobBatchCreateResponse(
            jobs=records,
            errors=errors,
            idempotency_key=normalized_idempotency_key,
        )
        await _store_idempotent_batch_response(cache_key, payload_hash, response)
        return response
    except Exception:
        # Before enqueue it is safe to release the claim for a corrected retry.
        # Afterwards, retaining the short-lived pending claim prevents duplicate
        # jobs if persisting the cached response itself fails.
        if cache_key and not enqueued:
            await _maybe_await(storage_client.delete(cache_key))
        raise


def _parse_status_filter(status: str | JobStatus | None) -> set[JobStatus] | None:
    if status is None:
        return None
    if isinstance(status, JobStatus):
        return {status}

    statuses: set[JobStatus] = set()
    for raw in str(status).split(","):
        value = raw.strip().lower()
        if not value or value == "all":
            continue
        if value == "processing":
            statuses.add(JobStatus.running)
            continue
        try:
            statuses.add(JobStatus(value))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid status filter: {value}") from exc

    return statuses or None


def _parse_datetime_filter(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {name} filter") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _record_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _matches_job_filters(
    record: JobRecord,
    *,
    statuses: set[JobStatus] | None,
    q: str | None,
    profile: str | None,
    source_root_key: str | None,
    source_type: str | None,
    include_archived: bool,
    created_after: datetime | None,
    created_before: datetime | None,
) -> bool:
    if record.archived and not include_archived:
        return False
    if statuses is not None and record.status not in statuses:
        return False
    if profile and record.profile != profile:
        return False
    if source_root_key and record.source_root_key != source_root_key:
        return False
    if source_type:
        normalized_source_type = source_type.lower().strip()
        is_server = bool(record.source_root_key and record.source_path)
        if normalized_source_type == "server" and not is_server:
            return False
        if normalized_source_type in {"legacy", "upload"} and is_server:
            return False
    if q:
        haystack = " ".join(
            str(part or "")
            for part in [
                record.id,
                record.input_filename,
                record.source_path,
                record.output_filename,
                record.error_message,
            ]
        ).lower()
        if q.lower() not in haystack:
            return False
    created_at = _record_datetime(record.created_at)
    if created_after is not None and (created_at is None or created_at < created_after):
        return False
    if created_before is not None and (created_at is None or created_at > created_before):
        return False
    return True


@router.get(
    "/api/v1/jobs", response_model=list[JobRecord], dependencies=[Depends(get_current_user)]
)
async def list_jobs(
    response: Response,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    profile: Annotated[str | None, Query(max_length=100)] = None,
    source_root_key: Annotated[str | None, Query(max_length=64)] = None,
    source_type: Annotated[str | None, Query(max_length=32)] = None,
    include_archived: bool = False,
    created_after: str | None = None,
    created_before: str | None = None,
) -> list[JobRecord]:
    jobs: list[JobRecord] = []
    next_cursor: int | None = None
    cursor_text = str(cursor) if cursor is not None else None
    scan_cursor: int | None = int(cursor_text) if cursor_text and cursor_text.isdigit() else 0
    statuses = _parse_status_filter(status)
    created_after_dt = _parse_datetime_filter(created_after, "created_after")
    created_before_dt = _parse_datetime_filter(created_before, "created_before")
    queue_positions_method = getattr(job_repository, "queue_positions", None)
    queue_positions = await _maybe_await(queue_positions_method()) if queue_positions_method else {}

    list_ids_method = getattr(job_repository, "list_ids", None)
    if list_ids_method is not None and not (cursor_text and cursor_text.isdigit()):
        ordered_ids = list(reversed(await _maybe_await(list_ids_method())))
        start = 0
        if cursor_text:
            try:
                start = ordered_ids.index(cursor_text) + 1
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid or expired cursor") from exc
        last_scanned_index: int | None = None
        for index, job_id in enumerate(ordered_ids[start:], start=start):
            last_scanned_index = index
            record = await _maybe_await(_get_job_record(str(job_id)))
            if record is None or not _matches_job_filters(
                record,
                statuses=statuses,
                q=q,
                profile=profile,
                source_root_key=source_root_key,
                source_type=source_type,
                include_archived=include_archived,
                created_after=created_after_dt,
                created_before=created_before_dt,
            ):
                continue
            if record.status == JobStatus.queued:
                record.queue_position = queue_positions.get(record.id)
                if record.queue_position:
                    record.estimated_start_seconds = max(0, record.queue_position - 1) * 60
            jobs.append(record)
            if len(jobs) >= limit:
                break
        if (
            response is not None
            and last_scanned_index is not None
            and last_scanned_index + 1 < len(ordered_ids)
        ):
            response.headers["X-Next-Cursor"] = str(ordered_ids[last_scanned_index])
        return jobs

    while scan_cursor is not None and len(jobs) < limit:
        records, page_next_cursor = await _maybe_await(
            job_repository.list_records_page(cursor=scan_cursor, limit=limit, newest_first=True)
        )
        if not records:
            break
        for record in records:
            if not _matches_job_filters(
                record,
                statuses=statuses,
                q=q,
                profile=profile,
                source_root_key=source_root_key,
                source_type=source_type,
                include_archived=include_archived,
                created_after=created_after_dt,
                created_before=created_before_dt,
            ):
                continue
            jobs.append(record)
            if record.status == JobStatus.queued:
                record.queue_position = queue_positions.get(record.id)
                if record.queue_position:
                    record.estimated_start_seconds = max(0, record.queue_position - 1) * 60
            if len(jobs) >= limit:
                next_cursor = page_next_cursor
                break
        scan_cursor = page_next_cursor

    if response is not None and next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(next_cursor)

    return jobs


async def _persist_job_record(record: JobRecord) -> None:
    await _maybe_await(job_repository.persist(record))


def _batch_summary_from_records(batch_id: str, records: list[JobRecord]) -> BatchSummaryDto:
    summary = BatchSummaryDto(batch_id=batch_id, total=len(records))
    progress_values: list[int] = []
    for record in records:
        setattr(summary, record.status.value, getattr(summary, record.status.value) + 1)
        progress_values.append(int(record.progress_percent or 0))
        if summary.created_at is None or record.created_at < summary.created_at:
            summary.created_at = record.created_at
        if summary.updated_at is None or record.updated_at > summary.updated_at:
            summary.updated_at = record.updated_at
    if progress_values:
        summary.progress_percent = int(sum(progress_values) / len(progress_values))
    return summary


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
            selected_ids = batch_ids[cursor : cursor + limit]
            indexed_batches: list[BatchSummaryDto] = []
            for batch_id in selected_ids:
                records = await _maybe_await(list_batch_records_method(batch_id))
                visible = [record for record in records if not record.archived]
                if visible:
                    indexed_batches.append(_batch_summary_from_records(batch_id, visible))
            next_cursor = cursor + limit if cursor + limit < len(batch_ids) else None
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


@router.get("/api/v1/jobs/stream", dependencies=[Depends(get_stream_user)])
async def stream_jobs(request: Request) -> StreamingResponse:
    async def _snapshot_event() -> tuple[str, str]:
        page, _ = await _maybe_await(
            job_repository.list_records_page(cursor=0, limit=250, newest_first=True)
        )
        records = [record for record in page if not record.archived]
        timestamp = now_iso()
        snapshot = json.dumps(
            [record.model_dump(mode="json") for record in records], sort_keys=True
        )
        event = (
            "event: jobs_snapshot\n"
            f"data: {json.dumps({'event': 'jobs_snapshot', 'timestamp': timestamp, 'data': {'jobs': [record.model_dump(mode='json') for record in records]}})}\n\n"
        )
        return event, snapshot

    async def _events() -> AsyncIterator[str]:
        if getattr(storage_client, "backend_name", "redis") == "redis":
            async with storage_client.pubsub() as pubsub:
                await pubsub.subscribe(JOB_EVENTS_CHANNEL)
                initial_event, _ = await _snapshot_event()
                yield initial_event
                last_heartbeat = time.monotonic()
                while not await request.is_disconnected():
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message and message.get("data"):
                        payload = str(message["data"])
                        try:
                            event_name = str(json.loads(payload).get("event") or "job_updated")
                        except json.JSONDecodeError:
                            logger.warning("ignored malformed job pub/sub event")
                            continue
                        yield f"event: {event_name}\ndata: {payload}\n\n"
                        last_heartbeat = time.monotonic()
                    elif time.monotonic() - last_heartbeat >= 15:
                        heartbeat = {
                            "event": "heartbeat",
                            "timestamp": now_iso(),
                            "data": {},
                        }
                        yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"
                        last_heartbeat = time.monotonic()
            return

        # Local SQLite mode has no cross-process pub/sub transport. Keep a
        # development-only snapshot fallback; production Redis streams deltas.
        initial_event, initial_snapshot = await _snapshot_event()
        yield initial_event
        last_snapshot = initial_snapshot
        while True:
            if await request.is_disconnected():
                break
            page, _ = await _maybe_await(
                job_repository.list_records_page(cursor=0, limit=250, newest_first=True)
            )
            records = [record for record in page if not record.archived]
            snapshot = json.dumps(
                [record.model_dump(mode="json") for record in records], sort_keys=True
            )
            timestamp = now_iso()
            if snapshot != last_snapshot:
                yield f"event: jobs_snapshot\ndata: {json.dumps({'event': 'jobs_snapshot', 'timestamp': timestamp, 'data': {'jobs': [record.model_dump(mode='json') for record in records]}})}\n\n"
                last_snapshot = snapshot
            else:
                yield f"event: heartbeat\ndata: {json.dumps({'event': 'heartbeat', 'timestamp': timestamp, 'data': {}})}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/api/v1/jobs/{job_id}", response_model=JobRecord, dependencies=[Depends(get_current_user)]
)
async def get_job(job_id: str) -> JobRecord:
    record = await _maybe_await(_get_job_record(job_id))
    if not record:
        raise HTTPException(status_code=404, detail="Job not found")
    return record


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
    try:
        raw_heartbeat = await _maybe_await(storage_client.get(WORKER_HEARTBEAT_KEY))
        heartbeat = json.loads(str(raw_heartbeat)) if raw_heartbeat else {}
        heartbeat_at = float(heartbeat.get("timestamp", 0))
        heartbeat_age = max(0.0, time.time() - heartbeat_at) if heartbeat_at else None
        hardware_encoders = [
            str(value) for value in heartbeat.get("hardware_encoders", []) if value
        ]
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
    )


def _safe_output_path(filename: str) -> Path:
    output_path = (settings.outputs_dir / filename).resolve()
    try:
        output_path.relative_to(settings.outputs_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid output filename") from exc
    return output_path


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

    entries: list[tuple[Path, os.stat_result]] = []
    for child in outputs_dir.iterdir():
        try:
            if not child.is_file() or child.name.startswith("."):
                continue
            if q and q.lower() not in child.name.lower():
                continue
            entries.append((child, child.stat()))
        except OSError:
            # Output may be concurrently cleaned up between directory scan and stat.
            continue

    entries.sort(key=lambda item: (item[1].st_mtime, item[0].name.lower()), reverse=True)
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


def _iter_file_range(path: Path, start: int, end: int):
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


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


def _thumbnail_path(filename: str) -> Path:
    directory = settings.temp_dir / "thumbnails"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{Path(filename).stem}.jpg"


@router.get("/api/v1/outputs/{filename}/thumbnail", dependencies=[Depends(get_stream_user)])
async def output_thumbnail(filename: str) -> FileResponse:
    output_path = _safe_output_path(filename)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    thumbnail = _thumbnail_path(filename)
    if not thumbnail.exists() or thumbnail.stat().st_mtime < output_path.stat().st_mtime:
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
            str(thumbnail),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return_code = await asyncio.wait_for(process.wait(), timeout=30)
        except TimeoutError as exc:
            process.kill()
            raise HTTPException(status_code=504, detail="Thumbnail generation timed out") from exc
        if return_code != 0 or not thumbnail.exists():
            raise HTTPException(status_code=500, detail="Thumbnail generation failed")
    return FileResponse(thumbnail, media_type="image/jpeg")


@router.delete("/api/v1/outputs/{filename}", dependencies=[Depends(get_current_user)])
async def delete_output(filename: str) -> dict[str, str]:
    output_path = _safe_output_path(filename)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    output_path.unlink()
    _thumbnail_path(filename).unlink(missing_ok=True)
    return {"deleted": filename}


@router.get(
    "/api/v1/jobs/{job_id}/log",
    dependencies=[Depends(get_current_user)],
)
async def download_job_log(job_id: str) -> FileResponse:
    record = await _get_job_record(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    log_path = (settings.logs_dir / f"{job_id}.ffmpeg.log").resolve()
    try:
        log_path.relative_to(settings.logs_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Job log not found")
    return FileResponse(log_path, filename=log_path.name, media_type="text/plain")


@router.post(
    "/api/v1/media/uploads",
    response_model=UploadResponse,
    status_code=201,
)
async def upload_media(
    file: Annotated[UploadFile, File()],
    actor: Annotated[str, Depends(get_current_user)],
) -> UploadResponse:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Unsupported video extension")
    safe_stem = (
        "".join(
            character if character.isalnum() or character in {"-", "_", "."} else "_"
            for character in Path(file.filename or "upload").stem
        ).strip("._")
        or "upload"
    )
    filename = f"{safe_stem}.{uuid.uuid4().hex[:8]}{suffix}"
    destination = settings.input_dir / filename
    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Upload exceeds configured limit")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    await _audit(actor, "media.upload", filename, size_bytes=size)
    return UploadResponse(input_filename=filename, size_bytes=size)


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
    lines = await asyncio.to_thread(audit_path.read_text, encoding="utf-8")
    events: list[AuditEventDto] = []
    for line in reversed(lines.splitlines()):
        try:
            events.append(AuditEventDto.model_validate_json(line))
        except ValueError:
            continue
        if len(events) >= limit:
            break
    return events


@router.delete("/api/v1/outputs", dependencies=[Depends(get_current_user)])
async def clear_outputs() -> dict[str, int]:
    outputs_dir = settings.outputs_dir.resolve()
    if not outputs_dir.exists():
        return {"deleted": 0}

    count = 0
    for child in outputs_dir.iterdir():
        # Includes video outputs and .srt sidecars (e.g. ``<stem>.srt``).
        if child.is_file() and not child.name.startswith("."):
            try:
                child.unlink()
                count += 1
            except Exception as exc:
                logger.error("Failed to delete %s: %s", child, exc)

    # Remove cached thumbnails generated for previews; otherwise they accumulate
    # indefinitely (one 480p JPEG per output).
    thumbnails_dir = (settings.temp_dir / "thumbnails").resolve()
    try:
        thumbnails_dir.relative_to(settings.temp_dir.resolve())
    except ValueError:
        thumbnails_dir = settings.temp_dir / "thumbnails"
    if thumbnails_dir.exists() and thumbnails_dir.is_dir():
        for child in thumbnails_dir.iterdir():
            if child.is_file() and not child.name.startswith("."):
                try:
                    child.unlink()
                    count += 1
                except Exception as exc:
                    logger.error("Failed to delete thumbnail %s: %s", child, exc)
    return {"deleted": count}


def _default_system_settings() -> SystemSettings:
    return SystemSettings(worker_concurrency=settings.worker_concurrency)


def _system_settings_response(value: SystemSettings) -> JSONResponse:
    """Keep legacy response shape while retaining a precise OpenAPI schema."""
    data = value.model_dump(mode="json")
    for key in ("retry", "disk_safety"):
        if data.get(key) is None:
            data.pop(key, None)
    for key in (
        "quality_crf",
        "target_video_bitrate",
        "audio_bitrate_kbps",
        "resolution",
        "encoder_preset",
        "hardware_acceleration",
    ):
        if data["default_export"].get(key) is None:
            data["default_export"].pop(key, None)
    for key in ("delete_terminal_jobs", "job_retention_days"):
        if data["auto_cleanup"].get(key) is None:
            data["auto_cleanup"].pop(key, None)
    return JSONResponse(data)


async def _load_system_settings() -> SystemSettings:
    raw = await _maybe_await(storage_client.get("system:settings"))
    defaults = _default_system_settings()
    if raw:
        try:
            stored = SystemSettings.model_validate_json(raw)
            return SystemSettings.model_validate(
                defaults.model_dump() | stored.model_dump(exclude_unset=True)
            )
        except Exception:
            logger.warning("stored system settings are invalid; using defaults")
    return defaults


@router.get(
    "/api/v1/settings",
    response_model=SystemSettings,
    dependencies=[Depends(get_current_user)],
)
async def get_system_settings() -> JSONResponse:
    value = await _maybe_await(_load_system_settings())
    return _system_settings_response(value)


@router.post(
    "/api/v1/settings",
    response_model=SystemSettings,
    dependencies=[Depends(get_current_user)],
)
async def update_system_settings(payload: SystemSettings) -> JSONResponse:
    merged = SystemSettings.model_validate(
        _default_system_settings().model_dump() | payload.model_dump(exclude_unset=True)
    )
    await _maybe_await(storage_client.set("system:settings", merged.model_dump_json()))
    return _system_settings_response(merged)


async def _cancel_record(record: JobRecord) -> tuple[JobRecord, str | None]:
    atomic_cancel = getattr(job_repository, "request_cancel", None)
    if atomic_cancel is not None:
        updated, reason = await _maybe_await(atomic_cancel(record.id))
        return (updated or record), reason

    if record.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
        return record, "Job is already completed"

    now = now_iso()
    record.cancel_requested = True
    record.updated_at = now
    record.progress_updated_at = now

    if record.status == JobStatus.queued:
        removed = await _maybe_await(job_repository.remove_from_queue(record.id))
        if removed > 0:
            record.status = JobStatus.cancelled
            record.progress_phase = "cancelled"
            record.progress_message = "Job cancelled before start"
            record.progress_percent = 0
        else:
            record.progress_phase = "cancelling"
            record.progress_message = "Cancellation requested"
    else:
        record.progress_phase = "cancelling"
        record.progress_message = "Cancellation requested"

    await _maybe_await(_persist_job_record(record))
    return record, None


@router.post(
    "/api/v1/jobs/bulk/cancel",
    response_model=JobBulkActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def cancel_jobs_bulk(
    payload: JobIdsRequest,
    actor: Annotated[str, Depends(get_current_user)] = "system",
) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = await _maybe_await(_get_job_record(job_id))
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        changed, reason = await _maybe_await(_cancel_record(record))
        if reason:
            skipped.append(JobActionSkip(job_id=job_id, reason=reason))
            continue
        updated.append(changed)

    result = JobBulkActionResponse(updated=updated, skipped=skipped)
    await _audit(actor, "jobs.cancel", ",".join(payload.job_ids), updated=len(updated))
    return result


@router.post(
    "/api/v1/jobs/{job_id}/cancel",
    response_model=JobRecord,
    dependencies=[Depends(get_current_user)],
)
async def cancel_job(
    job_id: str,
    actor: Annotated[str, Depends(get_current_user)] = "system",
) -> JobRecord:
    record = await _maybe_await(_get_job_record(job_id))
    if not record:
        raise HTTPException(status_code=404, detail="Job not found")

    updated, _ = await _maybe_await(_cancel_record(record))
    await _audit(actor, "job.cancel", job_id)
    return updated


@router.post(
    "/api/v1/jobs/bulk/start",
    response_model=JobBulkActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def start_jobs_bulk(payload: JobIdsRequest) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = await _maybe_await(_get_job_record(job_id))
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        if record.status == JobStatus.running:
            skipped.append(JobActionSkip(job_id=job_id, reason="Running job cannot be restarted"))
            continue

        if record.status == JobStatus.queued:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job is already queued"))
            continue

        now = now_iso()
        record.status = JobStatus.queued
        record.archived = False
        record.cancel_requested = False
        record.error_message = None
        record.progress_percent = 0
        record.progress_phase = "queued"
        record.progress_message = "Job queued"
        record.progress_updated_at = now
        record.updated_at = now
        record.started_at = None
        record.finished_at = None

        await _maybe_await(job_repository.requeue_existing(record))
        updated.append(record)

    return JobBulkActionResponse(updated=updated, skipped=skipped)


@router.post(
    "/api/v1/jobs/bulk/archive",
    response_model=JobBulkActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def archive_jobs_bulk(payload: JobIdsRequest) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = await _maybe_await(_get_job_record(job_id))
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        if record.status in {JobStatus.running, JobStatus.queued}:
            skipped.append(
                JobActionSkip(
                    job_id=job_id, reason="Active job cannot be archived; cancel it first"
                )
            )
            continue

        record.archived = True
        record.updated_at = now_iso()
        await _maybe_await(_persist_job_record(record))
        updated.append(record)

    return JobBulkActionResponse(updated=updated, skipped=skipped)


@router.post(
    "/api/v1/jobs/bulk/delete",
    response_model=JobBulkActionResponse,
    dependencies=[Depends(get_current_user)],
)
async def delete_jobs_bulk(
    payload: JobIdsRequest,
    actor: Annotated[str, Depends(get_current_user)] = "system",
) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = await _maybe_await(_get_job_record(job_id))
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        if record.status == JobStatus.running:
            skipped.append(JobActionSkip(job_id=job_id, reason="Running job cannot be deleted"))
            continue

        await _maybe_await(job_repository.delete(job_id))
        updated.append(record)

    result = JobBulkActionResponse(updated=updated, skipped=skipped)
    await _audit(actor, "jobs.delete", ",".join(payload.job_ids), deleted=len(updated))
    return result


async def _records_for_batch(batch_id: str) -> list[JobRecord]:
    indexed = getattr(job_repository, "list_batch_records", None)
    if indexed is not None:
        records = list(await _maybe_await(indexed(batch_id)))
        if records:
            return records
    records: list[JobRecord] = []
    cursor: int | None = 0
    while cursor is not None:
        page, cursor = await _maybe_await(
            job_repository.list_records_page(cursor=cursor, limit=500, newest_first=True)
        )
        records.extend(record for record in page if record.batch_id == batch_id)
    return records


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
    root_map = _build_media_roots_map()
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

    all_entries: list[MediaBrowseEntryDto] = []
    query = q.lower() if q else None

    if target != root.path and not query:
        parent = target.parent
        parent_rel = parent.relative_to(root.path).as_posix()
        all_entries.append(MediaBrowseEntryDto(type="dir", name="..", rel_path=parent_rel))

    resolved_root = root.path.resolve()

    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if query and query not in child.name.lower():
            continue

        try:
            if not child.resolve().is_relative_to(resolved_root):
                continue
        except (OSError, RuntimeError):
            # Skip files with broken symlinks or permission errors on resolve
            continue

        rel = child.relative_to(root.path).as_posix()

        if child.is_dir():
            all_entries.append(MediaBrowseEntryDto(type="dir", name=child.name, rel_path=rel))
            continue

        if child.suffix.lower() in VIDEO_EXTENSIONS:
            all_entries.append(MediaBrowseEntryDto(type="file", name=child.name, rel_path=rel))

    normalized_current = target.relative_to(root.path).as_posix()
    if normalized_current == ".":
        normalized_current = ""

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
    root_map = _build_media_roots_map()
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

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if target.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Path has unsupported extension")

    normalized_path = target.relative_to(root.path).as_posix()
    cache_key = (root.key, normalized_path)
    cached = _subtitle_probe_cache.get(cache_key)
    if cached:
        if cached[0] > time.monotonic():
            return cached[1]
        # Lazy eviction: remove expired entry to prevent memory leak.
        del _subtitle_probe_cache[cache_key]

    ffprobe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title",
        "-of",
        "json",
        str(target),
    ]

    try:
        probe_proc = subprocess.run(
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

    if probe_proc.returncode != 0:
        stderr_tail = (probe_proc.stderr or "ffprobe failed").strip()[-700:]
        raise HTTPException(status_code=500, detail=f"ffprobe error: {stderr_tail}")

    try:
        raw_data: Any = json.loads(probe_proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Failed to parse ffprobe output") from exc

    streams = raw_data.get("streams", []) if isinstance(raw_data, dict) else []
    tracks: list[MediaSubtitleTrackDto] = []

    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue

            stream_index = stream.get("index")
            if not isinstance(stream_index, int):
                continue

            tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
            language = str(tags.get("language") or "und").strip().lower() or "und"
            title_raw = tags.get("title")
            title = str(title_raw).strip() if title_raw is not None else None

            tracks.append(
                MediaSubtitleTrackDto(
                    index=stream_index,
                    language=language,
                    title=title or None,
                    codec_name=str(stream.get("codec_name") or "").strip() or None,
                )
            )

    result = MediaSubtitleProbeResponse(root_key=root.key, path=normalized_path, tracks=tracks)
    _subtitle_probe_cache[cache_key] = (time.monotonic() + SUBTITLE_CACHE_TTL_SECONDS, result)
    return result


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
frontend_dir = _PROJECT_ROOT / "frontend" / "dist"


@router.get("/")
async def root() -> FileResponse:
    index = frontend_dir / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Built UI not found")
    return FileResponse(index)
