from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

import video_converter.api.main as api
from video_converter.core.config import MediaRoot
from video_converter.core.models import JobCreateRequest


def _patch_media_root(monkeypatch: Any, root_path: Path) -> None:
    monkeypatch.setattr(
        api,
        "settings",
        type("_S", (), {"media_roots": (MediaRoot(key="root", label="Root", path=root_path),)})(),
    )


def test_source_payload_requires_root_and_path_together() -> None:
    payload = JobCreateRequest(source_root_key="root", source_path=None)
    with pytest.raises(HTTPException) as exc:
        api._validate_source_payload(payload)
    assert exc.value.status_code == 422


def test_source_payload_rejects_invalid_root_key(tmp_path: Path, monkeypatch: Any) -> None:
    _patch_media_root(monkeypatch, tmp_path)
    payload = JobCreateRequest(source_root_key="unknown", source_path="movie.mp4")

    with pytest.raises(HTTPException) as exc:
        api._validate_source_payload(payload)

    assert exc.value.status_code == 422


def test_source_payload_rejects_path_traversal(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_text("x", encoding="utf-8")

    _patch_media_root(monkeypatch, media_root)
    payload = JobCreateRequest(source_root_key="root", source_path="../outside.mp4")

    with pytest.raises(HTTPException) as exc:
        api._validate_source_payload(payload)

    assert exc.value.status_code == 422


def test_source_payload_rejects_non_video_extension(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    text_file = media_root / "notes.txt"
    text_file.write_text("hello", encoding="utf-8")

    _patch_media_root(monkeypatch, media_root)
    payload = JobCreateRequest(source_root_key="root", source_path="notes.txt")

    with pytest.raises(HTTPException) as exc:
        api._validate_source_payload(payload)

    assert exc.value.status_code == 422


def test_source_payload_accepts_valid_video_and_build_job_infers_name(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    video_file = media_root / "clip.mkv"
    video_file.write_text("video", encoding="utf-8")

    _patch_media_root(monkeypatch, media_root)
    payload = JobCreateRequest(source_root_key="root", source_path="clip.mkv", input_filename=None)

    record = api._build_job_record(payload)

    assert record.input_filename == "clip.mkv"
    assert record.source_root_key == "root"
    assert record.source_path == "clip.mkv"
