"""P1 behavior regressions: persisted outcomes, subtitle selection, hardware and dispatch."""
from __future__ import annotations

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


@pytest.mark.parametrize("sidecar_failure", ["exit", "missing", "timeout", "executable"])
def test_srt_failure_keeps_published_video(runtime, monkeypatch, sidecar_failure):
    _store, repo, settings = runtime
    monkeypatch.setattr(worker, "_probe_subtitle_streams", lambda _path: STREAMS)
    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", video_success)
    def sidecar(command, **_kwargs):
        assert (settings.outputs_dir / worker._build_output_path(settings.input_dir / "clip.mkv", "mp4", "p1-job").name).exists()
        assert command[command.index("-map") + 1] == "0:3"
        if sidecar_failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1800)
        if sidecar_failure == "executable":
            raise FileNotFoundError("ffmpeg unavailable")
        return subprocess.CompletedProcess(command, 1 if sidecar_failure == "exit" else 0, stderr="subtitle failure")
    monkeypatch.setattr(worker.subprocess, "run", sidecar)
    record = enqueue(repo, subtitle_export="separate_srt", subtitle_stream_indexes=[3])
    worker.process_job(record.id)
    result = repo.get(record.id)
    assert result.status == JobStatus.completed and "SRT export failed" in result.warnings[0]
    assert (settings.outputs_dir / result.output_filename).read_bytes() == b"completed-video"
    assert list(settings.temp_dir.iterdir()) == []


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
