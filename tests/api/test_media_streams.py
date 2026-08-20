from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncio

import pytest
from fastapi import HTTPException

import video_converter.api.routes as routes
from video_converter.api.routers import media
from video_converter.core.config import MediaRoot


def _run(coro):
    return asyncio.run(coro)


class _FakeCompletedProcess:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.returncode = 0
        self.stderr = ""
        if payload is None:
            payload = {
                "streams": [
                    {"index": 0, "codec_type": "video", "codec_name": "h264", "channels": 0, "tags": {"title": "Main"}},
                    {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "eng", "title": "English"}},
                    {"index": 2, "codec_type": "audio", "codec_name": "ac3", "channels": 6, "tags": {"language": "tur"}},
                    {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng", "title": "English SDH"}},
                    {"index": 4, "codec_type": "subtitle", "codec_name": "ass", "tags": {"language": "jpn"}},
                ]
            }
        self.stdout = json.dumps(payload)


def _patch_media_settings(monkeypatch: Any, media_root: Path) -> None:
    fake_settings = type(
        "_FakeSettings",
        (),
        {
            "media_roots": (MediaRoot(key="root", label="Root", path=media_root),),
            "outputs_dir": media_root / "outputs",
            "logs_dir": media_root / "logs",
            "data_dir": media_root / "data",
        },
    )()
    monkeypatch.setattr(routes, "settings", fake_settings, raising=False)
    # media module resolves via routes.settings lazily, no direct patch needed there.


def test_media_streams_returns_video_audio_subtitle(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "movie.mkv").write_text("video", encoding="utf-8")
    _patch_media_settings(monkeypatch, media_root)
    media._streams_probe_cache.clear()

    calls = 0

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        nonlocal calls
        calls += 1
        return _FakeCompletedProcess()

    monkeypatch.setattr(media.subprocess, "run", fake_run)

    first = _run(media.probe_media_streams(root_key="root", path="movie.mkv"))

    assert first.root_key == "root"
    assert first.path == "movie.mkv"
    # rel_path only, no absolute leak
    assert "/" not in first.path or first.path == "movie.mkv"
    assert len(first.video) == 1
    assert first.video[0].index == 0
    assert first.video[0].codec == "h264"
    assert len(first.audio) == 2
    assert {a.language for a in first.audio} == {"eng", "tur"}
    assert first.audio[0].channels == 2
    assert first.audio[1].channels == 6
    assert len(first.subtitle) == 2
    assert first.subtitle[0].language == "eng"
    assert first.subtitle[0].title == "English SDH"
    assert first.subtitle[1].language == "jpn"

    # cache hit: second call does not invoke ffprobe again
    second = _run(media.probe_media_streams(root_key="root", path="movie.mkv"))
    assert calls == 1
    assert second.video == first.video


def test_media_streams_traversal_blocked(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "movie.mkv").write_text("video", encoding="utf-8")
    _patch_media_settings(monkeypatch, media_root)
    media._streams_probe_cache.clear()

    with pytest.raises(HTTPException) as exc:
        _run(media.probe_media_streams(root_key="root", path="../movie.mkv"))
    assert exc.value.status_code in {400, 422}

    with pytest.raises(HTTPException) as exc2:
        _run(media.probe_media_streams(root_key="root", path="/etc/passwd"))
    assert exc2.value.status_code in {400, 422}


def test_media_streams_60s_cache_used(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "movie2.mkv").write_text("video", encoding="utf-8")
    _patch_media_settings(monkeypatch, media_root)
    media._streams_probe_cache.clear()

    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())
    first = _run(media.probe_media_streams(root_key="root", path="movie2.mkv"))
    # tamper cache expiry to simulate not-expired
    assert ("root", "movie2.mkv") in media._streams_probe_cache
    expiry, _ = media._streams_probe_cache[("root", "movie2.mkv")]
    assert expiry > media.time.monotonic()

    # second payload would be different but cache returns first
    different_payload = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}]}
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(payload=different_payload))
    second = _run(media.probe_media_streams(root_key="root", path="movie2.mkv"))
    # still returns cached h264 not hevc
    assert second.video[0].codec == "h264"


def test_media_streams_invalid_root(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "movie.mkv").write_text("video", encoding="utf-8")
    _patch_media_settings(monkeypatch, media_root)
    media._streams_probe_cache.clear()

    with pytest.raises(HTTPException) as exc:
        _run(media.probe_media_streams(root_key="unknown", path="movie.mkv"))
    assert exc.value.status_code == 404
