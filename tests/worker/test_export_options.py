from video_converter.worker.main import _resolve_export_options


def test_webm_forces_vp9_profile_and_opus_when_audio_copy_selected() -> None:
    profile, video_export, audio_export, subtitle_export, subtitle_language = (
        _resolve_export_options(
            {
                "profile": "h264_mp4",
                "video_export": "webm",
                "audio_export": "copy",
                "subtitle_export": "none",
                "subtitle_language": "",
            }
        )
    )

    assert profile == "vp9_webm"
    assert video_export == "webm"
    assert audio_export == "opus"
    assert subtitle_export == "none"
    assert subtitle_language is None


def test_invalid_profile_falls_back_to_h264_for_mp4_like_targets() -> None:
    profile, video_export, audio_export, subtitle_export, subtitle_language = (
        _resolve_export_options(
            {
                "profile": "invalid_profile",
                "video_export": "mp4",
                "audio_export": "aac",
                "subtitle_export": "embedded",
                "subtitle_language": "eng",
            }
        )
    )

    assert profile == "h264_mp4"
    assert video_export == "mp4"
    assert audio_export == "aac"
    assert subtitle_export == "embedded"
    assert subtitle_language == "eng"


def test_valid_h265_profile_is_preserved_for_mkv() -> None:
    profile, video_export, audio_export, subtitle_export, subtitle_language = (
        _resolve_export_options(
            {
                "profile": "h265_mp4",
                "video_export": "mkv",
                "audio_export": "mp3",
                "subtitle_export": "separate_srt",
                "subtitle_language": "tur",
            }
        )
    )

    assert profile == "h265_mp4"
    assert video_export == "mkv"
    assert audio_export == "mp3"
    assert subtitle_export == "separate_srt"
    assert subtitle_language == "tur"
