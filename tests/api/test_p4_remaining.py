from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import Response
from pydantic import ValidationError

import video_converter.api.main as api
import video_converter.api.routes as routes
from video_converter.core.models import JobCreateRequest, JobRecord, JobStatus, now_iso


def _job(job_id: str, status: JobStatus) -> JobRecord:
    timestamp = now_iso()
    return JobRecord(
        id=job_id,
        status=status,
        profile="h264_mp4",
        input_filename=f"{job_id}.mp4",
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_invalid_export_values_are_rejected_at_submit_time() -> None:
    for field, value in (
        ("profile", "unknown"),
        ("video_export", "avi"),
        ("audio_export", "flac"),
        ("subtitle_export", "burned"),
    ):
        with pytest.raises(ValidationError):
            JobCreateRequest.model_validate({field: value})


def test_filtered_listing_scans_later_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        0: ([_job("queued-2", JobStatus.queued), _job("queued-1", JobStatus.queued)], 2),
        2: (
            [_job("done-2", JobStatus.completed), _job("done-1", JobStatus.completed)],
            None,
        ),
    }

    class Repository:
        def list_records_page(self, *, cursor: int, limit: int, newest_first: bool):  # noqa: ARG002
            return pages[cursor]

    monkeypatch.setattr(api, "job_repository", Repository())
    result = api.list_jobs(
        response=Response(),
        status=JobStatus.completed,
        limit=2,
        cursor=0,
    )

    assert [record.id for record in result] == ["done-2", "done-1"]


def test_openapi_documents_create_status_and_structured_errors() -> None:
    schema = api.app.openapi()
    create = schema["paths"]["/api/v1/jobs"]["post"]
    batch = schema["paths"]["/api/v1/jobs/batch"]["post"]

    assert "201" in create["responses"]
    assert "201" in batch["responses"]
    assert (
        create["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/StructuredErrorResponse"
    )


def test_forgot_password_endpoint_remains_disabled() -> None:
    assert "/api/v1/auth/forgot-password" not in api.app.openapi()["paths"]


def test_sse_emits_initial_snapshot_then_pubsub_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _job("job-1", JobStatus.running)
    delta = {
        "event": "job_updated",
        "timestamp": now_iso(),
        "data": {"job": record.model_dump(mode="json")},
    }

    class Repository:
        async def list_records_page(self, **kwargs: Any):  # noqa: ARG002
            return [record], None

    class PubSub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any):
            return None

        async def subscribe(self, channel: str) -> None:  # noqa: ARG002
            return None

        async def get_message(self, **kwargs: Any):  # noqa: ARG002
            return {"data": json.dumps(delta)}

    class Storage:
        backend_name = "redis"

        def pubsub(self) -> PubSub:
            return PubSub()

    class Request:
        async def is_disconnected(self) -> bool:
            return False

    async def exercise() -> tuple[str, str]:
        monkeypatch.setattr(routes, "job_repository", Repository(), raising=False)
        monkeypatch.setattr(routes, "storage_client", Storage(), raising=False)
        response = await routes.stream_jobs(Request())  # type: ignore[arg-type]
        iterator = response.body_iterator
        snapshot = await anext(iterator)
        update = await anext(iterator)
        await iterator.aclose()
        return str(snapshot), str(update)

    snapshot, update = asyncio.run(exercise())

    assert snapshot.startswith("event: jobs_snapshot")
    assert update.startswith("event: job_updated")
    assert json.loads(update.split("data: ", 1)[1])["data"]["job"]["id"] == record.id
