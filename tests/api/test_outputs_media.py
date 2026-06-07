from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response

import video_converter.api.main as api
from video_converter.core.config import MediaRoot


class _FakeCompletedProcess:
    def __init__(self) -> None:
        self.returncode = 0
        self.stderr = ""
        self.stdout = json.dumps(
            {
                "streams": [
                    {
                        "index": 2,
                        "codec_name": "subrip",
                        "tags": {"language": "eng", "title": "English"},
                    }
                ]
            }
        )


def _patch_settings(monkeypatch: Any, *, media_root: Path | None = None, outputs_dir: Path | None = None) -> None:
    monkeypatch.setattr(
        api,
        "settings",
        type(
            "_S",
            (),
            {
                "media_roots": (MediaRoot(key="root", label="Root", path=media_root),) if media_root else (),
                "outputs_dir": outputs_dir,
            },
        )(),
    )


def test_media_browse_supports_limit_cursor_and_search(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "alpha.mp4").write_text("video", encoding="utf-8")
    (media_root / "beta.mkv").write_text("video", encoding="utf-8")
    (media_root / "gamma.txt").write_text("text", encoding="utf-8")
    (media_root / "series").mkdir()
    _patch_settings(monkeypatch, media_root=media_root)

    first_page = api.browse_media_root(root_key="root", limit=2, cursor=0)
    second_page = api.browse_media_root(root_key="root", limit=2, cursor=2)
    searched = api.browse_media_root(root_key="root", q="bet")

    assert [entry.name for entry in first_page.entries] == ["series", "alpha.mp4"]
    assert first_page.next_cursor == "2"
    assert [entry.name for entry in second_page.entries] == ["beta.mkv"]
    assert [entry.name for entry in searched.entries] == ["beta.mkv"]


def test_output_listing_and_download_use_safe_output_path(tmp_path: Path, monkeypatch: Any) -> None:
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    (outputs_dir / "done.mp4").write_text("video", encoding="utf-8")
    _patch_settings(monkeypatch, outputs_dir=outputs_dir)

    response = api.list_outputs(response=Response())
    download = api.download_output("done.mp4")

    assert [output.filename for output in response.outputs] == ["done.mp4"]
    assert response.outputs[0].download_url == "/api/v1/outputs/done.mp4/download"
    assert Path(download.path) == outputs_dir / "done.mp4"

    with pytest.raises(HTTPException) as exc:
        api.download_output("../secret.mp4")
    assert exc.value.status_code == 400


def test_subtitle_probe_uses_short_lived_cache(tmp_path: Path, monkeypatch: Any) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "movie.mkv").write_text("video", encoding="utf-8")
    _patch_settings(monkeypatch, media_root=media_root)
    api._subtitle_probe_cache.clear()

    calls = 0

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        nonlocal calls
        calls += 1
        return _FakeCompletedProcess()

    monkeypatch.setattr(api.subprocess, "run", fake_run)

    first = api.probe_media_subtitles(root_key="root", path="movie.mkv")
    second = api.probe_media_subtitles(root_key="root", path="movie.mkv")

    assert calls == 1
    assert first.tracks == second.tracks
    assert first.tracks[0].language == "eng"
