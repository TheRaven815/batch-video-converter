from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from video_converter.api import auth, routes
from video_converter.api.main import create_app
from video_converter.core.config import WORKER_HEARTBEAT_KEY


class _HealthyStorage:
    """Sync shim; the endpoints tolerate sync and awaitable storage methods."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def llen(self, key: str) -> int:  # noqa: ARG002 - queue names are irrelevant
        return 0

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True


class _HealthyRepository:
    def count_running_jobs(self) -> int:
        return 0


def test_worker_health_reports_ok_with_working_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generator expression containing ``await`` becomes an async generator.

    Passing it to ``sum(...)`` raised ``TypeError`` and made the probe report
    ``redis="error"`` even when the backend and the worker were healthy.
    """
    auth_settings = SimpleNamespace(
        app_username="admin",
        app_password="secret",
        jwt_secret="test-jwt-secret-with-at-least-32-bytes",
    )
    storage = _HealthyStorage()
    storage.set(
        WORKER_HEARTBEAT_KEY,
        json.dumps(
            {
                "timestamp": time.time(),
                "hardware_encoders": [],
                "worker_concurrency_limit": 1,
                "effective_worker_concurrency": 1,
            }
        ),
    )

    monkeypatch.setattr(auth, "get_settings", lambda: auth_settings)
    monkeypatch.setattr(auth, "_storage_client", None)
    monkeypatch.setattr(
        routes,
        "settings",
        SimpleNamespace(data_root=tmp_path, worker_concurrency=1),
    )
    monkeypatch.setattr(routes, "storage_client", storage)
    monkeypatch.setattr(routes, "job_repository", _HealthyRepository())

    with TestClient(create_app()) as client:
        login = client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "secret"},
        )
        assert login.status_code == 200
        response = client.get(
            "/api/v1/worker/health",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["redis"] == "ok"
    assert body["worker_online"] is True
    assert body["status"] == "ok"
    assert body["queue_depth"] == 0
