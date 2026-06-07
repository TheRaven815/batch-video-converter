from __future__ import annotations

import json
import logging
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import redis
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from video_converter.core.config import JOB_KEY_PREFIX, JOBS_INDEX_KEY, QUEUE_NAME, ensure_runtime_dirs, get_settings
from video_converter.core.job_repository import DEFAULT_STALE_RUNNING_SECONDS, JobRepository
from video_converter.core.path_validation import validate_source_path
from video_converter.core.storage import create_storage_client, is_redis_storage
from video_converter.core.models import (
    BatchCreateError,
    BatchListResponse,
    BatchSummaryDto,
    ErrorEnvelope,
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
    WorkerHealthResponse,
    now_iso,
)

settings = get_settings()
ensure_runtime_dirs(settings)

storage_client = create_storage_client(settings)
job_repository = JobRepository(storage_client)
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup: recover stale running jobs. Shutdown: no-op."""
    try:
        recovered = job_repository.recover_stale_running_jobs(stale_after_seconds=DEFAULT_STALE_RUNNING_SECONDS)
    except redis.RedisError:
        logger.exception("stale job recovery failed")
    except Exception:
        if is_redis_storage(storage_client):
            logger.exception("stale job recovery failed")
    else:
        if recovered:
            logger.info("recovered %s stale running jobs", len(recovered))
    yield


app = FastAPI(title="Video Converter API", version="0.1.0", lifespan=lifespan)


_ERROR_CODE_BY_STATUS = {
    400: "bad_request",
    404: "not_found",
    422: "validation_error",
    500: "internal_error",
    503: "service_unavailable",
}


def _error_code_from_message(message: str, status_code: int) -> str:
    normalized = message.lower()
    if "source_root_key" in normalized:
        return "invalid_source_root"
    if "kök dizin dışında" in normalized or "geçersiz path" in normalized:
        return "path_traversal_blocked"
    if "desteklenmeyen uzantı" in normalized:
        return "unsupported_extension"
    if "bulunamadı" in normalized or "not found" in normalized:
        return "not_found"
    return _ERROR_CODE_BY_STATUS.get(status_code, "api_error")


@app.exception_handler(HTTPException)
async def structured_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = str(exc.detail) if exc.detail else "Request failed"
    envelope = StructuredErrorResponse(
        error=ErrorEnvelope(
            code=_error_code_from_message(message, exc.status_code),
            message=message,
            recoverable=exc.status_code in {409, 422, 503},
            details={"path": request.url.path},
        )
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())


@app.get("/health/live", response_model=HealthResponse)
def health_live() -> HealthResponse:
    redis_status = "ok"
    try:
        storage_client.ping()
    except redis.RedisError:
        redis_status = "error"
    return HealthResponse(status="ok", redis=redis_status)


@app.get("/health/ready", response_model=HealthResponse)
def health_ready() -> HealthResponse:
    try:
        storage_client.ping()
    except redis.RedisError as exc:
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
        raise HTTPException(status_code=422, detail="source_root_key ve source_path birlikte verilmelidir")

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
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    )


def _enqueue_job(record: JobRecord) -> None:
    job_repository.enqueue(record)


def _enqueue_jobs(records: list[JobRecord]) -> None:
    job_repository.enqueue_many(records)


def _parse_job_record(raw: str) -> JobRecord | None:
    return job_repository.parse_record(raw)


def _get_job_record(job_id: str) -> JobRecord | None:
    return job_repository.get(job_id)


@app.post("/api/v1/jobs", response_model=JobRecord)
def create_job(payload: JobCreateRequest) -> JobRecord:
    record = _build_job_record(payload)
    _enqueue_job(record)
    return record


@app.post("/api/v1/jobs/validate", response_model=JobValidationResponse)
def validate_jobs(payload: JobBatchCreateRequest) -> JobValidationResponse:
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
                    error_code=_error_code_from_message(message, exc.status_code),
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
    return JobValidationResponse(items=items, valid_count=valid_count, invalid_count=len(items) - valid_count)


def _idempotency_cache_key(idempotency_key: str) -> str:
    return f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"


def _load_idempotent_batch_response(idempotency_key: str | None) -> JobBatchCreateResponse | None:
    if not idempotency_key:
        return None

    raw = storage_client.get(_idempotency_cache_key(idempotency_key))
    if not raw:
        return None

    try:
        return JobBatchCreateResponse.model_validate_json(str(raw))
    except Exception:  # noqa: BLE001
        return None


def _store_idempotent_batch_response(idempotency_key: str | None, response: JobBatchCreateResponse) -> None:
    if not idempotency_key:
        return
    storage_client.set(_idempotency_cache_key(idempotency_key), response.model_dump_json(), ex=24 * 60 * 60)


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
                    error_code=_error_code_from_message(message, exc.status_code),
                    message=message,
                    recoverable=exc.status_code == 422,
                )
            )
        else:
            valid_items.append((index, item))

    records: list[JobRecord] = []
    for _index, item in valid_items:
        records.append(_build_job_record(item, batch_id=batch_id))

    return records, errors


@app.post("/api/v1/jobs/batch", response_model=JobBatchCreateResponse)
def create_jobs_batch(
    payload: JobBatchCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> JobBatchCreateResponse:
    normalized_idempotency_key = idempotency_key.strip() if idempotency_key else None
    cached = _load_idempotent_batch_response(normalized_idempotency_key)
    if cached is not None:
        return cached

    batch_id = str(uuid.uuid4())
    records, errors = _validate_and_build_batch(payload.jobs, batch_id)

    if not records and errors:
        raise HTTPException(
            status_code=422,
            detail=f"All {len(errors)} job(s) failed validation",
        )

    if not records:
        raise HTTPException(status_code=422, detail="No jobs provided")

    _enqueue_jobs(records)
    response = JobBatchCreateResponse(
        jobs=records,
        errors=errors,
        idempotency_key=normalized_idempotency_key,
    )
    _store_idempotent_batch_response(normalized_idempotency_key, response)
    return response


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
            raise HTTPException(status_code=422, detail=f"Geçersiz status filtresi: {value}") from exc

    return statuses or None


def _parse_datetime_filter(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Geçersiz {name} filtresi") from exc
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
            for part in [record.id, record.input_filename, record.source_path, record.output_filename, record.error_message]
        ).lower()
        if q.lower() not in haystack:
            return False
    created_at = _record_datetime(record.created_at)
    if created_after is not None and (created_at is None or created_at < created_after):
        return False
    if created_before is not None and (created_at is None or created_at > created_before):
        return False
    return True


@app.get("/api/v1/jobs", response_model=list[JobRecord])
def list_jobs(
    response: Response,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
    profile: Annotated[str | None, Query(max_length=100)] = None,
    source_root_key: Annotated[str | None, Query(max_length=64)] = None,
    source_type: Annotated[str | None, Query(max_length=32)] = None,
    include_archived: bool = False,
    created_after: str | None = None,
    created_before: str | None = None,
) -> list[JobRecord]:
    ids = list(reversed(job_repository.list_ids()))
    jobs: list[JobRecord] = []
    next_cursor: int | None = None
    statuses = _parse_status_filter(status)
    created_after_dt = _parse_datetime_filter(created_after, "created_after")
    created_before_dt = _parse_datetime_filter(created_before, "created_before")

    for idx, job_id in enumerate(ids[cursor:], start=cursor):
        record = _get_job_record(job_id)
        if not record:
            continue
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
        if len(jobs) >= limit:
            next_cursor = idx
            break
        jobs.append(record)

    if response is not None and next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(next_cursor)

    return jobs


def _persist_job_record(record: JobRecord) -> None:
    job_repository.persist(record)


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


@app.get("/api/v1/batches", response_model=BatchListResponse)
def list_batches(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> BatchListResponse:
    grouped: dict[str, list[JobRecord]] = {}
    for job_id in reversed(job_repository.list_ids()):
        record = _get_job_record(job_id)
        if not record or not record.batch_id or record.archived:
            continue
        grouped.setdefault(record.batch_id, []).append(record)

    batches = [_batch_summary_from_records(batch_id, records) for batch_id, records in grouped.items()]
    batches.sort(key=lambda batch: (batch.updated_at or "", batch.batch_id), reverse=True)
    selected = batches[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < len(batches) else None
    if response is not None and next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(next_cursor)
    return BatchListResponse(batches=selected, next_cursor=str(next_cursor) if next_cursor is not None else None)


@app.get("/api/v1/jobs/stream")
def stream_jobs() -> StreamingResponse:
    def _events():
        last_snapshot = ""
        while True:
            records = [record for job_id in reversed(job_repository.list_ids()) if (record := _get_job_record(job_id)) and not record.archived]
            snapshot = json.dumps([record.model_dump(mode="json") for record in records], sort_keys=True)
            timestamp = now_iso()
            if snapshot != last_snapshot:
                yield f"event: jobs_snapshot\ndata: {json.dumps({'event': 'jobs_snapshot', 'timestamp': timestamp, 'data': {'jobs': [record.model_dump(mode='json') for record in records]}})}\n\n"
                last_snapshot = snapshot
            else:
                yield f"event: heartbeat\ndata: {json.dumps({'event': 'heartbeat', 'timestamp': timestamp, 'data': {}})}\n\n"
            time.sleep(5)

    return StreamingResponse(_events(), media_type="text/event-stream")


@app.get("/api/v1/jobs/{job_id}", response_model=JobRecord)
def get_job(job_id: str) -> JobRecord:
    record = _get_job_record(job_id)
    if not record:
        raise HTTPException(status_code=404, detail="Job not found")
    return record


@app.get("/api/v1/worker/health", response_model=WorkerHealthResponse)
def worker_health() -> WorkerHealthResponse:
    redis_status = "ok"
    try:
        storage_client.ping()
        queue_depth = int(storage_client.llen(QUEUE_NAME) or 0)
    except redis.RedisError:
        redis_status = "error"
        queue_depth = 0

    running_jobs = 0
    for job_id in job_repository.list_ids():
        record = _get_job_record(job_id)
        if record and record.status == JobStatus.running:
            running_jobs += 1

    return WorkerHealthResponse(
        status="ok" if redis_status == "ok" else "error",
        redis=redis_status,
        queue_depth=queue_depth,
        running_jobs=running_jobs,
        checked_at=now_iso(),
    )


def _safe_output_path(filename: str) -> Path:
    output_path = (settings.outputs_dir / filename).resolve()
    try:
        output_path.relative_to(settings.outputs_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Geçersiz output filename") from exc
    return output_path


@app.get("/api/v1/outputs", response_model=OutputListResponse)
def list_outputs(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> OutputListResponse:
    outputs_dir = settings.outputs_dir.resolve()
    if not outputs_dir.exists():
        return OutputListResponse(outputs=[])

    entries: list[Path] = []
    for child in outputs_dir.iterdir():
        if not child.is_file() or child.name.startswith("."):
            continue
        if q and q.lower() not in child.name.lower():
            continue
        entries.append(child)

    entries.sort(key=lambda p: (p.stat().st_mtime, p.name.lower()), reverse=True)
    selected = entries[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < len(entries) else None
    outputs = [
        OutputFileDto(
            filename=path.name,
            size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            download_url=f"/api/v1/outputs/{path.name}/download",
        )
        for path in selected
    ]
    if response is not None and next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(next_cursor)
    return OutputListResponse(outputs=outputs, next_cursor=str(next_cursor) if next_cursor is not None else None)


@app.get("/api/v1/outputs/{filename}/download")
def download_output(filename: str) -> FileResponse:
    output_path = _safe_output_path(filename)
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output bulunamadı")
    return FileResponse(output_path, filename=output_path.name)



def _cancel_record(record: JobRecord) -> tuple[JobRecord, str | None]:
    if record.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
        return record, "İş zaten sonlanmış durumda"

    now = now_iso()
    record.cancel_requested = True
    record.updated_at = now
    record.progress_updated_at = now

    if record.status == JobStatus.queued:
        removed = job_repository.remove_from_queue(record.id)
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

    _persist_job_record(record)
    return record, None


@app.post("/api/v1/jobs/bulk/cancel", response_model=JobBulkActionResponse)
def cancel_jobs_bulk(payload: JobIdsRequest) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = _get_job_record(job_id)
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        changed, reason = _cancel_record(record)
        if reason:
            skipped.append(JobActionSkip(job_id=job_id, reason=reason))
        updated.append(changed)

    return JobBulkActionResponse(updated=updated, skipped=skipped)


@app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobRecord)
def cancel_job(job_id: str) -> JobRecord:
    record = _get_job_record(job_id)
    if not record:
        raise HTTPException(status_code=404, detail="Job not found")

    updated, _ = _cancel_record(record)
    return updated


@app.post("/api/v1/jobs/bulk/start", response_model=JobBulkActionResponse)
def start_jobs_bulk(payload: JobIdsRequest) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = _get_job_record(job_id)
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        if record.status == JobStatus.running:
            skipped.append(JobActionSkip(job_id=job_id, reason="Running iş yeniden başlatılamaz"))
            continue

        if record.status == JobStatus.queued:
            skipped.append(JobActionSkip(job_id=job_id, reason="İş zaten queued durumda"))
            continue

        now = now_iso()
        record.status = JobStatus.queued
        record.cancel_requested = False
        record.error_message = None
        record.progress_percent = 0
        record.progress_phase = "queued"
        record.progress_message = "Job queued"
        record.progress_updated_at = now
        record.updated_at = now
        record.started_at = None
        record.finished_at = None

        job_repository.requeue_existing(record)
        updated.append(record)

    return JobBulkActionResponse(updated=updated, skipped=skipped)


@app.post("/api/v1/jobs/bulk/archive", response_model=JobBulkActionResponse)
def archive_jobs_bulk(payload: JobIdsRequest) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = _get_job_record(job_id)
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        if record.status == JobStatus.running:
            skipped.append(JobActionSkip(job_id=job_id, reason="Running iş arşivlenemez"))
            continue

        record.archived = True
        record.updated_at = now_iso()
        _persist_job_record(record)
        job_repository.remove_from_queue(job_id)
        updated.append(record)

    return JobBulkActionResponse(updated=updated, skipped=skipped)


@app.post("/api/v1/jobs/bulk/delete", response_model=JobBulkActionResponse)
def delete_jobs_bulk(payload: JobIdsRequest) -> JobBulkActionResponse:
    updated: list[JobRecord] = []
    skipped: list[JobActionSkip] = []

    for job_id in payload.job_ids:
        record = _get_job_record(job_id)
        if not record:
            skipped.append(JobActionSkip(job_id=job_id, reason="Job not found"))
            continue

        if record.status == JobStatus.running:
            skipped.append(JobActionSkip(job_id=job_id, reason="Running iş silinemez"))
            continue

        job_repository.delete(job_id)
        updated.append(record)

    return JobBulkActionResponse(updated=updated, skipped=skipped)


@app.get("/api/v1/media/roots", response_model=list[MediaRootDto])
def list_media_roots() -> list[MediaRootDto]:
    return [MediaRootDto(key=root.key, label=root.label) for root in settings.media_roots]


@app.get("/api/v1/media/browse", response_model=MediaBrowseResponse)
def browse_media_root(
    root_key: Annotated[str, Query(min_length=1, max_length=64)],
    path: Annotated[str, Query(max_length=2048)] = "",
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    cursor: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> MediaBrowseResponse:
    root_map = _build_media_roots_map()
    root = root_map.get(root_key)
    if root is None:
        raise HTTPException(status_code=404, detail="Kök dizin bulunamadı")

    target = (root.path / path).resolve()
    try:
        target.relative_to(root.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Geçersiz path") from exc

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Dizin bulunamadı")

    all_entries: list[MediaBrowseEntryDto] = []
    query = q.lower() if q else None

    if target != root.path and not query:
        parent = target.parent
        parent_rel = parent.relative_to(root.path).as_posix()
        all_entries.append(MediaBrowseEntryDto(type="dir", name="..", rel_path=parent_rel))

    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if query and query not in child.name.lower():
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


@app.get("/api/v1/media/subtitles", response_model=MediaSubtitleProbeResponse)
def probe_media_subtitles(
    root_key: Annotated[str, Query(min_length=1, max_length=64)],
    path: Annotated[str, Query(min_length=1, max_length=2048)],
) -> MediaSubtitleProbeResponse:
    root_map = _build_media_roots_map()
    root = root_map.get(root_key)
    if root is None:
        raise HTTPException(status_code=404, detail="Kök dizin bulunamadı")

    target = (root.path / path).resolve()
    try:
        target.relative_to(root.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Geçersiz path") from exc

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Dosya bulunamadı")

    if target.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=422, detail="path desteklenmeyen uzantı")

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
        probe_proc = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="ffprobe çalıştırılamadı") from exc

    if probe_proc.returncode != 0:
        stderr_tail = (probe_proc.stderr or "ffprobe failed").strip()[-700:]
        raise HTTPException(status_code=500, detail=f"ffprobe hatası: {stderr_tail}")

    try:
        raw_data: Any = json.loads(probe_proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="ffprobe çıktısı parse edilemedi") from exc

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
if frontend_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="ui")
    logger.info("serving frontend from %s", frontend_dir)
else:
    logger.warning("frontend dist not found at %s", frontend_dir)


@app.get("/")
def root() -> FileResponse:
    index = frontend_dir / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Built UI not found")
    return FileResponse(index)
