from __future__ import annotations

import threading
import time

from video_converter.core.storage import LocalFileStore


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
