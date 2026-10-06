from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from video_converter.core.job_repository import JobRepository
from video_converter.core.models import JobRecord, JobStatus, now_iso
from video_converter.core.storage import LocalFileStore
from video_converter.worker.main import (
    _audio_copy_fallback,
    _ffmpeg_command,
    _get_dynamic_concurrency,
    _is_transient_failure,
)


def _job(job_id: str = "retry-job") -> JobRecord:
    timestamp = now_iso()
    return JobRecord(
        id=job_id,
        status=JobStatus.running,
        profile="h264_mp4",
        input_filename="clip.mp4",
        created_at=timestamp,
        updated_at=timestamp,
        attempt_count=1,
        max_attempts=3,
    )


def test_quality_resolution_and_vp9_speed_options_are_applied() -> None:
    command = _ffmpeg_command(
        Path("/media/input.mkv"),
        Path("/data/output.webm"),
        profile="vp9_webm",
        video_export="webm",
        audio_export="opus",
        subtitle_export="none",
        subtitle_language=None,
        quality_crf=29,
        target_video_bitrate="2M",
        audio_bitrate_kbps=160,
        resolution="720p",
        encoder_preset="fast",
    )

    assert command[command.index("-c:v") + 1] == "libvpx-vp9"
    assert command[command.index("-row-mt") + 1] == "1"
    assert command[command.index("-b:v") + 1] == "2M"
    assert command[command.index("-b:a") + 1] == "160k"
    assert "scale=-2:'min(720,ih)'" in command


def test_v4l2_hardware_encoder_is_selected_when_requested() -> None:
    command = _ffmpeg_command(
        Path("/media/input.mkv"),
        Path("/data/output.mp4"),
        profile="h264_mp4",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
        hardware_encoder="h264_v4l2m2m",
    )
    assert command[command.index("-c:v") + 1] == "h264_v4l2m2m"


def test_retry_wait_is_not_enqueued_until_due(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    repository = JobRepository(store)
    record = _job()
    repository.enqueue(record)
    repository.acknowledge(record.id)
    repository.remove_from_queue(record.id)

    repository.schedule_retry(record, delay_seconds=30, reason="Resource temporarily unavailable")
    assert repository.queue_positions() == {}

    early = repository.requeue_due_retries(
        current_time=datetime.now(timezone.utc) + timedelta(seconds=10)
    )
    assert early == []

    due = repository.requeue_due_retries(
        current_time=datetime.now(timezone.utc) + timedelta(seconds=31)
    )
    assert [item.id for item in due] == [record.id]
    assert repository.queue_positions() == {record.id: 1}
    store.close()


def test_transient_failure_classification_is_conservative() -> None:
    assert _is_transient_failure("Device or resource busy")
    assert _is_transient_failure("network is unreachable")
    assert not _is_transient_failure("Invalid argument: codec is unsupported")


def test_mp4_audio_copy_falls_back_for_incompatible_codec() -> None:
    assert _audio_copy_fallback("copy", "mp4", "flac") == "aac"
    assert _audio_copy_fallback("copy", "mp4", "truehd") == "aac"
    assert _audio_copy_fallback("copy", "mp4", "aac") == "copy"
    assert _audio_copy_fallback("copy", "mkv", "flac") == "copy"


def test_dynamic_concurrency_is_clamped(monkeypatch) -> None:
    from video_converter.worker import main as worker

    monkeypatch.setattr(worker, "settings", worker.settings.model_copy(update={"worker_concurrency": 2}))
    monkeypatch.setattr(
        worker.storage_client,
        "get",
        lambda _key: '{"worker_concurrency": 999}',
    )
    assert _get_dynamic_concurrency() == 2

    monkeypatch.setattr(
        worker.storage_client,
        "get",
        lambda _key: '{"worker_concurrency": 0}',
    )
    assert _get_dynamic_concurrency() == 1
