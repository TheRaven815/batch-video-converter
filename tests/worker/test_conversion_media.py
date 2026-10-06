"""Consumer-visible conversion checks using real FFmpeg/ffprobe when installed."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from video_converter.worker import main as worker

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Real media regressions require FFmpeg and ffprobe",
)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)


def _streams(path: Path) -> list[dict]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)["streams"]


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("conversion-media") / "source.mkv"
    _run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=960x540:rate=10",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=5.1:sample_rate=48000",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "1", "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-c:a", "flac",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=tur", str(path),
    ])
    return path


def test_resolution_and_selected_audio_metadata(source, tmp_path) -> None:
    output = tmp_path / "selected.mp4"
    cmd = worker._ffmpeg_command(
        source, output, "h264_mp4", "mp4", "aac", "none", None,
        prefer_stream_copy_video=True, resolution="480p", quality_crf=0,
        audio_stream_indexes=[2, 1], audio_codec_map={1: "flac", 2: "flac"},
        audio_channels_map={1: 6, 2: 1}, audio_bitrate_kbps=160,
    )
    _run(cmd)
    streams = _streams(output)
    video = next(s for s in streams if s["codec_type"] == "video")
    audios = [s for s in streams if s["codec_type"] == "audio"]
    assert (video["width"], video["height"]) == (854, 480)
    assert [s["codec_name"] for s in audios] == ["aac", "aac"]
    assert [s["tags"]["language"] for s in audios] == ["tur", "eng"]
    assert [s["channels"] for s in audios] == [1, 6]


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("container,encoder", [("mp4", "aac"), ("mkv", "aac"), ("webm", "opus")])
def test_copy_downmix_real_channels(source, tmp_path, selected, container, encoder) -> None:
    output = tmp_path / f"stereo.{container}"
    cmd = worker._ffmpeg_command(
        source, output, "vp9_webm" if container == "webm" else "h264_mp4",
        container, "copy", "none", None,
        prefer_stream_copy_video=container != "webm",
        audio_stream_indexes=[1] if selected else None,
        audio_codec_map={1: "flac"}, audio_channels_map={1: 6},
        audio_channel_mode="downmix2", audio_bitrate_kbps=160,
    )
    _run(cmd)
    audio = next(s for s in _streams(output) if s["codec_type"] == "audio")
    assert audio["codec_name"] == encoder
    assert audio["channels"] == 2


@pytest.mark.parametrize("selected", [False, True])
def test_mp3_requested_bitrate(source, tmp_path, selected) -> None:
    output = tmp_path / "bitrate.mp4"
    _run(worker._ffmpeg_command(
        source, output, "h264_mp4", "mp4", "mp3", "none", None,
        prefer_stream_copy_video=True, audio_bitrate_kbps=160,
        audio_stream_indexes=[2] if selected else None,
        audio_codec_map={2: "flac"}, audio_channels_map={2: 1},
        audio_channel_mode="downmix2",
    ))
    audio = next(s for s in _streams(output) if s["codec_type"] == "audio")
    assert audio["codec_name"] == "mp3"
    # MP4's short-stream average includes encoder padding; still reject 192k fallback.
    assert int(audio["bit_rate"]) == pytest.approx(160000, rel=0.02)


def test_no_audio_remains_optional(source, tmp_path) -> None:
    silent = tmp_path / "silent.mkv"
    _run(["ffmpeg", "-y", "-i", str(source), "-map", "0:v:0", "-c:v", "copy", str(silent)])
    output = tmp_path / "silent.mp4"
    _run(worker._ffmpeg_command(
        silent, output, "h264_mp4", "mp4", "copy", "embedded", None,
        prefer_stream_copy_video=True, audio_channel_mode="downmix2",
    ))
    assert [s["codec_type"] for s in _streams(output)] == ["video"]


@pytest.mark.parametrize("quality", [None, 0])
def test_process_job_preserves_omitted_and_zero_quality(source, tmp_path, monkeypatch, quality) -> None:
    from video_converter.core.job_repository import JobRepository
    from video_converter.core.models import JobCreateRequest, JobRecord, JobStatus, now_iso
    from video_converter.core.storage import LocalFileStore

    store = LocalFileStore(tmp_path / "queue.sqlite3")
    repo = JobRepository(store)
    settings = worker.settings.model_copy(update={"data_root": tmp_path, "min_free_disk_bytes": 0})
    monkeypatch.setattr(worker, "settings", settings)
    monkeypatch.setattr(worker, "job_repository", repo)
    monkeypatch.setattr(worker, "storage_client", store)
    monkeypatch.setattr(worker, "_hardware_encoders", ["h264_v4l2m2m"])
    for directory in (settings.input_dir, settings.temp_dir, settings.outputs_dir, settings.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, settings.input_dir / "source.mkv")
    payload = JobCreateRequest(
        input_filename="source.mkv", hardware_acceleration="auto",
        **({"quality_crf": 0} if quality == 0 else {}),
    )
    assert payload.quality_crf == quality
    timestamp = now_iso()
    job_id = "zero-quality" if quality == 0 else "legacy-copy"
    record = JobRecord(
        id=job_id, status=JobStatus.queued, created_at=timestamp, updated_at=timestamp,
        **payload.model_dump(exclude={"quality_crf"} if quality is None else set()),
    )
    repo.enqueue(record)
    try:
        worker.process_job(job_id)
        updated = repo.get(job_id)
        assert updated is not None
        assert updated.status == JobStatus.completed, updated.error_message
        assert updated.quality_crf == quality
        if quality == 0:
            assert updated.hardware_acceleration_used is None
        output = settings.outputs_dir / updated.output_filename
        def video_digest(path):
            if quality == 0:
                cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "md5", "-"]
                return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30).stdout
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
                   "-show_data_hash", "sha256", "-of", "json", str(path)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            return [packet["data_hash"] for packet in json.loads(result.stdout)["packets"]]
        assert video_digest(output) == video_digest(source)
        assert next(s for s in _streams(output) if s["codec_type"] == "video")["codec_name"] == "h264"
    finally:
        store.close()


@pytest.fixture(scope="module")
def subtitle_source(source, tmp_path_factory):
    directory = tmp_path_factory.mktemp("subtitle-media")
    files = []
    for index in range(3):
        subtitle = directory / f"track{index}.srt"
        subtitle.write_text(f"1\n00:00:00,000 --> 00:00:00,800\nTrack {index}\n", encoding="utf-8")
        files.append(subtitle)
    output = directory / "subtitles.mkv"
    _run([
        "ffmpeg", "-y", "-i", str(source),
        "-i", str(files[0]), "-i", str(files[1]), "-i", str(files[2]),
        "-map", "0:v:0", "-map", "0:a:0", "-map", "1:s", "-map", "2:s", "-map", "3:s",
        "-c", "copy", "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=eng",
        "-metadata:s:s:2", "language=tur", str(output),
    ])
    return output


@pytest.mark.parametrize("container,mode,language,indexes,expected,warning", [
    ("mp4", "embedded", "eng", None, ["eng", "eng"], False),
    ("mkv", "embedded", "eng", None, ["eng", "eng"], False),
    ("webm", "embedded", "eng", None, ["eng", "eng"], False),
    ("mp4", "embedded", "eng", [4, 2, 4], ["tur", "eng"], False),
    ("mp4", "embedded", None, None, ["eng"], False),
    ("mp4", "embedded", "jpn", None, [], True),
    ("mkv", "separate_srt", "eng", None, ["Track 0", "Track 1"], False),
    ("mp4", "separate_srt", "eng", None, ["Track 0", "Track 1"], False),
    ("webm", "separate_srt", "eng", None, ["Track 0", "Track 1"], False),
    ("mp4", "separate_srt", "eng", [4, 2, 4], ["Track 2", "Track 0"], False),
    ("webm", "separate_srt", "jpn", None, [], True),
])
def test_process_numeric_subtitles_real_media(subtitle_source, tmp_path, monkeypatch, container, mode, language, indexes, expected, warning):
    from video_converter.core.job_repository import JobRepository
    from video_converter.core.models import JobRecord, JobStatus, now_iso
    from video_converter.core.storage import LocalFileStore

    store = LocalFileStore(tmp_path / "queue.sqlite3")
    repo = JobRepository(store)
    settings = worker.settings.model_copy(update={"data_root": tmp_path, "min_free_disk_bytes": 0})
    monkeypatch.setattr(worker, "settings", settings)
    monkeypatch.setattr(worker, "job_repository", repo)
    monkeypatch.setattr(worker, "storage_client", store)
    monkeypatch.setattr(worker, "_shutdown", worker._ShutdownManager())
    for directory in (settings.input_dir, settings.temp_dir, settings.outputs_dir, settings.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(subtitle_source, settings.input_dir / "subtitles.mkv")
    timestamp = now_iso()
    record = JobRecord(id="subtitle-media", status=JobStatus.queued, input_filename="subtitles.mkv", profile="h264_mp4",
                       video_export=container, audio_export="opus" if container == "webm" else "aac",
                       subtitle_export=mode, subtitle_language=language, subtitle_stream_indexes=indexes,
                       hardware_acceleration="disabled", created_at=timestamp, updated_at=timestamp)
    repo.enqueue(record)
    repo.dequeue(timeout=1)
    try:
        worker.process_job(record.id)
        result = repo.get(record.id)
        assert result.status == JobStatus.completed, result.error_message
        assert bool(result.warnings) == warning
        output = settings.outputs_dir / result.output_filename
        streams = _streams(output)
        subs = [stream for stream in streams if stream["codec_type"] == "subtitle"]
        if mode == "embedded":
            assert [stream.get("tags", {}).get("language") for stream in subs] == expected
        else:
            assert not subs
            sidecars = list(settings.outputs_dir.glob("*.srt"))
            assert len(sidecars) == len(expected)
            content = "\n".join(sidecar.read_text(encoding="utf-8") for sidecar in sidecars)
            assert all(text in content for text in expected)
            if indexes:
                assert "Track 1" not in content
        _run(["ffmpeg", "-v", "error", "-i", str(output), "-map", "0:v:0", "-f", "null", "-"])
        assert not list(settings.temp_dir.iterdir())
    finally:
        store.close()


def test_corrupt_media_with_null_retry_is_terminal(tmp_path, monkeypatch):
    from video_converter.core.job_repository import JobRepository
    from video_converter.core.models import JobRecord, JobStatus, now_iso
    from video_converter.core.storage import LocalFileStore

    store = LocalFileStore(tmp_path / "queue.sqlite3")
    repo = JobRepository(store)
    settings = worker.settings.model_copy(update={"data_root": tmp_path, "min_free_disk_bytes": 0})
    monkeypatch.setattr(worker, "settings", settings)
    monkeypatch.setattr(worker, "storage_client", store)
    monkeypatch.setattr(worker, "job_repository", repo)
    monkeypatch.setattr(worker, "_shutdown", worker._ShutdownManager())
    for directory in (settings.input_dir, settings.temp_dir, settings.outputs_dir, settings.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (settings.input_dir / "bad.mp4").write_bytes(b"not a media file")
    store.set("system:settings", '{"retry": null}')
    timestamp = now_iso()
    record = JobRecord(id="bad-media", status=JobStatus.queued, input_filename="bad.mp4", profile="h264_mp4",
                       hardware_acceleration="disabled", created_at=timestamp, updated_at=timestamp)
    repo.enqueue(record)
    repo.dequeue(timeout=1)
    try:
        worker.process_job(record.id)
        result = repo.get(record.id)
        assert result.status == JobStatus.failed and result.error_message
        assert result.next_retry_at is None
        assert not list(settings.outputs_dir.iterdir())
        assert not list(settings.temp_dir.iterdir())
    finally:
        store.close()


def test_bounded_x265_scaled_output_decodes(source, tmp_path):
    output = tmp_path / "threads.mkv"
    command = worker._ffmpeg_command(source, output, "h265_mp4", "mkv", "aac", "none", None,
                                     ffmpeg_threads=1, encoder_preset="ultrafast", resolution="480p")
    _run(command)
    video = next(stream for stream in _streams(output) if stream["codec_type"] == "video")
    assert video["codec_name"] == "hevc"
    assert (video["width"], video["height"]) == (854, 480)
    _run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"])
