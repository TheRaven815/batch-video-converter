from __future__ import annotations

from typing import Any

import video_converter.api.main as api
from video_converter.core.config import JOB_KEY_PREFIX, JOBS_INDEX_KEY, QUEUE_NAME
from video_converter.core.models import JobIdsRequest, JobRecord, JobStatus, now_iso


class _FakeJobRepository:
    def __init__(self) -> None:
        self.queue: list[str] = []
        self.index: list[str] = []
        self.deleted_keys: list[str] = []

    def remove_from_queue(self, job_id: str) -> int:
        return self.lrem(QUEUE_NAME, 0, job_id)

    def persist(self, record: JobRecord) -> None:
        return None

    def requeue_existing(self, record: JobRecord) -> None:
        self.queue.append(record.id)

    def delete(self, job_id: str) -> None:
        self.lrem(QUEUE_NAME, 0, job_id)
        self.lrem(JOBS_INDEX_KEY, 0, job_id)
        self.deleted_keys.append(f"{JOB_KEY_PREFIX}{job_id}")

    def lrem(self, key: str, count: int, value: str) -> int:
        target = self.queue if key == QUEUE_NAME else self.index
        removed = 0
        kept: list[str] = []
        for item in target:
            if item == value:
                removed += 1
                if count != 0 and removed >= count:
                    kept.extend(target[len(kept) + removed :])
                    break
            else:
                kept.append(item)
        else:
            if removed == 0:
                return 0

        if count == 0 and removed > 0:
            target[:] = [item for item in target if item != value]
            return removed

        if removed > 0:
            target[:] = kept
        return removed

    def rpush(self, key: str, value: str) -> int:
        if key == QUEUE_NAME:
            self.queue.append(value)
            return len(self.queue)
        if key == JOBS_INDEX_KEY:
            self.index.append(value)
            return len(self.index)
        return 0


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
    fake_repository = _FakeJobRepository()
    records = {
        "running-1": _make_job("running-1", JobStatus.running),
        "queued-1": _make_job("queued-1", JobStatus.queued),
        "done-1": _make_job("done-1", JobStatus.completed),
    }

    monkeypatch.setattr(api, "job_repository", fake_repository)
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))
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
    fake_repository = _FakeJobRepository()
    fake_repository.queue = ["queued-1", "other"]

    records = {
        "queued-1": _make_job("queued-1", JobStatus.queued),
        "done-1": _make_job("done-1", JobStatus.completed),
    }

    monkeypatch.setattr(api, "job_repository", fake_repository)
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))
    monkeypatch.setattr(api, "_persist_job_record", lambda record: None)

    payload = JobIdsRequest(job_ids=["queued-1", "done-1", "missing"])
    result = api.cancel_jobs_bulk(payload)

    assert {item.id for item in result.updated} == {"queued-1", "done-1"}
    assert records["queued-1"].status == JobStatus.cancelled
    assert records["queued-1"].progress_phase == "cancelled"

    skip_map = {item.job_id: item.reason for item in result.skipped}
    assert skip_map["done-1"] == "Job is already completed"
    assert skip_map["missing"] == "Job not found"


def test_archive_jobs_bulk_soft_deletes_non_running_records(monkeypatch: Any) -> None:
    fake_repository = _FakeJobRepository()
    fake_repository.queue = ["archive-1", "running-1", "other"]

    records = {
        "archive-1": _make_job("archive-1", JobStatus.completed),
        "running-1": _make_job("running-1", JobStatus.running),
    }

    monkeypatch.setattr(api, "job_repository", fake_repository)
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    payload = JobIdsRequest(job_ids=["archive-1", "running-1", "missing"])
    result = api.archive_jobs_bulk(payload)

    assert [item.id for item in result.updated] == ["archive-1"]
    assert records["archive-1"].archived is True
    assert "archive-1" not in fake_repository.queue

    skip_map = {item.job_id: item.reason for item in result.skipped}
    assert skip_map["running-1"] == "Running job cannot be archived"
    assert skip_map["missing"] == "Job not found"


def test_delete_jobs_bulk_skips_running_and_removes_persisted_records(monkeypatch: Any) -> None:
    fake_repository = _FakeJobRepository()
    fake_repository.queue = ["delete-1", "running-1", "other"]
    fake_repository.index = ["delete-1", "running-1", "other"]

    records = {
        "delete-1": _make_job("delete-1", JobStatus.completed),
        "running-1": _make_job("running-1", JobStatus.running),
    }

    monkeypatch.setattr(api, "job_repository", fake_repository)
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    payload = JobIdsRequest(job_ids=["delete-1", "running-1", "missing"])
    result = api.delete_jobs_bulk(payload)

    assert [item.id for item in result.updated] == ["delete-1"]
    assert "delete-1" not in fake_repository.queue
    assert "delete-1" not in fake_repository.index
    assert f"{JOB_KEY_PREFIX}delete-1" in fake_repository.deleted_keys

    skip_map = {item.job_id: item.reason for item in result.skipped}
    assert skip_map["running-1"] == "Running job cannot be deleted"
    assert skip_map["missing"] == "Job not found"
