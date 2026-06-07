from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaRoot:
    key: str
    label: str
    path: Path


QUEUE_NAME = "jobs:queue"
JOB_KEY_PREFIX = "job:"
JOBS_INDEX_KEY = "jobs:index"


@dataclass(frozen=True)
class Settings:
    redis_url: str
    data_root: Path
    input_dir: Path
    outputs_dir: Path
    temp_dir: Path
    logs_dir: Path
    data_dir: Path
    media_roots: tuple[MediaRoot, ...]
    worker_concurrency: int = 1


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



def get_settings() -> Settings:
    data_root = Path(os.getenv("DATA_ROOT", "/data"))
    input_dir = data_root / "input"
    outputs_dir = data_root / "outputs"
    temp_dir = data_root / "temp"
    logs_dir = data_root / "logs"
    data_dir = data_root / "data"

    media_mounts_raw = os.getenv("MEDIA_MOUNTS", "")
    media_roots = _parse_media_roots(media_mounts_raw, input_dir=input_dir)

    raw_concurrency = os.getenv("WORKER_CONCURRENCY", "1").strip()
    try:
        worker_concurrency = max(1, int(raw_concurrency))
    except ValueError:
        worker_concurrency = 1

    return Settings(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        data_root=data_root,
        input_dir=input_dir,
        outputs_dir=outputs_dir,
        temp_dir=temp_dir,
        logs_dir=logs_dir,
        data_dir=data_dir,
        media_roots=media_roots,
        worker_concurrency=worker_concurrency,
    )


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
