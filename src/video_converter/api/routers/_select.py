from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.routing import APIRoute

from video_converter.api.routes import router as all_routes


def select_routes(predicate: Callable[[str], bool]) -> APIRouter:
    """Slice ``routes.router`` by *predicate* without duplicating host-path logic.

    The predicate is evaluated on ``route.path`` exactly as registered; callers
    must ensure their string (e.g. ``/api/v1/media``) covers future endpoints
    such as ``/api/v1/media/streams`` to avoid host-path leakage risks
    (PLAN.md §9). New modular endpoints added directly to the slice router
    (e.g. ``media.router``) bypass this select and are mounted verbatim.
    """

    router = APIRouter()
    for route in all_routes.routes:
        if not isinstance(route, APIRoute) or not predicate(route.path):
            continue
        router.add_api_route(
            route.path,
            route.endpoint,
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            methods=route.methods,
            operation_id=route.operation_id,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            include_in_schema=route.include_in_schema,
            response_class=route.response_class,
            name=route.name,
            openapi_extra=route.openapi_extra,
        )
    return router
