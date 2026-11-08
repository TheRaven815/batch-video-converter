from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

AUDIT_LOG_NAME = "audit.jsonl"
AUDIT_ROTATED_NAME = "audit.jsonl.1"
AUDIT_MAX_BYTES = 2_000_000

# Both auth and route handlers append here; a shared lock keeps concurrent
# writers from interleaving partial JSON lines.
_audit_lock = threading.Lock()


def write_audit_event(logs_dir: Path | None, event: dict[str, Any]) -> None:
    """Append one JSONL audit event, rotating the file once it gets large."""
    if logs_dir is None:
        return
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / AUDIT_LOG_NAME
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
        with _audit_lock:
            try:
                if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > AUDIT_MAX_BYTES:
                    os.replace(path, logs_dir / AUDIT_ROTATED_NAME)
            except OSError:
                # Rotation is best-effort; appending is still safe.
                pass
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        # Audit persistence must never break the request that triggered it.
        return


def read_recent_audit_lines(logs_dir: Path | None, limit: int) -> list[str]:
    """Return at most ``limit`` trailing lines without loading the whole file."""
    if logs_dir is None or limit <= 0:
        return []
    path = logs_dir / AUDIT_LOG_NAME
    if not path.exists():
        return []

    block_size = 64 * 1024
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= limit:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            data = handle.read(read_size) + data

    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-limit:]
