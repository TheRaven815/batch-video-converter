from typing import Any

from fastapi import Response

import video_converter.api.main as api
from video_converter.core.models import JobRecord, JobStatus, now_iso


class _FakeJobRepository:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def list_ids(self) -> list[str]:
        return list(self._ids)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        return list(self._ids)


def _make_job(
    job_id: str,
    status: JobStatus,
    *,
    profile: str = "h264_mp4",
    source_root_key: str | None = None,
    source_path: str | None = None,
    created_at: str | None = None,
    batch_id: str | None = None,
    progress_percent: int = 0,
    archived: bool = False,
) -> JobRecord:
    ts = created_at or now_iso()
    return JobRecord(
        id=job_id,
        status=status,
        profile=profile,
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        input_filename=f"{job_id}.mp4",
        source_root_key=source_root_key,
        source_path=source_path,
        output_filename=None,
        error_message=None,
        progress_percent=progress_percent,
        progress_phase="queued",
        progress_message="queued",
        progress_updated_at=ts,
        cancel_requested=False,
        created_at=ts,
        updated_at=ts,
        batch_id=batch_id,
        archived=archived,
    )


def test_list_jobs_returns_newest_first_and_applies_limit(monkeypatch: Any) -> None:
    # Redis index insertion order: oldest -> newest
    ordered_ids = ["job-1", "job-2", "job-3"]
    records = {
        "job-1": _make_job("job-1", JobStatus.completed),
        "job-2": _make_job("job-2", JobStatus.running),
        "job-3": _make_job("job-3", JobStatus.queued),
    }

    monkeypatch.setattr(api, "job_repository", _FakeJobRepository(ordered_ids))
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    listed = api.list_jobs(response=Response(), status=None, limit=2)

    assert [job.id for job in listed] == ["job-3", "job-2"]


def test_list_jobs_status_filter_keeps_newest_first(monkeypatch: Any) -> None:
    ordered_ids = ["job-1", "job-2", "job-3", "job-4"]
    records = {
        "job-1": _make_job("job-1", JobStatus.queued),
        "job-2": _make_job("job-2", JobStatus.completed),
        "job-3": _make_job("job-3", JobStatus.completed),
        "job-4": _make_job("job-4", JobStatus.failed),
    }

    monkeypatch.setattr(api, "job_repository", _FakeJobRepository(ordered_ids))
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    listed = api.list_jobs(response=Response(), status=JobStatus.completed, limit=10)

    assert [job.id for job in listed] == ["job-3", "job-2"]


def test_list_jobs_accepts_comma_status_and_cursor(monkeypatch: Any) -> None:
    ordered_ids = ["job-1", "job-2", "job-3", "job-4"]
    records = {
        "job-1": _make_job("job-1", JobStatus.queued),
        "job-2": _make_job("job-2", JobStatus.completed),
        "job-3": _make_job("job-3", JobStatus.failed),
        "job-4": _make_job("job-4", JobStatus.running),
    }

    monkeypatch.setattr(api, "job_repository", _FakeJobRepository(ordered_ids))
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    listed = api.list_jobs(response=Response(), status="failed,completed", limit=10, cursor=1)

    assert [job.id for job in listed] == ["job-3", "job-2"]


def test_list_jobs_filters_by_q_profile_root_and_date(monkeypatch: Any) -> None:
    ordered_ids = ["job-1", "job-2", "job-3"]
    records = {
        "job-1": _make_job(
            "job-1",
            JobStatus.queued,
            profile="h264_mp4",
            source_root_key="filmler",
            source_path="movies/cat.mp4",
            created_at="2026-05-01T10:00:00+00:00",
        ),
        "job-2": _make_job(
            "job-2",
            JobStatus.queued,
            profile="vp9_webm",
            source_root_key="diziler",
            source_path="shows/cat.webm",
            created_at="2026-05-02T10:00:00+00:00",
        ),
        "job-3": _make_job(
            "job-3",
            JobStatus.queued,
            profile="vp9_webm",
            source_root_key="diziler",
            source_path="shows/dog.webm",
            created_at="2026-05-03T10:00:00+00:00",
        ),
    }

    monkeypatch.setattr(api, "job_repository", _FakeJobRepository(ordered_ids))
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    listed = api.list_jobs(
        response=Response(),
        q="cat",
        profile="vp9_webm",
        source_root_key="diziler",
        created_after="2026-05-02T00:00:00Z",
        created_before="2026-05-02T23:59:59Z",
    )

    assert [job.id for job in listed] == ["job-2"]


def test_list_jobs_filters_source_type_and_hides_archived(monkeypatch: Any) -> None:
    ordered_ids = ["legacy-1", "server-1", "archived-1"]
    records = {
        "legacy-1": _make_job("legacy-1", JobStatus.queued),
        "server-1": _make_job(
            "server-1", JobStatus.queued, source_root_key="filmler", source_path="clip.mp4"
        ),
        "archived-1": _make_job("archived-1", JobStatus.completed, archived=True),
    }

    monkeypatch.setattr(api, "job_repository", _FakeJobRepository(ordered_ids))
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    listed = api.list_jobs(response=Response(), source_type="server", limit=10)

    assert [job.id for job in listed] == ["server-1"]


def test_list_batches_returns_aggregate_counts_and_progress(monkeypatch: Any) -> None:
    ordered_ids = ["job-1", "job-2", "job-3"]
    records = {
        "job-1": _make_job("job-1", JobStatus.completed, batch_id="batch-a", progress_percent=100),
        "job-2": _make_job("job-2", JobStatus.running, batch_id="batch-a", progress_percent=50),
        "job-3": _make_job("job-3", JobStatus.failed, batch_id="batch-b", progress_percent=100),
    }

    monkeypatch.setattr(api, "job_repository", _FakeJobRepository(ordered_ids))
    monkeypatch.setattr(api, "_get_job_record", lambda job_id: records.get(job_id))

    result = api.list_batches(response=Response())

    by_id = {batch.batch_id: batch for batch in result.batches}
    assert by_id["batch-a"].total == 2
    assert by_id["batch-a"].completed == 1
    assert by_id["batch-a"].running == 1
    assert by_id["batch-a"].progress_percent == 75
