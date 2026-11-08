from __future__ import annotations

from typing import Any

import video_converter.api.main as api
from video_converter.core.config import JOB_KEY_PREFIX, JOBS_INDEX_KEY, QUEUE_NAME
from video_converter.core.models import JobIdsRequest, JobRecord, JobStatus, now_iso

from support import FakeJobRepository as _FakeJobRepository


def _make_job(job_id: str, status: JobStatus) -> JobRecord:
    ts = now_iso()
    return JobRecord(
        id=job_id,
        status=status,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        input_filename=f"{job_id}.mp4",
        source_root_key=None,
        source_path=None,
        output_filename=None,
        error_message=None,
        progress_percent=0,
        progress_phase="queued",
        progress_message="queued",
        progress_updated_at=ts,
        cancel_requested=False,
        created_at=ts,
        updated_at=ts,
    )


def test_start_jobs_bulk_skips_invalid_states_and_requeues_restartable(monkeypatch: Any) -> None:
    records = {
        "running-1": _make_job("running-1", JobStatus.running),
        "queued-1": _make_job("queued-1", JobStatus.queued),
        "done-1": _make_job("done-1", JobStatus.completed),
    }
    fake_repository = _FakeJobRepository(records=records)

    monkeypatch.setattr(api, "job_repository", fake_repository)
    monkeypatch.setattr(api, "_persist_job_record", lambda record: None)

    payload = JobIdsRequest(job_ids=["running-1", "queued-1", "done-1", "missing"])
    result = api.start_jobs_bulk(payload)

    assert [item.id for item in result.updated] == ["done-1"]
    assert records["done-1"].status == JobStatus.queued
    assert fake_repository.queue == ["done-1"]

    skip_map = {item.job_id: item.reason for item in result.skipped}
    assert "running-1" in skip_map
    assert "queued-1" in skip_map
    assert skip_map["missing"] == "Job not found"


def test_cancel_jobs_bulk_marks_queued_jobs_cancelled_when_removed_from_queue(
    monkeypatch: Any,
) -> None:
    records = {
        "queued-1": _make_job("queued-1", JobStatus.queued),
        "done-1": _make_job("done-1", JobStatus.completed),
    }
    fake_repository = _FakeJobRepository(records=records)
    fake_repository.queue = ["queued-1", "other"]

    monkeypatch.setattr(api, "job_repository", fake_repository)
    monkeypatch.setattr(api, "_persist_job_record", lambda record: None)

    payload = JobIdsRequest(job_ids=["queued-1", "done-1", "missing"])
    result = api.cancel_jobs_bulk(payload)

    assert {item.id for item in result.updated} == {"queued-1"}
    assert records["queued-1"].status == JobStatus.cancelled
    assert records["queued-1"].progress_phase == "cancelled"

    skip_map = {item.job_id: item.reason for item in result.skipped}
    assert skip_map["done-1"] == "Job is already completed"
    assert skip_map["missing"] == "Job not found"


def test_archive_jobs_bulk_soft_deletes_terminal_records_only(monkeypatch: Any) -> None:
    records = {
        "archive-1": _make_job("archive-1", JobStatus.completed),
        "running-1": _make_job("running-1", JobStatus.running),
        "queued-1": _make_job("queued-1", JobStatus.queued),
    }
    fake_repository = _FakeJobRepository(records=records)
    fake_repository.queue = ["queued-1", "other"]

    monkeypatch.setattr(api, "job_repository", fake_repository)

    payload = JobIdsRequest(job_ids=["archive-1", "running-1", "queued-1", "missing"])
    result = api.archive_jobs_bulk(payload)

    assert [item.id for item in result.updated] == ["archive-1"]
    assert records["archive-1"].archived is True

    # Archiving a queued job must not strand it: it stays queued and unarchived.
    assert records["queued-1"].archived is False
    assert "queued-1" in fake_repository.queue

    skip_map = {item.job_id: item.reason for item in result.skipped}
    assert skip_map["running-1"] == "Active job cannot be archived; cancel it first"
    assert skip_map["queued-1"] == "Active job cannot be archived; cancel it first"
    assert skip_map["missing"] == "Job not found"


def test_delete_jobs_bulk_skips_running_and_removes_persisted_records(monkeypatch: Any) -> None:
    records = {
        "delete-1": _make_job("delete-1", JobStatus.completed),
        "running-1": _make_job("running-1", JobStatus.running),
    }
    fake_repository = _FakeJobRepository(records=records)
    fake_repository.queue = ["delete-1", "running-1", "other"]
    fake_repository.index = ["delete-1", "running-1", "other"]

    monkeypatch.setattr(api, "job_repository", fake_repository)

    payload = JobIdsRequest(job_ids=["delete-1", "running-1", "missing"])
    result = api.delete_jobs_bulk(payload)

    assert [item.id for item in result.updated] == ["delete-1"]
    assert "delete-1" not in fake_repository.queue
    assert "delete-1" not in fake_repository.index
    assert f"{JOB_KEY_PREFIX}delete-1" in fake_repository.deleted_keys

    skip_map = {item.job_id: item.reason for item in result.skipped}
    assert skip_map["running-1"] == "Running job cannot be deleted"
    assert skip_map["missing"] == "Job not found"


def test_archived_terminal_job_can_be_started_and_becomes_visible(monkeypatch: Any) -> None:
    record = _make_job("restart-1", JobStatus.completed)
    records = {record.id: record}
    fake_repository = _FakeJobRepository(records=records)
    monkeypatch.setattr(api, "job_repository", fake_repository)

    archived = api.archive_jobs_bulk(JobIdsRequest(job_ids=[record.id]))
    restarted = api.start_jobs_bulk(JobIdsRequest(job_ids=[record.id]))

    assert [item.id for item in archived.updated] == [record.id]
    assert [item.id for item in restarted.updated] == [record.id]
    assert record.status == JobStatus.queued
    assert record.archived is False
    assert fake_repository.queue == [record.id]
