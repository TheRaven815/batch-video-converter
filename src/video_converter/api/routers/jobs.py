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
    VIDEO_EXTENSIONS,
    _apply_system_defaults,
    _audit,
    _batch_payload_hash,
    _build_job_record,
    _cancel_record,
    _claim_idempotency_key,
    _delete_job_artifacts,
    _enqueue_job,
    _enqueue_jobs,
    _idempotency_cache_key,
    _load_system_settings,
    _matches_job_filters,
    _maybe_await,
    _parse_datetime_filter,
    _parse_status_filter,
    _prefetch_job_records,
    _store_idempotent_batch_response,
    _validate_and_build_batch,
    _validate_source_payload,
)


async def _get_job_record(job_id: str) -> JobRecord | None:
    return await _routes._get_job_record(job_id)


async def _persist_job_record(record: JobRecord) -> None:
    return await _routes._persist_job_record(record)


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


@router.post(
    "/api/v1/jobs",
    response_model=JobRecord,
    status_code=201,
    dependencies=[Depends(get_current_user)],
)
async def create_job(payload: JobCreateRequest) -> JobRecord:
    defaults = await _load_system_settings()
    record = _build_job_record(_apply_system_defaults(payload, defaults))
    await _enqueue_job(record)
    return record


@router.post(
    "/api/v1/jobs/validate",
    response_model=JobValidationResponse,
    dependencies=[Depends(get_current_user)],
)
async def validate_jobs(payload: JobBatchCreateRequest) -> JobValidationResponse:
    defaults = await _load_system_settings()
    items: list[JobValidationItem] = []
    for index, item in enumerate(payload.jobs):
        merged = _apply_system_defaults(item, defaults)
        try:
            _validate_source_payload(merged)
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
        defaults = await _load_system_settings()
        records, errors = _validate_and_build_batch(payload.jobs, batch_id, defaults)

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
    cursor_text = str(cursor) if cursor is not None else None
    statuses = _parse_status_filter(status)
    created_after_dt = _parse_datetime_filter(created_after, "created_after")
    created_before_dt = _parse_datetime_filter(created_before, "created_before")
    queue_positions_method = getattr(job_repository, "queue_positions", None)
    queue_positions = await _maybe_await(queue_positions_method()) if queue_positions_method else {}
    concurrency = max(1, int(getattr(settings, "worker_concurrency", 1) or 1))
    running_records_method = getattr(job_repository, "list_running_records", None)
    running_records: list[JobRecord] = (
        await _maybe_await(running_records_method()) if running_records_method else []
    )
    running_count = len(running_records)
    if running_records_method is None:
        count_running_method = getattr(job_repository, "count_running_jobs", None)
        if count_running_method is not None:
            running_count = int(await _maybe_await(count_running_method()) or 0)
    running_etas = [
        record.progress_eta_seconds
        for record in running_records
        if record.progress_eta_seconds is not None
    ]
    slot_seconds = (
        max(1, round(sum(running_etas) / len(running_etas))) if running_etas else 60
    )

    list_ids_method = getattr(job_repository, "list_ids", None)
    if list_ids_method is None:
        # Backwards-compatible fallback for repositories exposing only the old
        # page API. Production repositories always provide list_ids.
        page_cursor: int | None = int(cursor_text) if cursor_text and cursor_text.isdigit() else 0
        last_page_cursor: int | None = None
        while page_cursor is not None and len(jobs) < limit:
            page, page_cursor = await _maybe_await(
                job_repository.list_records_page(
                    cursor=page_cursor, limit=limit, newest_first=True
                )
            )
            last_page_cursor = page_cursor
            if not page:
                break
            for record in page:
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
                if len(jobs) >= limit:
                    break
        if response is not None and last_page_cursor is not None and len(jobs) >= limit:
            response.headers["X-Next-Cursor"] = str(last_page_cursor)
        return jobs

    ordered_ids = list(reversed(await _maybe_await(list_ids_method())))
    start = 0
    if cursor_text:
        if cursor_text.isdigit():
            # Legacy offset cursors are converted to a position in the current
            # index instead of triggering the old page-scan that skipped rows
            # when filters applied.
            offset = max(0, int(cursor_text))
            start = min(offset, len(ordered_ids))
        else:
            try:
                start = ordered_ids.index(cursor_text) + 1
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid or expired cursor") from exc
    last_scanned_index: int | None = None
    chunk_size = 100
    for chunk_start in range(start, len(ordered_ids), chunk_size):
        if len(jobs) >= limit:
            break
        chunk = [str(job_id) for job_id in ordered_ids[chunk_start : chunk_start + chunk_size]]
        prefetched = await _prefetch_job_records(chunk)
        for offset, job_id in enumerate(chunk):
            last_scanned_index = chunk_start + offset
            record = prefetched.get(job_id)
            if record is None:
                record = await _maybe_await(_get_job_record(job_id))
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
                    jobs_ahead = max(0, record.queue_position - 1) + running_count
                    record.estimated_start_seconds = (jobs_ahead // concurrency) * slot_seconds
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
                await asyncio.to_thread(output.write, chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    await _audit(actor, "media.upload", filename, size_bytes=size)
    return UploadResponse(input_filename=filename, size_bytes=size)


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
    prefetched = await _prefetch_job_records(list(payload.job_ids))

    for job_id in payload.job_ids:
        record = prefetched.get(job_id)
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
    prefetched = await _prefetch_job_records(list(payload.job_ids))

    for job_id in payload.job_ids:
        record = prefetched.get(job_id)
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
    prefetched = await _prefetch_job_records(list(payload.job_ids))

    for job_id in payload.job_ids:
        record = prefetched.get(job_id)
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
    prefetched = await _prefetch_job_records(list(payload.job_ids))

    for job_id in payload.job_ids:
        record = prefetched.get(job_id)
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        if record.status == JobStatus.running:
            skipped.append(JobActionSkip(job_id=job_id, reason="Running job cannot be deleted"))
            continue

        await _maybe_await(job_repository.delete(job_id))
        await asyncio.to_thread(_delete_job_artifacts, record)
        updated.append(record)

    result = JobBulkActionResponse(updated=updated, skipped=skipped)
    await _audit(actor, "jobs.delete", ",".join(payload.job_ids), deleted=len(updated))
    return result
