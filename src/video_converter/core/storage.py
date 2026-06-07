from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import redis

from video_converter.core.config import Settings


class LocalPipeline:
    def __init__(self, store: LocalFileStore) -> None:
        self.store = store
        self.operations: list[tuple[str, tuple[Any, ...]]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> LocalPipeline:
        self.operations.append(("set", (key, value, ex)))
        return self

    def rpush(self, key: str, value: str) -> LocalPipeline:
        self.operations.append(("rpush", (key, value)))
        return self

    def lrem(self, key: str, count: int, value: str) -> LocalPipeline:
        self.operations.append(("lrem", (key, count, value)))
        return self

    def delete(self, key: str) -> LocalPipeline:
        self.operations.append(("delete", (key,)))
        return self

    def execute(self) -> list[Any]:
        return self.store._execute_pipeline(self.operations)


class LocalFileStore:
    """Small Redis-like persistent store for single-machine local development.

    This intentionally implements only the Redis methods used by this project.
    It is backed by SQLite so the API and worker can run as separate processes
    without a Redis server.

    A ``threading.Lock`` serialises access so that the store is safe to use
    from multiple worker threads (``WORKER_CONCURRENCY > 1``).
    """

    backend_name = "local"

    def __init__(self, path: Path) -> None:
        import threading as _threading

        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = _threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.path),
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            self._configure_connection(self._conn)
        return self._conn

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        """Apply SQLite settings needed for safe local multi-process use."""
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

    def _begin_write(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")

    def close(self) -> None:
        """Close the persistent connection if open."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            self._begin_write(conn)
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS lists (key TEXT NOT NULL, position INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key, position))"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_lists_key_position ON lists(key, position)"
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def ping(self) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("SELECT 1")
        return True

    def get(self, key: str) -> str | None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value, expires_at FROM kv WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            value, expires_at = row
            if expires_at is not None and float(expires_at) <= now:
                self._begin_write(conn)
                try:
                    conn.execute(
                        "DELETE FROM kv WHERE key = ? AND value = ? AND expires_at = ?",
                        (key, value, expires_at),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                return None
            return str(value)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        expires_at = time.time() + ex if ex else None
        with self._lock, self._connect() as conn:
            self._begin_write(conn)
            try:
                conn.execute(
                    "INSERT INTO kv(key, value, expires_at) VALUES(?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at",
                    (key, value, expires_at),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return True

    def delete(self, key: str) -> int:
        with self._lock, self._connect() as conn:
            self._begin_write(conn)
            try:
                cursor = conn.execute("DELETE FROM kv WHERE key = ?", (key,))
                deleted = int(cursor.rowcount or 0)
                conn.execute("COMMIT")
                return deleted
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def rpush(self, key: str, value: str) -> int:
        with self._lock, self._connect() as conn:
            self._begin_write(conn)
            try:
                next_position = self._next_position(conn, key)
                conn.execute(
                    "INSERT INTO lists(key, position, value) VALUES(?, ?, ?)",
                    (key, next_position, value),
                )
                length = conn.execute(
                    "SELECT COUNT(*) FROM lists WHERE key = ?", (key,)
                ).fetchone()[0]
                conn.execute("COMMIT")
                return int(length)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        with self._lock, self._connect() as conn:
            count = int(
                conn.execute("SELECT COUNT(*) FROM lists WHERE key = ?", (key,)).fetchone()[0]
            )

            if count == 0:
                return []

            # Normalize negative start (Python/Redis convention).
            if start < 0:
                start = max(0, count + start)

            # Resolve end: -1 means "to the end"; other negatives count from tail.
            if end == -1:
                end = count - 1
            elif end < 0:
                end = count + end

            # If start is beyond the list or past end, return empty.
            if start >= count or start > end:
                return []

            # Clamp end to the last valid index.
            end = min(end, count - 1)

            limit = end - start + 1
            rows = conn.execute(
                "SELECT value FROM lists WHERE key = ? ORDER BY position ASC LIMIT ? OFFSET ?",
                (key, limit, start),
            ).fetchall()

        return [str(row[0]) for row in rows]

    def llen(self, key: str) -> int:
        with self._lock, self._connect() as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM lists WHERE key = ?", (key,)).fetchone()[0]
            )

    def lrem(self, key: str, count: int, value: str) -> int:
        with self._lock, self._connect() as conn:
            self._begin_write(conn)
            try:
                rows = conn.execute(
                    "SELECT position FROM lists WHERE key = ? AND value = ? ORDER BY position ASC",
                    (key, value),
                ).fetchall()
                positions = [int(row[0]) for row in rows]
                if count > 0:
                    positions = positions[:count]
                elif count < 0:
                    positions = list(reversed(positions))[0 : abs(count)]
                if positions:
                    placeholders = ",".join("?" for _ in positions)
                    conn.execute(
                        f"DELETE FROM lists WHERE key = ? AND position IN ({placeholders})",
                        (key, *positions),
                    )
                conn.execute("COMMIT")
                return len(positions)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def blpop(self, key: str, timeout: int = 0) -> tuple[str, str] | None:
        deadline = None if timeout == 0 else time.monotonic() + timeout
        while True:
            popped = self._lpop(key)
            if popped is not None:
                return key, popped
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(0.25)

    def pipeline(
        self, transaction: bool = True
    ) -> LocalPipeline:  # noqa: ARG002 - mirrors redis-py API.
        return LocalPipeline(self)

    def _lpop(self, key: str) -> str | None:
        with self._lock, self._connect() as conn:
            self._begin_write(conn)
            try:
                row = conn.execute(
                    "SELECT position, value FROM lists WHERE key = ? ORDER BY position ASC LIMIT 1",
                    (key,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                position, value = row
                conn.execute("DELETE FROM lists WHERE key = ? AND position = ?", (key, position))
                conn.execute("COMMIT")
                return str(value)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _next_position(self, conn: sqlite3.Connection, key: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM lists WHERE key = ?", (key,)
        ).fetchone()
        return int(row[0])

    def _execute_pipeline(self, operations: Iterable[tuple[str, tuple[Any, ...]]]) -> list[Any]:
        results: list[Any] = []
        with self._lock, self._connect() as conn:
            self._begin_write(conn)
            try:
                for name, args in operations:
                    if name == "set":
                        key, value, ex = args
                        expires_at = time.time() + ex if ex else None
                        conn.execute(
                            "INSERT INTO kv(key, value, expires_at) VALUES(?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at",
                            (key, value, expires_at),
                        )
                        results.append(True)
                    elif name == "rpush":
                        key, value = args
                        next_position = self._next_position(conn, str(key))
                        conn.execute(
                            "INSERT INTO lists(key, position, value) VALUES(?, ?, ?)",
                            (key, next_position, value),
                        )
                        length = conn.execute(
                            "SELECT COUNT(*) FROM lists WHERE key = ?", (key,)
                        ).fetchone()[0]
                        results.append(int(length))
                    elif name == "lrem":
                        key, count, value = args
                        rows = conn.execute(
                            "SELECT position FROM lists WHERE key = ? AND value = ? ORDER BY position ASC",
                            (key, value),
                        ).fetchall()
                        positions = [int(row[0]) for row in rows]
                        if count > 0:
                            positions = positions[: int(count)]
                        elif count < 0:
                            positions = list(reversed(positions))[0 : abs(int(count))]
                        if positions:
                            placeholders = ",".join("?" for _ in positions)
                            conn.execute(
                                f"DELETE FROM lists WHERE key = ? AND position IN ({placeholders})",
                                (key, *positions),
                            )
                        results.append(len(positions))
                    elif name == "delete":
                        (key,) = args
                        cursor = conn.execute("DELETE FROM kv WHERE key = ?", (key,))
                        results.append(int(cursor.rowcount or 0))
                    else:
                        raise ValueError(f"Unsupported local pipeline operation: {name}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return results


StorageClient = redis.Redis | LocalFileStore


def get_storage_backend() -> str:
    value = os.getenv("VIDEO_CONVERTER_STORAGE", "redis").strip().lower()
    if value in {"local", "file", "sqlite"}:
        return "local"
    return "redis"


def create_storage_client(settings: Settings) -> StorageClient:
    backend = get_storage_backend()
    if backend == "local":
        return LocalFileStore(settings.data_dir / "local_queue.sqlite3")
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def storage_label(client: StorageClient) -> str:
    return getattr(client, "backend_name", "redis")


def is_redis_storage(client: StorageClient) -> bool:
    return storage_label(client) == "redis"
