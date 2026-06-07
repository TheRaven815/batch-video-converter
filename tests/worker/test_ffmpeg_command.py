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
    assert "-map" in cmd
    assert "0" in cmd
    assert "-0:s" in cmd
    assert "0:s:m:language:eng?" in cmd


def test_subtitle_embedded_without_language() -> None:
    """Embedded subtitles without a specific language should still set -c:s copy
    but should NOT add any -map filters (all subtitle streams pass through)."""
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
    assert not _has_flag(cmd, "-map")


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
