"""P1 behavior regressions: persisted outcomes, subtitle selection, hardware and dispatch."""
from __future__ import annotations

import errno
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from video_converter.core.config import WORKER_HEARTBEAT_KEY
from video_converter.core.job_repository import JobRepository
from video_converter.core.models import JobRecord, JobStatus, RetrySettings, now_iso
from video_converter.core.storage import LocalFileStore
from video_converter.worker import main as worker


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    repo = JobRepository(store)
    settings = worker.settings.model_copy(update={"data_root": tmp_path, "min_free_disk_bytes": 0, "worker_concurrency": 2})
    for directory in (settings.input_dir, settings.temp_dir, settings.outputs_dir, settings.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (settings.input_dir / "clip.mkv").write_bytes(b"media-boundary")
    monkeypatch.setattr(worker, "settings", settings)
    monkeypatch.setattr(worker, "storage_client", store)
    monkeypatch.setattr(worker, "job_repository", repo)
    monkeypatch.setattr(worker, "_shutdown", worker._ShutdownManager())
    monkeypatch.setattr(worker, "_hardware_encoders", [])
    monkeypatch.setattr(worker, "_probe_duration_seconds", lambda _path: 1.0)
    monkeypatch.setattr(worker, "_probe_video_codec", lambda _path: "vp9")
    monkeypatch.setattr(worker, "_probe_audio_codec", lambda _path: "aac")
    yield store, repo, settings
    worker._shutdown.request_shutdown()
    store.close()


def enqueue(repo, **options):
    timestamp = now_iso()
    record = JobRecord(id="p1-job", status=JobStatus.queued, input_filename="clip.mkv", profile="h264_mp4",
                       created_at=timestamp, updated_at=timestamp, **options)
    repo.enqueue(record)
    repo.dequeue(timeout=1)
    return record


def video_success(_job, command, **_kwargs):
    Path(command[-1]).write_bytes(b"completed-video")
    return 0, ""


@pytest.mark.parametrize("retry", [None, {}])
@pytest.mark.parametrize("failure", ["missing", "bad", "transient"])
def test_retry_null_or_missing_never_leaves_running(runtime, monkeypatch, retry, failure):
    store, repo, settings = runtime
    store.set("system:settings", json.dumps({"retry": retry} if retry is None else {}))
    record = enqueue(repo)
    if failure == "missing":
        (settings.input_dir / "clip.mkv").unlink()
    else:
        monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", lambda *_a, **_kw: (
            1, "Resource temporarily unavailable" if failure == "transient" else "Invalid data found when processing input"
        ))
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == (JobStatus.queued if failure == "transient" else JobStatus.failed)
    assert result.progress_phase == ("retry_wait" if failure == "transient" else "failed")
    assert result.next_retry_at if failure == "transient" else result.error_message
    assert result.status != JobStatus.running
    if failure == "transient":
        from datetime import datetime
        delay = (datetime.fromisoformat(result.next_retry_at) - datetime.fromisoformat(result.updated_at)).total_seconds()
        assert delay == pytest.approx(RetrySettings().initial_backoff_seconds, abs=1)


STREAMS = [
    {"index": 7, "codec": "subrip", "language": "eng"},
    {"index": 3, "codec": "subrip", "language": "eng"},
    {"index": 9, "codec": "hdmv_pgs_subtitle", "language": "tur"},
]


def test_numeric_selection_precedence_order_and_bitmap_policy():
    selected, warnings = worker._select_subtitle_streams(STREAMS, [7, 3, 7, 99], "tur", "embedded", "mp4")
    assert [s["index"] for s in selected] == [7, 3]
    assert warnings == ["Requested subtitle index 99 was not found"]
    selected, warnings = worker._select_subtitle_streams(STREAMS, None, "eng", "embedded", "mp4")
    assert [s["index"] for s in selected] == [3, 7] and not warnings
    selected, warnings = worker._select_subtitle_streams(STREAMS, None, None, "embedded", "mp4")
    assert [s["index"] for s in selected] == [3] and not warnings
    selected, warnings = worker._select_subtitle_streams(STREAMS, [9], None, "embedded", "mkv")
    assert [s["index"] for s in selected] == [9] and not warnings
    for mode, container in [("embedded", "mp4"), ("embedded", "webm"), ("separate_srt", "mkv")]:
        selected, warnings = worker._select_subtitle_streams(STREAMS, [9], None, mode, container)
        assert not selected and "cannot be exported" in warnings[0]


@pytest.mark.parametrize("streams,language,indexes,warning", [
    (STREAMS, "jpn", None, "language jpn"),
    (None, "eng", None, "probe failed"),
    ([], None, None, "No subtitle"),
    (STREAMS, None, [9], "cannot be exported"),
])
def test_missing_subtitles_persist_completed_warning(runtime, monkeypatch, streams, language, indexes, warning):
    _store, repo, settings = runtime
    monkeypatch.setattr(worker, "_probe_subtitle_streams", lambda _path: streams)
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", video_success)
    record = enqueue(repo, subtitle_export="embedded", subtitle_language=language, subtitle_stream_indexes=indexes)
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.completed
    assert warning in result.warnings[0]
    assert result.progress_message == "Conversion completed with warnings"
    assert (settings.outputs_dir / result.output_filename).read_bytes() == b"completed-video"


@pytest.mark.parametrize("sidecar_failure", ["exit", "missing", "executable", "cancelled"])
def test_srt_failure_keeps_published_video(runtime, monkeypatch, sidecar_failure):
    _store, repo, settings = runtime
    monkeypatch.setattr(worker, "_probe_subtitle_streams", lambda _path: STREAMS)
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", video_success)
    def sidecar(_job_id, _input, indexes, targets):
        assert len(indexes) == len(targets) == 1
        assert indexes == [3]
        assert (settings.outputs_dir / worker._build_output_path(settings.input_dir / "clip.mkv", "mp4", "p1-job").name).exists()
        if sidecar_failure == "cancelled":
            raise worker._SubtitleExtractionCancelled("SRT export cancelled")
        if sidecar_failure == "executable":
            raise FileNotFoundError("ffmpeg unavailable")
        if sidecar_failure == "exit":
            raise RuntimeError("subtitle failure")
        # "missing": the pass "succeeded" but produced no usable SRT.
    monkeypatch.setattr(worker, "_extract_subtitle_sidecars", sidecar)
    record = enqueue(repo, subtitle_export="separate_srt", subtitle_stream_indexes=[3])
    worker.process_job(record.id)
    result = repo.get(record.id)
    if sidecar_failure == "cancelled":
        assert result.status == JobStatus.cancelled
    else:
        assert result.status == JobStatus.completed and "SRT export failed" in result.warnings[0]
        assert (settings.outputs_dir / result.output_filename).read_bytes() == b"completed-video"
    assert list(settings.temp_dir.iterdir()) == []


class _FakeProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.terminated = False

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


class _StuckProc(_FakeProc):
    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            return self.returncode
        raise subprocess.TimeoutExpired("ffmpeg", timeout)


def test_extract_subtitle_sidecars_uses_one_ffmpeg_pass(runtime, monkeypatch):
    _store, _repo, settings = runtime
    captured: dict[str, list[str]] = {}

    def fake_popen(command, **kwargs):  # noqa: ARG001
        captured["command"] = list(command)
        return _FakeProc(0)

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker, "_is_cancel_requested", lambda _job_id: False)
    worker._extract_subtitle_sidecars(
        "job",
        Path("input.mkv"),
        [2, 4],
        [settings.temp_dir / "a.srt", settings.temp_dir / "b.srt"],
    )

    command = captured["command"]
    maps = [command[index + 1] for index, part in enumerate(command) if part == "-map"]
    assert maps == ["0:2", "0:4"]
    assert command.count("ffmpeg") == 1


def test_extract_subtitle_sidecars_times_out(runtime, monkeypatch):
    _store, _repo, _settings = runtime
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *_a, **_kw: _StuckProc())
    monkeypatch.setattr(worker, "_is_cancel_requested", lambda _job_id: False)
    ticks = iter([0.0, 10_000.0])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))

    with pytest.raises(RuntimeError, match="timed out"):
        worker._extract_subtitle_sidecars("job", Path("input.mkv"), [2], [Path("out.srt")])


def test_extract_subtitle_sidecars_honours_cancel(runtime, monkeypatch):
    _store, _repo, _settings = runtime
    monkeypatch.setattr(worker, "_is_cancel_requested", lambda _job_id: True)

    with pytest.raises(worker._SubtitleExtractionCancelled):
        worker._extract_subtitle_sidecars("job", Path("input.mkv"), [2], [Path("out.srt")])


@pytest.mark.parametrize("fallback", ["hardware", "copy"])
def test_fallback_preserves_numeric_subtitles(runtime, monkeypatch, fallback):
    _store, repo, _settings = runtime
    monkeypatch.setattr(worker, "_probe_subtitle_streams", lambda _path: STREAMS)
    monkeypatch.setattr(worker, "_hardware_encoders", ["h264_v4l2m2m"])
    monkeypatch.setattr(worker, "_probe_video_codec", lambda _path: "h264" if fallback == "copy" else "vp9")
    commands = []
    def run(job_id, command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            return 1, "encode unavailable"
        return video_success(job_id, command, **kwargs)
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", run)
    record = enqueue(repo, subtitle_export="embedded", subtitle_stream_indexes=[7, 3, 7], subtitle_language="tur")
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.completed and result.hardware_acceleration_used is None
    assert len(commands) == 2
    assert commands[0][commands[0].index("-c:v") + 1] == ("copy" if fallback == "copy" else "h264_v4l2m2m")
    assert commands[1][commands[1].index("-c:v") + 1] == "libx264"
    for command in commands:
        assert [command[i + 1] for i, part in enumerate(command) if part == "-map"] == ["0:v:0", "0:a:0?", "0:7", "0:3"]
        assert not any("language:" in part for part in command)


def test_explicit_video_copy_ignores_encoder_settings(runtime, monkeypatch):
    _store, repo, _settings = runtime
    commands = []

    def run(job_id, command, **kwargs):
        commands.append(command)
        return video_success(job_id, command, **kwargs)

    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", run)
    record = enqueue(
        repo,
        copy_video=True,
        quality_crf=20,
        target_video_bitrate="4M",
        resolution="1080p",
        hardware_acceleration="v4l2m2m",
    )
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.completed, result.error_message
    assert result.warnings and "copy" in result.warnings[0].lower()
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("-c:v") + 1] == "copy"
    assert "-crf" not in command
    assert "-b:v" not in command
    assert "-vf" not in command


def test_explicit_video_copy_failure_does_not_reencode(runtime, monkeypatch):
    _store, repo, _settings = runtime
    commands = []

    def run(*args, **_kwargs):
        commands.append(args)
        return 1, "Video codec not supported in MP4"

    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", run)
    record = enqueue(repo, copy_video=True)
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.failed
    assert len(commands) == 1
    assert "Video copy (remux) failed" in result.error_message


@pytest.mark.parametrize("quality,available,expected", [(0, True, "cannot honor CRF"), (None, False, "unavailable"), (None, True, "V4L2 encoding failed")])
def test_explicit_hardware_never_silently_falls_back(runtime, monkeypatch, quality, available, expected):
    _store, repo, _settings = runtime
    monkeypatch.setattr(worker, "_hardware_encoders", ["h264_v4l2m2m"] if available else [])
    calls = []
    def run(*args, **_kwargs):
        calls.append(args)
        return 1, "driver failure"
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", run)
    record = enqueue(repo, hardware_acceleration="v4l2m2m", quality_crf=quality)
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.failed and expected in result.error_message
    assert len(calls) == (1 if available and quality is None else 0)


@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
def test_hardware_failure_does_not_fallback_during_stop(runtime, monkeypatch, stop):
    _store, repo, _settings = runtime
    monkeypatch.setattr(worker, "_hardware_encoders", ["h264_v4l2m2m"])
    calls = []
    def run(job_id, *_args, **_kwargs):
        calls.append(job_id)
        if stop == "shutdown":
            worker._shutdown.request_shutdown()
        else:
            current = repo.get(job_id)
            current.cancel_requested = True
            repo.persist(current)
        return 130, "interrupted"
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", run)
    record = enqueue(repo)
    worker.process_job(record.id)
    assert len(calls) == 1
    assert repo.get(record.id).status == (JobStatus.queued if stop == "shutdown" else JobStatus.cancelled)


def test_hardware_capability_requires_successful_encode(monkeypatch):
    def probe(command, **kwargs):
        assert "-encoders" not in command and "-frames:v" in command
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(command, 0 if "h264_v4l2m2m" in command else 1, stdout="hevc_v4l2m2m")
    monkeypatch.setattr(worker.subprocess, "run", probe)
    assert worker._probe_hardware_encoders() == ["h264_v4l2m2m"]
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(worker.subprocess, "run", timeout)
    assert worker._probe_hardware_encoders() == []


@pytest.mark.parametrize("profile,container", [("h264_mp4", "mp4"), ("h265_mp4", "mkv"), ("vp9_webm", "webm")])
def test_decoder_filter_and_encoder_thread_scopes(profile, container):
    command = worker._ffmpeg_command(Path("input.mkv"), Path(f"output.{container}"), profile, container, "opus" if container == "webm" else "aac", "none", None, ffmpeg_threads=2, resolution="480p")
    positions = [i for i, part in enumerate(command) if part == "-threads"]
    assert positions[0] < command.index("-i") < positions[1]
    assert [command[i + 1] for i in positions] == ["2", "2"]
    assert command[command.index("-filter_threads") + 1] == "2"
    assert command[command.index("-filter_complex_threads") + 1] == "2"
    if profile == "h265_mp4":
        assert command[command.index("-x265-params") + 1] == "pools=2:frame-threads=2"


def test_restored_concurrency_cannot_exceed_environment(runtime, monkeypatch):
    store, _repo, settings = runtime
    monkeypatch.setattr(worker, "settings", settings.model_copy(update={"worker_concurrency": 1}))
    for value in [8, 99, None]:
        store.set("system:settings", json.dumps({"worker_concurrency": value}))
        assert worker._get_dynamic_concurrency() == 1
    store.delete("system:settings")
    assert worker._get_dynamic_concurrency() == 1


def test_lowering_dispatch_keeps_running_jobs_and_blocks_new(runtime, monkeypatch):
    store, repo, _settings = runtime
    started = [threading.Event() for _ in range(3)]
    release = [threading.Event() for _ in range(3)]
    finished = [threading.Event() for _ in range(3)]
    timestamp = now_iso()
    for i in range(3):
        repo.enqueue(JobRecord(id=str(i), status=JobStatus.queued, input_filename="clip.mkv", profile="h264_mp4", created_at=timestamp, updated_at=timestamp))
    def process(job_id):
        index = int(job_id)
        started[index].set()
        try:
            assert release[index].wait(10)
        finally:
            finished[index].set()
    monkeypatch.setattr(worker, "process_job", process)
    with ThreadPoolExecutor(max_workers=1) as executor:
        loop = executor.submit(worker._run_concurrent, 8)
        try:
            assert started[0].wait(5) and started[1].wait(5)
            heartbeat = json.loads(store.get(WORKER_HEARTBEAT_KEY))
            assert heartbeat["worker_concurrency_limit"] == 2
            assert heartbeat["effective_worker_concurrency"] == 2
            store.set("system:settings", '{"worker_concurrency": 1}')
            release[0].set()
            assert finished[0].wait(5)
            assert not started[2].wait(0.8)
            assert not finished[1].is_set()
            release[1].set()
            assert started[2].wait(5)
        finally:
            worker._shutdown.request_shutdown()
            for event in release:
                event.set()
            loop.result(timeout=10)


@pytest.mark.parametrize("audio", ["aac", "mp3"])
def test_legacy_webm_explicit_audio_is_rejected(audio):
    with pytest.raises(ValueError, match="WebM supports Opus"):
        worker._resolve_export_options({"video_export": "webm", "audio_export": audio})


def test_fresh_attempt_clears_stale_warning_and_hardware(runtime, monkeypatch):
    _store, repo, _settings = runtime
    record = enqueue(repo, warnings=["Prior attempt subtitle failure"], hardware_acceleration_used="h264_v4l2m2m")
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", video_success)
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.completed
    assert not result.warnings and result.hardware_acceleration_used is None
    assert result.progress_message == "Conversion completed"


def test_explicit_hardware_copy_eligible_source_still_encodes(runtime, monkeypatch):
    _store, repo, _settings = runtime
    monkeypatch.setattr(worker, "_probe_video_codec", lambda _path: "h264")
    monkeypatch.setattr(worker, "_hardware_encoders", ["h264_v4l2m2m"])
    commands = []
    def run(job_id, command, **kwargs):
        commands.append(command)
        return video_success(job_id, command, **kwargs)
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", run)
    record = enqueue(repo, hardware_acceleration="v4l2m2m")
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.completed
    assert result.hardware_acceleration_used == "h264_v4l2m2m"
    assert commands[0][commands[0].index("-c:v") + 1] == "h264_v4l2m2m"


def test_output_settings_default_to_app_outputs_without_deletion():
    resolved = worker._resolve_output_settings({})
    assert resolved.location == worker.OutputLocation.outputs_dir
    assert resolved.delete_source_after_success is False


def test_temp_output_stays_beside_source_in_source_dir_mode(tmp_path):
    output = tmp_path / "clip.12345678.mp4"
    temp = worker._build_temp_output_path(
        output, "job-id", worker.OutputLocation.source_dir
    )
    assert temp.parent == tmp_path
    assert temp.suffix == ".mp4"
    assert temp.name.startswith(".bvc-")

    staged = worker._build_temp_output_path(
        output, "job-id", worker.OutputLocation.outputs_dir
    )
    assert staged.parent == worker.settings.temp_dir


def test_output_directory_write_probe_cleans_up(tmp_path):
    worker._ensure_output_directory_writable(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_output_directory_write_probe_reports_read_only(tmp_path, monkeypatch):
    def read_only_mkstemp(*_args, **_kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(worker.tempfile, "mkstemp", read_only_mkstemp)
    with pytest.raises(RuntimeError) as exc_info:
        worker._ensure_output_directory_writable(tmp_path, source_dir=True)
    message = str(exc_info.value)
    assert "not writable" in message
    assert "MEDIA_*_READ_ONLY=false" in message


def test_atomic_move_cross_device_publishes_complete_file(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination_dir = tmp_path / "destination"
    destination_dir.mkdir()
    destination = destination_dir / "output.mp4"
    payload = b"converted-video" * 1024
    source.write_bytes(payload)

    real_replace = worker.os.replace
    replace_calls = {"count": 0}

    def flaky_replace(src, dst):
        if Path(src) == source and replace_calls["count"] == 0:
            replace_calls["count"] += 1
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(worker.os, "replace", flaky_replace)
    worker._atomic_move(source, destination)

    assert destination.read_bytes() == payload
    assert not source.exists()
    assert not list(destination_dir.glob(".bvc-publish-*"))


def test_source_deletion_requires_a_published_output(tmp_path):
    source = tmp_path / "clip.mkv"
    source.write_bytes(b"media")
    with pytest.raises(RuntimeError, match="missing"):
        worker._delete_source_after_success(source, tmp_path / "missing.mp4")
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError, match="empty"):
        worker._delete_source_after_success(source, empty)
    assert source.exists()


@pytest.mark.parametrize("delete_source", [False, True])
def test_source_dir_output_and_optional_source_delete(tmp_path, monkeypatch, delete_source):
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    repo = JobRepository(store)
    media_root = tmp_path / "media"
    media_root.mkdir()
    source = media_root / "clip.mkv"
    source.write_bytes(b"media-boundary")
    settings = worker.settings.model_copy(
        update={
            "data_root": tmp_path / "data",
            "media_mounts": f"Media={media_root}",
            "min_free_disk_bytes": 0,
        }
    )
    for directory in (settings.input_dir, settings.temp_dir, settings.outputs_dir, settings.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(worker, "settings", settings)
    monkeypatch.setattr(worker, "storage_client", store)
    monkeypatch.setattr(worker, "job_repository", repo)
    monkeypatch.setattr(worker, "_shutdown", worker._ShutdownManager())
    monkeypatch.setattr(worker, "_hardware_encoders", [])
    monkeypatch.setattr(worker, "_probe_duration_seconds", lambda _path: 1.0)
    monkeypatch.setattr(worker, "_probe_video_codec", lambda _path: "vp9")
    monkeypatch.setattr(worker, "_probe_audio_codec", lambda _path: "aac")
    ffmpeg_output_paths: list[Path] = []

    def source_dir_video_success(job, command, **kwargs):
        ffmpeg_output_paths.append(Path(command[-1]))
        return video_success(job, command, **kwargs)

    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", source_dir_video_success)
    with worker._runtime_settings_cache_lock:
        worker._runtime_settings_cache.clear()
    store.set(
        "system:settings",
        json.dumps(
            {
                "output": {
                    "location": "source_dir",
                    "delete_source_after_success": delete_source,
                }
            }
        ),
    )
    timestamp = now_iso()
    record = JobRecord(
        id="source-dir",
        status=JobStatus.queued,
        profile="h264_mp4",
        source_root_key="media",
        source_path="clip.mkv",
        created_at=timestamp,
        updated_at=timestamp,
    )
    repo.enqueue(record)
    repo.dequeue(timeout=1)
    try:
        worker.process_job(record.id)
        result = repo.get(record.id)
        assert result.status == JobStatus.completed, result.error_message
        assert result.output_location == worker.OutputLocation.source_dir
        assert ffmpeg_output_paths
        assert ffmpeg_output_paths[0].parent == media_root
        assert ffmpeg_output_paths[0].name.startswith(".bvc-")
        output = media_root / f"clip.{record.id[:8]}.mp4"
        assert output.read_bytes() == b"completed-video"
        assert source.exists() is (not delete_source)
    finally:
        store.close()
