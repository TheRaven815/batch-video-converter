from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import video_converter.api.auth as auth
import video_converter.api.main as api


class _AuthStorage:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True


class _JobRepositoryStub:
    def recover_stale_running_jobs(self, stale_after_seconds: int) -> list[object]:  # noqa: ARG002
        return []


@pytest.fixture()
def auth_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = type(
        "_AuthSettings",
        (),
        {
            "app_username": "admin",
            "app_password": "secret-password",
            "jwt_secret": "test-jwt-secret-with-at-least-32-bytes",
        },
    )()

    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "_storage_client", _AuthStorage())
    monkeypatch.setattr(api, "job_repository", _JobRepositoryStub())
    api.app.dependency_overrides.clear()

    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


def test_login_endpoint_issues_bearer_token(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "secret-password"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_token_endpoint_remains_backward_compatible_alias(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/token",
        data={"username": "admin", "password": "secret-password"},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


def test_login_rejects_invalid_credentials_with_canonical_error(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "authentication_failed",
        "message": "Invalid username or password",
        "recoverable": False,
        "details": {"path": "/api/v1/auth/login"},
    }


def test_protected_endpoint_rejects_missing_token_with_canonical_error(
    auth_client: TestClient,
) -> None:
    response = auth_client.get("/api/v1/jobs")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "authentication_failed",
        "message": "Could not validate credentials",
        "recoverable": False,
        "details": {"path": "/api/v1/jobs"},
    }
