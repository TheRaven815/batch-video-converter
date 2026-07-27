from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class MediaRoot:
    key: str
    label: str
    path: Path


QUEUE_NAME = "jobs:queue"
HIGH_PRIORITY_QUEUE_NAME = "jobs:queue:high"
LOW_PRIORITY_QUEUE_NAME = "jobs:queue:low"
PROCESSING_QUEUE_NAME = "jobs:processing"
JOB_KEY_PREFIX = "job:"
JOBS_INDEX_KEY = "jobs:index"
BATCHES_INDEX_KEY = "batches:index"
BATCH_JOBS_KEY_PREFIX = "batch:jobs:"
JOB_EVENTS_CHANNEL = "jobs:events"
WORKER_HEARTBEAT_KEY = "worker:heartbeat"


class Settings(BaseSettings):
    """Environment-backed application settings.

    Derived directories deliberately live here so their defaults cannot drift
    between the API, worker and launch scripts.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    redis_url: str = "redis://redis:6379/0"
    video_converter_storage: Literal["redis", "local"] = "redis"
    data_root: Path = Path("/data")
    media_mounts: str = ""
    worker_concurrency: int = Field(default=1, ge=1, le=8)
    ffmpeg_threads: int = Field(default=1, ge=1, le=32)
    ffmpeg_stall_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    min_free_disk_bytes: int = Field(default=536_870_912, ge=0)
    max_upload_bytes: int = Field(default=10_737_418_240, ge=1)
    app_username: str = "admin"
    app_password: str = ""
    jwt_secret: str = ""

    @field_validator("video_converter_storage", mode="before")
    @classmethod
    def _normalize_storage_backend(cls, value: object) -> str:
        normalized = str(value or "redis").strip().lower()
        return "local" if normalized in {"local", "file", "sqlite"} else normalized

    @field_validator("data_root", mode="before")
    @classmethod
    def _normalize_data_root(cls, value: object) -> Path:
        return Path(str(value or "/data"))

    @field_validator("worker_concurrency", mode="before")
    @classmethod
    def _normalize_worker_concurrency(cls, value: object) -> int:
        try:
            return max(1, min(8, int(str(value).strip())))
        except (TypeError, ValueError):
            return 1

    @field_validator("ffmpeg_threads", mode="before")
    @classmethod
    def _normalize_ffmpeg_threads(cls, value: object) -> int:
        try:
            return max(1, min(32, int(str(value).strip())))
        except (TypeError, ValueError):
            return 1

    @property
    def input_dir(self) -> Path:
        return self.data_root / "input"

    @property
    def outputs_dir(self) -> Path:
        return self.data_root / "outputs"

    @property
    def temp_dir(self) -> Path:
        return self.data_root / "temp"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def data_dir(self) -> Path:
        return self.data_root / "data"

    @property
    def media_roots(self) -> tuple[MediaRoot, ...]:
        return _parse_media_roots(self.media_mounts, input_dir=self.input_dir)


def _derive_key_from_label(label: str, used_keys: set[str]) -> str:
    """Derive a stable, deterministic root key from a media root label.

    The key is the label lowercased with non-alphanumeric characters replaced
    by underscores.  Duplicate keys are disambiguated with a numeric suffix
    (``_2``, ``_3``, …).
    """
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not key:
        key = "root"

    candidate = key
    suffix = 2
    while candidate in used_keys:
        candidate = f"{key}_{suffix}"
        suffix += 1

    used_keys.add(candidate)
    return candidate


def _parse_media_roots(raw_value: str, *, input_dir: Path) -> tuple[MediaRoot, ...]:
    if not raw_value.strip():
        return (MediaRoot(key="input", label="Input", path=input_dir.resolve()),)

    roots: list[MediaRoot] = []
    used_keys: set[str] = set()
    for idx, chunk in enumerate(raw_value.split(";"), start=1):
        chunk = chunk.strip()
        if not chunk:
            continue

        if "=" in chunk:
            left, right = chunk.split("=", 1)
            label = left.strip() or f"Root {idx}"
            path_str = right.strip()
        else:
            label = f"Root {idx}"
            path_str = chunk

        if not path_str:
            continue

        key = _derive_key_from_label(label, used_keys)
        roots.append(MediaRoot(key=key, label=label, path=Path(path_str).resolve()))

    if not roots:
        return (MediaRoot(key="input", label="Input", path=input_dir.resolve()),)

    return tuple(roots)


def _load_or_create_jwt_secret(data_dir: Path) -> str:
    """Persist an auto-generated JWT secret so sessions survive restarts.

    Regenerating the secret on every process start invalidated all sessions
    and broke multi-process deployments (each process got its own secret).
    """
    secret_path = data_dir / "jwt_secret"
    try:
        existing = secret_path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass

    import secrets

    secret = secrets.token_hex(32)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        secret_path.touch(mode=0o600, exist_ok=True)
        secret_path.write_text(secret)
        secret_path.chmod(0o600)
    except OSError:
        # DATA_ROOT not writable: fall back to an ephemeral secret rather than
        # failing startup; sessions will not survive restarts in that case.
        pass
    return secret


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    if settings.jwt_secret.strip():
        return settings
    return settings.model_copy(update={"jwt_secret": _load_or_create_jwt_secret(settings.data_dir)})


def ensure_runtime_dirs(settings: Settings) -> None:
    for path in [
        settings.data_root,
        settings.input_dir,
        settings.outputs_dir,
        settings.temp_dir,
        settings.logs_dir,
        settings.data_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
