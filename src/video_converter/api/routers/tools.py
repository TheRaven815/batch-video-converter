from __future__ import annotations

import asyncio
import errno
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request

from video_converter.api.auth import get_current_user
from video_converter.core.models import Mp4FixRequest, Mp4FixResponse
from video_converter.core.path_validation import validate_source_path

router = APIRouter()

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
}


def _build_media_roots_map() -> dict[str, object]:
    from video_converter.api import routes as routes_module

    cfg = getattr(routes_module, "settings", None)
    if cfg is None or not hasattr(cfg, "media_roots"):
        from video_converter.core.config import get_settings

        cfg = get_settings()
    return {root.key: root for root in cfg.media_roots}  # type: ignore[attr-defined]


def _resolve_input_path(payload: Mp4FixRequest) -> Path:
    from video_converter.api import routes as routes_module

    settings = getattr(routes_module, "settings", None)
    if settings is None:
        from video_converter.core.config import get_settings

        settings = get_settings()

    has_root = bool(payload.source_root_key)
    has_path = bool(payload.source_path)

    if has_root or has_path:
        if not (has_root and has_path):
            raise HTTPException(
                status_code=422, detail="source_root_key and source_path must be provided together"
            )
        try:
            return validate_source_path(
                _build_media_roots_map(),
                payload.source_root_key or "",
                payload.source_path or "",
                supported_extensions=VIDEO_EXTENSIONS,
            )
        except ValueError as exc:
            from video_converter.api.errors import path_validation_error

            # 400 for traversal to satisfy Faz 3 test expectation
            if exc.__class__.__name__ == "SourcePathTraversalError":
                raise path_validation_error(exc, status_code=400) from exc
            raise path_validation_error(exc) from exc

    if payload.input_filename:
        candidate = (Path(settings.input_dir) / str(payload.input_filename)).resolve()  # type: ignore[attr-defined]
        try:
            candidate.relative_to(Path(settings.input_dir).resolve())  # type: ignore[attr-defined]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid input_filename") from exc
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Input file not found")
        return candidate

    raise HTTPException(
        status_code=422, detail="source_root_key/source_path or input_filename required"
    )


@router.post(
    "/api/v1/tools/mp4-fix",
    response_model=Mp4FixResponse,
    dependencies=[Depends(get_current_user)],
)
async def mp4_fix(payload: Mp4FixRequest, request: Request) -> Mp4FixResponse:
    input_path = _resolve_input_path(payload)

    # Only mp4-ish inputs make sense for faststart fix; warn-free but enforce extension
    if input_path.suffix.lower() not in {".mp4", ".m4v", ".mov"}:
        raise HTTPException(status_code=422, detail="mp4-fix only supports .mp4/.m4v/.mov files")

    from video_converter.api import routes as routes_module
    from video_converter.core.config import get_settings
    from video_converter.worker.main import fix_mp4

    cfg = getattr(routes_module, "settings", None) or get_settings()
    cancel_event = threading.Event()

    async def watch_disconnect() -> None:
        while not cancel_event.is_set():
            if await request.is_disconnected():
                cancel_event.set()
                return
            await asyncio.sleep(0.1)

    disconnect_task = asyncio.create_task(watch_disconnect())
    repair_task = asyncio.create_task(
        asyncio.to_thread(fix_mp4, input_path, runtime_settings=cfg, cancel_event=cancel_event)
    )
    try:
        output_path = await asyncio.shield(repair_task)
    except asyncio.CancelledError:
        cancel_event.set()
        await asyncio.gather(repair_task, return_exceptions=True)
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        status = 499 if cancel_event.is_set() else 500
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        status = 507 if exc.errno == errno.ENOSPC else 500
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    finally:
        cancel_event.set()
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)

    return Mp4FixResponse(
        filename=output_path.name,
        download_url=f"/api/v1/outputs/{quote(output_path.name, safe='')}/download",
        source_path=payload.source_path,
        message="Onarılmış MP4 kopyası oluşturuldu. Kaynak dosya değiştirilmedi.",
    )
