from __future__ import annotations

from datetime import datetime, timedelta, timezone

from video_converter.core.config import JOBS_INDEX_KEY, QUEUE_NAME
from video_converter.core.job_repository import JobRepository
from video_converter.core.models import JobRecord, JobStatus, now_iso


class _Pipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    def set(self, key: str, value: str) -> "_Pipeline":
        self.commands.append(("set", (key, value)))
        return self

    def rpush(self, key: str, value: str) -> "_Pipeline":
        self.commands.append(("rpush", (key, value)))
        return self

    def lrem(self, key: str, count: int, value: str) -> "_Pipeline":
        self.commands.append(("lrem", (key, count, value)))
        return self

    def delete(self, key: str) -> "_Pipeline":
        self.commands.append(("delete", (key,)))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for name, args in self.commands:
            results.append(getattr(self.redis, name)(*args))
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {QUEUE_NAME: [], JOBS_INDEX_KEY: []}

    def pipeline(self, transaction: bool = True) -> _Pipeline:
        return _Pipeline(self)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    def lrem(self, key: str, count: int, value: str) -> int:
        values = self.lists.setdefault(key, [])
        removed = values.count(value)
        self.lists[key] = [item for item in values if item != value]
        return removed

    def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0


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
    assert repository.get("fresh").status == JobStatus.running  # type: ignore[union-attr]
    assert repository.get("done").status == JobStatus.completed  # type: ignore[union-attr]
