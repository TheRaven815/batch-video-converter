from __future__ import annotations

import errno
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from video_converter.core.config import get_settings


def fix_mp4(
    source_path: Path,
    output_path: Path | None = None,
    *,
    runtime_settings: Any | None = None,
    cancel_event: Any | None = None,
) -> Path:
    """Remux an immutable source into a new MP4 under DATA_ROOT/outputs."""
    cfg = runtime_settings if runtime_settings is not None else get_settings()
    src = Path(source_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"source not found: {src}")
    data_root = Path(cfg.data_root).resolve()
    outputs = Path(cfg.outputs_dir).resolve()
    temp_root = Path(cfg.temp_dir).resolve()
    if outputs != data_root / "outputs" or temp_root != data_root / "temp":
        raise ValueError("MP4 repair directories must stay under DATA_ROOT")
    dst = (
        Path(output_path).resolve()
        if output_path is not None
        else outputs / f"{src.stem[:40]}.fixed.{uuid.uuid4().hex}.mp4"
    )
    if dst.parent != outputs or dst.suffix.lower() != ".mp4" or dst == src:
        raise ValueError("MP4 repair destination must be a new MP4 in DATA_ROOT/outputs")
    if dst.exists():
        raise FileExistsError(f"output already exists: {dst.name}")
    outputs.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    source_size = src.stat().st_size
    required_bytes = source_size + max(1_048_576, source_size // 20)
    reserve = max(0, int(getattr(cfg, "min_free_disk_bytes", 536_870_912)))
    if any(shutil.disk_usage(path).free < required_bytes + reserve for path in (temp_root, outputs)):
        raise OSError(errno.ENOSPC, "Insufficient free disk space for MP4 repair")
    timeout = max(1, min(3600, int(getattr(cfg, "ffmpeg_stall_timeout_seconds", 300))))
    with tempfile.TemporaryDirectory(prefix="mp4-fix-", dir=temp_root) as work_dir:
        tmp = Path(work_dir) / "repaired.mp4"
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(src),
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        with (Path(work_dir) / "ffmpeg.stderr").open("w+b") as error_log:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("MP4 repair cancelled")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=error_log)
            deadline = time.monotonic() + timeout
            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("MP4 repair cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(f"MP4 repair timed out after {timeout} seconds")
                    try:
                        proc.wait(timeout=min(0.25, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            if proc.returncode != 0:
                error_log.seek(max(0, error_log.seek(0, os.SEEK_END) - 700))
                detail = error_log.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(detail or "ffmpeg failed")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("MP4 repair cancelled")
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced no repaired MP4")
        if dst.exists():
            raise FileExistsError(f"output already exists: {dst.name}")
        try:
            os.replace(tmp, dst)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # Stage on the output filesystem, keeping incomplete copies out of its file list.
            with tempfile.TemporaryDirectory(prefix=".mp4-fix-", dir=outputs) as publish_dir:
                staged = Path(publish_dir) / "repaired.mp4"
                shutil.copyfile(tmp, staged)
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("MP4 repair cancelled")
                if dst.exists():
                    raise FileExistsError(f"output already exists: {dst.name}")
                os.replace(staged, dst)
    return dst
