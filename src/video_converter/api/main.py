from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import wraps
from inspect import isawaitable
from types import ModuleType

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from video_converter.api import auth, routes
from video_converter.api.async_storage import AsyncJobRepository, create_async_storage_client
from video_converter.api.errors import structured_http_exception_handler, structured_validation_exception_handler
from video_converter.api.routers import audit, batches, health, jobs, media, outputs, tools, ui
from video_converter.api.routers import settings as settings_router
from video_converter.core.config import ensure_runtime_dirs, get_settings
from video_converter.core.storage import StorageError

logger = logging.getLogger("api")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s service=api message=%(message)s",
    )


async def storage_error_handler(_request, exc: StorageError) -> JSONResponse:
    logger.exception("storage backend failure", exc_info=exc)
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "storage_unavailable",
                "message": "Storage backend unavailable",
                "recoverable": True,
            }
        },
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Own runtime resources and release them together on shutdown."""
    has_injected_runtime = all(
        name in routes.__dict__ for name in ("settings", "storage_client", "job_repository")
    )
    if has_injected_runtime:
        configured_settings = routes.settings
        storage = routes.storage_client
    else:
        configured_settings = get_settings()
        ensure_runtime_dirs(configured_settings)
        storage = create_async_storage_client(configured_settings)
        repository = AsyncJobRepository(storage)
        routes.configure_runtime(configured_settings, storage, repository)
    if auth._storage_client is None:
        auth.configure_runtime(configured_settings, storage)

    try:
        yield
    finally:
        close = getattr(storage, "aclose", None) or getattr(storage, "close", None)
        if close is not None:
            result = close()
            if isawaitable(result):
                await result


def create_app() -> FastAPI:
    application = FastAPI(title="Video Converter API", version="0.1.0", lifespan=lifespan)
    application.add_exception_handler(HTTPException, structured_http_exception_handler)
    application.add_exception_handler(StorageError, storage_error_handler)
    application.add_exception_handler(RequestValidationError, structured_validation_exception_handler)
    application.include_router(auth.router, prefix="/api/v1")
    for route_group in (
        health.router,
        jobs.router,
        batches.router,
        outputs.router,
        media.router,
        settings_router.router,
        audit.router,
        tools.router,
        ui.router,
    ):
        application.include_router(route_group)

    if routes.frontend_dir.exists():
        application.mount(
            "/ui",
            StaticFiles(directory=str(routes.frontend_dir), html=True),
            name="ui",
        )
        logger.info("serving frontend from %s", routes.frontend_dir)
    else:
        logger.warning("frontend dist not found at %s", routes.frontend_dir)
    return application


app = create_app()

# Compatibility handles for tests and integrations that injected the previous
# module-level runtime. Assignments are mirrored to ``api.routes`` below.
settings = None
storage_client = None
job_repository = None


def __getattr__(name: str):
    """Keep internal helper imports compatible while they migrate to services."""
    if name in {"get_current_user", "get_stream_user"}:
        return getattr(auth, name)
    try:
        value = getattr(routes, name)
    except AttributeError:
        raise
    if name.startswith("_") or not inspect.iscoroutinefunction(value):
        return value

    @wraps(value)
    def run_compat(*args, **kwargs):
        return asyncio.run(value(*args, **kwargs))

    return run_compat


class _CompatibilityModule(ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        if name in {"settings", "storage_client", "job_repository"} or hasattr(routes, name):
            setattr(routes, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _CompatibilityModule
