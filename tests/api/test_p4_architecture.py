from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from redis.asyncio import Redis as AsyncRedis

from video_converter.api import auth
from video_converter.api.async_storage import (
    AsyncJobRepository,
    AsyncLocalStore,
    create_async_storage_client,
)
from video_converter.api.errors import error_code, path_validation_error
from video_converter.api.main import create_app
from video_converter.core.config import Settings
from video_converter.core.models import JobRecord, JobStatus, now_iso
from video_converter.core.path_validation import (
    InvalidSourceRootKeyError,
    SourcePathTraversalError,
    UnsupportedSourceExtensionError,
)


def test_settings_loads_environment_and_derives_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("VIDEO_CONVERTER_STORAGE", "local")
    monkeypatch.setenv("WORKER_CONCURRENCY", "3")
    monkeypatch.setenv("FFMPEG_THREADS", "2")
    monkeypatch.setenv("MEDIA_MOUNTS", "Movies=/media/movies")

    settings = Settings(_env_file=None)

    assert settings.video_converter_storage == "local"
    assert settings.worker_concurrency == 3
    assert settings.ffmpeg_threads == 2
    assert settings.outputs_dir == tmp_path / "outputs"
    assert settings.media_roots[0].key == "movies"


def test_async_storage_factory_uses_redis_asyncio() -> None:
    client = create_async_storage_client(
        Settings(
            _env_file=None,
            video_converter_storage="redis",
            redis_url="redis://localhost:6379/15",
        )
    )
    assert isinstance(client, AsyncRedis)


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (InvalidSourceRootKeyError("bad root"), "invalid_source_root"),
        (SourcePathTraversalError("outside"), "path_traversal_blocked"),
        (UnsupportedSourceExtensionError("extension"), "unsupported_extension"),
    ],
)
def test_path_errors_keep_typed_codes(exception: Exception, expected: str) -> None:
    assert error_code(path_validation_error(exception)) == expected


def test_auth_storage_must_be_supplied_by_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_storage_client", None)
    with pytest.raises(RuntimeError, match="not initialized"):
        auth.get_storage()


def test_app_factory_does_not_create_runtime_storage() -> None:
    application = create_app()
    assert application.state is not None


def test_local_async_storage_is_reserved_for_local_mode(tmp_path: Path) -> None:
    client = create_async_storage_client(
        Settings(
            _env_file=None,
            video_converter_storage="local",
            data_root=tmp_path,
        )
    )
    assert isinstance(client, AsyncLocalStore)


def test_async_repository_enqueues_and_reads_local_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        storage = create_async_storage_client(
            Settings(
                _env_file=None,
                video_converter_storage="local",
                data_root=tmp_path,
            )
        )
        repository = AsyncJobRepository(storage)
        timestamp = now_iso()
        record = JobRecord(
            id="async-job",
            status=JobStatus.queued,
            profile="h264_mp4",
            input_filename="movie.mp4",
            created_at=timestamp,
            updated_at=timestamp,
        )

        await repository.enqueue(record)
        stored = await repository.get(record.id)
        page, next_cursor = await repository.list_records_page()

        assert stored is not None and stored.id == record.id
        assert [item.id for item in page] == [record.id]
        assert next_cursor is None
        await storage.aclose()

    asyncio.run(exercise())
