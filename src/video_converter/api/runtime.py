from __future__ import annotations

from typing import Any


class _RuntimeProxy:
    """Indirection so routers read the single runtime bound by ``routes``.

    ``api.routes`` remains the one place where the lifespan binds settings and
    repositories; tests also patch those attributes there. Proxy reads resolve
    at call time so an already-imported router still sees the current value.
    """

    def __init__(self, attribute: str) -> None:
        object.__setattr__(self, "_attribute", attribute)

    def _target(self) -> Any:
        from video_converter.api import routes

        return getattr(routes, self._attribute)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._target(), name, value)

    def __bool__(self) -> bool:
        return bool(self._target())

    def __iter__(self):
        return iter(self._target())

    def __getitem__(self, item: Any) -> Any:
        return self._target()[item]

    def __len__(self) -> int:
        return len(self._target())


settings = _RuntimeProxy("settings")
storage_client = _RuntimeProxy("storage_client")
job_repository = _RuntimeProxy("job_repository")
