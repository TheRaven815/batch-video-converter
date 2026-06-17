from __future__ import annotations

import json
import logging
import signal
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import redis

from video_converter.core.config import QUEUE_NAME, ensure_runtime_dirs, get_settings
from video_converter.core.job_repository import DEFAULT_STALE_RUNNING_SECONDS, JobRepository
from video_converter.core.models import JobStatus
from video_converter.core.path_validation import validate_source_path
from video_converter.core.storage import create_storage_client, is_redis_storage

settings = get_settings()
ensure_runtime_dirs(settings)

storage_client = create_storage_client(settings)
redis_client = storage_client
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


def _probe_subtitle_languages(input_path: Path) -> set[str]:
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream_tags=language",
        "-of",
        "json",
        str(input_path),
    ]
    try:
        proc = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return set()
    except FileNotFoundError:
        return set()

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return set()

    streams = payload.get("streams", []) if isinstance(payload, dict) else []
    languages: set[str] = set()
    if not isinstance(streams, list):
        return languages

    for stream in streams:
        if not isinstance(stream, dict):
            continue
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        lang = str(tags.get("language") or "").strip().lower()
        if lang:
            languages.add(lang)

    return languages


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
        if subtitle_language:
            cmd.extend(
                ["-map", "0", "-map", "-0:s", "-map", f"0:s:m:language:{subtitle_language}?"]
            )
        cmd.extend(["-c:s", "copy"])

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
        return 127, "ffmpeg executable not found. Please ensure FFmpeg is installed and in your PATH."

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

            if subtitle_export in {"embedded", "separate_srt"} and subtitle_language:
                available_subtitle_languages = _probe_subtitle_languages(input_path)
                if subtitle_language not in available_subtitle_languages:
                    job_logger.warning(
                        "requested subtitle language not found: %s (available=%s), job will continue without matching subtitle",
                        subtitle_language,
                        sorted(available_subtitle_languages),
                    )

            duration_seconds = _probe_duration_seconds(input_path)
            prefer_stream_copy_video = profile == "h264_mp4" and video_export in {"mp4", "mkv"}
            cmd = _ffmpeg_command(
                input_path,
                output_path,
                profile,
                video_export,
                audio_export,
                subtitle_export,
                subtitle_language,
                prefer_stream_copy_video=prefer_stream_copy_video,
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
                    output_path,
                    profile,
                    video_export,
                    audio_export,
                    subtitle_export,
                    subtitle_language,
                    prefer_stream_copy_video=False,
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
                    str(subtitle_output),
                ]
                try:
                    subtitle_proc = subprocess.run(
                        subtitle_cmd, capture_output=True, text=True, check=False
                    )
                    if subtitle_proc.returncode != 0:
                        subtitle_stderr = (subtitle_proc.stderr or "").strip()[-400:]
                        job_logger.warning(
                            "separate_srt export skipped: %s",
                            subtitle_stderr or "subtitle stream not found",
                        )
                except FileNotFoundError:
                    job_logger.warning("separate_srt export skipped: ffmpeg executable not found")

            job_repository.update_status(
                job_id,
                JobStatus.completed,
                progress_percent=100,
                progress_phase="completed",
                progress_message="Conversion completed",
                output_filename=output_path.name,
            )
            job_logger.info("status set to completed")
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


def _get_dynamic_concurrency() -> int:
    try:
        raw = storage_client.get("system:settings")
        if raw:
            data = json.loads(raw)
            return int(data.get("worker_concurrency", settings.worker_concurrency))
    except Exception:
        pass
    return settings.worker_concurrency


def _run_concurrent(max_pool_size: int) -> None:
    """Run the worker loop with concurrent job processing via a thread pool."""
    active_futures: dict[Future[None], str] = {}

    with ThreadPoolExecutor(max_workers=max_pool_size, thread_name_prefix="job") as executor:
        while not _shutdown.is_shutting_down:
            # Reap completed futures to free tracking slots
            for future in [f for f in active_futures if f.done()]:
                jid = active_futures.pop(future)
                try:
                    future.result()  # propagate exceptions for logging
                except Exception:
                    logger.exception("unhandled exception in job thread", extra={"job_id": jid})

            # Check dynamic concurrency setting
            dynamic_concurrency = _get_dynamic_concurrency()

            # If dynamic limit reached, wait briefly before trying to fetch
            if len(active_futures) >= dynamic_concurrency:
                time.sleep(0.5)
                continue

            try:
                message = redis_client.blpop(QUEUE_NAME, timeout=5)
                if not message:
                    continue
                _, job_id = message

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


def _run_single() -> None:
    """Run the worker loop in single-threaded (backward-compatible) mode."""
    while not _shutdown.is_shutting_down:
        try:
            message = redis_client.blpop(QUEUE_NAME, timeout=5)
            if not message:
                continue
            _, job_id = message
            process_job(job_id)
        except redis.RedisError:
            logger.exception("redis error in worker loop", extra={"job_id": "-"})
            time.sleep(2)
        except Exception:
            if is_redis_storage(storage_client):
                logger.exception("storage error in worker loop", extra={"job_id": "-"})
            else:
                logger.exception("local storage error in worker loop", extra={"job_id": "-"})
            time.sleep(2)


def _handle_signal(signum: int, _frame: Any) -> None:
    logger.info("received signal %s, initiating graceful shutdown", signum, extra={"job_id": "-"})
    _shutdown.request_shutdown()


def run() -> None:
    concurrency = settings.worker_concurrency
    logger.info("worker started (concurrency=%d)", concurrency, extra={"job_id": "-"})

    # Install signal handlers for graceful shutdown (both modes)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        recovered = job_repository.recover_stale_running_jobs(
            stale_after_seconds=DEFAULT_STALE_RUNNING_SECONDS
        )
    except redis.RedisError:
        logger.exception("stale job recovery failed", extra={"job_id": "-"})
    except Exception:
        if is_redis_storage(storage_client):
            logger.exception("stale job recovery failed", extra={"job_id": "-"})
    else:
        if recovered:
            logger.info("recovered %s stale running jobs", len(recovered), extra={"job_id": "-"})

    # We use a fixed upper bound pool size so threads can scale up to this limit dynamically.
    pool_size = max(8, concurrency)
    _run_concurrent(pool_size)

    # Post-loop cleanup: safety net to catch any jobs still marked as running
    _shutdown.graceful_shutdown(proc_timeout=5.0)
    logger.info("worker shut down", extra={"job_id": "-"})


if __name__ == "__main__":
    run()
