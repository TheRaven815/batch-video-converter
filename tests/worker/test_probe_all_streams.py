from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_converter.worker.main import _probe_all_streams


class _FakeCompletedProcess:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.returncode = 0
        self.stderr = ""
        self.stdout = json.dumps(payload)


def test_probe_all_streams_parses_video_audio_subtitle(monkeypatch: Any) -> None:
    payload = {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "tags": {"title": "Main Video"}},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "eng", "title": "Stereo"}},
            {"index": 2, "codec_type": "audio", "codec_name": "ac3", "channels": 6, "tags": {"language": "tur"}},
            {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "ENG", "title": "English SDH"}},
            {"index": 4, "codec_type": "subtitle", "codec_name": "ass", "tags": {"language": "jpn"}},
            {"index": 99, "codec_type": "data", "codec_name": "bin_data"},
        ]
    }

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(payload)

    monkeypatch.setattr("video_converter.worker.main.subprocess.run", fake_run)

    result = _probe_all_streams(Path("/tmp/fake.mkv"))

    assert result is not None
    assert len(result["video"]) == 1
    assert result["video"][0] == {"index": 0, "codec": "h264", "language": None, "channels": None, "title": "Main Video"}
    assert len(result["audio"]) == 2
    assert result["audio"][0]["language"] == "eng"
    assert result["audio"][0]["channels"] == 2
    assert result["audio"][0]["title"] == "Stereo"
    assert result["audio"][1]["language"] == "tur"
    assert result["audio"][1]["channels"] == 6
    assert len(result["subtitle"]) == 2
    # language is lowercased
    assert result["subtitle"][0]["language"] == "eng"
    assert result["subtitle"][0]["title"] == "English SDH"
    assert result["subtitle"][1]["language"] == "jpn"
    # data stream is ignored
    assert "data" not in result


def test_probe_all_streams_returns_none_on_failure(monkeypatch: Any) -> None:
    def fake_run_fail(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError

    # Patch via _run_ffprobe_json indirectly — make subprocess.run fail,
    # _run_ffprobe_json returns None, so _probe_all_streams returns None
    monkeypatch.setattr("video_converter.worker.main.subprocess.run", fake_run_fail)

    result = _probe_all_streams(Path("/tmp/missing.mkv"))
    assert result is None


def test_probe_all_streams_handles_channels_as_string(monkeypatch: Any) -> None:
    payload = {
        "streams": [
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": "2", "tags": {"language": "eng"}},
        ]
    }

    monkeypatch.setattr(
        "video_converter.worker.main.subprocess.run", lambda *a, **k: _FakeCompletedProcess(payload)
    )

    result = _probe_all_streams(Path("/tmp/fake.mkv"))

    assert result is not None
    assert result["audio"][0]["channels"] == 2
