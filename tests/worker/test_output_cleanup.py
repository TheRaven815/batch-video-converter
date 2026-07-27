from __future__ import annotations

import os
import time

import video_converter.worker.main as worker


def test_output_cleanup_respects_retention_and_keep_minimum(tmp_path, monkeypatch) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    newest = outputs / "newest.mp4"
    old_kept = outputs / "old-kept.mp4"
    old_deleted = outputs / "old-deleted.mp4"
    for path in (newest, old_kept, old_deleted):
        path.write_text("video", encoding="utf-8")

    now = time.time()
    os.utime(newest, (now, now))
    os.utime(old_kept, (now - 40 * 86400, now - 40 * 86400))
    os.utime(old_deleted, (now - 50 * 86400, now - 50 * 86400))
    monkeypatch.setattr(worker, "settings", type("_Settings", (), {"outputs_dir": outputs})())

    deleted = worker._cleanup_outputs(
        {
            "auto_cleanup": {
                "enabled": True,
                "retention_days": 30,
                "keep_minimum_outputs": 2,
            }
        },
        now=now,
    )

    assert deleted == 1
    assert newest.exists()
    assert old_kept.exists()
    assert not old_deleted.exists()
