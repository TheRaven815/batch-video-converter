from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any


class SourcePathValidationError(ValueError):
    """Base error for source path validation failures."""


class InvalidSourceRootKeyError(SourcePathValidationError):
    """Raised when the source root key is not configured."""


class SourcePathTraversalError(SourcePathValidationError):
    """Raised when source_path resolves outside the configured root."""


class SourceFileNotFoundError(SourcePathValidationError):
    """Raised when source_path does not point to an existing file."""


class UnsupportedSourceExtensionError(SourcePathValidationError):
    """Raised when source_path has an unsupported file extension."""


def _root_path(root: Any) -> Path:
    path = getattr(root, "path", root)
    return path if isinstance(path, Path) else Path(str(path))


def validate_source_path(
    media_roots: Mapping[str, Any],
    source_root_key: str,
    source_path: str,
    *,
    supported_extensions: Collection[str] | None = None,
    file_not_found_message: str = "source_path geçerli bir dosya değil",
) -> Path:
    root = media_roots.get(str(source_root_key))
    if root is None:
        raise InvalidSourceRootKeyError("Geçersiz source_root_key")

    root_path = _root_path(root).resolve()
    requested = (root_path / str(source_path)).resolve()
    try:
        requested.relative_to(root_path)
    except ValueError as exc:
        raise SourcePathTraversalError("source_path kök dizin dışında") from exc

    if not requested.exists() or not requested.is_file():
        raise SourceFileNotFoundError(file_not_found_message)

    if supported_extensions is not None and requested.suffix.lower() not in supported_extensions:
        raise UnsupportedSourceExtensionError("source_path desteklenmeyen uzantı")

    return requested
