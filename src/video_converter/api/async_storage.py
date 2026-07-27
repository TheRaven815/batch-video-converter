from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from redis import asyncio as redis_async

from video_converter.core.config import (
    JOB_EVENTS_CHANNEL,
    JOB_KEY_PREFIX,
    JOBS_INDEX_KEY,
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

    async def persist(self, record: JobRecord) -> None:
        previous = await self.get(record.id)
        pipe = self.storage.pipeline(transaction=True)
        pipe.set(f"{JOB_KEY_PREFIX}{record.id}", record.model_dump_json())
        self._sync_running_index(
            pipe, record.id, previous.status if previous else None, record.status
        )
        await pipe.execute()
        await self._publish_record(record)

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
            pipe.rpush(QUEUE_NAME, record.id)
            if record.status == JobStatus.running:
                pipe.sadd(RUNNING_JOBS_INDEX_KEY, record.id)
        await pipe.execute()
        for record in materialized:
            await self._publish_record(record)

    async def list_ids(self) -> list[str]:
        return list(await self.storage.lrange(JOBS_INDEX_KEY, 0, -1))

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
        return int(await self.storage.lrem(QUEUE_NAME, 0, job_id) or 0)

    async def requeue_existing(self, record: JobRecord) -> None:
        previous = await self.get(record.id)
        pipe = self.storage.pipeline(transaction=True)
        pipe.set(f"{JOB_KEY_PREFIX}{record.id}", record.model_dump_json())
        self._sync_running_index(
            pipe, record.id, previous.status if previous else None, record.status
        )
        pipe.lrem(PROCESSING_QUEUE_NAME, 0, record.id)
        pipe.lrem(QUEUE_NAME, 0, record.id)
        pipe.rpush(QUEUE_NAME, record.id)
        await pipe.execute()
        await self._publish_record(record)

    async def delete(self, job_id: str) -> None:
        pipe = self.storage.pipeline(transaction=True)
        pipe.lrem(QUEUE_NAME, 0, job_id)
        pipe.lrem(PROCESSING_QUEUE_NAME, 0, job_id)
        pipe.lrem(JOBS_INDEX_KEY, 0, job_id)
        pipe.srem(RUNNING_JOBS_INDEX_KEY, job_id)
        pipe.delete(f"{JOB_KEY_PREFIX}{job_id}")
        await pipe.execute()
        await self._publish_deleted(job_id)

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
