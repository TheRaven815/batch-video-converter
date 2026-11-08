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
    RETRY_JOBS_INDEX_KEY,
)
from video_converter.core.job_repository_shared import (
    DEFAULT_STALE_RUNNING_SECONDS,
    RUNNING_JOBS_INDEX_KEY,
    append_limited as _append_limited,
    is_stale as _is_stale,
    parse_job_record,
    running_index_operations,
    sync_running_index,
)
from video_converter.core.models import JobRecord, JobStatus, OutputLocation, now_iso

logger = logging.getLogger(__name__)


class AsyncJobRepository:
    """API-side repository using awaitable Redis/SQLite operations."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    @staticmethod
    def parse_record(raw: str) -> JobRecord | None:
        return parse_job_record(raw)

    async def get(self, job_id: str) -> JobRecord | None:
        raw = await self.storage.get(f"{JOB_KEY_PREFIX}{job_id}")
        return self.parse_record(str(raw)) if raw else None

    async def _compare_and_store(
        self,
        record: JobRecord,
        *,
        expected_raw: str | None,
        previous_status: JobStatus | None,
    ) -> bool:
        key = f"{JOB_KEY_PREFIX}{record.id}"
        serialized = record.model_dump_json()
        combined = getattr(self.storage, "compare_and_set_with_ops", None)
        if combined is not None:
            return bool(
                await combined(
                    key,
                    expected_raw,
                    serialized,
                    self._running_index_operations(
                        record.id, previous_status, record.status
                    ),
                )
            )
        compare_and_set = getattr(self.storage, "compare_and_set", None)
        if compare_and_set is not None:
            if not await compare_and_set(key, expected_raw, serialized):
                return False
            pipe = self.storage.pipeline(transaction=True)
            self._sync_running_index(pipe, record.id, previous_status, record.status)
            await pipe.execute()
            return True

        pipe = self.storage.pipeline(transaction=True)
        if not hasattr(pipe, "watch"):
            # SQLite/simple pipelines have no WATCH: read then write, still
            # guarded by the caller's bounded CAS retry loop.
            current = await self.storage.get(key)
            if (str(current) if current is not None else None) != expected_raw:
                return False
            pipe.set(key, serialized)
            self._sync_running_index(pipe, record.id, previous_status, record.status)
            await pipe.execute()
            return True
        try:
            await pipe.watch(key)
            current = await pipe.get(key)
            if (str(current) if current is not None else None) != expected_raw:
                await pipe.unwatch()
                return False
            pipe.multi()
            pipe.set(key, serialized)
            self._sync_running_index(pipe, record.id, previous_status, record.status)
            await pipe.execute()
            return True
        except WatchError:
            return False
        finally:
            reset = getattr(pipe, "reset", None)
            if reset is not None:
                result = reset()
                if hasattr(result, "__await__"):
                    await result

    async def persist(self, record: JobRecord) -> None:
        key = f"{JOB_KEY_PREFIX}{record.id}"
        for _ in range(8):
            raw = await self.storage.get(key)
            expected = str(raw) if raw is not None else None
            previous = self.parse_record(expected) if expected else None
            candidate = record.model_copy(deep=True)
            if previous is not None and previous.cancel_requested:
                candidate.cancel_requested = True
            if await self._compare_and_store(
                candidate,
                expected_raw=expected,
                previous_status=previous.status if previous else None,
            ):
                await self._publish_record(candidate)
                return
        raise RuntimeError(f"Concurrent update conflict for job {record.id}")

    async def enqueue(self, record: JobRecord) -> None:
        await self.enqueue_many([record])

    async def enqueue_many(self, records: Iterable[JobRecord]) -> None:
        materialized = list(records)
        if not materialized:
            return
        pipe = self.storage.pipeline(transaction=True)
        for record in materialized:
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
        await pipe.execute()
        for record in materialized:
            await self._publish_record(record)

    async def get_many(self, job_ids: Iterable[str]) -> dict[str, JobRecord]:
        ids = [str(job_id) for job_id in job_ids]
        if not ids:
            return {}
        records: dict[str, JobRecord] = {}
        mget = getattr(self.storage, "mget", None)
        if mget is None:
            for job_id in ids:
                record = await self.get(job_id)
                if record is not None:
                    records[job_id] = record
            return records
        values = await mget([f"{JOB_KEY_PREFIX}{job_id}" for job_id in ids])
        for job_id, raw in zip(ids, values or []):
            if raw is None:
                continue
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            record = self.parse_record(text)
            if record is not None:
                records[job_id] = record
        return records

    async def list_ids(self) -> list[str]:
        return list(await self.storage.lrange(JOBS_INDEX_KEY, 0, -1))

    async def _index_members(self, key: str) -> list[str]:
        smembers = getattr(self.storage, "smembers", None)
        if smembers is None:
            return await self.list_ids()
        try:
            return [str(value) for value in await smembers(key)]
        except Exception:  # noqa: BLE001 - backend shim without set support
            return await self.list_ids()

    async def list_batch_ids(self) -> list[str]:
        return list(reversed(await self.storage.lrange(BATCHES_INDEX_KEY, 0, -1)))

    async def list_batch_records(self, batch_id: str) -> list[JobRecord]:
        ids = await self.storage.lrange(f"{BATCH_JOBS_KEY_PREFIX}{batch_id}", 0, -1)
        records_by_id = await self.get_many(str(job_id) for job_id in ids)
        return [records_by_id[str(job_id)] for job_id in ids if str(job_id) in records_by_id]

    async def list_records_page(
        self, *, cursor: int = 0, limit: int = 100, newest_first: bool = True
    ) -> tuple[list[JobRecord], int | None]:
        if limit <= 0:
            return [], None
        total = int(await self.storage.llen(JOBS_INDEX_KEY) or 0)
        if cursor >= total:
            return [], None
        if newest_first:
            end = total - cursor - 1
            start = max(0, end - limit + 1)
            ids = list(reversed(await self.storage.lrange(JOBS_INDEX_KEY, start, end)))
        else:
            start = cursor
            end = min(total - 1, cursor + limit - 1)
            ids = list(await self.storage.lrange(JOBS_INDEX_KEY, start, end))
        records_by_id = await self.get_many(ids)
        records = [records_by_id[str(job_id)] for job_id in ids if str(job_id) in records_by_id]
        next_cursor = cursor + len(ids) if cursor + len(ids) < total else None
        return records, next_cursor

    async def count_running_jobs(self) -> int:
        return int(await self.storage.scard(RUNNING_JOBS_INDEX_KEY) or 0)

    async def list_running_records(self) -> list[JobRecord]:
        ids = await self._index_members(RUNNING_JOBS_INDEX_KEY)
        records_by_id = await self.get_many(ids)
        return [
            records_by_id[job_id]
            for job_id in ids
            if job_id in records_by_id and records_by_id[job_id].status == JobStatus.running
        ]

    async def remove_from_queue(self, job_id: str) -> int:
        removed = 0
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            removed += int(await self.storage.lrem(queue, 0, job_id) or 0)
        return removed

    async def request_cancel(self, job_id: str) -> tuple[JobRecord | None, str | None]:
        key = f"{JOB_KEY_PREFIX}{job_id}"
        for _ in range(8):
            raw = await self.storage.get(key)
            expected = str(raw) if raw is not None else None
            record = self.parse_record(expected) if expected else None
            if record is None:
                return None, "Job not found"
            if record.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
                return record, "Job is already completed"

            previous_status = record.status
            now = now_iso()
            record.cancel_requested = True
            record.updated_at = now
            record.progress_updated_at = now
            if record.status == JobStatus.queued:
                record.progress_phase = "cancelling"
                record.progress_message = "Cancellation requested"
            else:
                record.progress_phase = "cancelling"
                record.progress_message = "Cancellation requested"

            if await self._compare_and_store(
                record,
                expected_raw=expected,
                previous_status=previous_status,
            ):
                await self._publish_record(record)
                if previous_status == JobStatus.queued:
                    removed = await self.remove_from_queue(record.id)
                    should_cancel = removed > 0
                    if not should_cancel:
                        # A retry-wait job is not on any queue list, and neither
                        # is a job that raced with the worker. Neither will ever
                        # observe cancel_requested, so cancel it immediately.
                        processing = await self.storage.lrange(
                            PROCESSING_QUEUE_NAME, 0, -1
                        )
                        should_cancel = record.id not in {str(value) for value in processing}
                    if should_cancel:
                        cancelled = record.model_copy(deep=True)
                        cancelled.status = JobStatus.cancelled
                        cancelled.progress_phase = "cancelled"
                        cancelled.progress_message = "Job cancelled before start"
                        cancelled.progress_percent = 0
                        cancelled.finished_at = now
                        cancelled.next_retry_at = None
                        if await self._compare_and_store(
                            cancelled,
                            expected_raw=record.model_dump_json(),
                            previous_status=JobStatus.queued,
                        ):
                            record = cancelled
                            await self.storage.srem(RETRY_JOBS_INDEX_KEY, record.id)
                            await self._publish_record(record)
                            return record, None
                        # A concurrent write changed the record between the two
                        # CAS steps; retry the cancellation against fresh state.
                        continue
                return record, None
        raise RuntimeError(f"Concurrent cancellation conflict for job {job_id}")

    async def requeue_existing(self, record: JobRecord) -> None:
        key = f"{JOB_KEY_PREFIX}{record.id}"
        stored_record: JobRecord | None = None
        for _ in range(8):
            raw = await self.storage.get(key)
            expected = str(raw) if raw is not None else None
            previous = self.parse_record(expected) if expected else None
            if previous is not None and (
                previous.cancel_requested
                or previous.status in {JobStatus.completed, JobStatus.cancelled}
            ):
                return
            stored_record = record.model_copy(deep=True)
            if await self._compare_and_store(
                stored_record,
                expected_raw=expected,
                previous_status=previous.status if previous else None,
            ):
                break
        else:
            raise RuntimeError(f"Concurrent requeue conflict for job {record.id}")

        pipe = self.storage.pipeline(transaction=True)
        pipe.lrem(PROCESSING_QUEUE_NAME, 0, record.id)
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            pipe.lrem(queue, 0, record.id)
        pipe.srem(RETRY_JOBS_INDEX_KEY, record.id)
        if record.priority > 0:
            pipe.rpush(HIGH_PRIORITY_QUEUE_NAME, record.id)
        elif record.priority < 0:
            pipe.rpush(LOW_PRIORITY_QUEUE_NAME, record.id)
        else:
            pipe.rpush(QUEUE_NAME, record.id)
        await pipe.execute()
        current = await self.get(record.id)
        if current is not None and (
            current.cancel_requested or current.status in {JobStatus.completed, JobStatus.cancelled}
        ):
            await self.remove_from_queue(record.id)
            return
        await self._publish_record(stored_record)

    async def delete(self, job_id: str) -> None:
        record = await self.get(job_id)
        pipe = self.storage.pipeline(transaction=True)
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            pipe.lrem(queue, 0, job_id)
        pipe.lrem(PROCESSING_QUEUE_NAME, 0, job_id)
        pipe.lrem(JOBS_INDEX_KEY, 0, job_id)
        pipe.srem(RETRY_JOBS_INDEX_KEY, job_id)
        if record and record.batch_id:
            batch_key = f"{BATCH_JOBS_KEY_PREFIX}{record.batch_id}"
            batch_ids = await self.storage.lrange(batch_key, 0, -1)
            remaining = [str(batch_job) for batch_job in batch_ids if str(batch_job) != job_id]
            pipe.lrem(batch_key, 0, job_id)
            if not remaining:
                pipe.lrem(BATCHES_INDEX_KEY, 0, record.batch_id)
        pipe.srem(RUNNING_JOBS_INDEX_KEY, job_id)
        pipe.delete(f"{JOB_KEY_PREFIX}{job_id}")
        await pipe.execute()
        await self._publish_deleted(job_id)

    async def queue_positions(self) -> dict[str, int]:
        queued: list[str] = []
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            queued.extend(await self.storage.lrange(queue, 0, -1))
        return {str(job_id): position for position, job_id in enumerate(queued, start=1)}

    def _sync_running_index(
        self, pipe: Any, job_id: str, previous: JobStatus | None, current: JobStatus
    ) -> None:
        sync_running_index(
            pipe, job_id, previous, current, index_key=RUNNING_JOBS_INDEX_KEY
        )

    @staticmethod
    def _running_index_operations(
        job_id: str, previous: JobStatus | None, current: JobStatus
    ) -> list[tuple[str, tuple[Any, ...]]]:
        return running_index_operations(
            job_id, previous, current, index_key=RUNNING_JOBS_INDEX_KEY
        )

    async def _publish_record(self, record: JobRecord) -> None:
        try:
            await self.storage.publish(
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
            logger.warning("failed to publish async job update", exc_info=True)

    async def _publish_deleted(self, job_id: str) -> None:
        try:
            await self.storage.publish(
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
            logger.warning("failed to publish async job deletion", exc_info=True)

    async def recover_stale_running_jobs(
        self,
        *,
        stale_after_seconds: int = DEFAULT_STALE_RUNNING_SECONDS,
        exclude_ids: set[str] | None = None,
    ) -> list[JobRecord]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        recovered: list[JobRecord] = []
        for job_id in await self._index_members(RUNNING_JOBS_INDEX_KEY):
            if exclude_ids and job_id in exclude_ids:
                continue
            record = await self.get(job_id)
            if (
                record is None
                or record.status != JobStatus.running
                or not _is_stale(record, cutoff)
            ):
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
            await self.requeue_existing(record)
            recovered.append(record)
        return recovered


    # -- worker-facing operations (sync facade delegates here) ---------------

    async def dequeue(self, timeout: int = 5) -> str | None:
        """Atomically move the next job id to the processing list."""
        per_queue_timeout = max(1, timeout // 3)
        for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
            job_id = await self.storage.blmove(
                queue, PROCESSING_QUEUE_NAME, per_queue_timeout, "LEFT", "RIGHT"
            )
            if job_id:
                return str(job_id)
        return None

    async def acknowledge(self, job_id: str) -> None:
        await self.storage.lrem(PROCESSING_QUEUE_NAME, 0, job_id)

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        progress_percent: int | None,
        progress_phase: str,
        progress_message: str | None,
        output_filename: str | None = None,
        output_location: OutputLocation | None = None,
        error: str | None = None,
        telemetry: dict[str, Any] | None = None,
        log_line: str | None = None,
    ) -> JobRecord | None:
        key = f"{JOB_KEY_PREFIX}{job_id}"
        for _ in range(8):
            raw = await self.storage.get(key)
            expected_raw = str(raw) if raw is not None else None
            record = self.parse_record(expected_raw) if expected_raw else None
            if record is None:
                return None

            previous_status = record.status
            if previous_status == JobStatus.cancelled and status != JobStatus.cancelled:
                return record

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

            if telemetry:
                for key_name, value in telemetry.items():
                    if hasattr(record, key_name):
                        setattr(record, key_name, value)
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

            timeline: list[dict[str, Any]] = (
                list(record.timeline) if isinstance(record.timeline, list) else []
            )
            if (
                previous_status != status
                or not timeline
                or timeline[-1].get("phase") != progress_phase
            ):
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

            if log_line:
                log_tail: list[str] = (
                    list(record.log_tail) if isinstance(record.log_tail, list) else []
                )
                record.log_tail = _append_limited(log_tail, log_line[-500:], 50)

            if output_filename:
                record.output_filename = output_filename

            if output_location is not None:
                record.output_location = output_location

            if await self._compare_and_store(
                record,
                expected_raw=expected_raw,
                previous_status=previous_status,
            ):
                await self._publish_record(record)
                return record
        logger.warning("gave up updating job %s after repeated CAS conflicts", job_id)
        return None

    async def schedule_retry(
        self, record: JobRecord, *, delay_seconds: int, reason: str
    ) -> bool:
        key = f"{JOB_KEY_PREFIX}{record.id}"
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, delay_seconds))
        for _ in range(8):
            raw = await self.storage.get(key)
            expected_raw = str(raw) if raw is not None else None
            previous = self.parse_record(expected_raw) if expected_raw else None
            if previous is None:
                return False
            if previous.cancel_requested or previous.status == JobStatus.cancelled:
                return False

            now = now_iso()
            candidate = previous.model_copy(deep=True)
            candidate.status = JobStatus.queued
            candidate.cancel_requested = False
            candidate.next_retry_at = retry_at.isoformat()
            candidate.retry_reason = reason[-1000:]
            candidate.error_message = reason[-1000:]
            candidate.progress_phase = "retry_wait"
            candidate.progress_message = f"Retrying in {max(1, delay_seconds)} seconds"
            candidate.progress_updated_at = now
            candidate.updated_at = now
            candidate.finished_at = None

            if not await self._compare_and_store(
                candidate,
                expected_raw=expected_raw,
                previous_status=previous.status,
            ):
                continue

            pipe = self.storage.pipeline(transaction=True)
            pipe.lrem(PROCESSING_QUEUE_NAME, 0, candidate.id)
            for queue in (HIGH_PRIORITY_QUEUE_NAME, QUEUE_NAME, LOW_PRIORITY_QUEUE_NAME):
                pipe.lrem(queue, 0, candidate.id)
            pipe.sadd(RETRY_JOBS_INDEX_KEY, candidate.id)
            self._sync_running_index(pipe, candidate.id, previous.status, candidate.status)
            await pipe.execute()
            for field_name in record.__class__.model_fields:
                setattr(record, field_name, getattr(candidate, field_name))
            await self._publish_record(candidate)
            return True
        logger.warning("could not schedule retry for job %s after CAS conflicts", record.id)
        return False

    async def requeue_due_retries(
        self, *, current_time: datetime | None = None
    ) -> list[JobRecord]:
        current_time = current_time or datetime.now(timezone.utc)
        requeued: list[JobRecord] = []
        for job_id in await self._index_members(RETRY_JOBS_INDEX_KEY):
            record = await self.get(job_id)
            if record is None or record.status != JobStatus.queued or not record.next_retry_at:
                await self.storage.srem(RETRY_JOBS_INDEX_KEY, job_id)
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
            await self.requeue_existing(record)
            await self.storage.srem(RETRY_JOBS_INDEX_KEY, job_id)
            requeued.append(record)
        return requeued

    async def delete_terminal_before(
        self, cutoff: datetime, on_delete: Any | None = None
    ) -> int:
        deleted = 0
        for job_id in await self.list_ids():
            record = await self.get(job_id)
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
                if on_delete is not None:
                    try:
                        on_delete(record)
                    except Exception:  # noqa: BLE001 - artifact cleanup is best-effort
                        logger.warning(
                            "job artifact cleanup failed for %s", job_id, exc_info=True
                        )
                await self.delete(job_id)
                deleted += 1
        return deleted

    async def recover_processing_orphans(self) -> list[JobRecord]:
        recovered: list[JobRecord] = []
        for job_id in list(await self.storage.lrange(PROCESSING_QUEUE_NAME, 0, -1)):
            record = await self.get(str(job_id))
            if record is None or record.status in {
                JobStatus.completed,
                JobStatus.failed,
                JobStatus.cancelled,
            }:
                await self.storage.lrem(PROCESSING_QUEUE_NAME, 0, job_id)
                continue
            now = now_iso()
            record.status = JobStatus.queued
            record.cancel_requested = False
            record.progress_percent = 0
            record.progress_phase = "queued"
            record.progress_message = "Recovered interrupted job and requeued"
            record.progress_updated_at = now
            record.updated_at = now
            record.started_at = None
            record.finished_at = None
            await self.requeue_existing(record)
            recovered.append(record)
        return recovered
