from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis  # noqa: F401 - re-exported for integrations/tests via api.main
from fastapi import HTTPException
from fastapi.responses import JSONResponse

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

settings: Settings | None = None
storage_client: Any = None
job_repository: Any = None
logger = logging.getLogger("api")


def _write_audit_event(event: AuditEventDto) -> None:
    if settings is None or not hasattr(settings, "logs_dir"):
        return
    write_audit_event(settings.logs_dir, event.model_dump(mode="json"))


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
    return await maybe_await(value)






VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".webm",
        ".m4v",
        ".mpg",
        ".mpeg",
    }
)
SUBTITLE_CACHE_TTL_SECONDS = 300
SUBTITLE_CACHE_MAX_ENTRIES = 512
IDEMPOTENCY_KEY_PREFIX = "idempotency:batch:"
# (expiry, source mtime_ns, source size, response)
_subtitle_probe_cache: dict[
    tuple[str, str], tuple[float, int, int, MediaSubtitleProbeResponse]
] = {}


def _store_subtitle_probe(
    cache_key: tuple[str, str],
    mtime_ns: int,
    size: int,
    result: MediaSubtitleProbeResponse,
) -> None:
    now = time.monotonic()
    if len(_subtitle_probe_cache) >= SUBTITLE_CACHE_MAX_ENTRIES:
        for key in [key for key, value in _subtitle_probe_cache.items() if value[0] <= now]:
            _subtitle_probe_cache.pop(key, None)
        while len(_subtitle_probe_cache) >= SUBTITLE_CACHE_MAX_ENTRIES:
            _subtitle_probe_cache.pop(next(iter(_subtitle_probe_cache)))
    _subtitle_probe_cache[cache_key] = (
        now + SUBTITLE_CACHE_TTL_SECONDS,
        mtime_ns,
        size,
        result,
    )


def _build_media_roots_map() -> dict[str, Any]:
    return build_media_roots_map(settings)


def _validate_source_payload(payload: JobCreateRequest) -> None:
    has_root = bool(payload.source_root_key)
    has_path = bool(payload.source_path)
    if has_root != has_path:
        raise HTTPException(
            status_code=422, detail="source_root_key and source_path must be provided together"
        )

    if has_root:
        try:
            validate_source_path(
                _build_media_roots_map(),
                payload.source_root_key or "",
                payload.source_path or "",
                supported_extensions=VIDEO_EXTENSIONS,
            )
        except ValueError as exc:
            raise path_validation_error(exc) from exc
        return

    filename = (payload.input_filename or "").strip()
    if not filename:
        raise HTTPException(
            status_code=422,
            detail="input_filename or source_root_key/source_path is required",
        )

    input_root = settings.input_dir.resolve()
    candidate = (input_root / filename).resolve()
    try:
        candidate.relative_to(input_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="input_filename is invalid") from exc

    if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=422, detail="input_filename has unsupported extension")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=422, detail="input_filename is not a valid file")


_SYSTEM_DEFAULT_EXPORT_FIELDS = (
    "profile",
    "video_export",
    "audio_export",
    "subtitle_export",
    "subtitle_language",
    "quality_crf",
    "target_video_bitrate",
    "audio_bitrate_kbps",
    "resolution",
    "encoder_preset",
    "hardware_acceleration",
)


def _apply_system_defaults(
    payload: JobCreateRequest, system_settings: SystemSettings
) -> JobCreateRequest:
    """Fill request fields the client did not send from server-side defaults."""
    explicit = payload.model_fields_set
    updates: dict[str, Any] = {}
    default_export = system_settings.default_export

    for field in _SYSTEM_DEFAULT_EXPORT_FIELDS:
        if field in explicit:
            continue
        value = getattr(default_export, field, None)
        if value is not None:
            updates[field] = value

    if "max_attempts" not in explicit and system_settings.retry is not None:
        updates["max_attempts"] = system_settings.retry.max_attempts

    if not updates:
        return payload

    merged = {**payload.model_dump(), **updates}
    try:
        return JobCreateRequest.model_validate(merged)
    except ValueError:
        # A stored audio default may be incompatible with an explicitly
        # requested container (e.g. WebM + AAC). Keep the explicit container.
        updates.pop("audio_export", None)
        return JobCreateRequest.model_validate({**payload.model_dump(), **updates})


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


async def _prefetch_job_records(job_ids: list[str]) -> dict[str, JobRecord]:
    """Batch-load records for list/bulk endpoints (avoids N+1 gets)."""
    if not job_ids:
        return {}
    get_many = getattr(job_repository, "get_many", None)
    if get_many is None:
        records: dict[str, JobRecord] = {}
        for job_id in job_ids:
            record = await _maybe_await(_get_job_record(job_id))
            if record is not None:
                records[job_id] = record
        return records
    result = await _maybe_await(get_many(job_ids))
    return result if isinstance(result, dict) else {}






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
    system_settings: SystemSettings | None = None,
) -> tuple[list[JobRecord], list[BatchCreateError]]:
    """Validate all items first, then build records for valid ones.

    Returns a tuple of (valid_records, errors). Every item is validated
    before any record is built so that a single failure does not leave
    the batch in a partially-enqueued state.
    """
    errors: list[BatchCreateError] = []
    valid_items: list[tuple[int, JobCreateRequest]] = []
    defaults = system_settings or _default_system_settings()

    for index, item in enumerate(items):
        merged = _apply_system_defaults(item, defaults)
        try:
            _validate_source_payload(merged)
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
            valid_items.append((index, merged))

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










def _safe_output_path(filename: str) -> Path:
    output_path = (settings.outputs_dir / filename).resolve()
    try:
        output_path.relative_to(settings.outputs_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid output filename") from exc
    return output_path


def _scan_output_entries(
    outputs_dir: Path, query: str | None
) -> list[tuple[Path, os.stat_result]]:
    entries: list[tuple[Path, os.stat_result]] = []
    for child in outputs_dir.iterdir():
        try:
            if not child.is_file() or child.name.startswith("."):
                continue
            if query and query.lower() not in child.name.lower():
                continue
            entries.append((child, child.stat()))
        except OSError:
            # Output may be concurrently cleaned up between directory scan and stat.
            continue
    entries.sort(key=lambda item: (item[1].st_mtime, item[0].name.lower()), reverse=True)
    return entries






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




def _thumbnail_path(filename: str) -> Path:
    directory = settings.temp_dir / "thumbnails"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{Path(filename).name}.jpg"












def _delete_output_files(outputs_dir: Path, thumbnails_dir: Path) -> int:
    count = 0
    for child in outputs_dir.iterdir():
        # Includes video outputs and .srt sidecars (e.g. ``<stem>.srt``).
        if child.is_file() and not child.name.startswith("."):
            try:
                child.unlink()
                count += 1
            except Exception as exc:
                logger.error("Failed to delete %s: %s", child, exc)

    if thumbnails_dir.exists() and thumbnails_dir.is_dir():
        for child in thumbnails_dir.iterdir():
            if child.is_file() and not child.name.startswith("."):
                try:
                    child.unlink()
                    count += 1
                except Exception as exc:
                    logger.error("Failed to delete thumbnail %s: %s", child, exc)
    return count




def _default_system_settings() -> SystemSettings:
    configured = globals().get("settings")
    worker_concurrency = int(getattr(configured, "worker_concurrency", 1) or 1)
    limit = max(1, min(8, worker_concurrency))
    return SystemSettings(worker_concurrency=limit, worker_concurrency_limit=limit)


def _system_settings_response(value: SystemSettings) -> JSONResponse:
    """Keep legacy response shape while retaining a precise OpenAPI schema."""
    data = value.model_dump(mode="json")
    data["worker_concurrency_limit"] = max(1, min(8, settings.worker_concurrency))
    data["worker_concurrency"] = min(data["worker_concurrency"], data["worker_concurrency_limit"])
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
    storage = globals().get("storage_client")
    defaults = _default_system_settings()
    if storage is None:
        return defaults
    raw = await _maybe_await(storage.get("system:settings"))
    if raw:
        try:
            stored = SystemSettings.model_validate_json(raw)
            stored.worker_concurrency = min(stored.worker_concurrency, defaults.worker_concurrency_limit)
            stored.worker_concurrency_limit = defaults.worker_concurrency_limit
            return SystemSettings.model_validate(
                defaults.model_dump() | stored.model_dump(exclude_unset=True)
            )
        except Exception:
            logger.warning("stored system settings are invalid; using defaults")
    return defaults






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
        if removed > 0 or record.next_retry_at or record.progress_phase == "retry_wait":
            record.status = JobStatus.cancelled
            record.progress_phase = "cancelled"
            record.progress_message = "Job cancelled before start"
            record.progress_percent = 0
            record.next_retry_at = None
            record.finished_at = now
        else:
            record.progress_phase = "cancelling"
            record.progress_message = "Cancellation requested"
    else:
        record.progress_phase = "cancelling"
        record.progress_message = "Cancellation requested"

    await _maybe_await(_persist_job_record(record))
    return record, None










def _delete_job_artifacts(record: JobRecord) -> None:
    """Remove per-job logs and uploaded inputs when a job is deleted."""
    configured = globals().get("settings")
    if configured is None or not hasattr(configured, "logs_dir"):
        return
    log_path = (configured.logs_dir / f"{record.id}.ffmpeg.log").resolve()
    try:
        log_path.relative_to(configured.logs_dir.resolve())
        log_path.unlink(missing_ok=True)
    except (OSError, ValueError, AttributeError):
        logger.warning("could not remove job log for %s", record.id, exc_info=True)

    if record.source_path or not record.input_filename or not hasattr(configured, "input_dir"):
        return
    input_root = configured.input_dir.resolve()
    input_path = (input_root / record.input_filename).resolve()
    try:
        input_path.relative_to(input_root)
    except ValueError:
        return
    try:
        if input_path.is_file():
            input_path.unlink()
    except OSError:
        logger.warning("could not remove uploaded input for %s", record.id, exc_info=True)




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






def _collect_browse_entries(
    root: Any, target: Path, query: str | None
) -> tuple[list[MediaBrowseEntryDto], str]:
    all_entries: list[MediaBrowseEntryDto] = []
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
    return all_entries, normalized_current






_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
frontend_dir = _PROJECT_ROOT / "frontend" / "dist"
