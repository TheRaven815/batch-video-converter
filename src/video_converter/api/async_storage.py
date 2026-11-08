from __future__ import annotations

import asyncio
from typing import Any

from redis import asyncio as redis_async

from video_converter.core.config import Settings
from video_converter.core.job_repository_async import AsyncJobRepository
from video_converter.core.storage import LocalFileStore


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
        return await asyncio.to_thread(self._pipeline.execute)


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
            # SQLite access is blocking; keep it off the API event loop.
            return await asyncio.to_thread(target, *args, **kwargs)

        return call

    async def aclose(self) -> None:
        self._store.close()


AsyncStorageClient = redis_async.Redis | AsyncLocalStore


def create_async_storage_client(settings: Settings) -> AsyncStorageClient:
    if settings.video_converter_storage == "local":
        return AsyncLocalStore(LocalFileStore(settings.data_dir / "local_queue.sqlite3"))
    return redis_async.Redis.from_url(settings.redis_url, decode_responses=True)
