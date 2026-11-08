from __future__ import annotations

from typing import Any, Iterable

import redis

from video_converter.core.config import (
    JOB_KEY_PREFIX,
    JOBS_INDEX_KEY,
    QUEUE_NAME,
)
from video_converter.core.models import JobRecord, JobStatus


class FakePipeline:
    """Minimal redis-py pipeline backed by :class:`FakeRedis`."""

    def __init__(self, client: "FakeRedis") -> None:
        self.client = client
        self.commands: list[tuple[str, tuple[Any, ...]]] = []

    def _queue(self, name: str, *args: Any) -> "FakePipeline":
        self.commands.append((name, args))
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> "FakePipeline":
        return self._queue("set", key, value, ex)

    def rpush(self, key: str, value: str) -> "FakePipeline":
        return self._queue("rpush", key, value)

    def lpush(self, key: str, value: str) -> "FakePipeline":
        return self._queue("lpush", key, value)

    def lrem(self, key: str, count: int, value: str) -> "FakePipeline":
        return self._queue("lrem", key, count, value)

    def sadd(self, key: str, *values: str) -> "FakePipeline":
        return self._queue("sadd", key, *values)

    def srem(self, key: str, *values: str) -> "FakePipeline":
        return self._queue("srem", key, *values)

    def delete(self, key: str) -> "FakePipeline":
        return self._queue("delete", key)

    def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args in self.commands:
            if name == "set":
                key, value, ex = args
                results.append(self.client.set(key, value, ex=ex))
            else:
                results.append(getattr(self.client, name)(*args))
        return results


class FakeRedis:
    """In-memory subset of the Redis commands used by repositories/tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {
            QUEUE_NAME: [],
            JOBS_INDEX_KEY: [],
        }
        self.sets: dict[str, set[str]] = {}
        self.published: list[tuple[str, str]] = []
        self.ping_raises = False

    def pipeline(self, transaction: bool = True) -> FakePipeline:  # noqa: ARG002
        return FakePipeline(self)

    def ping(self) -> bool:
        if self.ping_raises:
            raise redis.RedisError("redis unavailable")
        return True

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,  # noqa: ARG002 - expiration not modelled
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls = getattr(self, "mget_calls", 0) + 1
        return [self.values.get(key) for key in keys]

    def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0

    def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    def lrem(self, key: str, count: int, value: str) -> int:
        values = self.lists.setdefault(key, [])
        if count == 0:
            removed = values.count(value)
            self.lists[key] = [item for item in values if item != value]
            return removed

        indexes = [index for index, item in enumerate(values) if item == value]
        selected = set(indexes[:count] if count > 0 else list(reversed(indexes))[: abs(count)])
        self.lists[key] = [item for index, item in enumerate(values) if index not in selected]
        return len(selected)

    def sadd(self, key: str, *values: str) -> int:
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(values)
        return len(target) - before

    def srem(self, key: str, *values: str) -> int:
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.difference_update(values)
        return before - len(target)

    def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 0


class FakeJobRepository:
    """Small in-memory job repository shared by API unit tests."""

    def __init__(
        self,
        ids: Iterable[str] = (),
        records: dict[str, JobRecord] | None = None,
    ) -> None:
        self.index: list[str] = list(ids)
        self.records: dict[str, JobRecord] = dict(records or {})
        self.queue: list[str] = []
        self.deleted_keys: list[str] = []
        self.persisted: list[JobRecord] = []

    @property
    def _ids(self) -> list[str]:
        return self.index

    def get(self, job_id: str) -> JobRecord | None:
        return self.records.get(job_id)

    def get_many(self, job_ids: Iterable[str]) -> dict[str, JobRecord]:
        return {
            job_id: self.records[job_id]
            for job_id in job_ids
            if job_id in self.records
        }

    def list_ids(self) -> list[str]:
        return list(self.index)

    def list_records_page(
        self, *, cursor: int = 0, limit: int = 100, newest_first: bool = True
    ) -> tuple[list[JobRecord], int | None]:
        ids = list(reversed(self.index)) if newest_first else list(self.index)
        page = ids[cursor : cursor + limit]
        records = [self.records[job_id] for job_id in page if job_id in self.records]
        next_cursor = cursor + len(page) if cursor + len(page) < len(ids) else None
        return records, next_cursor

    def count_running_jobs(self) -> int:
        return sum(1 for record in self.records.values() if record.status == JobStatus.running)

    def list_running_records(self) -> list[JobRecord]:
        return [
            record for record in self.records.values() if record.status == JobStatus.running
        ]

    def queue_positions(self) -> dict[str, int]:
        return {}

    def enqueue_many(self, records: Iterable[JobRecord]) -> None:
        for record in records:
            self.records[record.id] = record
            self.index.append(record.id)
            self.queue.append(record.id)

    def persist(self, record: JobRecord) -> None:
        self.records[record.id] = record
        self.persisted.append(record)

    def remove_from_queue(self, job_id: str) -> int:
        return self.lrem(QUEUE_NAME, 0, job_id)

    def requeue_existing(self, record: JobRecord) -> None:
        if record.id not in self.index:
            self.index.append(record.id)
        if record.id not in self.queue:
            self.queue.append(record.id)

    def delete(self, job_id: str) -> None:
        self.lrem(QUEUE_NAME, 0, job_id)
        self.lrem(JOBS_INDEX_KEY, 0, job_id)
        self.records.pop(job_id, None)
        self.deleted_keys.append(f"{JOB_KEY_PREFIX}{job_id}")

    def lrem(self, key: str, count: int, value: str) -> int:
        target = self.queue if key == QUEUE_NAME else self.index
        values = list(target)
        if count == 0:
            removed = values.count(value)
            target[:] = [item for item in values if item != value]
            return removed
        indexes = [index for index, item in enumerate(values) if item == value]
        selected = set(indexes[:count] if count > 0 else list(reversed(indexes))[: abs(count)])
        target[:] = [item for index, item in enumerate(values) if index not in selected]
        return len(selected)

    def rpush(self, key: str, value: str) -> int:
        target = self.queue if key == QUEUE_NAME else self.index
        target.append(value)
        return len(target)
