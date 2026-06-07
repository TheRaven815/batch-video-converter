from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import video_converter.api.main as api
from video_converter.core.config import MediaRoot
from video_converter.core.models import JobBatchCreateRequest, JobCreateRequest


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value


class _CapturingJobRepository:
    def __init__(self) -> None:
        self.enqueued_batches: list[list[str]] = []

    def enqueue_many(self, records: list[Any]) -> None:
        self.enqueued_batches.append([record.id for record in records])


def _patch_media_root(monkeypatch: Any, root_path: Path) -> None:
    monkeypatch.setattr(
        api,
        "settings",
        type("_S", (), {"media_roots": (MediaRoot(key="root", label="Root", path=root_path),)})(),
    )


def test_batch_request_rejects_empty_jobs() -> None:
    with pytest.raises(ValidationError):
        JobBatchCreateRequest(jobs=[])


def test_create_jobs_batch_enqueues_all_records_in_single_repository_call(
    tmp_path: Path, monkeypatch: Any
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    (media_root / "one.mp4").write_text("video", encoding="utf-8")
    (media_root / "two.mkv").write_text("video", encoding="utf-8")
    _patch_media_root(monkeypatch, media_root)

    fake_repository = _CapturingJobRepository()
    monkeypatch.setattr(api, "job_repository", fake_repository)

    payload = JobBatchCreateRequest(
        jobs=[
            JobCreateRequest(source_root_key="root", source_path="one.mp4"),
            JobCreateRequest(source_root_key="root", source_path="two.mkv"),
        ]
    )

    response = api.create_jobs_batch(payload)

    assert len(response.jobs) == 2
    assert response.errors == []
    assert response.jobs[0].batch_id
    assert response.jobs[0].batch_id == response.jobs[1].batch_id
    assert fake_repository.enqueued_batches == [[job.id for job in response.jobs]]


def test_create_jobs_batch_reuses_idempotency_key_response(
    tmp_path: Path, monkeypatch: Any
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    (media_root / "one.mp4").write_text("video", encoding="utf-8")
    _patch_media_root(monkeypatch, media_root)

    fake_repository = _CapturingJobRepository()
    fake_redis = _FakeRedis()
    monkeypatch.setattr(api, "job_repository", fake_repository)
    monkeypatch.setattr(api, "storage_client", fake_redis)

    payload = JobBatchCreateRequest(
        jobs=[JobCreateRequest(source_root_key="root", source_path="one.mp4")]
    )

    first = api.create_jobs_batch(payload, idempotency_key="repeat-click")
    second = api.create_jobs_batch(payload, idempotency_key="repeat-click")

    assert [job.id for job in second.jobs] == [job.id for job in first.jobs]
    assert second.idempotency_key == "repeat-click"
    assert fake_repository.enqueued_batches == [[job.id for job in first.jobs]]


def test_create_jobs_batch_raises_when_all_items_fail_validation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """When every item in the batch fails validation, HTTP 422 is raised and
    nothing is enqueued (no partial state)."""
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    (media_root / "invalid1.txt").write_text("not video", encoding="utf-8")
    (media_root / "invalid2.txt").write_text("not video", encoding="utf-8")
    _patch_media_root(monkeypatch, media_root)

    fake_repository = _CapturingJobRepository()
    monkeypatch.setattr(api, "job_repository", fake_repository)

    payload = JobBatchCreateRequest(
        jobs=[
            JobCreateRequest(source_root_key="root", source_path="invalid1.txt"),
            JobCreateRequest(source_root_key="root", source_path="invalid2.txt"),
        ]
    )

    with pytest.raises(HTTPException):
        api.create_jobs_batch(payload)

    assert fake_repository.enqueued_batches == []


def test_create_jobs_batch_enqueues_valid_items_and_reports_errors_for_invalid(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Mixed valid/invalid batch: valid items are enqueued atomically, errors
    are returned in the response for invalid items (no exception raised)."""
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    (media_root / "valid.mp4").write_text("video", encoding="utf-8")
    (media_root / "invalid.txt").write_text("not video", encoding="utf-8")
    _patch_media_root(monkeypatch, media_root)

    fake_repository = _CapturingJobRepository()
    monkeypatch.setattr(api, "job_repository", fake_repository)

    payload = JobBatchCreateRequest(
        jobs=[
            JobCreateRequest(source_root_key="root", source_path="valid.mp4"),
            JobCreateRequest(source_root_key="root", source_path="invalid.txt"),
        ]
    )

    response = api.create_jobs_batch(payload)

    # Only the valid item should be enqueued
    assert len(response.jobs) == 1
    assert response.jobs[0].source_path == "valid.mp4"
    assert response.jobs[0].batch_id
    assert fake_repository.enqueued_batches == [[response.jobs[0].id]]

    # The invalid item should appear in errors
    assert len(response.errors) == 1
    assert response.errors[0].index == 1
    assert response.errors[0].source_path == "invalid.txt"
    assert response.errors[0].error_code == "unsupported_extension"
    assert response.errors[0].recoverable is True


def test_create_jobs_batch_enqueues_only_valid_from_multiple_mixed_items(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Batch with several valid and invalid items: all valid items are enqueued
    in a single repository call, and all invalid items appear in errors."""
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    (media_root / "a.mp4").write_text("video", encoding="utf-8")
    (media_root / "b.mkv").write_text("video", encoding="utf-8")
    (media_root / "bad1.txt").write_text("not video", encoding="utf-8")
    (media_root / "c.mov").write_text("video", encoding="utf-8")
    (media_root / "bad2.doc").write_text("not video", encoding="utf-8")
    _patch_media_root(monkeypatch, media_root)

    fake_repository = _CapturingJobRepository()
    monkeypatch.setattr(api, "job_repository", fake_repository)

    payload = JobBatchCreateRequest(
        jobs=[
            JobCreateRequest(source_root_key="root", source_path="a.mp4"),
            JobCreateRequest(source_root_key="root", source_path="bad1.txt"),
            JobCreateRequest(source_root_key="root", source_path="b.mkv"),
            JobCreateRequest(source_root_key="root", source_path="bad2.doc"),
            JobCreateRequest(source_root_key="root", source_path="c.mov"),
        ]
    )

    response = api.create_jobs_batch(payload)

    assert len(response.jobs) == 3
    assert len(response.errors) == 2

    # All valid jobs share the same batch_id
    batch_ids = {job.batch_id for job in response.jobs}
    assert len(batch_ids) == 1

    # All valid jobs enqueued in a single batch call
    assert fake_repository.enqueued_batches == [[job.id for job in response.jobs]]

    # Error indices correspond to the invalid input positions
    assert [e.index for e in response.errors] == [1, 3]
    assert all(e.error_code == "unsupported_extension" for e in response.errors)


def test_validate_jobs_reports_item_level_errors(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True)
    (media_root / "valid.mp4").write_text("video", encoding="utf-8")
    (media_root / "invalid.txt").write_text("not video", encoding="utf-8")
    _patch_media_root(monkeypatch, media_root)

    payload = JobBatchCreateRequest(
        jobs=[
            JobCreateRequest(source_root_key="root", source_path="valid.mp4"),
            JobCreateRequest(source_root_key="root", source_path="invalid.txt"),
        ]
    )

    result = api.validate_jobs(payload)

    assert result.valid_count == 1
    assert result.invalid_count == 1
    assert result.items[0].valid is True
    assert result.items[1].valid is False
    assert result.items[1].error_code == "unsupported_extension"
