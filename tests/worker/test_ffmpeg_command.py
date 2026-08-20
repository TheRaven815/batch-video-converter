"""Unit tests for the _ffmpeg_command() function in worker/worker.py.

These tests exercise all code paths in FFmpeg command generation:
- Video codec selection (stream copy, H.264, H.265, VP9)
- Audio codec selection (copy, AAC, MP3, Opus, unknown fallback)
- Subtitle handling (embedded with/without language, separate_srt, none)
- Output format variations (mp4 faststart, mkv, webm)
- Edge cases (unknown profile, prefer_stream_copy priority)
"""

from __future__ import annotations

from pathlib import Path

from video_converter.worker.main import _ffmpeg_command

INPUT = Path("/media/input/sample_video.mkv")
OUTPUT_MP4 = Path("/data/outputs/sample_video.abc12345.mp4")
OUTPUT_MKV = Path("/data/outputs/sample_video.abc12345.mkv")
OUTPUT_WEBM = Path("/data/outputs/sample_video.abc12345.webm")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract(cmd: list[str], flag: str) -> str | None:
    """Return the value immediately following *flag* in *cmd*, or None."""
    for i, token in enumerate(cmd):
        if token == flag and i + 1 < len(cmd):
            return cmd[i + 1]
    return None


def _has_flag(cmd: list[str], flag: str) -> bool:
    return flag in cmd


# ---------------------------------------------------------------------------
# 1. Video codec – stream copy
# ---------------------------------------------------------------------------


def test_h264_stream_copy_when_prefer_stream_copy_true() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        prefer_stream_copy_video=True,
    )

    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert str(INPUT) in cmd
    assert _extract(cmd, "-c:v") == "copy"
    assert str(OUTPUT_MP4) == cmd[-1]


# ---------------------------------------------------------------------------
# 2. Video codec – H.264 encode
# ---------------------------------------------------------------------------


def test_h264_encode_when_stream_copy_false() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        prefer_stream_copy_video=False,
    )

    assert _extract(cmd, "-c:v") == "libx264"
    assert _extract(cmd, "-preset") == "veryfast"
    assert _extract(cmd, "-crf") == "23"


# ---------------------------------------------------------------------------
# 3. Video codec – H.265/HEVC
# ---------------------------------------------------------------------------


def test_h265_profile_mkv_output() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MKV,
        profile="h265_mp4",
        video_export="mkv",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
    )

    assert _extract(cmd, "-c:v") == "libx265"
    assert _extract(cmd, "-preset") == "medium"
    assert _extract(cmd, "-crf") == "28"
    # MKV should not have faststart
    assert not _has_flag(cmd, "-movflags")


# ---------------------------------------------------------------------------
# 4. Video codec – VP9 / WebM
# ---------------------------------------------------------------------------


def test_vp9_webm_profile_with_opus_audio() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="opus",
        subtitle_export="none",
        subtitle_language=None,
    )

    assert _extract(cmd, "-c:v") == "libvpx-vp9"
    assert _extract(cmd, "-crf") == "33"
    assert _extract(cmd, "-b:v") == "0"
    assert _extract(cmd, "-c:a") == "libopus"
    assert _extract(cmd, "-b:a") == "96k"
    # WebM should not have faststart
    assert not _has_flag(cmd, "-movflags")


# ---------------------------------------------------------------------------
# 5. Audio codec paths
# ---------------------------------------------------------------------------


def test_audio_copy() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert _extract(cmd, "-c:a") == "copy"


def test_audio_aac_encode() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert _extract(cmd, "-c:a") == "aac"
    assert _extract(cmd, "-b:a") == "128k"


def test_audio_mp3_encode() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="mp3",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert _extract(cmd, "-c:a") == "libmp3lame"
    assert _extract(cmd, "-b:a") == "192k"


def test_audio_opus_encode() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="opus",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert _extract(cmd, "-c:a") == "libopus"
    assert _extract(cmd, "-b:a") == "96k"


def test_unknown_audio_export_falls_back_to_aac() -> None:
    """An unrecognised audio_export value should fall back to AAC 128k."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="wav",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert _extract(cmd, "-c:a") == "aac"
    assert _extract(cmd, "-b:a") == "128k"


# ---------------------------------------------------------------------------
# 6. Subtitle handling
# ---------------------------------------------------------------------------


def test_subtitle_embedded_with_language() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MKV,
        profile="h265_mp4",
        video_export="mkv",
        audio_export="copy",
        subtitle_export="embedded",
        subtitle_language="eng",
    )

    assert _extract(cmd, "-c:s") == "copy"
    # Explicit stream selection: one video, one audio, matching subtitles.
    # A bare "-map 0" would also pull attachments/data streams.
    assert "0:v:0" in cmd
    assert "0:a:0?" in cmd
    assert "0:s:m:language:eng?" in cmd
    assert "0" not in cmd
    assert "-0:s" not in cmd


def test_subtitle_embedded_without_language() -> None:
    """Embedded subtitles without a language pick the first subtitle stream explicitly."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MKV,
        profile="h264_mp4",
        video_export="mkv",
        audio_export="aac",
        subtitle_export="embedded",
        subtitle_language=None,
    )

    assert _extract(cmd, "-c:s") == "copy"
    assert "0:v:0" in cmd
    assert "0:a:0?" in cmd
    assert "0:s:0?" in cmd


def test_subtitle_embedded_mp4_uses_mov_text() -> None:
    """MP4 cannot hold SRT/ASS; embedded subtitles must be transcoded to mov_text."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="embedded",
        subtitle_language="eng",
    )

    assert _extract(cmd, "-c:s") == "mov_text"


def test_subtitle_embedded_webm_uses_webvtt() -> None:
    """WebM only accepts WebVTT subtitle streams."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="opus",
        subtitle_export="embedded",
        subtitle_language=None,
    )

    assert _extract(cmd, "-c:s") == "webvtt"


def test_subtitle_none_produces_no_subtitle_flags() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
    )

    assert not _has_flag(cmd, "-c:s")
    assert "-sn" in cmd
    assert "copy" not in cmd or _extract(cmd, "-c:v") == "copy" or _extract(cmd, "-c:a") == "copy"


def test_subtitle_separate_srt_produces_no_subtitle_flags_in_main_command() -> None:
    """separate_srt is handled externally (process_job builds a second FFmpeg call);
    the main _ffmpeg_command should NOT include subtitle codec flags."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="separate_srt",
        subtitle_language="tur",
    )

    assert not _has_flag(cmd, "-c:s")
    assert "-sn" in cmd


# ---------------------------------------------------------------------------
# 7. Output format – movflags faststart
# ---------------------------------------------------------------------------


def test_mp4_output_gets_faststart() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert _has_flag(cmd, "-movflags")
    assert _extract(cmd, "-movflags") == "+faststart"


def test_mkv_output_no_faststart() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MKV,
        profile="h264_mp4",
        video_export="mkv",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert not _has_flag(cmd, "-movflags")


def test_webm_output_no_faststart() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="opus",
        subtitle_export="none",
        subtitle_language=None,
    )
    assert not _has_flag(cmd, "-movflags")


# ---------------------------------------------------------------------------
# 8. Unknown / fallback profile
# ---------------------------------------------------------------------------


def test_unknown_profile_defaults_to_h264() -> None:
    """A profile value not matching any known preset should use libx264."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="some_unknown_profile",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
    )

    assert _extract(cmd, "-c:v") == "libx264"
    assert _extract(cmd, "-preset") == "veryfast"
    assert _extract(cmd, "-crf") == "23"


# ---------------------------------------------------------------------------
# 9. prefer_stream_copy_video takes precedence over profile
# ---------------------------------------------------------------------------


def test_stream_copy_overrides_h265_profile() -> None:
    """Even with h265 profile, prefer_stream_copy_video=True should produce -c:v copy."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MKV,
        profile="h265_mp4",
        video_export="mkv",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
        prefer_stream_copy_video=True,
    )

    assert _extract(cmd, "-c:v") == "copy"


def test_stream_copy_overrides_vp9_profile() -> None:
    """Even with vp9_webm profile, prefer_stream_copy_video=True should produce -c:v copy."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="opus",
        subtitle_export="none",
        subtitle_language=None,
        prefer_stream_copy_video=True,
    )

    assert _extract(cmd, "-c:v") == "copy"


# ---------------------------------------------------------------------------
# 10. Combined scenarios – custom export options
# ---------------------------------------------------------------------------


def test_h265_mkv_with_mp3_and_embedded_subtitles() -> None:
    """A realistic combined scenario: H.265 MKV, MP3 audio, embedded subs with language."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MKV,
        profile="h265_mp4",
        video_export="mkv",
        audio_export="mp3",
        subtitle_export="embedded",
        subtitle_language="fre",
    )

    assert _extract(cmd, "-c:v") == "libx265"
    assert _extract(cmd, "-c:a") == "libmp3lame"
    assert _extract(cmd, "-b:a") == "192k"
    assert _extract(cmd, "-c:s") == "copy"
    assert "0:s:m:language:fre?" in cmd
    assert not _has_flag(cmd, "-movflags")


def test_h264_mp4_with_aac_and_no_subtitles() -> None:
    """Default/common scenario: H.264 MP4, AAC audio, no subtitles."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
        prefer_stream_copy_video=False,
    )

    assert _extract(cmd, "-c:v") == "libx264"
    assert _extract(cmd, "-c:a") == "aac"
    assert _extract(cmd, "-b:a") == "128k"
    assert not _has_flag(cmd, "-c:s")
    assert _extract(cmd, "-movflags") == "+faststart"


def test_vp9_webm_with_copy_audio_forced_to_still_appear_in_cmd() -> None:
    """When audio_export is 'copy' (even though _resolve_export_options normalises it
    to 'opus' for webm), _ffmpeg_command faithfully renders -c:a copy.
    The normalisation happens upstream; _ffmpeg_command itself does not override."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
    )

    assert _extract(cmd, "-c:v") == "libvpx-vp9"
    # _ffmpeg_command doesn't normalise; it renders what it receives
    assert _extract(cmd, "-c:a") == "copy"


# ---------------------------------------------------------------------------
# 11. Command structure invariants
# ---------------------------------------------------------------------------


def test_command_always_starts_with_ffmpeg_y_i() -> None:
    """Every generated command must begin with ffmpeg -y -i <input>."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
    )

    assert cmd[0] == "ffmpeg"
    assert cmd[1] == "-y"
    assert cmd[2] == "-i"
    assert cmd[3] == str(INPUT)


def test_command_always_ends_with_output_path() -> None:
    """The output path is always the last element."""
    for out in (OUTPUT_MP4, OUTPUT_MKV, OUTPUT_WEBM):
        cmd = _ffmpeg_command(
            INPUT,
            out,
            profile="h264_mp4",
            video_export="mp4",
            audio_export="copy",
            subtitle_export="none",
            subtitle_language=None,
        )
        assert cmd[-1] == str(out)


def test_ffmpeg_threads_are_bounded_and_rendered() -> None:
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
        ffmpeg_threads=4,
    )

    assert _extract(cmd, "-threads") == "4"

    bounded = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="aac",
        subtitle_export="none",
        subtitle_language=None,
        ffmpeg_threads=100,
    )
    assert _extract(bounded, "-threads") == "32"


def test_input_path_not_duplicated_at_end() -> None:
    """Ensure input path only appears once (after -i)."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
    )

    assert cmd.count(str(INPUT)) == 1


# ---------------------------------------------------------------------------
# 12. Multi-audio (Faz 2) – copy compatible codecs
# ---------------------------------------------------------------------------


def test_multi_audio_copy() -> None:
    """audio_stream_indexes=[1,2] with compatible codecs -> two '-map 0:<idx>' and copy per stream."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        audio_stream_indexes=[1, 2],
        audio_codec_map={1: "aac", 2: "ac3"},
        audio_channels_map={1: 2, 2: 6},
        audio_channel_mode="preserve",
    )
    # Must contain per-stream maps
    assert cmd.count("-map") >= 3  # 0:v:0 + 2 audios
    assert "0:1" in cmd
    assert "0:2" in cmd
    assert cmd.count("0:1") == 1
    assert cmd.count("0:2") == 1
    # Compatible codecs -> copy per stream
    assert "-c:a:0" in cmd
    assert "-c:a:1" in cmd
    # Find values after flags
    idx0 = cmd.index("-c:a:0")
    assert cmd[idx0 + 1] == "copy"
    idx1 = cmd.index("-c:a:1")
    assert cmd[idx1 + 1] == "copy"
    # Must NOT contain single -c:a copy (without stream spec)
    # The command should have per-stream entries, not generic
    # Generic -c:a would be second token "copy" without colon, but we allow per-stream only
    # Ensure no plain "-c:a" without colon for multi
    for tok in cmd:
        if tok == "-c:a":
            raise AssertionError("multi-audio should use -c:a:<n> notplain -c:a")
    # backward compat: single audio still works (already tested above)


def test_multi_audio_transcode() -> None:
    """Incompatible codec -> transcode to aac with bitrate 96*channels and -ac."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        audio_stream_indexes=[1],
        audio_codec_map={1: "flac"},  # not in mp4 compatible set
        audio_channels_map={1: 1},
        audio_channel_mode="preserve",
    )
    assert "0:1" in cmd
    assert "-c:a:0" in cmd
    idx = cmd.index("-c:a:0")
    assert cmd[idx + 1] == "aac"
    # bitrate 96*1 = 96k
    assert "-b:a:0" in cmd
    assert _extract(cmd, "-b:a:0") == "96k"
    assert "-ac:a:0" in cmd
    assert _extract(cmd, "-ac:a:0") == "1"

    # 2ch -> 192k, 6ch -> 576k
    cmd2 = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        audio_stream_indexes=[2],
        audio_codec_map={2: "truehd"},
        audio_channels_map={2: 6},
        audio_channel_mode="preserve",
    )
    assert _extract(cmd2, "-b:a:0") == "576k"
    assert _extract(cmd2, "-ac:a:0") == "6"

    # downmix2 forces 2ch
    cmd3 = _ffmpeg_command(
        INPUT,
        OUTPUT_MP4,
        profile="h264_mp4",
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        audio_stream_indexes=[2],
        audio_codec_map={2: "truehd"},
        audio_channels_map={2: 6},
        audio_channel_mode="downmix2",
    )
    assert _extract(cmd3, "-b:a:0") == "192k"
    assert _extract(cmd3, "-ac:a:0") == "2"


def test_multi_audio_webm_fallback_to_opus() -> None:
    """webm container: opus/vorbis copy else opus transcode."""
    # compatible vorbis -> copy
    cmd_copy = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        audio_stream_indexes=[1],
        audio_codec_map={1: "vorbis"},
        audio_channels_map={1: 2},
        audio_channel_mode="preserve",
    )
    assert _extract(cmd_copy, "-c:a:0") == "copy"
    # incompatible aac -> opus
    cmd_trans = _ffmpeg_command(
        INPUT,
        OUTPUT_WEBM,
        profile="vp9_webm",
        video_export="webm",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        audio_stream_indexes=[1],
        audio_codec_map={1: "aac"},
        audio_channels_map={1: 2},
        audio_channel_mode="preserve",
    )
    assert _extract(cmd_trans, "-c:a:0") == "libopus"
    assert _extract(cmd_trans, "-b:a:0") == "192k"


def test_multi_audio_mkv_all_copy() -> None:
    """mkv container copies any codec."""
    cmd = _ffmpeg_command(
        INPUT,
        OUTPUT_MKV,
        profile="h264_mp4",
        video_export="mkv",
        audio_export="copy",
        subtitle_export="none",
        subtitle_language=None,
        audio_stream_indexes=[1, 2],
        audio_codec_map={1: "flac", 2: "truehd"},
        audio_channels_map={1: 2, 2: 8},
        audio_channel_mode="preserve",
    )
    assert _extract(cmd, "-c:a:0") == "copy"
    assert _extract(cmd, "-c:a:1") == "copy"


# ---------------------------------------------------------------------------
# 13. Multi-SRT lang suffix (Faz 2)
# ---------------------------------------------------------------------------


def test_multi_srt_lang_suffix() -> None:
    """Same language twice -> .<lang>.srt and .<lang>1.srt suffix logic."""
    from video_converter.worker.main import _srt_suffix_for_lang

    assert _srt_suffix_for_lang("eng", 0) == ".eng.srt"
    assert _srt_suffix_for_lang("eng", 1) == ".eng1.srt"
    assert _srt_suffix_for_lang("tur", 0) == ".tur.srt"
    assert _srt_suffix_for_lang("tur", 1) == ".tur1.srt"
    assert _srt_suffix_for_lang("und", 0) == ".und.srt"

    # Simulate process_job loop with defaultdict
    from collections import defaultdict

    lang_counts: dict[str, int] = defaultdict(int)
    suffixes: list[str] = []
    for lang in ["eng", "eng"]:
        count = lang_counts[lang]
        suffix = _srt_suffix_for_lang(lang, count)
        suffixes.append(suffix)
        lang_counts[lang] += 1
    assert suffixes == [".eng.srt", ".eng1.srt"]

    # Different langs keep separate counters
    lang_counts = defaultdict(int)
    suffixes = []
    for lang in ["eng", "tur", "eng"]:
        count = lang_counts[lang]
        suffix = _srt_suffix_for_lang(lang, count)
        suffixes.append(suffix)
        lang_counts[lang] += 1
    assert suffixes == [".eng.srt", ".tur.srt", ".eng1.srt"]


# ---------------------------------------------------------------------------
# 14. Skip existing output (Faz 2)
# ---------------------------------------------------------------------------


def test_skip_existing(tmp_path, monkeypatch) -> None:
    """skip_existing_output=True and output exists -> job marked completed without ffmpeg."""
    from video_converter.core.job_repository import JobRepository
    from video_converter.core.models import JobRecord, JobStatus, now_iso
    from video_converter.core.storage import LocalFileStore
    from video_converter.worker import main as worker

    # Setup isolated storage & dirs
    store = LocalFileStore(tmp_path / "queue.sqlite3")
    repo = JobRepository(store)
    monkeypatch.setattr(worker, "job_repository", repo)
    # Use tmp_path as DATA_ROOT for isolation – Settings is frozen, so replace whole object
    new_settings = worker.settings.model_copy(update={"data_root": tmp_path})
    monkeypatch.setattr(worker, "settings", new_settings)
    for p in (
        new_settings.input_dir,
        new_settings.outputs_dir,
        new_settings.temp_dir,
        new_settings.logs_dir,
    ):
        p.mkdir(parents=True, exist_ok=True)

    # Create dummy input file
    input_file = new_settings.input_dir / "sample.mkv"
    input_file.write_bytes(b"dummy")

    # Compute expected output path (via helper) – should land under tmp outputs_dir
    job_id = "testjob12345678"
    # Need to ensure _build_output_path uses new settings; it reads settings.outputs_dir at call time
    output_path = worker._build_output_path(input_file, "mp4", job_id)
    assert str(output_path).startswith(str(new_settings.outputs_dir))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"existing output")

    timestamp = now_iso()
    record = JobRecord(
        id=job_id,
        status=JobStatus.queued,
        profile="h264_mp4",
        input_filename="sample.mkv",
        source_root_key=None,
        source_path=None,
        created_at=timestamp,
        updated_at=timestamp,
        video_export="mp4",
        audio_export="copy",
        subtitle_export="none",
        skip_existing_output=True,
    )
    repo.enqueue(record)
    # dequeue simulation: job is already queued, process_job will fetch via repo.get

    called = {"ffmpeg": False}

    def fake_ffmpeg(*args, **kwargs):
        called["ffmpeg"] = True
        return (0, "")

    monkeypatch.setattr(worker, "_run_ffmpeg_with_progress", fake_ffmpeg)

    # Also ensure probe not called unnecessarily (but still may be called before skip check)
    # Our skip check is after _resolve_input_path and before probes, so probe should NOT be called
    # To verify, monkeypatch probes to fail if called after skip
    def fail_probe(*args, **kwargs):
        raise AssertionError("probe should not be called when skip_existing and file exists")

    # Only patch ffprobe-dependent helpers that are after skip check
    # _probe_duration_seconds is after skip, so patch to catch unwanted calls
    monkeypatch.setattr(worker, "_probe_duration_seconds", fail_probe)

    worker.process_job(job_id)

    assert (
        called["ffmpeg"] is False
    ), "ffmpeg should not run when output exists and skip_existing=True"
    updated = repo.get(job_id)
    assert updated is not None
    assert updated.status == JobStatus.completed
    assert updated.progress_percent == 100
    assert (
        "atland" in (updated.progress_message or "").lower()
        or "atland" in (updated.log_tail[-1] if updated.log_tail else "").lower()
        if updated.log_tail
        else True
    )
    store.close()
