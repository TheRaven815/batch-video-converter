from __future__ import annotations

from pathlib import Path

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
    try:
        video = media_root / "clip.mp4"
        video.write_bytes(b"fake-mp4")

        def fake_fix_mp4(source_path: Path, output_path: Path | None = None) -> Path:
            assert Path(source_path).resolve() == video.resolve()
            return Path(source_path)

        monkeypatch.setattr("video_converter.worker.main.fix_mp4", fake_fix_mp4)

        resp = client.post(
            "/api/v1/tools/mp4-fix",
            json={"source_root_key": "root", "source_path": "clip.mp4"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["filename"] == "clip.mp4"
        assert body["status"] == "fixed"
    finally:
        api.app.dependency_overrides.clear()
