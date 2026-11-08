from __future__ import annotations

from datetime import datetime, timedelta, timezone

from video_converter.core.config import (
    BATCH_JOBS_KEY_PREFIX,
    BATCHES_INDEX_KEY,
    JOBS_INDEX_KEY,
    QUEUE_NAME,
)
from video_converter.core.job_repository import RUNNING_JOBS_INDEX_KEY, JobRepository
from video_converter.core.models import JobRecord, JobStatus, now_iso

from support import FakeRedis as _FakeRedis


def _make_job(
    job_id: str, status: JobStatus, *, progress_updated_at: str | None = None
) -> JobRecord:
    ts = now_iso()
    return JobRecord(
        id=job_id,
        status=status,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        input_filename=f"{job_id}.mp4",
        progress_percent=50 if status == JobStatus.running else 0,
        progress_phase=status.value,
        progress_message=status.value,
        progress_updated_at=progress_updated_at or ts,
        created_at=ts,
        updated_at=progress_updated_at or ts,
    )


def test_enqueue_many_persists_index_and_queue_in_one_pipeline() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    records = [_make_job("job-1", JobStatus.queued), _make_job("job-2", JobStatus.queued)]

    repository.enqueue_many(records)

    assert fake_redis.lists[JOBS_INDEX_KEY] == ["job-1", "job-2"]
    assert fake_redis.lists[QUEUE_NAME] == ["job-1", "job-2"]
    assert repository.get("job-1") is not None
    assert repository.get("job-2") is not None


def test_enqueue_many_builds_batch_indices() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    first = _make_job("job-1", JobStatus.queued)
    second = _make_job("job-2", JobStatus.queued)
    first.batch_id = second.batch_id = "batch-1"

    repository.enqueue_many([first, second])

    assert repository.list_batch_ids() == ["batch-1"]
    assert [record.id for record in repository.list_batch_records("batch-1")] == [
        "job-1",
        "job-2",
    ]
    assert fake_redis.lists[f"{BATCH_JOBS_KEY_PREFIX}batch-1"] == ["job-1", "job-2"]
    assert fake_redis.lists[BATCHES_INDEX_KEY] == ["batch-1"]


def test_list_records_page_reads_only_requested_index_slice() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    repository.enqueue_many([_make_job(f"job-{idx}", JobStatus.queued) for idx in range(1, 6)])

    records, next_cursor = repository.list_records_page(cursor=1, limit=2, newest_first=True)

    assert [record.id for record in records] == ["job-4", "job-3"]
    assert next_cursor == 3


def test_get_many_batches_records_with_one_mget() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    repository.enqueue_many(
        [_make_job("job-1", JobStatus.queued), _make_job("job-2", JobStatus.queued)]
    )

    records = repository.get_many(["job-1", "job-2", "missing"])

    assert set(records) == {"job-1", "job-2"}
    assert records["job-1"].id == "job-1"
    assert fake_redis.mget_calls == 1


def test_recover_stale_running_jobs_requeues_only_old_running_jobs() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh_timestamp = datetime.now(timezone.utc).isoformat()
    stale = _make_job("stale", JobStatus.running, progress_updated_at=old_timestamp)
    fresh = _make_job("fresh", JobStatus.running, progress_updated_at=fresh_timestamp)
    completed = _make_job("done", JobStatus.completed, progress_updated_at=old_timestamp)
    repository.enqueue_many([stale, fresh, completed])
    fake_redis.lists[QUEUE_NAME] = []

    recovered = repository.recover_stale_running_jobs(stale_after_seconds=3600)

    assert [job.id for job in recovered] == ["stale"]
    recovered_stale = repository.get("stale")
    assert recovered_stale is not None
    assert recovered_stale.status == JobStatus.queued
    assert recovered_stale.progress_phase == "queued"
    assert fake_redis.lists[QUEUE_NAME] == ["stale"]
    assert fake_redis.sets[RUNNING_JOBS_INDEX_KEY] == {"fresh"}
    assert repository.count_running_jobs() == 1
    assert repository.get("fresh").status == JobStatus.running  # type: ignore[union-attr]
    assert repository.get("done").status == JobStatus.completed  # type: ignore[union-attr]


def test_update_status_does_not_clear_existing_error_when_error_is_omitted() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    record = _make_job("job-1", JobStatus.failed)
    record.error_message = "original failure"
    repository.enqueue(record)

    updated = repository.update_status(
        record.id,
        JobStatus.failed,
        progress_percent=None,
        progress_phase="failed",
        progress_message="Still failed",
    )

    assert updated is not None
    assert updated.error_message == "original failure"


def test_requeue_existing_keeps_only_one_queue_entry() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    record = _make_job("job-1", JobStatus.running)
    repository.enqueue(record)
    record.status = JobStatus.queued

    repository.requeue_existing(record)
    repository.requeue_existing(record)

    assert fake_redis.lists[QUEUE_NAME] == [record.id]


def test_schedule_retry_never_clears_an_existing_cancellation() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    record = _make_job("job-1", JobStatus.running)
    repository.enqueue(record)
    stored = repository.get(record.id)
    assert stored is not None
    stored.cancel_requested = True
    stored.status = JobStatus.queued
    fake_redis.set(f"job:{record.id}", stored.model_dump_json())

    scheduled = repository.schedule_retry(
        stored, delay_seconds=30, reason="temporary failure"
    )

    assert scheduled is False
    current = repository.get(record.id)
    assert current is not None
    assert current.cancel_requested is True
    assert current.next_retry_at is None


def test_update_status_does_not_resurrect_a_cancelled_job() -> None:
    fake_redis = _FakeRedis()
    repository = JobRepository(fake_redis)  # type: ignore[arg-type]
    record = _make_job("job-1", JobStatus.cancelled)
    repository.enqueue(record)

    updated = repository.update_status(
        record.id,
        JobStatus.completed,
        progress_percent=100,
        progress_phase="completed",
        progress_message="late completion",
    )

    assert updated is not None
    assert updated.status == JobStatus.cancelled
    assert repository.get(record.id).status == JobStatus.cancelled  # type: ignore[union-attr]
