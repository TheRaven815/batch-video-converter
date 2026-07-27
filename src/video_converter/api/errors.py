from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from video_converter.core.models import ErrorEnvelope, StructuredErrorResponse
from video_converter.core.path_validation import (
    InvalidSourceRootKeyError,
    SourceFileNotFoundError,
    SourcePathTraversalError,
    UnsupportedSourceExtensionError,
)


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    recoverable: bool = False


class ApiHTTPException(HTTPException):
    def __init__(self, status_code: int, error: ApiError, *, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=error.message, headers=headers)
        self.error = error


_STATUS_ERRORS = {
    400: "bad_request",
    401: "authentication_failed",
    404: "not_found",
    422: "validation_error",
    500: "internal_error",
    503: "service_unavailable",
}


def path_validation_error(exc: Exception, *, status_code: int = 422) -> ApiHTTPException:
    mapping = {
        InvalidSourceRootKeyError: "invalid_source_root",
        SourcePathTraversalError: "path_traversal_blocked",
        SourceFileNotFoundError: "not_found",
        UnsupportedSourceExtensionError: "unsupported_extension",
    }
    code = next(
        (value for kind, value in mapping.items() if isinstance(exc, kind)), "validation_error"
    )
    return ApiHTTPException(
        status_code,
        ApiError(code, str(exc), recoverable=status_code in {409, 422, 503}),
    )


def error_code(exc: HTTPException) -> str:
    if isinstance(exc, ApiHTTPException):
        return exc.error.code
    return _STATUS_ERRORS.get(exc.status_code, "api_error")


async def structured_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = str(exc.detail) if exc.detail else "Request failed"
    configured = exc.error if isinstance(exc, ApiHTTPException) else None
    envelope = StructuredErrorResponse(
        error=ErrorEnvelope(
            code=configured.code if configured else error_code(exc),
            message=configured.message if configured else message,
            recoverable=(
                configured.recoverable if configured else exc.status_code in {409, 422, 503}
            ),
            details={"path": request.url.path},
        )
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope.model_dump(),
        headers=exc.headers,
    )
