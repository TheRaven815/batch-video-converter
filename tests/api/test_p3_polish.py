from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

import video_converter.api.main as api
from video_converter.api.errors import error_code
from video_converter.core.models import (
    DefaultExportSettings,
    JobCreateRequest,
    JobRecord,
    JobStatus,
    RetrySettings,
    now_iso,
)


def _job(job_id: str, status: JobStatus, **overrides) -> JobRecord:
    timestamp = now_iso()
    data = {
        "id": job_id,
        "status": status,
        "profile": "h264_mp4",
        "input_filename": f"{job_id}.mp4",
        "created_at": timestamp,
        "updated_at": timestamp,
        **overrides,
    }
    return JobRecord(**data)


def test_default_export_rejects_invalid_target_bitrate() -> None:
    with pytest.raises(ValidationError):
        DefaultExportSettings(target_video_bitrate="4Mbps")

    assert DefaultExportSettings(target_video_bitrate="4500k").target_video_bitrate == "4500k"


@pytest.mark.parametrize("field", ["audio_stream_indexes", "subtitle_stream_indexes"])
def test_stream_indexes_reject_negative_values(field: str) -> None:
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate({field: [-1]})

    request = JobCreateRequest.model_validate({field: [0, 2]})
    assert getattr(request, field) == [0, 2]


def test_retry_settings_reject_inverted_backoff_bounds() -> None:
    with pytest.raises(ValidationError):
        RetrySettings(initial_backoff_seconds=120, max_backoff_seconds=60)

    settings = RetrySettings(initial_backoff_seconds=60, max_backoff_seconds=60)
    assert settings.max_backoff_seconds == settings.initial_backoff_seconds


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (409, "conflict"),
        (413, "payload_too_large"),
        (416, "range_not_satisfiable"),
        (429, "rate_limited"),
        (499, "client_closed_request"),
        (504, "gateway_timeout"),
        (507, "insufficient_storage"),
    ],
)
def test_structured_error_codes_cover_extended_statuses(
    status_code: int, expected: str
) -> None:
    assert error_code(HTTPException(status_code=status_code, detail="x")) == expected


def test_estimated_start_accounts_for_concurrency_and_running_eta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = [
        _job("q1", JobStatus.queued),
        _job("q2", JobStatus.queued),
        _job("q3", JobStatus.queued),
    ]
    running = _job("run", JobStatus.running, progress_eta_seconds=90)

    class Repository:
        def list_ids(self) -> list[str]:
            return ["q1", "q2", "q3"]

        def get_many(self, job_ids):
            return {job.id: job for job in queued if job.id in set(job_ids)}

        def queue_positions(self) -> dict[str, int]:
            return {"q1": 1, "q2": 2, "q3": 3}

        def list_running_records(self) -> list[JobRecord]:
            return [running]

    monkeypatch.setattr(api, "job_repository", Repository())
    monkeypatch.setattr(api, "settings", SimpleNamespace(worker_concurrency=2))

    result = api.list_jobs(response=Response(), limit=10)

    assert {record.id: record.estimated_start_seconds for record in result} == {
        "q1": 0,
        "q2": 90,
        "q3": 90,
    }
    assert {record.id: record.queue_position for record in result} == {
        "q1": 1,
        "q2": 2,
        "q3": 3,
    }
