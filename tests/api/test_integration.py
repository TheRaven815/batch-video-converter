from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import video_converter.api.main as api
from video_converter.core.config import JOB_KEY_PREFIX, JOBS_INDEX_KEY, QUEUE_NAME, MediaRoot
from video_converter.core.job_repository import JobRepository
from video_converter.core.models import JobRecord, JobStatus, now_iso


class _Pipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> "_Pipeline":
        self.commands.append(("set", (key, value, ex)))
        return self

    def rpush(self, key: str, value: str) -> "_Pipeline":
        self.commands.append(("rpush", (key, value)))
        return self

    def lrem(self, key: str, count: int, value: str) -> "_Pipeline":
        self.commands.append(("lrem", (key, count, value)))
        return self

    def delete(self, key: str) -> "_Pipeline":
        self.commands.append(("delete", (key,)))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for name, args in self.commands:
            results.append(getattr(self.redis, name)(*args))
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {QUEUE_NAME: [], JOBS_INDEX_KEY: []}
        self.ping_raises = False

    def ping(self) -> bool:
        if self.ping_raises:
            raise api.redis.RedisError("redis unavailable")
        return True

    def pipeline(
        self, transaction: bool = True
    ) -> _Pipeline:  # noqa: ARG002 - mirrors redis-py API.
        return _Pipeline(self)

    def set(
        self, key: str, value: str, ex: int | None = None
    ) -> bool:  # noqa: ARG002 - expiration not needed here.
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
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
        if count > 0:
            selected = set(indexes[:count])
        else:
            selected = set(list(reversed(indexes))[: abs(count)])
        self.lists[key] = [item for index, item in enumerate(values) if index not in selected]
        return len(selected)

    def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0


@pytest.fixture()
def integration_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, _FakeRedis, Path]]:
    media_root = tmp_path / "media"
    outputs_dir = tmp_path / "outputs"
    data_root = tmp_path / "data"
    input_dir = data_root / "input"
    temp_dir = data_root / "temp"
    logs_dir = data_root / "logs"
    data_dir = data_root / "data"
    for path in (media_root, outputs_dir, input_dir, temp_dir, logs_dir, data_dir):
        path.mkdir(parents=True, exist_ok=True)

    settings = type(
        "_Settings",
        (),
        {
            "redis_url": "redis://test/0",
            "data_root": data_root,
            "input_dir": input_dir,
            "outputs_dir": outputs_dir,
            "temp_dir": temp_dir,
            "logs_dir": logs_dir,
            "data_dir": data_dir,
            "media_roots": (MediaRoot(key="root", label="Root", path=media_root.resolve()),),
            "worker_concurrency": 1,
        },
    )()

    fake_redis = _FakeRedis()
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "storage_client", fake_redis)
    monkeypatch.setattr(api, "job_repository", JobRepository(fake_redis))
    api._subtitle_probe_cache.clear()

    # Override authentication dependency for testing
    api.app.dependency_overrides[api.get_current_user] = lambda: "admin"
    try:
        with TestClient(api.app) as client:
            yield client, fake_redis, media_root
    finally:
        api.app.dependency_overrides.clear()


def _make_job(job_id: str, status: JobStatus, *, input_filename: str | None = None) -> JobRecord:
    ts = now_iso()
    return JobRecord(
        id=job_id,
        status=status,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        input_filename=input_filename or f"{job_id}.mp4",
        output_filename=None,
        error_message=None,
        progress_percent=0,
        progress_phase=status.value,
        progress_message=status.value,
        progress_updated_at=ts,
        cancel_requested=False,
        created_at=ts,
        updated_at=ts,
    )


def _persist_records(fake_redis: _FakeRedis, records: list[JobRecord]) -> None:
    repository = JobRepository(fake_redis)
    for record in records:
        repository.persist(record)
        fake_redis.rpush(JOBS_INDEX_KEY, record.id)
        if record.status == JobStatus.queued:
            fake_redis.rpush(QUEUE_NAME, record.id)


def test_system_settings_include_persistent_defaults(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, _, _ = integration_client

    response = client.get("/api/v1/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker_concurrency"] == 1
    assert payload["default_export"] == {
        "profile": "h264_mp4",
        "video_export": "mp4",
        "audio_export": "copy",
        "subtitle_export": "none",
        "subtitle_language": None,
    }
    assert payload["auto_cleanup"] == {
        "enabled": False,
        "retention_days": 30,
        "keep_minimum_outputs": 10,
    }
    assert payload["ui"] == {"theme": "dark", "density": "comfortable"}


def test_system_settings_persist_extended_payload(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, fake_redis, _ = integration_client
    payload = {
        "worker_concurrency": 3,
        "default_export": {
            "profile": "vp9_webm",
            "video_export": "webm",
            "audio_export": "opus",
            "subtitle_export": "embedded",
            "subtitle_language": "tur",
        },
        "auto_cleanup": {
            "enabled": True,
            "retention_days": 14,
            "keep_minimum_outputs": 5,
        },
        "ui": {"theme": "system", "density": "compact"},
    }

    update_response = client.post("/api/v1/settings", json=payload)
    get_response = client.get("/api/v1/settings")

    assert update_response.status_code == 200
    assert update_response.json() == payload
    assert get_response.json() == payload
    assert fake_redis.get("system:settings") is not None


def test_system_settings_accept_legacy_worker_only_payload(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, _, _ = integration_client

    response = client.post("/api/v1/settings", json={"worker_concurrency": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker_concurrency"] == 2
    assert payload["default_export"]["profile"] == "h264_mp4"
    assert payload["auto_cleanup"]["enabled"] is False
    assert payload["ui"]["theme"] == "dark"


def test_health_endpoints_report_liveness_and_readiness(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, fake_redis, _media_root = integration_client

    live_response = client.get("/health/live")
    ready_response = client.get("/health/ready")

    assert live_response.status_code == 200
    assert live_response.json() == {"status": "ok", "redis": "ok"}
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ready", "redis": "ok"}

    fake_redis.ping_raises = True
    degraded_live_response = client.get("/health/live")
    degraded_ready_response = client.get("/health/ready")

    assert degraded_live_response.status_code == 200
    assert degraded_live_response.json() == {"status": "ok", "redis": "error"}
    assert degraded_ready_response.status_code == 503
    assert degraded_ready_response.json()["error"]["code"] == "service_unavailable"


def test_job_creation_get_and_list_flow(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, fake_redis, media_root = integration_client
    (media_root / "movie.mp4").write_text("video", encoding="utf-8")

    create_response = client.post(
        "/api/v1/jobs",
        json={"source_root_key": "root", "source_path": "movie.mp4", "profile": "h264_mp4"},
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["status"] == "queued"
    assert created["input_filename"] == "movie.mp4"
    assert created["source_root_key"] == "root"
    assert created["source_path"] == "movie.mp4"
    assert fake_redis.lists[QUEUE_NAME] == [created["id"]]
    assert fake_redis.lists[JOBS_INDEX_KEY] == [created["id"]]

    get_response = client.get(f"/api/v1/jobs/{created['id']}")
    list_response = client.get("/api/v1/jobs")

    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]
    assert list_response.status_code == 200
    assert [job["id"] for job in list_response.json()] == [created["id"]]


def test_batch_job_creation_returns_valid_jobs_and_partial_failures(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, fake_redis, media_root = integration_client
    (media_root / "valid.mp4").write_text("video", encoding="utf-8")
    (media_root / "invalid.txt").write_text("not video", encoding="utf-8")

    response = client.post(
        "/api/v1/jobs/batch",
        json={
            "jobs": [
                {"source_root_key": "root", "source_path": "valid.mp4"},
                {"source_root_key": "root", "source_path": "invalid.txt"},
            ]
        },
        headers={"Idempotency-Key": "mixed-batch"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 1
    assert payload["jobs"][0]["source_path"] == "valid.mp4"
    assert payload["jobs"][0]["batch_id"]
    assert len(payload["errors"]) == 1
    assert payload["errors"][0]["index"] == 1
    assert payload["errors"][0]["error_code"] == "unsupported_extension"
    assert payload["idempotency_key"] == "mixed-batch"
    assert fake_redis.lists[QUEUE_NAME] == [payload["jobs"][0]["id"]]

    cached_response = client.post(
        "/api/v1/jobs/batch",
        json={"jobs": [{"source_root_key": "root", "source_path": "valid.mp4"}]},
        headers={"Idempotency-Key": "mixed-batch"},
    )
    assert cached_response.status_code == 200
    assert cached_response.json()["jobs"][0]["id"] == payload["jobs"][0]["id"]
    assert fake_redis.lists[QUEUE_NAME] == [payload["jobs"][0]["id"]]


def test_batch_job_creation_returns_structured_error_when_all_items_fail(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, fake_redis, media_root = integration_client
    (media_root / "invalid.txt").write_text("not video", encoding="utf-8")

    response = client.post(
        "/api/v1/jobs/batch",
        json={"jobs": [{"source_root_key": "root", "source_path": "invalid.txt"}]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert fake_redis.lists[QUEUE_NAME] == []
    assert fake_redis.lists[JOBS_INDEX_KEY] == []


def test_job_cancellation_marks_queued_job_cancelled_and_removes_queue_entry(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, fake_redis, media_root = integration_client
    (media_root / "cancel-me.mp4").write_text("video", encoding="utf-8")
    created = client.post(
        "/api/v1/jobs",
        json={"source_root_key": "root", "source_path": "cancel-me.mp4"},
    ).json()

    response = client.post(f"/api/v1/jobs/{created['id']}/cancel")

    assert response.status_code == 200
    cancelled = response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert cancelled["progress_phase"] == "cancelled"
    assert fake_redis.lists[QUEUE_NAME] == []


def test_media_roots_and_browse_flow(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, _fake_redis, media_root = integration_client
    (media_root / "series").mkdir()
    (media_root / "series" / "episode.mkv").write_text("video", encoding="utf-8")
    (media_root / "movie.mp4").write_text("video", encoding="utf-8")
    (media_root / "notes.txt").write_text("not listed", encoding="utf-8")
    (media_root / ".hidden.mp4").write_text("hidden", encoding="utf-8")

    roots_response = client.get("/api/v1/media/roots")
    browse_response = client.get("/api/v1/media/browse", params={"root_key": "root"})
    nested_response = client.get(
        "/api/v1/media/browse", params={"root_key": "root", "path": "series"}
    )
    traversal_response = client.get(
        "/api/v1/media/browse", params={"root_key": "root", "path": ".."}
    )

    assert roots_response.status_code == 200
    assert roots_response.json() == [{"key": "root", "label": "Root"}]
    assert browse_response.status_code == 200
    assert [entry["name"] for entry in browse_response.json()["entries"]] == ["series", "movie.mp4"]
    assert nested_response.status_code == 200
    assert [entry["name"] for entry in nested_response.json()["entries"]] == ["..", "episode.mkv"]
    assert traversal_response.status_code == 400
    assert traversal_response.json()["error"]["code"] == "path_traversal_blocked"


def test_bulk_actions_start_cancel_and_delete(
    integration_client: tuple[TestClient, _FakeRedis, Path],
) -> None:
    client, fake_redis, _media_root = integration_client
    restartable = _make_job("failed-1", JobStatus.failed)
    queued = _make_job("queued-1", JobStatus.queued)
    completed = _make_job("completed-1", JobStatus.completed)
    running = _make_job("running-1", JobStatus.running)
    _persist_records(fake_redis, [restartable, queued, completed, running])

    start_response = client.post(
        "/api/v1/jobs/bulk/start",
        json={"job_ids": ["failed-1", "queued-1", "missing"]},
    )
    assert start_response.status_code == 200
    start_payload = start_response.json()
    assert [job["id"] for job in start_payload["updated"]] == ["failed-1"]
    assert start_payload["updated"][0]["status"] == "queued"
    assert fake_redis.lists[QUEUE_NAME].count("failed-1") == 1
    assert {item["job_id"] for item in start_payload["skipped"]} == {"queued-1", "missing"}

    cancel_response = client.post(
        "/api/v1/jobs/bulk/cancel",
        json={"job_ids": ["queued-1", "completed-1", "missing"]},
    )
    assert cancel_response.status_code == 200
    cancel_payload = cancel_response.json()
    updated_by_id = {job["id"]: job for job in cancel_payload["updated"]}
    assert updated_by_id["queued-1"]["status"] == "cancelled"
    assert updated_by_id["completed-1"]["status"] == "completed"
    assert {item["job_id"] for item in cancel_payload["skipped"]} == {"completed-1", "missing"}
    assert "queued-1" not in fake_redis.lists[QUEUE_NAME]

    delete_response = client.post(
        "/api/v1/jobs/bulk/delete",
        json={"job_ids": ["completed-1", "running-1", "missing"]},
    )
    assert delete_response.status_code == 200
    delete_payload = delete_response.json()
    assert [job["id"] for job in delete_payload["updated"]] == ["completed-1"]
    assert {item["job_id"] for item in delete_payload["skipped"]} == {"running-1", "missing"}
    assert "completed-1" not in fake_redis.lists[JOBS_INDEX_KEY]
    assert f"{JOB_KEY_PREFIX}completed-1" not in fake_redis.values
