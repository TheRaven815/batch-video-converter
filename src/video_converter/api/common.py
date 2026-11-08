from __future__ import annotations

from inspect import isawaitable
from typing import Any

# Single source for the media extensions accepted by browsing, uploads,
# probes and source-path validation.
VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".webm",
        ".m4v",
        ".mpg",
        ".mpeg",
    }
)


def build_media_roots_map(settings_obj: Any) -> dict[str, Any]:
    """Map media root keys to their configured ``MediaRoot`` objects."""
    roots = getattr(settings_obj, "media_roots", None) or []
    return {root.key: root for root in roots}


def runtime_media_roots_map() -> dict[str, Any]:
    """Root map for routers, reading the module-level settings at call time."""
    from video_converter.api import routes as routes_module

    cfg = getattr(routes_module, "settings", None)
    if cfg is None or not hasattr(cfg, "media_roots"):
        from video_converter.core.config import get_settings

        cfg = get_settings()
    return build_media_roots_map(cfg)


async def maybe_await(value: Any) -> Any:
    """Await ``value`` when needed, so sync and async storages share a call site."""
    return await value if isawaitable(value) else value
