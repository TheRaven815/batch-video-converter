from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Any, Iterable

from video_converter.core.job_repository_async import AsyncJobRepository
from video_converter.core.job_repository_shared import (
    DEFAULT_STALE_RUNNING_SECONDS,
    RUNNING_JOBS_INDEX_KEY,
    append_limited as _append_limited,
    is_stale as _is_stale,
)
from video_converter.core.models import JobRecord, JobStatus

__all__ = [
    "DEFAULT_STALE_RUNNING_SECONDS",
    "RUNNING_JOBS_INDEX_KEY",
    "JobRepository",
]

_local = threading.local()
_background_loop: asyncio.AbstractEventLoop | None = None
_background_lock = threading.Lock()


def _get_background_loop() -> asyncio.AbstractEventLoop:
    global _background_loop
    with _background_lock:
        if _background_loop is None or _background_loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="job-repository-loop", daemon=True
            )
            thread.start()
            _background_loop = loop
        return _background_loop


def _run(coro: Any) -> Any:
    """Run one repository coroutine without blocking a caller's event loop.

    Worker threads call the synchronous facade from plain code and use a
    thread-local loop (fast path). When the facade is called from inside an
    already-running loop (for example a sync repository injected into the API
    in tests) the coroutine is handed to a dedicated background loop instead
    of failing with "event loop is already running".
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = getattr(_local, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            _local.loop = loop
        return loop.run_until_complete(coro)
    return asyncio.run_coroutine_threadsafe(coro, _get_background_loop()).result()


class _SyncPipelineAdapter:
    """Expose a blocking Redis/SQLite pipeline through the async driver."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    def __getattr__(self, name: str):
        target = getattr(self._pipeline, name)
        if name in {"watch", "get", "unwatch"}:

            async def awaited(*args: Any, **kwargs: Any) -> Any:
                return target(*args, **kwargs)

            return awaited

        def call(*args: Any, **kwargs: Any) -> "_SyncPipelineAdapter":
            target(*args, **kwargs)
            return self

        return call

    async def execute(self) -> list[Any]:
        return self._pipeline.execute()

    def reset(self) -> Any:
        return getattr(self._pipeline, "reset", lambda: None)()


class _SyncStoreAdapter:
    """Async-shaped view over a blocking storage client."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def pipeline(self, transaction: bool = True) -> _SyncPipelineAdapter:
        return _SyncPipelineAdapter(self._store.pipeline(transaction=transaction))

    def __getattr__(self, name: str):
        target = getattr(self._store, name)
        if not callable(target):
            return target

        async def call(*args: Any, **kwargs: Any) -> Any:
            return target(*args, **kwargs)

        return call


class JobRepository:
    """Synchronous worker facade over :class:`AsyncJobRepository`."""

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        self._impl = AsyncJobRepository(_SyncStoreAdapter(redis_client))

    @staticmethod
    def parse_record(raw: str) -> JobRecord | None:
        return AsyncJobRepository.parse_record(raw)

    def get(self, job_id: str) -> JobRecord | None:
        return _run(self._impl.get(job_id))

    def get_many(self, job_ids: Iterable[str]) -> dict[str, JobRecord]:
        return _run(self._impl.get_many(job_ids))

    def persist(self, record: JobRecord) -> None:
        _run(self._impl.persist(record))

    def enqueue(self, record: JobRecord) -> None:
        _run(self._impl.enqueue(record))

    def enqueue_many(self, records: Iterable[JobRecord]) -> None:
        _run(self._impl.enqueue_many(records))

    def list_ids(self) -> list[str]:
        return _run(self._impl.list_ids())

    def list_batch_ids(self) -> list[str]:
        return _run(self._impl.list_batch_ids())

    def list_batch_records(self, batch_id: str) -> list[JobRecord]:
        return _run(self._impl.list_batch_records(batch_id))

    def list_records_page(
        self, *, cursor: int = 0, limit: int = 100, newest_first: bool = True
    ) -> tuple[list[JobRecord], int | None]:
        return _run(
            self._impl.list_records_page(
                cursor=cursor, limit=limit, newest_first=newest_first
            )
        )

    def count_running_jobs(self) -> int:
        return _run(self._impl.count_running_jobs())

    def list_running_records(self) -> list[JobRecord]:
        return _run(self._impl.list_running_records())

    def remove_from_queue(self, job_id: str) -> int:
        return _run(self._impl.remove_from_queue(job_id))

    def dequeue(self, timeout: int = 5) -> str | None:
        return _run(self._impl.dequeue(timeout=timeout))

    def acknowledge(self, job_id: str) -> None:
        _run(self._impl.acknowledge(job_id))

    def requeue_existing(self, record: JobRecord) -> None:
        _run(self._impl.requeue_existing(record))

    def delete(self, job_id: str) -> None:
        _run(self._impl.delete(job_id))

    def queue_positions(self) -> dict[str, int]:
        return _run(self._impl.queue_positions())

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
        return _run(
            self._impl.update_status(
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
        )

    def schedule_retry(
        self, record: JobRecord, *, delay_seconds: int, reason: str
    ) -> bool:
        return _run(
            self._impl.schedule_retry(
                record, delay_seconds=delay_seconds, reason=reason
            )
        )

    def requeue_due_retries(
        self, *, current_time: datetime | None = None
    ) -> list[JobRecord]:
        return _run(self._impl.requeue_due_retries(current_time=current_time))

    def delete_terminal_before(
        self, cutoff: datetime, on_delete: Any | None = None
    ) -> int:
        return _run(self._impl.delete_terminal_before(cutoff, on_delete))

    def recover_stale_running_jobs(
        self,
        *,
        stale_after_seconds: int = DEFAULT_STALE_RUNNING_SECONDS,
        exclude_ids: set[str] | None = None,
    ) -> list[JobRecord]:
        return _run(
            self._impl.recover_stale_running_jobs(
                stale_after_seconds=stale_after_seconds, exclude_ids=exclude_ids
            )
        )

    def recover_processing_orphans(self) -> list[JobRecord]:
        return _run(self._impl.recover_processing_orphans())

    def _index_members(self, key: str) -> list[str]:
        return _run(self._impl._index_members(key))
