from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from redis import asyncio as redis_async
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
    Settings,
)
from video_converter.core.job_repository import (
    DEFAULT_STALE_RUNNING_SECONDS,
    RUNNING_JOBS_INDEX_KEY,
    _is_stale,
)
from video_converter.core.models import JobRecord, JobStatus, now_iso
from video_converter.core.storage import LocalFileStore

logger = logging.getLogger(__name__)


class AsyncLocalPipeline:
    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    def __getattr__(self, name: str):
        target = getattr(self._pipeline, name)

        def call(*args: Any, **kwargs: Any) -> AsyncLocalPipeline:
            target(*args, **kwargs)
            return self

        return call

    async def execute(self) -> list[Any]:
        return self._pipeline.execute()


class AsyncLocalStore:
    """Awaitable facade for the SQLite-only development backend."""

    backend_name = "local"

    def __init__(self, store: LocalFileStore) -> None:
        self._store = store

    def pipeline(self, transaction: bool = True) -> AsyncLocalPipeline:  # noqa: ARG002
        return AsyncLocalPipeline(self._store.pipeline(transaction=transaction))

    def __getattr__(self, name: str):
        target = getattr(self._store, name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            return target(*args, **kwargs)

        return call

    async def aclose(self) -> None:
        self._store.close()


AsyncStorageClient = redis_async.Redis | AsyncLocalStore


def create_async_storage_client(settings: Settings) -> AsyncStorageClient:
    if settings.video_converter_storage == "local":
        return AsyncLocalStore(LocalFileStore(settings.data_dir / "local_queue.sqlite3"))
    return redis_async.Redis.from_url(settings.redis_url, decode_responses=True)


class AsyncJobRepository:
    """API-side repository using awaitable Redis/SQLite operations."""

    def __init__(self, storage: AsyncStorageClient) -> None:
        self.storage = storage

    @staticmethod
    def parse_record(raw: str) -> JobRecord | None:
        try:
            data = json.loads(raw)
            return JobRecord.model_validate(data) if isinstance(data, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

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
        compare_and_set = getattr(self.storage, "compare_and_set", None)
        if compare_and_set is not None:
            if not await compare_and_set(key, expected_raw, serialized):
                return False
            pipe = self.storage.pipeline(transaction=True)
            self._sync_running_index(pipe, record.id, previous_status, record.status)
            await pipe.execute()
            return True

        pipe = self.storage.pipeline(transaction=True)
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

    async def list_ids(self) -> list[str]:
        return list(await self.storage.lrange(JOBS_INDEX_KEY, 0, -1))

    async def list_batch_ids(self) -> list[str]:
        return list(reversed(await self.storage.lrange(BATCHES_INDEX_KEY, 0, -1)))

    async def list_batch_records(self, batch_id: str) -> list[JobRecord]:
        ids = await self.storage.lrange(f"{BATCH_JOBS_KEY_PREFIX}{batch_id}", 0, -1)
        records: list[JobRecord] = []
        for job_id in ids:
            if record := await self.get(str(job_id)):
                records.append(record)
        return records

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
        records: list[JobRecord] = []
        for job_id in ids:
            record = await self.get(str(job_id))
            if record is not None:
                records.append(record)
        next_cursor = cursor + len(ids) if cursor + len(ids) < total else None
        return records, next_cursor

    async def count_running_jobs(self) -> int:
        return int(await self.storage.scard(RUNNING_JOBS_INDEX_KEY) or 0)

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
                    if removed > 0:
                        cancelled = record.model_copy(deep=True)
                        cancelled.status = JobStatus.cancelled
                        cancelled.progress_phase = "cancelled"
                        cancelled.progress_message = "Job cancelled before start"
                        cancelled.progress_percent = 0
                        cancelled.finished_at = now
                        if await self._compare_and_store(
                            cancelled,
                            expected_raw=record.model_dump_json(),
                            previous_status=JobStatus.queued,
                        ):
                            record = cancelled
                            await self._publish_record(record)
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
        if record and record.batch_id:
            pipe.lrem(f"{BATCH_JOBS_KEY_PREFIX}{record.batch_id}", 0, job_id)
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
        if previous == current:
            return
        if previous == JobStatus.running:
            pipe.srem(RUNNING_JOBS_INDEX_KEY, job_id)
        if current == JobStatus.running:
            pipe.sadd(RUNNING_JOBS_INDEX_KEY, job_id)

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
        for job_id in await self.list_ids():
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
