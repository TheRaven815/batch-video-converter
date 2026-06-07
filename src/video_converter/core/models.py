from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"


class JobCreateRequest(BaseModel):
    input_filename: Optional[str] = Field(default=None, max_length=1024)
    source_root_key: Optional[str] = Field(default=None, max_length=64)
    source_path: Optional[str] = Field(default=None, max_length=2048)
    profile: str = Field(default="h264_mp4", max_length=100)
    video_export: str = Field(default="mp4", max_length=16)
    audio_export: str = Field(default="copy", max_length=16)
    subtitle_export: str = Field(default="none", max_length=32)
    subtitle_language: Optional[str] = Field(default=None, max_length=32)


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

    profile: str
    video_export: str = Field(default="mp4", max_length=16)
    audio_export: str = Field(default="copy", max_length=16)
    subtitle_export: str = Field(default="none", max_length=32)
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


class OutputListResponse(BaseModel):
    outputs: list[OutputFileDto]
    next_cursor: Optional[str] = None


class WorkerHealthResponse(BaseModel):
    status: str
    redis: str
    queue_depth: int
    running_jobs: int
    checked_at: str


class JobIdsRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_ids(self) -> "JobIdsRequest":
        normalized: list[str] = []
        for raw in self.job_ids:
            value = str(raw or "").strip()
            if value:
                normalized.append(value)
        # aynı id'leri tekilleştir
        self.job_ids = list(dict.fromkeys(normalized))
        return self


class JobActionSkip(BaseModel):
    job_id: str
    reason: str


class JobBulkActionResponse(BaseModel):
    updated: list[JobRecord] = Field(default_factory=list)
    skipped: list[JobActionSkip] = Field(default_factory=list)


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
