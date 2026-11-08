from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from video_converter.core.models import JobRecord, JobStatus

DEFAULT_STALE_RUNNING_SECONDS = 60 * 60
RUNNING_JOBS_INDEX_KEY = "jobs:status:running"


def append_limited(values: list[Any], item: Any, limit: int) -> list[Any]:
    """Append while keeping only the newest ``limit`` entries."""
    return [*values, item][-limit:]


def is_stale(record: JobRecord, cutoff: datetime) -> bool:
    """True when a running job has not reported progress since ``cutoff``."""
    timestamp = record.progress_updated_at or record.updated_at or record.started_at
    if not timestamp:
        return True
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < cutoff


def parse_job_record(raw: str | None) -> JobRecord | None:
    """Decode one stored job record, tolerating corrupt/legacy payloads."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return JobRecord.model_validate(data)
    except ValueError:
        return None


def running_index_operations(
    job_id: str,
    previous: JobStatus | None,
    current: JobStatus,
    *,
    index_key: str,
) -> list[tuple[str, tuple[Any, ...]]]:
    """SADD/SREM ops that keep a running-jobs index in step with a status change."""
    if previous == current:
        return []
    operations: list[tuple[str, tuple[Any, ...]]] = []
    if previous == JobStatus.running:
        operations.append(("srem", (index_key, job_id)))
    if current == JobStatus.running:
        operations.append(("sadd", (index_key, job_id)))
    return operations


def sync_running_index(
    pipe: Any,
    job_id: str,
    previous: JobStatus | None,
    current: JobStatus,
    *,
    index_key: str,
) -> None:
    """Queue the same index changes on a Redis/SQLite pipeline."""
    if previous == current:
        return
    if previous == JobStatus.running:
        pipe.srem(index_key, job_id)
    if current == JobStatus.running:
        pipe.sadd(index_key, job_id)
