from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import redis

from video_converter.core.config import ensure_runtime_dirs, get_settings
from video_converter.core.job_repository import DEFAULT_STALE_RUNNING_SECONDS, JobRepository
from video_converter.core.models import JobStatus
from video_converter.core.path_validation import validate_source_path
from video_converter.core.storage import create_storage_client, is_redis_storage

settings = get_settings()
ensure_runtime_dirs(settings)

storage_client = create_storage_client(settings)
job_repository = JobRepository(storage_client)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s service=worker job_id=%(job_id)s message=%(message)s",
)
logger = logging.getLogger("worker")


class JobAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        kwargs.setdefault("extra", {})
        kwargs["extra"].setdefault("job_id", self.extra.get("job_id", "-"))
        return msg, kwargs


# ---------------------------------------------------------------------------
# Graceful shutdown manager
# ---------------------------------------------------------------------------


class _ShutdownManager:
    """Tracks active FFmpeg processes and in-progress job IDs for graceful shutdown.

    When a SIGTERM/SIGINT signal arrives the manager can quickly terminate all
    running FFmpeg subprocesses and mark any still-running jobs as ``failed``
    so they do not remain stuck in ``running`` status in Redis.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_procs: dict[str, subprocess.Popen[str]] = {}  # job_id → process
        self._active_job_ids: set[str] = set()
        self._shutdown_requested = threading.Event()

    # -- query ---------------------------------------------------------------

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_requested.is_set()

    # -- registration --------------------------------------------------------

    def register_proc(self, job_id: str, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._active_procs[job_id] = proc

    def unregister_proc(self, job_id: str) -> None:
        with self._lock:
            self._active_procs.pop(job_id, None)

    def register_job(self, job_id: str) -> None:
        with self._lock:
            self._active_job_ids.add(job_id)

    def unregister_job(self, job_id: str) -> None:
        with self._lock:
            self._active_job_ids.discard(job_id)
            self._active_procs.pop(job_id, None)

    # -- signal handling -----------------------------------------------------

    def request_shutdown(self) -> None:
        logger.info("graceful shutdown requested", extra={"job_id": "-"})
        self._shutdown_requested.set()

    def active_job_ids(self) -> set[str]:
        with self._lock:
            return set(self._active_job_ids)

    # -- cleanup actions -----------------------------------------------------

    def terminate_active_procs(self, timeout: float = 5.0) -> None:
        """SIGTERM all tracked FFmpeg processes, then SIGKILL stragglers."""
        with self._lock:
            procs = dict(self._active_procs)

        if not procs:
            return

        logger.info("terminating %d active FFmpeg process(es)", len(procs), extra={"job_id": "-"})
        for _job_id, proc in procs.items():
            try:
                proc.terminate()
            except OSError:
                pass

        deadline = time.monotonic() + timeout
        for _job_id, proc in procs.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    proc.kill()
                except OSError:
                    pass
                continue
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass

    def fail_active_jobs(self, message: str = "Worker shutdown during processing") -> None:
        """Mark every job still tracked as active whose Redis status is *running* as *failed*."""
        with self._lock:
            job_ids = list(self._active_job_ids)

        for job_id in job_ids:
            try:
                record = job_repository.get(job_id)
                if record is not None and record.status == JobStatus.running:
                    job_repository.update_status(
                        job_id,
                        JobStatus.failed,
                        progress_percent=record.progress_percent,
                        progress_phase="failed",
                        progress_message=message,
                        error=message,
                    )
                    logger.info(
                        "marked running job as failed due to shutdown", extra={"job_id": job_id}
                    )
            except Exception:
                logger.exception(
                    "could not mark job as failed during shutdown", extra={"job_id": job_id}
                )

    def graceful_shutdown(self, proc_timeout: float = 5.0) -> None:
        """Full graceful-shutdown sequence: terminate FFmpeg → fail remaining jobs."""
        self._shutdown_requested.set()
        self.terminate_active_procs(timeout=proc_timeout)
        self.fail_active_jobs()


_shutdown = _ShutdownManager()


# ---------------------------------------------------------------------------
# Path & export helpers
# ---------------------------------------------------------------------------


def _resolve_input_path(data: dict[str, Any]) -> Path:
    source_root_key = data.get("source_root_key")
    source_path = data.get("source_path")

    if source_root_key and source_path:
        return validate_source_path(
            {root.key: root.path for root in settings.media_roots},
            str(source_root_key),
            str(source_path),
            file_not_found_message="Source file not found",
        )

    input_filename = data.get("input_filename")
    if not input_filename:
        raise ValueError("input_filename is empty")

    candidate = (settings.input_dir / str(input_filename)).resolve()
    try:
        candidate.relative_to(settings.input_dir.resolve())
    except ValueError as exc:
        raise ValueError("input_filename is invalid") from exc

    if not candidate.exists() or not candidate.is_file():
        raise ValueError("input file not found")

    return candidate


def _legacy_exports_from_profile(profile: str) -> tuple[str, str]:
    if profile == "vp9_webm":
        return "webm", "opus"
    return "mp4", "aac"


def _resolve_export_options(data: dict[str, Any]) -> tuple[str, str, str, str, str | None]:
    profile = str(data.get("profile") or "h264_mp4").strip()

    legacy_video, legacy_audio = _legacy_exports_from_profile(profile)

    raw_video = str(data.get("video_export") or "").lower().strip()
    raw_audio = str(data.get("audio_export") or "").lower().strip()
    raw_subtitle = str(data.get("subtitle_export") or "").lower().strip()
    raw_subtitle_language = str(data.get("subtitle_language") or "").lower().strip()

    video_export = raw_video if raw_video in {"mp4", "mkv", "webm"} else legacy_video
    audio_export = raw_audio if raw_audio in {"copy", "aac", "mp3", "opus"} else legacy_audio
    subtitle_export = (
        raw_subtitle if raw_subtitle in {"none", "embedded", "separate_srt"} else "none"
    )
    subtitle_language = raw_subtitle_language if raw_subtitle_language else None

    # Profile normalization for container/codec compatibility:
    # - Force VP9 profile on WebM target (prevents libx264 + webm errors)
    # - Fallback to H.264 default on MP4/MKV target if invalid/empty profile received
    if video_export == "webm":
        profile = "vp9_webm"
        # Stream copy in WebM container frequently causes incompatible codec errors (e.g. AAC/AC3).
        # For stability, fallback copy to Opus in WebM even if user selects "copy".
        if audio_export == "copy":
            audio_export = "opus"
    elif profile not in {"h264_mp4", "h265_mp4", "vp9_webm"}:
        profile = "h264_mp4"

    return profile, video_export, audio_export, subtitle_export, subtitle_language


def _build_output_path(input_path: Path, video_export: str, job_id: str) -> Path:
    ext = f".{video_export}" if video_export in {"mp4", "mkv", "webm"} else ".mp4"
    filename = f"{input_path.stem}.{job_id[:8]}{ext}"
    return settings.outputs_dir / filename


# Subtitle codecs that can be transcoded to text-based formats (mov_text/webvtt).
# Everything else (PGS, VOBSUB, DVB, …) is bitmap-based and cannot be embedded
# into MP4/WebM containers.
_TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}

_PROBE_TIMEOUT_SECONDS = 30


def _run_ffprobe_json(probe_cmd: list[str]) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None

    return payload if isinstance(payload, dict) else None


def _probe_subtitle_streams(input_path: Path) -> list[dict[str, str]] | None:
    """Return subtitle streams as ``{"codec", "language"}`` dicts, or None if probing fails."""
    payload = _run_ffprobe_json(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=codec_name:stream_tags=language",
            "-of",
            "json",
            str(input_path),
        ]
    )
    if payload is None:
        return None

    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        return None

    result: list[dict[str, str]] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        result.append(
            {
                "codec": str(stream.get("codec_name") or "").strip().lower(),
                "language": str((tags or {}).get("language") or "").strip().lower(),
            }
        )
    return result


def _probe_subtitle_languages(input_path: Path) -> set[str]:
    streams = _probe_subtitle_streams(input_path) or []
    return {stream["language"] for stream in streams if stream["language"]}


def _probe_video_codec(input_path: Path) -> str | None:
    payload = _run_ffprobe_json(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(input_path),
        ]
    )
    if payload is None:
        return None

    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        return None

    codec = str(streams[0].get("codec_name") or "").strip().lower()
    return codec or None


def _subtitle_codec_for_container(output_path: Path) -> str:
    suffix = output_path.suffix.lower()
    if suffix == ".mp4":
        return "mov_text"
    if suffix == ".webm":
        return "webvtt"
    return "copy"


def _ffmpeg_command(
    input_path: Path,
    output_path: Path,
    profile: str,
    video_export: str,
    audio_export: str,
    subtitle_export: str,
    subtitle_language: str | None,
    *,
    prefer_stream_copy_video: bool = False,
    ffmpeg_threads: int = 1,
) -> list[str]:
    cmd = ["ffmpeg", "-y", "-i", str(input_path)]

    if prefer_stream_copy_video:
        cmd.extend(["-c:v", "copy"])
    elif profile == "h265_mp4":
        cmd.extend(["-c:v", "libx265", "-preset", "medium", "-crf", "28"])
    elif profile == "vp9_webm":
        cmd.extend(["-c:v", "libvpx-vp9", "-crf", "33", "-b:v", "0"])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])

    # Bound each encoder independently so concurrent jobs cannot each consume
    # every CPU visible to the container.
    cmd.extend(["-threads", str(max(1, min(32, ffmpeg_threads)))])

    if audio_export == "copy":
        cmd.extend(["-c:a", "copy"])
    elif audio_export == "aac":
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    elif audio_export == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])
    elif audio_export == "opus":
        cmd.extend(["-c:a", "libopus", "-b:a", "96k"])
    else:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])

    if subtitle_export == "embedded":
        # Select exactly one video and one audio stream plus the requested
        # subtitle stream(s); a bare "-map 0" would also pull attachments and
        # data streams, which MP4/WebM muxers reject.
        if subtitle_language:
            cmd.extend(
                ["-map", "0:v:0", "-map", "0:a:0?", "-map", f"0:s:m:language:{subtitle_language}?"]
            )
        else:
            cmd.extend(["-map", "0:v:0", "-map", "0:a:0?", "-map", "0:s:0?"])
        cmd.extend(["-c:s", _subtitle_codec_for_container(output_path)])
    else:
        # "none" and "separate_srt" must not embed subtitles in the video output.
        cmd.append("-sn")

    if output_path.suffix.lower() == ".mp4":
        cmd.extend(["-movflags", "+faststart"])

    cmd.append(str(output_path))
    return cmd


def _probe_duration_seconds(input_path: Path) -> float | None:
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(input_path),
    ]
    try:
        proc = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None
    except FileNotFoundError:
        return None

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    fmt = payload.get("format")
    if not isinstance(fmt, dict):
        return None

    raw_duration = fmt.get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        return None

    if duration <= 0:
        return None

    return duration


def _parse_ffmpeg_out_time_seconds(progress_snapshot: dict[str, str]) -> float | None:
    out_time_us = progress_snapshot.get("out_time_us")
    if out_time_us:
        try:
            return max(0.0, float(out_time_us) / 1_000_000.0)
        except ValueError:
            pass

    out_time_ms = progress_snapshot.get("out_time_ms")
    if out_time_ms:
        try:
            return max(0.0, float(out_time_ms) / 1_000_000.0)
        except ValueError:
            pass

    raw_out_time = progress_snapshot.get("out_time")
    if not raw_out_time:
        return None

    parts = raw_out_time.split(":")
    if len(parts) != 3:
        return None

    try:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return None

    return max(0.0, hours * 3600.0 + minutes * 60.0 + seconds)


def _is_cancel_requested(job_id: str) -> bool:
    record = job_repository.get(job_id)
    return bool(record and record.cancel_requested)


def _run_ffmpeg_with_progress(
    job_id: str,
    cmd: list[str],
    *,
    duration_seconds: float | None,
) -> tuple[int, str]:
    progress_cmd = [*cmd[:-1], "-progress", "pipe:1", "-nostats", cmd[-1]]
    try:
        proc = subprocess.Popen(
            progress_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError:
        return (
            127,
            "ffmpeg executable not found. Please ensure FFmpeg is installed and in your PATH.",
        )

    stderr_lines: list[str] = []

    def _read_stderr() -> None:
        if proc.stderr:
            for raw_line in proc.stderr:
                stderr_lines.append(raw_line)
                if len(stderr_lines) > 50:
                    stderr_lines.pop(0)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    # Track the process so the shutdown manager can terminate it
    _shutdown.register_proc(job_id, proc)

    snapshot: dict[str, str] = {}
    cancelled_flag = {"value": False}

    def _cancel_watcher() -> None:
        while proc.poll() is None:
            # Check graceful shutdown first (local flag, no Redis round-trip)
            if _shutdown.is_shutting_down:
                cancelled_flag["value"] = True
                try:
                    proc.terminate()
                except OSError:
                    pass
                return
            if _is_cancel_requested(job_id):
                cancelled_flag["value"] = True
                try:
                    proc.terminate()
                except OSError:
                    pass
                return
            time.sleep(0.35)

    watcher = threading.Thread(target=_cancel_watcher, daemon=True)
    watcher.start()

    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            if "=" in line:
                key, value = line.split("=", 1)
                snapshot[key.strip()] = value.strip()

            if snapshot.get("progress") == "continue":
                processed_seconds = _parse_ffmpeg_out_time_seconds(snapshot)
                telemetry: dict[str, Any] = {
                    "progress_fps": _safe_float(snapshot.get("fps")),
                    "progress_speed": snapshot.get("speed"),
                    "progress_bitrate": snapshot.get("bitrate"),
                    "progress_out_time_seconds": processed_seconds,
                }
                if duration_seconds and processed_seconds is not None:
                    percent = int(
                        max(0.0, min(99.0, (processed_seconds / duration_seconds) * 100.0))
                    )
                    speed_value = _parse_speed_multiplier(snapshot.get("speed"))
                    if speed_value and speed_value > 0:
                        telemetry["progress_eta_seconds"] = int(
                            max(0.0, (duration_seconds - processed_seconds) / speed_value)
                        )
                    job_repository.update_status(
                        job_id,
                        JobStatus.running,
                        progress_percent=percent,
                        progress_phase="transcoding",
                        progress_message=f"FFmpeg processing ({percent}%)",
                        telemetry=telemetry,
                        log_line=f"frame={snapshot.get('frame', '?')} fps={snapshot.get('fps', '?')} bitrate={snapshot.get('bitrate', '?')} speed={snapshot.get('speed', '?')}",
                    )
                else:
                    job_repository.update_status(
                        job_id,
                        JobStatus.running,
                        progress_percent=None,
                        progress_phase="transcoding",
                        progress_message="FFmpeg processing",
                        telemetry=telemetry,
                        log_line=f"frame={snapshot.get('frame', '?')} fps={snapshot.get('fps', '?')} bitrate={snapshot.get('bitrate', '?')} speed={snapshot.get('speed', '?')}",
                    )
                snapshot.clear()

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        stderr_thread.join(timeout=2)
        stderr_text = "".join(stderr_lines)

        if cancelled_flag["value"] or _is_cancel_requested(job_id):
            return 130, (stderr_text or "cancelled")

        return proc.returncode or 0, (stderr_text or "")
    finally:
        _shutdown.unregister_proc(job_id)


def _safe_float(value: str | None) -> float | None:
    try:
        return float(str(value)) if value not in {None, "N/A"} else None
    except ValueError:
        return None


def _parse_speed_multiplier(value: str | None) -> float | None:
    if not value:
        return None
    return _safe_float(value.rstrip("x"))


def _downgrade_bitmap_subtitles(
    subtitle_export: str,
    subtitle_language: str | None,
    video_export: str,
    subtitle_streams: list[dict[str, str]] | None,
    job_logger: logging.LoggerAdapter,
) -> str:
    """Skip embedding when a bitmap subtitle would be selected for an MP4/WebM target.

    Bitmap formats (PGS, VOBSUB, DVB, …) cannot be transcoded to mov_text or
    webvtt, so attempting to embed them is a guaranteed FFmpeg failure.
    """
    if subtitle_export != "embedded" or video_export not in {"mp4", "webm"}:
        return subtitle_export
    if not subtitle_streams:
        return subtitle_export

    if subtitle_language:
        candidates = [s for s in subtitle_streams if s["language"] == subtitle_language]
    else:
        candidates = subtitle_streams[:1]

    if candidates and any(
        s["codec"] and s["codec"] not in _TEXT_SUBTITLE_CODECS for s in candidates
    ):
        job_logger.warning(
            "bitmap subtitle stream cannot be embedded into %s; continuing without subtitles",
            video_export,
        )
        return "none"
    return subtitle_export


def process_job(job_id: str) -> None:
    job_logger = JobAdapter(logger, {"job_id": job_id})
    job_logger.info("picked from queue")

    # Track this job so the shutdown manager can mark it as failed
    _shutdown.register_job(job_id)

    try:
        record = job_repository.get(job_id)
        if not record:
            job_logger.error("job payload not found/invalid")
            return

        data = record.model_dump()

        if record.cancel_requested:
            job_repository.update_status(
                job_id,
                JobStatus.cancelled,
                progress_percent=0,
                progress_phase="cancelled",
                progress_message="Job cancelled before start",
            )
            job_logger.info("job cancelled before processing")
            return

        job_repository.update_status(
            job_id,
            JobStatus.running,
            progress_percent=0,
            progress_phase="preparing",
            progress_message="Preparing conversion",
            log_line="Worker picked up job and is preparing FFmpeg command",
        )

        try:
            input_path = _resolve_input_path(data)
            profile, video_export, audio_export, subtitle_export, subtitle_language = (
                _resolve_export_options(data)
            )
            output_path = _build_output_path(input_path, video_export, job_id)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Transcode into the temp dir and move into outputs/ only on
            # success, so partial files are never listed or downloadable.
            temp_output_path = settings.temp_dir / output_path.name
            temp_output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_subtitle_path = settings.temp_dir / f"{output_path.stem}.srt"

            if subtitle_export in {"embedded", "separate_srt"}:
                subtitle_streams = _probe_subtitle_streams(input_path)
                if subtitle_language and subtitle_streams is not None:
                    available_subtitle_languages = {
                        stream["language"] for stream in subtitle_streams if stream["language"]
                    }
                    if subtitle_language not in available_subtitle_languages:
                        job_logger.warning(
                            "requested subtitle language not found: %s (available=%s), job will continue without matching subtitle",
                            subtitle_language,
                            sorted(available_subtitle_languages),
                        )
                subtitle_export = _downgrade_bitmap_subtitles(
                    subtitle_export, subtitle_language, video_export, subtitle_streams, job_logger
                )

            duration_seconds = _probe_duration_seconds(input_path)
            prefer_stream_copy_video = (
                profile == "h264_mp4"
                and video_export in {"mp4", "mkv"}
                and _probe_video_codec(input_path) == "h264"
            )
            try:
                cmd = _ffmpeg_command(
                    input_path,
                    temp_output_path,
                    profile,
                    video_export,
                    audio_export,
                    subtitle_export,
                    subtitle_language,
                    prefer_stream_copy_video=prefer_stream_copy_video,
                    ffmpeg_threads=settings.ffmpeg_threads,
                )
                return_code, stderr_text = _run_ffmpeg_with_progress(
                    job_id, cmd, duration_seconds=duration_seconds
                )

                if (
                    return_code != 0
                    and prefer_stream_copy_video
                    and not _is_cancel_requested(job_id)
                    and not _shutdown.is_shutting_down
                ):
                    job_logger.warning("stream copy failed, falling back to re-encode")
                    job_repository.update_status(
                        job_id,
                        JobStatus.running,
                        progress_percent=0,
                        progress_phase="preparing",
                        progress_message="Stream copy failed, retrying with re-encode",
                    )
                    fallback_cmd = _ffmpeg_command(
                        input_path,
                        temp_output_path,
                        profile,
                        video_export,
                        audio_export,
                        subtitle_export,
                        subtitle_language,
                        prefer_stream_copy_video=False,
                        ffmpeg_threads=settings.ffmpeg_threads,
                    )
                    return_code, stderr_text = _run_ffmpeg_with_progress(
                        job_id, fallback_cmd, duration_seconds=duration_seconds
                    )

                if return_code == 130:
                    # Distinguish between cancel and shutdown
                    if _shutdown.is_shutting_down and not _is_cancel_requested(job_id):
                        job_repository.update_status(
                            job_id,
                            JobStatus.failed,
                            progress_percent=None,
                            progress_phase="failed",
                            progress_message="Worker shutdown during processing",
                            error="Worker shutdown during processing",
                        )
                        job_logger.info("status set to failed (worker shutdown)")
                        return
                    if _is_cancel_requested(job_id):
                        job_repository.update_status(
                            job_id,
                            JobStatus.cancelled,
                            progress_percent=0,
                            progress_phase="cancelled",
                            progress_message="Job cancelled",
                        )
                        job_logger.info("status set to cancelled")
                        return

                if return_code != 0:
                    stderr_tail = (stderr_text or "ffmpeg failed").strip()[-700:]
                    raise RuntimeError(stderr_tail)

                if subtitle_export == "separate_srt":
                    subtitle_output = output_path.parent / f"{output_path.stem}.srt"
                    subtitle_cmd = [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(input_path),
                        "-map",
                        f"0:s:m:language:{subtitle_language}?" if subtitle_language else "0:s:0?",
                        "-threads",
                        str(settings.ffmpeg_threads),
                        str(temp_subtitle_path),
                    ]
                    try:
                        subtitle_proc = subprocess.run(
                            subtitle_cmd, capture_output=True, text=True, check=False, timeout=1800
                        )
                        if subtitle_proc.returncode == 0 and temp_subtitle_path.exists():
                            os.replace(temp_subtitle_path, subtitle_output)
                        else:
                            subtitle_stderr = (subtitle_proc.stderr or "").strip()[-400:]
                            job_logger.warning(
                                "separate_srt export skipped: %s",
                                subtitle_stderr or "subtitle stream not found",
                            )
                    except FileNotFoundError:
                        job_logger.warning(
                            "separate_srt export skipped: ffmpeg executable not found"
                        )
                    except subprocess.TimeoutExpired:
                        job_logger.warning("separate_srt export skipped: extraction timed out")

                os.replace(temp_output_path, output_path)

                job_repository.update_status(
                    job_id,
                    JobStatus.completed,
                    progress_percent=100,
                    progress_phase="completed",
                    progress_message="Conversion completed",
                    output_filename=output_path.name,
                )
                job_logger.info("status set to completed")
            finally:
                # On success the temp files were moved away; on failure,
                # cancel, or shutdown this removes the partial output.
                temp_output_path.unlink(missing_ok=True)
                temp_subtitle_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            job_repository.update_status(
                job_id,
                JobStatus.failed,
                progress_percent=100,
                progress_phase="failed",
                progress_message="Conversion failed",
                error=str(exc),
            )
            job_logger.exception("status set to failed")
    finally:
        _shutdown.unregister_job(job_id)
        try:
            job_repository.acknowledge(job_id)
        except Exception:
            job_logger.exception("failed to acknowledge job on processing list")


def _get_dynamic_concurrency() -> int:
    try:
        raw = storage_client.get("system:settings")
        if raw:
            data = json.loads(raw)
            return int(data.get("worker_concurrency", settings.worker_concurrency))
    except Exception:
        pass
    return settings.worker_concurrency


_PERIODIC_RECOVERY_INTERVAL_SECONDS = 600
_OUTPUT_CLEANUP_INTERVAL_SECONDS = 60 * 60


def _cleanup_outputs(
    raw_settings: dict[str, Any] | None = None, *, now: float | None = None
) -> int:
    """Delete expired outputs while preserving the configured newest minimum."""
    if raw_settings is None:
        try:
            stored = storage_client.get("system:settings")
            raw_settings = json.loads(stored) if stored else {}
        except Exception:
            logger.exception("failed to load output cleanup settings", extra={"job_id": "-"})
            return 0

    cleanup = raw_settings.get("auto_cleanup")
    if not isinstance(cleanup, dict) or not cleanup.get("enabled"):
        return 0

    retention_days = max(1, min(365, int(cleanup.get("retention_days", 30))))
    keep_minimum = max(0, min(10000, int(cleanup.get("keep_minimum_outputs", 10))))
    cutoff = (now if now is not None else time.time()) - retention_days * 24 * 60 * 60
    candidates = sorted(
        (
            path
            for path in settings.outputs_dir.iterdir()
            if path.is_file() and not path.name.startswith(".")
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )

    deleted = 0
    for path in candidates[keep_minimum:]:
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            deleted += 1
        except OSError:
            logger.exception("failed to delete expired output %s", path, extra={"job_id": "-"})
    return deleted


def _run_concurrent(max_pool_size: int) -> None:
    """Run the worker loop with concurrent job processing via a thread pool."""
    active_futures: dict[Future[None], str] = {}
    last_recovery = time.monotonic()
    last_cleanup = 0.0

    with ThreadPoolExecutor(max_workers=max_pool_size, thread_name_prefix="job") as executor:
        while not _shutdown.is_shutting_down:
            # Reap completed futures to free tracking slots
            for future in [f for f in active_futures if f.done()]:
                jid = active_futures.pop(future)
                try:
                    future.result()  # propagate exceptions for logging
                except Exception:
                    logger.exception("unhandled exception in job thread", extra={"job_id": jid})

            # Periodically requeue jobs stuck in "running" (e.g. after a crash
            # elsewhere); jobs actively handled by this worker are excluded.
            if time.monotonic() - last_recovery >= _PERIODIC_RECOVERY_INTERVAL_SECONDS:
                last_recovery = time.monotonic()
                try:
                    recovered = job_repository.recover_stale_running_jobs(
                        stale_after_seconds=DEFAULT_STALE_RUNNING_SECONDS,
                        exclude_ids=_shutdown.active_job_ids(),
                    )
                    if recovered:
                        logger.info(
                            "recovered %d stale running job(s)",
                            len(recovered),
                            extra={"job_id": "-"},
                        )
                except Exception:
                    logger.exception("periodic stale job recovery failed", extra={"job_id": "-"})

            if time.monotonic() - last_cleanup >= _OUTPUT_CLEANUP_INTERVAL_SECONDS:
                last_cleanup = time.monotonic()
                deleted = _cleanup_outputs()
                if deleted:
                    logger.info("deleted %d expired output(s)", deleted, extra={"job_id": "-"})

            # Check dynamic concurrency setting
            dynamic_concurrency = _get_dynamic_concurrency()

            # If dynamic limit reached, wait briefly before trying to fetch
            if len(active_futures) >= dynamic_concurrency:
                time.sleep(0.5)
                continue

            try:
                job_id = job_repository.dequeue(timeout=5)
                if not job_id:
                    continue

                future = executor.submit(process_job, job_id)
                active_futures[future] = job_id
                logger.info(
                    "dispatched job (%d/%d active, max %d slots)",
                    len(active_futures),
                    dynamic_concurrency,
                    max_pool_size,
                    extra={"job_id": job_id},
                )
            except redis.RedisError:
                logger.exception("redis error in worker loop", extra={"job_id": "-"})
                time.sleep(2)
            except Exception:
                if is_redis_storage(storage_client):
                    logger.exception("storage error in worker loop", extra={"job_id": "-"})
                else:
                    logger.exception("local storage error in worker loop", extra={"job_id": "-"})
                time.sleep(2)

        # Graceful drain: terminate FFmpeg first so futures complete quickly
        if active_futures:
            logger.info("draining %d active job(s)", len(active_futures), extra={"job_id": "-"})
            _shutdown.terminate_active_procs(timeout=5.0)
            for future in active_futures:
                try:
                    future.result(timeout=600)
                except Exception:
                    pass


def _handle_signal(signum: int, _frame: Any) -> None:
    logger.info("received signal %s, initiating graceful shutdown", signum, extra={"job_id": "-"})
    _shutdown.request_shutdown()


def run() -> None:
    concurrency = settings.worker_concurrency
    logger.info("worker started (concurrency=%d)", concurrency, extra={"job_id": "-"})

    # Install signal handlers for graceful shutdown (both modes)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # At startup this worker owns no jobs, so anything on the processing list
    # or still marked "running" was orphaned by a previous worker process.
    try:
        recovered = job_repository.recover_processing_orphans()
        recovered += job_repository.recover_stale_running_jobs(stale_after_seconds=0)
    except redis.RedisError:
        logger.exception("startup job recovery failed", extra={"job_id": "-"})
    except Exception:
        if is_redis_storage(storage_client):
            logger.exception("startup job recovery failed", extra={"job_id": "-"})
    else:
        if recovered:
            logger.info(
                "recovered %s interrupted/stale jobs", len(recovered), extra={"job_id": "-"}
            )

    # We use a fixed upper bound pool size so threads can scale up to this limit dynamically.
    pool_size = max(8, concurrency)
    _run_concurrent(pool_size)

    # Post-loop cleanup: safety net to catch any jobs still marked as running
    _shutdown.graceful_shutdown(proc_timeout=5.0)
    logger.info("worker shut down", extra={"job_id": "-"})


if __name__ == "__main__":
    run()
