from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"


class ExportProfile(StrEnum):
    h264_mp4 = "h264_mp4"
    h265_mp4 = "h265_mp4"
    vp9_webm = "vp9_webm"


class VideoExport(StrEnum):
    mp4 = "mp4"
    mkv = "mkv"
    webm = "webm"


class AudioExport(StrEnum):
    copy = "copy"
    aac = "aac"
    mp3 = "mp3"
    opus = "opus"


class SubtitleExport(StrEnum):
    none = "none"
    embedded = "embedded"
    separate_srt = "separate_srt"


class VideoResolution(StrEnum):
    original = "original"
    p1080 = "1080p"
    p720 = "720p"
    p480 = "480p"


class EncoderPreset(StrEnum):
    ultrafast = "ultrafast"
    veryfast = "veryfast"
    fast = "fast"
    medium = "medium"
    slow = "slow"


class HardwareAcceleration(StrEnum):
    auto = "auto"
    disabled = "disabled"
    v4l2m2m = "v4l2m2m"


class JobCreateRequest(BaseModel):
    input_filename: Optional[str] = Field(default=None, max_length=1024)
    source_root_key: Optional[str] = Field(default=None, max_length=64)
    source_path: Optional[str] = Field(default=None, max_length=2048)
    profile: ExportProfile = ExportProfile.h264_mp4
    video_export: VideoExport = VideoExport.mp4
    audio_export: AudioExport = AudioExport.copy
    subtitle_export: SubtitleExport = SubtitleExport.none
    subtitle_language: Optional[str] = Field(default=None, max_length=32)
    quality_crf: int = Field(default=23, ge=0, le=51)
    target_video_bitrate: Optional[str] = Field(
        default=None, pattern=r"^\d+(?:[kKmM])?$", max_length=16
    )
    audio_bitrate_kbps: int = Field(default=128, ge=32, le=512)
    resolution: VideoResolution = VideoResolution.original
    encoder_preset: EncoderPreset = EncoderPreset.veryfast
    hardware_acceleration: HardwareAcceleration = HardwareAcceleration.auto
    max_attempts: int = Field(default=3, ge=1, le=10)
    priority: int = Field(default=0, ge=-10, le=10)


class JobBatchCreateRequest(BaseModel):
    jobs: list[JobCreateRequest] = Field(default_factory=list, min_length=1)


class JobRecord(BaseModel):
    id: str
    status: JobStatus

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_legacy_status(cls, value: str) -> str:
        """Normalize legacy 'processing' status to 'running' for backward compatibility."""
        return "running" if value == "processing" else value

    profile: ExportProfile
    video_export: VideoExport = VideoExport.mp4
    audio_export: AudioExport = AudioExport.copy
    subtitle_export: SubtitleExport = SubtitleExport.none
    subtitle_language: Optional[str] = Field(default=None, max_length=32)
    input_filename: Optional[str] = None
    source_root_key: Optional[str] = None
    source_path: Optional[str] = None
    output_filename: Optional[str] = None
    error_message: Optional[str] = None
    progress_percent: Optional[int] = Field(default=0, ge=0, le=100)
    progress_phase: Optional[str] = Field(default="queued", max_length=100)
    progress_message: Optional[str] = Field(default=None, max_length=500)
    progress_updated_at: Optional[str] = None
    progress_eta_seconds: Optional[int] = Field(default=None, ge=0)
    progress_fps: Optional[float] = Field(default=None, ge=0)
    progress_speed: Optional[str] = Field(default=None, max_length=32)
    progress_bitrate: Optional[str] = Field(default=None, max_length=64)
    progress_out_time_seconds: Optional[float] = Field(default=None, ge=0)
    log_tail: list[str] = Field(default_factory=list, max_length=50)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    archived: bool = False
    cancel_requested: bool = False
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    batch_id: Optional[str] = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    next_retry_at: Optional[str] = None
    retry_reason: Optional[str] = Field(default=None, max_length=1000)
    quality_crf: int = Field(default=23, ge=0, le=51)
    target_video_bitrate: Optional[str] = Field(default=None, max_length=16)
    audio_bitrate_kbps: int = Field(default=128, ge=32, le=512)
    resolution: VideoResolution = VideoResolution.original
    encoder_preset: EncoderPreset = EncoderPreset.veryfast
    hardware_acceleration: HardwareAcceleration = HardwareAcceleration.auto
    hardware_acceleration_used: Optional[str] = Field(default=None, max_length=64)
    priority: int = Field(default=0, ge=-10, le=10)
    queue_position: Optional[int] = Field(default=None, ge=1)
    estimated_start_seconds: Optional[int] = Field(default=None, ge=0)
    telemetry_history: list[dict[str, Any]] = Field(default_factory=list, max_length=120)
    log_download_url: Optional[str] = None


class BatchCreateError(BaseModel):
    index: int
    input_filename: Optional[str] = None
    source_root_key: Optional[str] = None
    source_path: Optional[str] = None
    error_code: Optional[str] = None
    message: str
    recoverable: bool = False


class JobBatchCreateResponse(BaseModel):
    jobs: list[JobRecord]
    errors: list[BatchCreateError] = Field(default_factory=list)
    idempotency_key: Optional[str] = None


class JobValidationItem(BaseModel):
    index: int
    valid: bool
    input_filename: Optional[str] = None
    source_root_key: Optional[str] = None
    source_path: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    recoverable: bool = False


class JobValidationResponse(BaseModel):
    items: list[JobValidationItem]
    valid_count: int
    invalid_count: int


class BatchSummaryDto(BaseModel):
    batch_id: str
    total: int
    queued: int = 0
    running: int = 0
    cancelled: int = 0
    completed: int = 0
    failed: int = 0
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class BatchListResponse(BaseModel):
    batches: list[BatchSummaryDto]
    next_cursor: Optional[str] = None


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    recoverable: bool = False
    details: dict[str, Any] | None = None


class StructuredErrorResponse(BaseModel):
    error: ErrorEnvelope


class JobStreamEvent(BaseModel):
    event: str
    timestamp: str
    data: dict[str, Any]


class OutputFileDto(BaseModel):
    filename: str
    size_bytes: int
    modified_at: str
    download_url: str
    preview_url: Optional[str] = None
    thumbnail_url: Optional[str] = None


class OutputListResponse(BaseModel):
    outputs: list[OutputFileDto]
    next_cursor: Optional[str] = None


class WorkerHealthResponse(BaseModel):
    status: str
    redis: str
    queue_depth: int
    running_jobs: int
    cpu_percent: float
    checked_at: str
    worker_online: bool = False
    heartbeat_age_seconds: Optional[float] = Field(default=None, ge=0)
    disk_total_bytes: int = Field(default=0, ge=0)
    disk_used_bytes: int = Field(default=0, ge=0)
    disk_free_bytes: int = Field(default=0, ge=0)
    disk_used_percent: float = Field(default=0, ge=0, le=100)
    hardware_encoders: list[str] = Field(default_factory=list)


class JobIdsRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _normalize_ids(self) -> "JobIdsRequest":
        normalized: list[str] = []
        for raw in self.job_ids:
            value = str(raw or "").strip()
            if value:
                normalized.append(value)
        # Deduplicate identical IDs
        self.job_ids = list(dict.fromkeys(normalized))
        return self


class JobActionSkip(BaseModel):
    job_id: str
    reason: str


class JobBulkActionResponse(BaseModel):
    updated: list[JobRecord] = Field(default_factory=list)
    skipped: list[JobActionSkip] = Field(default_factory=list)


class BatchActionResponse(BaseModel):
    batch_id: str
    action: str
    result: JobBulkActionResponse


class MediaRootDto(BaseModel):
    key: str
    label: str


class MediaBrowseEntryDto(BaseModel):
    type: str
    name: str
    rel_path: str


class MediaBrowseResponse(BaseModel):
    root_key: str
    current_path: str
    entries: list[MediaBrowseEntryDto]
    next_cursor: Optional[str] = None


class MediaSubtitleTrackDto(BaseModel):
    index: int
    language: str
    title: Optional[str] = None
    codec_name: Optional[str] = None


class MediaSubtitleProbeResponse(BaseModel):
    root_key: str
    path: str
    tracks: list[MediaSubtitleTrackDto]


class HealthResponse(BaseModel):
    status: str
    redis: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutoCleanupSettings(BaseModel):
    enabled: bool = False
    retention_days: int = Field(default=30, ge=1, le=365)
    keep_minimum_outputs: int = Field(default=10, ge=0, le=10000)
    delete_terminal_jobs: Optional[bool] = None
    job_retention_days: Optional[int] = Field(default=None, ge=1, le=3650)


class RetrySettings(BaseModel):
    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_seconds: int = Field(default=10, ge=1, le=3600)
    max_backoff_seconds: int = Field(default=300, ge=1, le=86400)


class DiskSafetySettings(BaseModel):
    minimum_free_bytes: int = Field(default=536_870_912, ge=0)


class UiPreferences(BaseModel):
    theme: str = Field(default="dark", pattern="^(dark|light|system)$")
    density: str = Field(default="comfortable", pattern="^(comfortable|compact)$")


class DefaultExportSettings(BaseModel):
    profile: ExportProfile = ExportProfile.h264_mp4
    video_export: VideoExport = VideoExport.mp4
    audio_export: AudioExport = AudioExport.copy
    subtitle_export: SubtitleExport = SubtitleExport.none
    subtitle_language: Optional[str] = Field(default=None, max_length=32)
    quality_crf: Optional[int] = Field(default=None, ge=0, le=51)
    target_video_bitrate: Optional[str] = Field(default=None, max_length=16)
    audio_bitrate_kbps: Optional[int] = Field(default=None, ge=32, le=512)
    resolution: Optional[VideoResolution] = None
    encoder_preset: Optional[EncoderPreset] = None
    hardware_acceleration: Optional[HardwareAcceleration] = None

    @field_validator("subtitle_language", mode="before")
    @classmethod
    def _normalize_subtitle_language(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class SystemSettings(BaseModel):
    worker_concurrency: int = Field(default=1, ge=1, le=8)
    default_export: DefaultExportSettings = Field(default_factory=DefaultExportSettings)
    auto_cleanup: AutoCleanupSettings = Field(default_factory=AutoCleanupSettings)
    retry: Optional[RetrySettings] = None
    disk_safety: Optional[DiskSafetySettings] = None
    ui: UiPreferences = Field(default_factory=UiPreferences)


class UploadResponse(BaseModel):
    input_filename: str
    size_bytes: int


class AuditEventDto(BaseModel):
    at: str
    actor: str
    action: str
    target: str
    details: dict[str, Any] = Field(default_factory=dict)
