from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from redis.exceptions import WatchError

from video_converter.core.config import (
    BATCH_JOBS_KEY_PREFIX,
    BATCHES_INDEX_KEY,
    HIGH_PRIORITY_QUEUE_NAME,
    JOB_EVENTS_CHANNEL,
    JOB_KEY_PREFIX,
    JOBS_INDEX_KEY,
    LOW_PRIORITY_QUEUE_NAME,
    PROCESSING_QUEUE_NAME,
    QUEUE_NAME,
)
from video_converter.core.models import JobRecord, JobStatus, now_iso
from video_converter.core.storage import StorageClient

DEFAULT_STALE_RUNNING_SECONDS = 60 * 60
RUNNING_JOBS_INDEX_KEY = "jobs:status:running"
logger = logging.getLogger(__name__)


def _append_limited(values: list[Any], item: Any, limit: int) -> list[Any]:
    return [*values, item][-limit:]


class JobRepository:
    def __init__(self, redis_client: StorageClient) -> None:
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

    def _compare_and_store(
        self,
        record: JobRecord,
        *,
        expected_raw: str | None,
        previous_status: JobStatus | None,
    ) -> bool:
        key = f"{JOB_KEY_PREFIX}{record.id}"
        serialized = record.model_dump_json()
        compare_and_set = getattr(self.redis, "compare_and_set", None)
        if compare_and_set is not None:
            if not compare_and_set(key, expected_raw, serialized):
                return False
            pipe = self.redis.pipeline(transaction=True)
            self._sync_running_index(pipe, record.id, previous_status, record.status)
            pipe.execute()
            return True

        pipe = self.redis.pipeline(transaction=True)
        if not hasattr(pipe, "watch"):
            current = self.redis.get(key)
            if (str(current) if current is not None else None) != expected_raw:
                return False
            pipe.set(key, serialized)
            self._sync_running_index(pipe, record.id, previous_status, record.status)
            pipe.execute()
            return True
        try:
            pipe.watch(key)
            current = pipe.get(key)
            if (str(current) if current is not None else None) != expected_raw:
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.set(key, serialized)
            self._sync_running_index(pipe, record.id, previous_status, record.status)
            pipe.execute()
            return True
        except WatchError:
            return False
        finally:
            reset = getattr(pipe, "reset", None)
            if reset is not None:
                reset()

    def persist(self, record: JobRecord) -> None:
        key = f"{JOB_KEY_PREFIX}{record.id}"
        for _ in range(8):
            raw = self.redis.get(key)
            expected = str(raw) if raw is not None else None
            previous = self.parse_record(expected) if expected else None
            candidate = record.model_copy(deep=True)
            # Never erase a concurrently requested cancellation with a stale
            # worker progress write.
            if previous is not None and previous.cancel_requested:
                candidate.cancel_requested = True
            if self._compare_and_store(
                candidate,
                expected_raw=expected,
                previous_status=previous.status if previous else None,
            ):
                self._publish_record(candidate)
                return
        raise RuntimeError(f"Concurrent update conflict for job {record.id}")

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
            if record.priority > 0:
                pipe.rpush(HIGH_PRIORITY_QUEUE_NAME, record.id)
            elif record.priority < 0:
                pipe.rpush(LOW_PRIORITY_QUEUE_NAME, record.id)
            else:
                pipe.rpush(QUEUE_NAME, record.id)
            if record.status == JobStatus.running:
                pipe.sadd(RUNNING_JOBS_INDEX_KEY, record.id)
            if record.batch_id:
                pipe.lrem(BATCHES_INDEX_KEY, 0, record.batch_id)
                pipe.rpush(BATCHES_INDEX_KEY, record.batch_id)
                pipe.rpush(f"{BATCH_JOBS_KEY_PREFIX}{record.batch_id}", record.id)
        pipe.execute()
        for record in records:
            self._publish_record(record)

    def list_ids(self) -> list[str]:
        return list(self.redis.lrange(JOBS_INDEX_KEY, 0, -1))

    def list_batch_ids(self) -> list[str]:
        return list(reversed(self.redis.lrange(BATCHES_INDEX_KEY, 0, -1)))

    def list_batch_records(self, batch_id: str) -> list[JobRecord]:
        ids = self.redis.lrange(f"{BATCH_JOBS_KEY_PREFIX}{batch_id}", 0, -1)
        return [record for job_id in ids if (record := self.get(str(job_id))) is not None]

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
        return int(self.redis.scard(RUNNING_JOBS_INDEX_KEY) or 0)

    def remove_from_queue(self, job_id: str) -> int:
        return sum(
            int(self.redis.lrem(queue, 0, job_id) or 0)
            for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME)
        )

    def dequeue(self, timeout: int = 5) -> str | None:
        """Atomically move the next job id from the queue to the processing list.

        The id stays on the processing list until :meth:`acknowledge` (or a
        requeue) removes it, so jobs survive a worker crash between dequeue
        and completion.
        """
        per_queue_timeout = max(1, timeout // 3)
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            job_id = self.redis.blmove(
                queue, PROCESSING_QUEUE_NAME, per_queue_timeout, "LEFT", "RIGHT"
            )
            if job_id:
                return str(job_id)
        return None

    def acknowledge(self, job_id: str) -> None:
        """Drop a job id from the processing list once handling has finished."""
        self.redis.lrem(PROCESSING_QUEUE_NAME, 0, job_id)

    def requeue_existing(self, record: JobRecord) -> None:
        key = f"{JOB_KEY_PREFIX}{record.id}"
        stored_record: JobRecord | None = None
        for _ in range(8):
            raw = self.redis.get(key)
            expected = str(raw) if raw is not None else None
            previous = self.parse_record(expected) if expected else None
            if previous is not None and (
                previous.cancel_requested
                or previous.status in {JobStatus.completed, JobStatus.cancelled}
            ):
                return
            stored_record = record.model_copy(deep=True)
            if self._compare_and_store(
                stored_record,
                expected_raw=expected,
                previous_status=previous.status if previous else None,
            ):
                break
        else:
            raise RuntimeError(f"Concurrent requeue conflict for job {record.id}")

        pipe = self.redis.pipeline(transaction=True)
        # lrem before rpush keeps the queue and processing list duplicate-free
        # even when recovery paths race with each other.
        pipe.lrem(PROCESSING_QUEUE_NAME, 0, record.id)
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            pipe.lrem(queue, 0, record.id)
        if record.priority > 0:
            pipe.rpush(HIGH_PRIORITY_QUEUE_NAME, record.id)
        elif record.priority < 0:
            pipe.rpush(LOW_PRIORITY_QUEUE_NAME, record.id)
        else:
            pipe.rpush(QUEUE_NAME, record.id)
        pipe.execute()

        # A cancellation may land after the record CAS but before the queue
        # transaction. In that ordering cancellation wins and removes the
        # newly-added queue entry.
        current = self.get(record.id)
        if current is not None and (
            current.cancel_requested or current.status in {JobStatus.completed, JobStatus.cancelled}
        ):
            self.remove_from_queue(record.id)
            return
        self._publish_record(stored_record)

    def delete(self, job_id: str) -> None:
        pipe = self.redis.pipeline(transaction=True)
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            pipe.lrem(queue, 0, job_id)
        pipe.lrem(PROCESSING_QUEUE_NAME, 0, job_id)
        pipe.lrem(JOBS_INDEX_KEY, 0, job_id)
        record = self.get(job_id)
        if record and record.batch_id:
            pipe.lrem(f"{BATCH_JOBS_KEY_PREFIX}{record.batch_id}", 0, job_id)
        pipe.srem(RUNNING_JOBS_INDEX_KEY, job_id)
        pipe.delete(f"{JOB_KEY_PREFIX}{job_id}")
        pipe.execute()
        self._publish_deleted(job_id)

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
        key = f"{JOB_KEY_PREFIX}{job_id}"
        raw = self.redis.get(key)
        expected_raw = str(raw) if raw is not None else None
        record = self.parse_record(expected_raw) if expected_raw else None
        if record is None:
            return None

        previous_status = record.status
        now = now_iso()

        record.status = status
        record.updated_at = now
        if error is not None:
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
            telemetry_point = {
                "at": now,
                "fps": telemetry.get("progress_fps"),
                "speed": telemetry.get("progress_speed"),
                "bitrate": telemetry.get("progress_bitrate"),
                "out_time_seconds": telemetry.get("progress_out_time_seconds"),
                "progress_percent": record.progress_percent,
            }
            record.telemetry_history = _append_limited(
                list(record.telemetry_history), telemetry_point, 120
            )

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

        if not self._compare_and_store(
            record,
            expected_raw=expected_raw,
            previous_status=previous_status,
        ):
            return self.update_status(
                job_id,
                status,
                progress_percent=progress_percent,
                progress_phase=progress_phase,
                progress_message=progress_message,
                output_filename=output_filename,
                error=error,
                telemetry=telemetry,
                log_line=log_line,
            )
        self._publish_record(record)
        return record

    def schedule_retry(self, record: JobRecord, *, delay_seconds: int, reason: str) -> None:
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, delay_seconds))
        now = now_iso()
        record.status = JobStatus.queued
        record.cancel_requested = False
        record.next_retry_at = retry_at.isoformat()
        record.retry_reason = reason[-1000:]
        record.error_message = reason[-1000:]
        record.progress_phase = "retry_wait"
        record.progress_message = f"Retrying in {max(1, delay_seconds)} seconds"
        record.progress_updated_at = now
        record.updated_at = now
        record.finished_at = None
        previous = self.get(record.id)
        pipe = self.redis.pipeline(transaction=True)
        pipe.set(f"{JOB_KEY_PREFIX}{record.id}", record.model_dump_json())
        pipe.lrem(PROCESSING_QUEUE_NAME, 0, record.id)
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            pipe.lrem(queue, 0, record.id)
        self._sync_running_index(
            pipe, record.id, previous.status if previous else JobStatus.running, record.status
        )
        pipe.execute()
        self._publish_record(record)

    def requeue_due_retries(self, *, current_time: datetime | None = None) -> list[JobRecord]:
        current_time = current_time or datetime.now(timezone.utc)
        requeued: list[JobRecord] = []
        for job_id in self.list_ids():
            record = self.get(job_id)
            if record is None or record.status != JobStatus.queued or not record.next_retry_at:
                continue
            try:
                due = datetime.fromisoformat(record.next_retry_at)
            except ValueError:
                due = current_time
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due > current_time:
                continue
            record.next_retry_at = None
            record.progress_phase = "queued"
            record.progress_message = "Retry queued"
            record.updated_at = now_iso()
            self.requeue_existing(record)
            requeued.append(record)
        return requeued

    def queue_positions(self) -> dict[str, int]:
        queued = [
            *self.redis.lrange(HIGH_PRIORITY_QUEUE_NAME, 0, -1),
            *self.redis.lrange(QUEUE_NAME, 0, -1),
            *self.redis.lrange(LOW_PRIORITY_QUEUE_NAME, 0, -1),
        ]
        return {str(job_id): position for position, job_id in enumerate(queued, start=1)}

    def delete_terminal_before(self, cutoff: datetime) -> int:
        deleted = 0
        for job_id in self.list_ids():
            record = self.get(job_id)
            if record is None or record.status not in {
                JobStatus.completed,
                JobStatus.failed,
                JobStatus.cancelled,
            }:
                continue
            finished = record.finished_at or record.updated_at
            try:
                timestamp = datetime.fromisoformat(finished)
            except (TypeError, ValueError):
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp < cutoff:
                self.delete(job_id)
                deleted += 1
        return deleted

    def _sync_running_index(
        self, pipe: Any, job_id: str, previous_status: JobStatus | None, new_status: JobStatus
    ) -> None:
        if previous_status == new_status:
            return
        if previous_status == JobStatus.running:
            pipe.srem(RUNNING_JOBS_INDEX_KEY, job_id)
        if new_status == JobStatus.running:
            pipe.sadd(RUNNING_JOBS_INDEX_KEY, job_id)

    def _publish_record(self, record: JobRecord) -> None:
        try:
            self.redis.publish(
                JOB_EVENTS_CHANNEL,
                json.dumps(
                    {
                        "event": "job_updated",
                        "timestamp": now_iso(),
                        "data": {"job": record.model_dump(mode="json")},
                    }
                ),
            )
        except Exception:
            logger.warning("failed to publish job update", exc_info=True)

    def _publish_deleted(self, job_id: str) -> None:
        try:
            self.redis.publish(
                JOB_EVENTS_CHANNEL,
                json.dumps(
                    {
                        "event": "job_deleted",
                        "timestamp": now_iso(),
                        "data": {"job_id": job_id},
                    }
                ),
            )
        except Exception:
            logger.warning("failed to publish job deletion", exc_info=True)

    def recover_processing_orphans(self) -> list[JobRecord]:
        """Requeue jobs stranded on the processing list.

        Safe to call only when no job on the processing list is actively being
        worked on (i.e. at worker startup): every id found there was dequeued
        by a worker that never finished it.
        """
        recovered: list[JobRecord] = []
        for job_id in list(self.redis.lrange(PROCESSING_QUEUE_NAME, 0, -1)):
            record = self.get(job_id)
            if record is None or record.status in {
                JobStatus.completed,
                JobStatus.failed,
                JobStatus.cancelled,
            }:
                self.redis.lrem(PROCESSING_QUEUE_NAME, 0, job_id)
                continue
            self._requeue_recovered(record, "Recovered interrupted job and requeued")
            recovered.append(record)
        return recovered

    def recover_stale_running_jobs(
        self,
        *,
        stale_after_seconds: int = DEFAULT_STALE_RUNNING_SECONDS,
        exclude_ids: set[str] | None = None,
    ) -> list[JobRecord]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        recovered: list[JobRecord] = []

        for job_id in self.list_ids():
            if exclude_ids and job_id in exclude_ids:
                continue
            record = self.get(job_id)
            if record is None or record.status != JobStatus.running:
                continue
            if not _is_stale(record, cutoff):
                continue

            self._requeue_recovered(record, "Recovered stale running job and requeued")
            recovered.append(record)

        return recovered

    def _requeue_recovered(self, record: JobRecord, message: str) -> None:
        now = now_iso()
        record.status = JobStatus.queued
        record.cancel_requested = False
        record.progress_percent = 0
        record.progress_phase = "queued"
        record.progress_message = message
        record.progress_updated_at = now
        record.updated_at = now
        record.started_at = None
        record.finished_at = None
        self.requeue_existing(record)


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
