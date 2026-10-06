from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi import HTTPException
from fastapi.testclient import TestClient

import video_converter.api.main as api
from video_converter.core.config import MediaRoot
from video_converter.core.job_repository import JobRepository


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool:  # noqa: ARG002
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0

    def pipeline(self, transaction: bool = True):  # noqa: ARG002
        class _P:
            def __init__(self, outer: _FakeRedis) -> None:
                self.outer = outer
                self.cmds: list[tuple[str, tuple]] = []

            def set(self, *a, **kw):  # type: ignore[no-untyped-def]
                self.cmds.append(("set", a))
                return self

            def rpush(self, *a, **kw):  # type: ignore[no-untyped-def]
                self.cmds.append(("rpush", a))
                return self

            def execute(self):  # type: ignore[no-untyped-def]
                res: list = []
                for name, args in self.cmds:
                    res.append(getattr(self.outer, name)(*args))
                return res

        return _P(self)

    def publish(self, *a, **kw) -> int:  # noqa: ARG002
        return 0


def _make_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    data_root = tmp_path / "data"
    for sub in ("input", "outputs", "temp", "logs", "data"):
        (data_root / sub).mkdir(parents=True, exist_ok=True)
    settings = type(
        "_S",
        (),
        {
            "data_root": data_root,
            "input_dir": data_root / "input",
            "outputs_dir": data_root / "outputs",
            "temp_dir": data_root / "temp",
            "logs_dir": data_root / "logs",
            "data_dir": data_root / "data",
            "media_roots": (MediaRoot(key="root", label="Root", path=media_root.resolve()),),
            "worker_concurrency": 1,
            "max_upload_bytes": 10 * 1024 * 1024,
            "min_free_disk_bytes": 0,
            "ffmpeg_stall_timeout_seconds": 30,
        },
    )()
    fake = _FakeRedis()
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "storage_client", fake)
    monkeypatch.setattr(api, "job_repository", JobRepository(fake))
    api.app.dependency_overrides[api.get_current_user] = lambda: "test-user"
    api.app.dependency_overrides[api.get_stream_user] = lambda: "test-user"
    client = TestClient(api.app)
    return client, media_root


def test_mp4_fix_endpoint_exists_and_rejects_traversal(tmp_path: Path, monkeypatch) -> None:
    client, _media_root = _make_client(tmp_path, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/tools/mp4-fix",
            json={"source_root_key": "root", "source_path": "../outside.mp4"},
        )
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert "error" in data

        resp2 = client.post("/api/v1/tools/mp4-fix", json={})
        assert resp2.status_code in {400, 422}
    finally:
        api.app.dependency_overrides.clear()


def test_mp4_fix_endpoint_success_with_mocked_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    client, media_root = _make_client(tmp_path, monkeypatch)
    import video_converter.worker.main as worker

    try:
        video = media_root / "clip.mp4"
        video.write_bytes(b"source-mp4")
        video.chmod(0o444)
        media_root.chmod(0o555)
        monkeypatch.setattr(worker, "settings", SimpleNamespace(data_root=tmp_path / "stale"))

        class FakeProcess:
            returncode = 0

            def __init__(self, cmd, **kwargs):
                assert Path(cmd[cmd.index("-i") + 1]) == video.resolve()
                target = Path(cmd[-1])
                assert target.is_relative_to((tmp_path / "data" / "temp").resolve())
                target.write_bytes(b"repaired-mp4")

            def wait(self, timeout):
                assert 0 < timeout <= 0.25
                return 0

            def poll(self):
                return 0

        monkeypatch.setattr(worker.subprocess, "Popen", FakeProcess)
        responses = [
            client.post(
                "/api/v1/tools/mp4-fix",
                json={"source_root_key": "root", "source_path": "clip.mp4"},
            )
            for _ in range(2)
        ]
        filenames = []
        for resp in responses:
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "fixed"
            assert body["filename"] != video.name
            filenames.append(body["filename"])
            output = tmp_path / "data" / "outputs" / body["filename"]
            assert output.read_bytes() == b"repaired-mp4"
            download = client.get(body["download_url"])
            assert download.status_code == 200, download.text
            assert download.content == b"repaired-mp4"
        assert filenames[0] != filenames[1]
        assert video.read_bytes() == b"source-mp4"
        assert list(media_root.iterdir()) == [video]
        assert not list((tmp_path / "data" / "temp").iterdir())
    finally:
        media_root.chmod(0o755)
        video.chmod(0o644)
        api.app.dependency_overrides.clear()


@pytest.mark.parametrize("failure,status", [("ffmpeg", 500), ("disk", 507)])
def test_mp4_fix_failure_leaves_source_and_outputs_unchanged(tmp_path, monkeypatch, failure, status):
    client, media_root = _make_client(tmp_path, monkeypatch)
    import video_converter.worker.main as worker

    video = media_root / "clip.mp4"
    video.write_bytes(b"source-mp4")

    class FailedProcess:
        returncode = 1

        def __init__(self, cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"partial")
            kwargs["stderr"].write(b"broken MP4")

        def wait(self, timeout):
            return 1

        def poll(self):
            return 1

    if failure == "disk":
        monkeypatch.setattr(worker.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    else:
        monkeypatch.setattr(worker.subprocess, "Popen", FailedProcess)
    try:
        resp = client.post(
            "/api/v1/tools/mp4-fix",
            json={"source_root_key": "root", "source_path": "clip.mp4"},
        )
        assert resp.status_code == status, resp.text
        assert video.read_bytes() == b"source-mp4"
        assert not list((tmp_path / "data" / "outputs").iterdir())
        assert not list((tmp_path / "data" / "temp").iterdir())
    finally:
        api.app.dependency_overrides.clear()


def test_mp4_fix_rejects_upload_traversal(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        resp = client.post("/api/v1/tools/mp4-fix", json={"input_filename": "../outside.mp4"})
        assert resp.status_code == 400
        from types import SimpleNamespace
        from video_converter.api import auth

        monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(
            app_username="admin", app_password="configured-test-password",
            jwt_secret="test-secret-with-at-least-32-bytes",
        ))
        monkeypatch.setattr(auth, "_storage_client", _FakeRedis())
        api.app.dependency_overrides.clear()
        assert client.post("/api/v1/tools/mp4-fix", json={}).status_code == 401
    finally:
        api.app.dependency_overrides.clear()


def test_mp4_fix_disconnect_stops_child_and_cleans_temp(tmp_path, monkeypatch):
    client, media_root = _make_client(tmp_path, monkeypatch)
    import video_converter.worker.main as worker
    from video_converter.api.routers import tools
    from video_converter.core.models import Mp4FixRequest

    source = media_root / "clip.mp4"
    source.write_bytes(b"source")
    processes = []

    class Process:
        returncode = None
        terminated = False

        def __init__(self, cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"partial")
            processes.append(self)

        def wait(self, timeout=None):
            if not self.terminated:
                import subprocess
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            self.returncode = -15
            return -15

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

    class DisconnectedRequest:
        async def is_disconnected(self):
            return bool(processes)

    monkeypatch.setattr(worker.subprocess, "Popen", Process)
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(tools.mp4_fix(
                Mp4FixRequest(source_root_key="root", source_path="clip.mp4"),
                DisconnectedRequest(),
            ))
        assert error.value.status_code == 499
        assert processes[0].terminated
        assert source.read_bytes() == b"source"
        assert not list((tmp_path / "data" / "outputs").iterdir())
        assert not list((tmp_path / "data" / "temp").iterdir())
    finally:
        api.app.dependency_overrides.clear()
        client.close()
