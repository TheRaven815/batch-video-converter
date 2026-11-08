from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from video_converter.core.storage import LocalFileStore, StorageError


def test_local_file_store_persists_values_and_lists_across_instances(tmp_path) -> None:
    db_path = tmp_path / "queue.sqlite3"
    first = LocalFileStore(db_path)

    assert first.ping() is True
    assert first.set("job:1", "payload") is True
    assert first.rpush("jobs:queue", "job-1") == 1
    assert first.rpush("jobs:queue", "job-2") == 2

    second = LocalFileStore(db_path)
    assert second.get("job:1") == "payload"
    assert second.lrange("jobs:queue", 0, -1) == ["job-1", "job-2"]
    assert second.llen("jobs:queue") == 2


def test_local_file_store_mget_skips_missing_and_expired_keys(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    store.set("a", "1")
    store.set("b", "2")
    store.set("expired", "3", ex=1)
    store._conn.execute("UPDATE kv SET expires_at = ? WHERE key = 'expired'", (time.time() - 5,))

    assert store.mget(["a", "missing", "b"]) == ["1", None, "2"]
    assert store.mget(["expired"]) == [None]
    assert store.mget([]) == []


def test_local_file_store_prunes_expired_kv_rows_on_write(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    store.set("old", "x", ex=1)
    store._conn.execute(
        "UPDATE kv SET expires_at = ? WHERE key = 'old'", (time.time() - 5,)
    )
    store._next_expiry_prune = 0

    store.set("new", "y")

    remaining = store._conn.execute("SELECT key FROM kv ORDER BY key").fetchall()
    assert [row[0] for row in remaining] == ["new"]


def test_local_file_store_tracks_list_lengths_without_count_per_push(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")

    assert store.rpush("q", "a") == 1
    assert store.lpush("q", "b") == 2
    assert store.llen("q") == 2
    assert store.lrem("q", 1, "a") == 1
    assert store.rpush("q", "c") == 2

    row = store._conn.execute(
        "SELECT length FROM list_lengths WHERE key = 'q'"
    ).fetchone()
    assert row is not None and int(row[0]) == 2
    assert store.lrange("q", 0, -1) == ["b", "c"]


def test_local_file_store_backfills_length_for_legacy_list_rows(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    store._conn.executemany(
        "INSERT INTO lists(key, position, value) VALUES(?, ?, ?)",
        [("legacy", 0, "a"), ("legacy", 1, "b")],
    )

    assert store.llen("legacy") == 2
    assert store.rpush("legacy", "c") == 3
    assert store.lrange("legacy", 0, -1) == ["a", "b", "c"]


def test_local_file_store_blmove_keeps_both_lengths(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    store.rpush("source", "job-1")
    store.rpush("source", "job-2")

    assert store.blmove("source", "target", timeout=1) == "job-1"
    assert store.llen("source") == 1
    assert store.llen("target") == 1
    assert store.blpop("target", timeout=1) == ("target", "job-1")
    assert store.llen("target") == 0


def test_local_file_store_pipeline_and_blpop(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    pipe = store.pipeline(transaction=True)
    pipe.set("job:1", "payload")
    pipe.rpush("jobs:index", "job-1")
    pipe.rpush("jobs:queue", "job-1")

    assert pipe.execute() == [True, 1, 1]
    assert store.get("job:1") == "payload"
    assert store.lrange("jobs:index", 0, -1) == ["job-1"]
    assert store.blpop("jobs:queue", timeout=1) == ("jobs:queue", "job-1")
    assert store.blpop("jobs:queue", timeout=1) is None


def test_local_file_store_blpop_waits_for_other_process_like_writer(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    result: list[tuple[str, str] | None] = []

    def popper() -> None:
        result.append(store.blpop("jobs:queue", timeout=3))

    thread = threading.Thread(target=popper)
    thread.start()
    time.sleep(0.5)
    LocalFileStore(tmp_path / "queue.sqlite3").rpush("jobs:queue", "job-1")
    thread.join(timeout=5)

    assert result == [("jobs:queue", "job-1")]


def test_local_file_store_lrem_delete_and_expiry(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    store.rpush("jobs:queue", "job-1")
    store.rpush("jobs:queue", "job-2")
    store.rpush("jobs:queue", "job-1")

    assert store.lrem("jobs:queue", 0, "job-1") == 2
    assert store.lrange("jobs:queue", 0, -1) == ["job-2"]

    assert store.set("cache", "value", ex=1) is True
    assert store.get("cache") == "value"
    time.sleep(1.1)
    assert store.get("cache") is None

    assert store.set("job:2", "payload") is True
    assert store.delete("job:2") == 1
    assert store.get("job:2") is None


def test_local_file_store_set_nx_and_compare_and_set_are_atomic(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")

    assert store.set("claim", "first", nx=True) is True
    assert store.set("claim", "second", nx=True) is False
    assert store.get("claim") == "first"
    assert store.compare_and_set("claim", "stale", "third") is False
    assert store.compare_and_set("claim", "first", "third") is True
    assert store.get("claim") == "third"


def test_local_file_store_translates_sqlite_failures(monkeypatch, tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")

    def fail_connect():
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(store, "_connect", fail_connect)
    with pytest.raises(StorageError, match="database unavailable"):
        store.get("claim")


def test_local_file_store_sets_are_unique_and_pipeline_safe(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")

    assert store.sadd("running", "job-1", "job-1", "job-2") == 2
    assert store.scard("running") == 2
    pipe = store.pipeline()
    pipe.srem("running", "job-1")
    pipe.sadd("running", "job-3")
    assert pipe.execute() == [1, 1]
    assert store.scard("running") == 2


def test_local_file_store_pipeline_rolls_back_on_unsupported_operation(tmp_path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")

    with pytest.raises(ValueError, match="Unsupported local pipeline operation"):
        store._execute_pipeline(
            [
                ("set", ("rolled-back", "value", None)),
                ("unsupported", ()),
            ]
        )

    assert store.get("rolled-back") is None
