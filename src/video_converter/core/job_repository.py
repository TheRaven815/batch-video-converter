from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from redis import Redis

from video_converter.core.config import JOB_KEY_PREFIX, JOBS_INDEX_KEY, QUEUE_NAME
from video_converter.core.models import JobRecord, JobStatus, now_iso

DEFAULT_STALE_RUNNING_SECONDS = 60 * 60
RUNNING_JOBS_INDEX_KEY = "jobs:status:running"


def _append_limited(values: list[Any], item: Any, limit: int) -> list[Any]:
    return [*values, item][-limit:]


class JobRepository:
    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    def parse_record(self, raw: str) -> JobRecord | None:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        try:
            return JobRecord.model_validate(data)
        except Exception:  # noqa: BLE001
            return None

    def get(self, job_id: str) -> JobRecord | None:
        raw = self.redis.get(f"{JOB_KEY_PREFIX}{job_id}")
        if not raw:
            return None
        return self.parse_record(str(raw))

    def persist(self, record: JobRecord) -> None:
        previous = self.get(record.id)
        pipe = self.redis.pipeline(transaction=True)
        pipe.set(f"{JOB_KEY_PREFIX}{record.id}", record.model_dump_json())
        self._sync_running_index(
            pipe, record.id, previous.status if previous else None, record.status
        )
        pipe.execute()

    def enqueue(self, record: JobRecord) -> None:
        self.enqueue_many([record])

    def enqueue_many(self, records: Iterable[JobRecord]) -> None:
        records = list(records)
        if not records:
            return

        pipe = self.redis.pipeline(transaction=True)
        for record in records:
            pipe.set(f"{JOB_KEY_PREFIX}{record.id}", record.model_dump_json())
            pipe.rpush(JOBS_INDEX_KEY, record.id)
            pipe.rpush(QUEUE_NAME, record.id)
            if record.status == JobStatus.running:
                pipe.lrem(RUNNING_JOBS_INDEX_KEY, 0, record.id)
                pipe.rpush(RUNNING_JOBS_INDEX_KEY, record.id)
        pipe.execute()

    def list_ids(self) -> list[str]:
        return list(self.redis.lrange(JOBS_INDEX_KEY, 0, -1))

    def list_records_page(
        self, *, cursor: int = 0, limit: int = 100, newest_first: bool = True
    ) -> tuple[list[JobRecord], int | None]:
        if limit <= 0:
            return [], None

        total = int(self.redis.llen(JOBS_INDEX_KEY) or 0)
        if cursor >= total:
            return [], None

        if newest_first:
            end = total - cursor - 1
            start = max(0, end - limit + 1)
            ids = list(reversed(self.redis.lrange(JOBS_INDEX_KEY, start, end)))
        else:
            start = cursor
            end = min(total - 1, cursor + limit - 1)
            ids = list(self.redis.lrange(JOBS_INDEX_KEY, start, end))

        records = [record for job_id in ids if (record := self.get(job_id)) is not None]
        next_cursor = cursor + len(ids) if cursor + len(ids) < total else None
        return records, next_cursor

    def count_running_jobs(self) -> int:
        return int(self.redis.llen(RUNNING_JOBS_INDEX_KEY) or 0)

    def remove_from_queue(self, job_id: str) -> int:
        return int(self.redis.lrem(QUEUE_NAME, 0, job_id) or 0)

    def requeue_existing(self, record: JobRecord) -> None:
        previous = self.get(record.id)
        pipe = self.redis.pipeline(transaction=True)
        pipe.set(f"{JOB_KEY_PREFIX}{record.id}", record.model_dump_json())
        self._sync_running_index(
            pipe, record.id, previous.status if previous else None, record.status
        )
        pipe.rpush(QUEUE_NAME, record.id)
        pipe.execute()

    def delete(self, job_id: str) -> None:
        pipe = self.redis.pipeline(transaction=True)
        pipe.lrem(QUEUE_NAME, 0, job_id)
        pipe.lrem(JOBS_INDEX_KEY, 0, job_id)
        pipe.lrem(RUNNING_JOBS_INDEX_KEY, 0, job_id)
        pipe.delete(f"{JOB_KEY_PREFIX}{job_id}")
        pipe.execute()

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        progress_percent: int | None,
        progress_phase: str,
        progress_message: str | None,
        output_filename: str | None = None,
        error: str | None = None,
        telemetry: dict[str, Any] | None = None,
        log_line: str | None = None,
    ) -> JobRecord | None:
        """Update a job record's status, progress, telemetry, timeline and log_tail.

        Returns the updated ``JobRecord`` or ``None`` when the job does not
        exist or the stored payload cannot be parsed.
        """
        record = self.get(job_id)
        if record is None:
            return None

        previous_status = record.status
        now = now_iso()

        record.status = status
        record.updated_at = now
        record.error_message = error

        if status == JobStatus.running and previous_status != JobStatus.running:
            record.started_at = now
            record.finished_at = None
            record.attempt_count = (record.attempt_count or 0) + 1
        elif status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
            record.finished_at = now

        if progress_percent is None:
            record.progress_percent = None
        else:
            record.progress_percent = max(0, min(100, int(progress_percent)))

        record.progress_phase = progress_phase
        record.progress_message = progress_message
        record.progress_updated_at = now

        # Apply telemetry fields (progress_fps, progress_speed, etc.)
        if telemetry:
            for key, value in telemetry.items():
                if hasattr(record, key):
                    setattr(record, key, value)

        # Append to timeline when status or phase changes
        timeline: list[dict[str, Any]] = (
            list(record.timeline) if isinstance(record.timeline, list) else []
        )
        if previous_status != status or not timeline or timeline[-1].get("phase") != progress_phase:
            record.timeline = _append_limited(
                timeline,
                {
                    "at": now,
                    "status": status.value,
                    "phase": progress_phase,
                    "message": progress_message,
                },
                40,
            )

        # Append to log_tail
        if log_line:
            log_tail: list[str] = list(record.log_tail) if isinstance(record.log_tail, list) else []
            record.log_tail = _append_limited(log_tail, log_line[-500:], 50)

        if output_filename:
            record.output_filename = output_filename

        pipe = self.redis.pipeline(transaction=True)
        pipe.set(f"{JOB_KEY_PREFIX}{record.id}", record.model_dump_json())
        self._sync_running_index(pipe, record.id, previous_status, status)
        pipe.execute()
        return record

    def _sync_running_index(
        self, pipe: Any, job_id: str, previous_status: JobStatus | None, new_status: JobStatus
    ) -> None:
        if previous_status == new_status:
            return
        if previous_status == JobStatus.running:
            pipe.lrem(RUNNING_JOBS_INDEX_KEY, 0, job_id)
        if new_status == JobStatus.running:
            pipe.lrem(RUNNING_JOBS_INDEX_KEY, 0, job_id)
            pipe.rpush(RUNNING_JOBS_INDEX_KEY, job_id)

    def recover_stale_running_jobs(
        self, *, stale_after_seconds: int = DEFAULT_STALE_RUNNING_SECONDS
    ) -> list[JobRecord]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        recovered: list[JobRecord] = []

        for job_id in self.list_ids():
            record = self.get(job_id)
            if record is None or record.status != JobStatus.running:
                continue
            if not _is_stale(record, cutoff):
                continue

            now = now_iso()
            record.status = JobStatus.queued
            record.cancel_requested = False
            record.progress_percent = 0
            record.progress_phase = "queued"
            record.progress_message = "Recovered stale running job and requeued"
            record.progress_updated_at = now
            record.updated_at = now
            record.started_at = None
            record.finished_at = None
            self.requeue_existing(record)
            recovered.append(record)

        return recovered


def _is_stale(record: JobRecord, cutoff: datetime) -> bool:
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
