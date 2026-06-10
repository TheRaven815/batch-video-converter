# AGENTS.md

This file is the project guide for coding agents and developers working in this repository. Keep the existing user changes intact; especially in a dirty worktree, touch only the changes you make yourself.

## Project Overview

Batch Video Converter is a FastAPI + Python worker + React/Vite application that lets users browse media folders on the server from a web UI and add selected video files to a batch FFmpeg conversion queue. The target scenarios are Raspberry Pi, small VPS, and Docker/Coolify deployments.

Main capabilities:

- FastAPI API with endpoints for health checks, media browsing, job creation, batch creation, job listing, bulk actions, output listing/downloading, and worker health.
- Python worker that pulls jobs from Redis or a local SQLite-like queue, runs FFmpeg/ffprobe, updates progress/telemetry/log_tail, and performs graceful shutdown.
- Vite + TypeScript + React frontend for browsing media roots, staging files, configuring export settings, probing subtitle languages, viewing the queue dashboard, batch summaries, bulk actions, and recent outputs.
- The frontend is built during the Docker image build; the final Python image includes the FastAPI runtime, packages, and FFmpeg.

## Architecture and Directories

- `src/video_converter/api/main.py`: FastAPI application. API endpoints, source path validation, idempotent batch create, media browse/subtitle probe, job stream, output download, and static frontend serving live here.
- `src/video_converter/worker/main.py`: Worker entry point. Pulls jobs from the queue, resolves input/output paths, normalizes export options, builds the FFmpeg command, reads progress, and updates job state.
- `src/video_converter/core/config.py`: Environment variables, `Settings`, `MediaRoot`, `DATA_ROOT` subdirectories, `MEDIA_MOUNTS` parsing, and queue key constants.
- `src/video_converter/core/models.py`: Pydantic API/job models and status enums. This is the source of truth for the backend contract.
- `src/video_converter/core/job_repository.py`: Job persistence, enqueue, requeue, delete, status update, and stale running recovery operations over Redis/Local store.
- `src/video_converter/core/path_validation.py`: Security layer that prevents escaping the configured root for `source_root_key + source_path` and checks file/extension validity.
- `src/video_converter/core/storage.py`: `redis` backend or SQLite-based Redis-like `LocalFileStore` for local development.
- `frontend/src/api.ts`: Frontend HTTP client functions.
- `frontend/src/models.ts`: TypeScript API/model types; keep them synchronized with the backend Pydantic models.
- `frontend/src/main.tsx`: Main React state/flow; media selection, staging, polling/SSE fallback, job refresh, batch submit, and preset logic.
- `frontend/src/components/ui.tsx`: Shared UI components.
- `frontend/src/utils/constants.ts` and `frontend/src/utils/helpers.ts`: Constants, default export settings, filtering/sorting/format helpers.
- `tests/`: pytest package split into `api`, `core`, and `worker` subgroups.
- `Dockerfile`, `docker-compose.yml`, `docker-compose.coolify.yml`, `.env.example`, `.env.coolify.example`: Build/deploy and environment contract.
- `run_local.py`: Local launcher that starts the API and worker from the same terminal without Docker.

## Technology Stack

- Backend: Python 3.11+, FastAPI, Uvicorn, Pydantic v2, redis-py.
- Worker: Python, FFmpeg, ffprobe, optional concurrency with a thread pool.
- Storage/queue: Redis for Docker/production; SQLite file for local development with `VIDEO_CONVERTER_STORAGE=local`.
- Frontend: React 19, TypeScript 6, Vite 8.
- Tests: pytest, httpx/TestClient, fake/local storage test helpers.
- Container: multi-stage Dockerfile; Node stage builds the frontend, Python slim final image, FFmpeg installed via apt.

## Commands

### Python Setup

Windows example:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
```

Linux/macOS example:

```sh
python -m venv venv
. venv/bin/activate
pip install -r requirements-dev.txt
```

`pytest.ini` contains `pythonpath = src`; tests do not need an extra `PYTHONPATH`. For manual Python commands, use `PYTHONPATH=src` or prefer `run_local.py`.

### Local Run (No Docker)

```bat
python run_local.py
python run_local.py --api-only
python run_local.py --worker-only
python run_local.py --host 127.0.0.1 --port 8765
python run_local.py --no-browser
python run_local.py --storage redis
```

Default local mode:

```env
VIDEO_CONVERTER_STORAGE=local
REDIS_URL=redis://localhost:6380/0
DATA_ROOT=./data
MEDIA_MOUNTS=Movies=./media/filmler;Series=./media/diziler
```

`local` mode does not require Redis; job records and the queue are kept in a SQLite file such as `DATA_ROOT/data/local_queue.sqlite3`. FFmpeg and ffprobe still must be available on PATH.

### Docker Compose

```bat
copy .env.example .env
mkdir data
mkdir media\filmler
mkdir media\diziler
docker compose up --build -d
```

UI: `http://localhost:8765/`

Local compose exposes the Redis port to the host with `6380:6379`. Do not expose Redis to the internet in public/production environments; keep it only inside the Docker network or a private network.

### Frontend

```bat
cd frontend && npm install
cd frontend && npm run dev
cd frontend && npm run build
cd frontend && npm run preview
```

- Vite dev server port is `5173`.
- `frontend/vite.config.ts` forwards API proxies to `http://localhost:8765`.
- Do not change the `base: '/ui/'` setting carelessly; FastAPI serves the built SPA under `/ui` and serves the entry file for root `/` requests.
- `frontend/dist/` is build output and must not be committed.

### Tests

```bat
venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

More targeted runs:

```bat
pytest tests/api
pytest tests/core
pytest tests/worker
pytest tests/worker/test_ffmpeg_command.py
```

The main quality gate for the frontend is `npm run build`; this command runs `tsc -b` and `vite build`.

## Environment Variables

- `VIDEO_CONVERTER_STORAGE`: `redis` or `local`. Use `redis` for Docker/production and `local` for single-machine local development.
- `REDIS_URL`: Connection URL when using the Redis backend. Docker default is `redis://redis:6379/0`.
- `DATA_ROOT`: Application working root. `input`, `outputs`, `temp`, `logs`, and `data` are created under it.
- `MEDIA_MOUNTS`: Media roots browsable in the UI. Format: `Label=/container/path;Label2=/container/path2`.
- `WORKER_CONCURRENCY`: Number of jobs to process at the same time. Default is `1`; keep it at 1 on Raspberry Pi/resource-constrained machines.

Paths in `MEDIA_MOUNTS` are paths inside the container, not host paths. These paths must match the compose volume lines exactly.

## API Notes

Important endpoints:

- `GET /health/live`, `GET /health/ready`
- `POST /api/v1/jobs`
- `POST /api/v1/jobs/validate`
- `POST /api/v1/jobs/batch`
- `GET /api/v1/jobs`
- `GET /api/v1/batches`
- `GET /api/v1/jobs/stream`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/worker/health`
- `GET /api/v1/outputs`
- `GET /api/v1/outputs/{filename}/download`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/jobs/bulk/cancel`
- `POST /api/v1/jobs/bulk/start`
- `POST /api/v1/jobs/bulk/archive`
- `POST /api/v1/jobs/bulk/delete`
- `GET /api/v1/media/roots`
- `GET /api/v1/media/browse`
- `GET /api/v1/media/subtitles`

When changing the API contract:

- Update `src/video_converter/core/models.py` and `frontend/src/models.ts` types together.
- Add tests for new response/error fields or update existing API tests.
- The structured error envelope uses `StructuredErrorResponse` / `ErrorEnvelope`; the frontend reads error messages from it in `api.ts`.
- The batch create endpoint supports `Idempotency-Key`; repeated calls with the same key may return a cached response.
- `list_jobs` filters must remain compatible with frontend `JobFilters`.
- When changing static frontend serving paths (`/ui` and `/`), check the Vite `base` setting and Docker build flow together.

## Worker Notes

- The worker reads job IDs from the `QUEUE_NAME = jobs:queue` list and reads/updates records through `JobRepository`.
- State flow: `queued -> running -> completed/failed`; cancellation uses `cancel_requested` and `cancelled`.
- Startup and the API lifespan may requeue stale `running` jobs after a configured duration.
- `_resolve_input_path` first uses `source_root_key + source_path`; `input_filename` with `DATA_ROOT/input` is supported for backward compatibility.
- `_resolve_export_options` normalizes video/audio/subtitle export fields. For a WebM target, the profile becomes `vp9_webm`; in WebM, `audio_export=copy` falls back to `opus` for stability.
- Output files are written under `DATA_ROOT/outputs` in the form `input_stem.jobprefix.ext`.
- FFmpeg progress information is reflected into `progress_percent`, `progress_phase`, `progress_message`, `progress_eta_seconds`, `progress_fps`, `progress_speed`, `progress_bitrate`, `log_tail`, and `timeline` fields.
- On SIGTERM/SIGINT, graceful shutdown terminates active FFmpeg processes and tries to mark running jobs as failed.
- If `WORKER_CONCURRENCY` is increased, test CPU/RAM, shared `/data` and media mount access, FFmpeg IO load, and Redis/local store behavior.

## Frontend Notes

- API call functions are centralized in `frontend/src/api.ts`; when adding a new endpoint, write a typed helper there.
- TypeScript models in `frontend/src/models.ts` must stay synchronized with backend Pydantic models.
- The main application manages stateful flows in `frontend/src/main.tsx`: media roots, browser entries, staging, export settings, jobs, outputs, batches, worker health, SSE/poll fallback, and local presets.
- Prefer splitting UI components into `frontend/src/components/ui.tsx`; do not overload the main file with unnecessary JSX.
- Fixed export options, allowed extensions, and polling intervals live in `frontend/src/utils/constants.ts`.
- Helper functions live in `frontend/src/utils/helpers.ts`; pay attention to sorting/formatting/normalization behavior with tests or builds.
- The LocalStorage preset key may affect existing users' settings; consider migration or backward compatibility when changing it.
- Avoid increasing the current language/tone inconsistency in UI text; new user-facing main text should be consistent and understandable where possible.

## Path, Security, and Storage Rules

- Media sources are read only from roots defined by `MEDIA_MOUNTS`.
- `source_path` is never trusted directly as a filesystem path; always apply root-relative validation such as `validate_source_path()`.
- Path traversal must be prevented: test scenarios such as `..`, absolute paths, escaping the root via symlink/resolve, and similar cases.
- `MEDIA_MOUNTS` uses container paths; host path information must not leak into API payloads or frontend state.
- Media mounts should be `:ro` in Docker. The application does not modify source media.
- The only writable area is under `DATA_ROOT`: `input`, `outputs`, `temp`, `logs`, `data`.
- Preserve filename sanitization on output download endpoints; user input must not escape `DATA_ROOT/outputs`.
- Do not commit real `.env` files, media archives, output videos, SQLite/Redis data, logs, or build/cache folders.
- For public repository hygiene, follow the list in the README: real `.env*` files, `data/`, `media/`, large video files, `frontend/node_modules/`, `frontend/dist/`, `.pytest_cache/`, `.coverage*`, `venv/`, etc. must stay out.

## Docker and Deploy

- `Dockerfile` has two stages: `node:22-slim` builds the frontend, and `python:3.11-slim` creates the final runtime image.
- The final image ships with defaults `PYTHONPATH=/app/src`, `VIDEO_CONVERTER_STORAGE=redis`, and `DATA_ROOT=/data`.
- FFmpeg is installed into the final image via apt; without FFmpeg, the worker cannot perform real conversions.
- `docker-compose.yml` is for local development; the `app` service runs both API and worker via `entrypoint.sh`, exposes `8765:8765`; Redis exposes `6380:6379`.
- `docker-compose.coolify.yml` is for Coolify; example public port is `7777:8765`, health check is `/health/ready`, and it has `app-data` and `redis-data` named volumes.
- In Coolify, the public service must be `app`, the container port must be `8765`, and the compose file must be `docker-compose.coolify.yml`.
- Do not expose the Redis host port publicly in production. Keep Redis inside the Docker network/private network.
- When changing media mounts, update both `MEDIA_MOUNTS` and the `app` service volume lines at the same time.
- Before increasing the worker replica count, verify FFmpeg resource usage and access to the same volumes.

## Code Standards

- Preserve the existing `from __future__ import annotations` style in Python code.
- Use type hints; keep Pydantic models as the central source for the API contract.
- Do not remove backward compatibility fields unnecessarily (`profile`, legacy `input_filename`, legacy `processing -> running` normalization, etc.).
- Perform job state updates through `JobRepository`; do not scatter Redis key/list manipulation.
- Consider error messages and recoverable/error_code behavior together with frontend impact.
- Add short explanatory comments for long/complex logic if helpful; do not comment obvious lines.
- Preserve type-only imports in the frontend, and centralize API types in `models.ts`.
- Do not commit generated/build output; `frontend/dist` is produced by Docker build or local build.
- Prefer ASCII; use non-ASCII only when there is a clear reason or the target file already uses it. This file is intentionally written mostly in ASCII.

## Test and Change Guidelines

When making changes, run at least the relevant targeted tests:

- API endpoint/model/path change: `pytest tests/api tests/core`
- Worker/FFmpeg command/export change: `pytest tests/worker tests/core`
- Storage/repository change: `pytest tests/core/test_job_repository.py tests/core/test_local_storage.py`
- Frontend type/API/UI change: `cd frontend && npm run build`
- Docker/env change: `docker compose config` and, if needed, `docker compose up --build`

When writing tests:

- Use fake files and monkeypatching in unit tests that do not require real media/FFmpeg.
- Preserve negative scenarios such as path traversal, unsupported extension, missing file, invalid root, and mixed batch.
- In worker FFmpeg command tests, check container/codec compatibility, subtitle export, and legacy profile behavior.
- Do not break idempotency, pagination/cursor, bulk action, or stale recovery behavior.

## Agent Working Rules

- Do not revert the user's existing changes; if you did not make it, do not delete, reset, or check it out.
- Do not use destructive commands such as `git reset --hard`, `git checkout --`, bulk deletion, or similar operations unless the user explicitly asks for or approves them.
- Do not perform broad refactors without review. First read the relevant README, backend, frontend, test, and Docker/env files.
- If the API contract changes, consider the backend model, frontend type, API client, and tests together.
- Be extra careful when changing path/security/storage areas; escaping the root and leaking host paths are the most critical risks.
- Do not create/commit large files, media, outputs, secrets, or local runtime data.
- If `rg` is unavailable, use an alternative search method; on Windows, prefer `findstr` or a Python one-liner.
- Check that you are in the project root before running commands.
- If you cannot run tests, state why in the final note and list which commands should be run.

## Common Change Guide

### Adding a New API Field

1. Update the Pydantic model in `src/video_converter/core/models.py`.
2. Update create/list/response logic in `src/video_converter/api/main.py`.
3. Update the type in `frontend/src/models.ts` and, if needed, the helper in `frontend/src/api.ts`.
4. If it will be shown in the UI, handle it in `frontend/src/main.tsx` or `frontend/src/components/ui.tsx`.
5. Run `pytest tests/api` and `cd frontend && npm run build`.

### Adding a New Export Option

1. Update backend `JobCreateRequest`/`JobRecord` field validation and worker `_resolve_export_options` logic.
2. Test FFmpeg command construction and container/codec compatibility.
3. Update frontend `VideoExport`/`AudioExport`/`SubtitleExport` types, constants, and UI option lists.
4. Add new cases for `tests/worker/test_export_options.py` and `tests/worker/test_ffmpeg_command.py`.

### Adding a New Media Root

1. Append `Label=/container/path` to `MEDIA_MOUNTS` in `.env` or `.env.coolify`.
2. Add a `:ro` volume for the same container path to the `app` service in `docker-compose.yml` or `docker-compose.coolify.yml`.
3. Verify that the host directory exists and that the container path matches `MEDIA_MOUNTS`.
4. Check `GET /api/v1/media/roots` and the browse flow in the UI.

### Changing Job Listing or Dashboard

1. Review backend `GET /api/v1/jobs` filter/cursor behavior.
2. Update frontend `JobFilters`, `listJobs`, `sortJobs`, `mergeJobs`, and dashboard state together.
3. Make sure you do not break SSE/poll fallback behavior.
4. Run `pytest tests/api/test_job_listing.py` and `npm run build`.

### Changing the Storage Backend or Queue

1. Try to preserve the `JobRepository` API; Redis and `LocalFileStore` should support the same methods.
2. Test the impact of `QUEUE_NAME`, `JOBS_INDEX_KEY`, and `JOB_KEY_PREFIX`.
3. Do not break lock/WAL/busy_timeout behavior for multi-process local SQLite usage.
4. Run `pytest tests/core/test_job_repository.py tests/core/test_local_storage.py tests/api/test_integration.py`.

### Changing Docker/Coolify

1. Update `Dockerfile`, the relevant compose file, and env example files together.
2. Check `DATA_ROOT`, `MEDIA_MOUNTS`, volume paths, and healthcheck compatibility.
3. Be careful not to expose Redis on a public port.
4. Run `docker compose config` and, if possible, a build smoke test.
