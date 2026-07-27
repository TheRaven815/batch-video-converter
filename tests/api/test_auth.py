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


def _make_client(
    monkeypatch: pytest.MonkeyPatch, *, app_password: str = "secret-password"
) -> TestClient:
    settings = type(
        "_AuthSettings",
        (),
        {
            "app_username": "admin",
            "app_password": app_password,
            "jwt_secret": "test-jwt-secret-with-at-least-32-bytes",
        },
    )()

    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "_storage_client", _AuthStorage())
    monkeypatch.setattr(auth, "_login_failures", {})
    monkeypatch.setattr(api, "job_repository", _JobRepositoryStub())
    api.app.dependency_overrides.clear()
    return TestClient(api.app)


@pytest.fixture()
def auth_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    try:
        with _make_client(monkeypatch) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


@pytest.fixture()
def unconfigured_auth_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    try:
        with _make_client(monkeypatch, app_password="") as client:
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


def test_login_rate_limited_after_repeated_failures(auth_client: TestClient) -> None:
    for _ in range(auth.LOGIN_MAX_FAILURES):
        response = auth_client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "wrong-password"},
        )
        assert response.status_code == 401

    locked = auth_client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "secret-password"},
    )
    assert locked.status_code == 429
    assert "retry-after" in locked.headers


def test_empty_password_disables_login_instead_of_disabling_auth(
    unconfigured_auth_client: TestClient,
) -> None:
    login = unconfigured_auth_client.post(
        "/api/v1/auth/login", data={"username": "admin", "password": ""}
    )
    assert login.status_code == 503

    protected = unconfigured_auth_client.get("/api/v1/jobs")
    assert protected.status_code == 503


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "secret-password"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_stream_ticket_is_issued_and_rejected_on_regular_endpoints(
    auth_client: TestClient,
) -> None:
    token = _login(auth_client)

    response = auth_client.post(
        "/api/v1/auth/stream-ticket", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    assert ticket

    # A stream ticket must not work as a general-purpose access token.
    misuse = auth_client.post(
        "/api/v1/auth/stream-ticket", headers={"Authorization": f"Bearer {ticket}"}
    )
    assert misuse.status_code == 401


def test_credentials_change_revokes_previous_tokens(auth_client: TestClient) -> None:
    import time as _time

    token = _login(auth_client)

    # Ensure the revocation timestamp lands in a later second than the token's iat.
    _time.sleep(1.1)
    update = auth_client.put(
        "/api/v1/auth/credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "secret-password", "new_password": "brand-new-password"},
    )
    assert update.status_code == 200

    revoked = auth_client.post(
        "/api/v1/auth/stream-ticket", headers={"Authorization": f"Bearer {token}"}
    )
    assert revoked.status_code == 401

    relogin = auth_client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "brand-new-password"},
    )
    assert relogin.status_code == 200


def test_setup_status_reports_needed_only_when_unconfigured(
    unconfigured_auth_client: TestClient,
) -> None:
    response = unconfigured_auth_client.get("/api/v1/auth/setup-status")
    assert response.status_code == 200
    assert response.json() == {"needs_setup": True}


def test_setup_status_false_when_env_credentials_exist(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/auth/setup-status")
    assert response.status_code == 200
    assert response.json() == {"needs_setup": False}


def test_first_run_setup_creates_account_and_logs_in(
    unconfigured_auth_client: TestClient,
) -> None:
    client = unconfigured_auth_client

    setup = client.post(
        "/api/v1/auth/setup",
        json={"username": "operator", "password": "a-strong-password"},
    )
    assert setup.status_code == 200
    token = setup.json()["access_token"]
    assert token

    # The returned token is immediately usable.
    probe = client.post("/api/v1/auth/stream-ticket", headers={"Authorization": f"Bearer {token}"})
    assert probe.status_code == 200

    # Setup is one-shot: a second attempt is rejected.
    again = client.post(
        "/api/v1/auth/setup",
        json={"username": "intruder", "password": "another-password"},
    )
    assert again.status_code == 409

    # And normal login works with the created credentials.
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "operator", "password": "a-strong-password"},
    )
    assert login.status_code == 200


def test_setup_rejected_when_env_credentials_exist(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/setup",
        json={"username": "intruder", "password": "another-password"},
    )
    assert response.status_code == 409


def test_setup_enforces_password_policy(unconfigured_auth_client: TestClient) -> None:
    response = unconfigured_auth_client.post(
        "/api/v1/auth/setup",
        json={"username": "operator", "password": "short"},
    )
    assert response.status_code == 422


def test_stored_legacy_hash_upgrades_to_bcrypt_on_login(auth_client: TestClient) -> None:
    import json as _json

    storage = auth.get_storage()
    storage.set(
        auth.AUTH_CREDENTIALS_KEY,
        _json.dumps({"username": "admin", "password_hash": auth._legacy_hash("secret-password")}),
    )

    token = _login(auth_client)
    assert token

    upgraded = _json.loads(storage.get(auth.AUTH_CREDENTIALS_KEY))
    assert upgraded["password_hash"].startswith("$2")
    assert auth.verify_password("secret-password", upgraded["password_hash"])
