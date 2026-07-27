# Batch Video Converter

Batch Video Converter is a FastAPI, Python worker, and React/Vite application for browsing media folders on a server and sending selected video files to a batch FFmpeg conversion queue. It is designed for Raspberry Pi, small VPS, local development, Docker Compose, and Coolify deployments.

The application keeps source media read-only, writes runtime data under a single `DATA_ROOT`, and exposes a web UI for selecting server-side media, configuring export options, monitoring jobs, and downloading completed outputs.

## Features

- **Authentication**: JWT-based secure login system for the web UI and API endpoints.
- **Dark Theme UI**: A sleek, responsive dark theme with a dedicated settings panel.
- Server-side media browser backed by configured `MEDIA_MOUNTS` roots.
- Batch job creation with validation and idempotent `Idempotency-Key` support.
- FFmpeg worker with progress, telemetry, log tail, graceful shutdown, and stale running job recovery.
- Export controls for video container, audio handling, subtitle handling, and subtitle language selection.
- Queue dashboard with job filters, SSE/poll fallback, batch summaries, bulk actions, and recent outputs.
- Redis-backed queue/storage for Docker and production deployments.
- SQLite-backed local queue/storage for single-machine development without Redis.
- Docker image that builds the frontend and includes the FastAPI runtime plus FFmpeg.

## Architecture

```text
Browser UI
  -> FastAPI API (src/video_converter/api/main.py)
  -> JobRepository (src/video_converter/core/job_repository.py)
  -> Redis or local SQLite-like store (src/video_converter/core/storage.py)
  -> Python worker (src/video_converter/worker/main.py)
  -> FFmpeg / ffprobe
  -> DATA_ROOT/outputs
```

Key parts:

- `src/video_converter/api/main.py`: FastAPI app, API endpoints, job creation, media browsing, subtitle probing, output downloads, SSE stream, and static frontend serving.
- `src/video_converter/api/auth.py`: JWT token generation, password verification, and authentication dependency.
- `src/video_converter/worker/main.py`: Worker loop, queue consumption, input/output path resolution, export option normalization, FFmpeg command construction, progress parsing, and shutdown handling.
- `src/video_converter/core/config.py`: Environment parsing, `Settings`, media root parsing, `DATA_ROOT` subdirectories, auth credentials, and queue constants.
- `src/video_converter/core/models.py`: Pydantic API and job models; this is the backend contract source of truth.
- `src/video_converter/core/path_validation.py`: Root-relative source path validation and path traversal protection.
- `src/video_converter/core/storage.py`: Redis storage or SQLite-backed local storage.
- `frontend/src/`: React/Vite frontend, typed API client, models, UI components, constants, and helpers.
- `tests/`: pytest suites for API, core/storage/path behavior, and worker/FFmpeg command logic.

## Technology Stack

- Backend: Python 3.11+, FastAPI, Uvicorn, Pydantic v2, redis-py.
- Worker: Python, FFmpeg, ffprobe, optional concurrent processing via `WORKER_CONCURRENCY`.
- Storage and queue: Redis for Docker/production; local SQLite-backed store for development.
- Frontend: React 19, TypeScript 6, Vite 8.
- Testing: pytest, httpx/TestClient, fake/local storage test helpers.
- Container: multi-stage Dockerfile with `node:22-slim` frontend build and `python:3.11-slim` final image.

## Requirements

For local development without Docker:

- Python 3.11+
- Node.js/npm compatible with the frontend lockfile
- `ffmpeg` and `ffprobe` available on `PATH`
- Python dependencies from `requirements.txt` or `requirements-dev.txt`
- Redis only if you run with `VIDEO_CONVERTER_STORAGE=redis`

For Docker/Coolify:

- Docker and Docker Compose
- Host media directories mounted read-only into the combined `app` container
- Redis available through the Compose service
- Enough CPU/RAM for FFmpeg jobs; keep concurrency at `1` on constrained devices

## Quick Start With Docker Compose

1. Copy the environment template:

```bat
copy .env.example .env
```

On Linux/macOS:

```sh
cp .env.example .env
```

2. Create the example runtime and media folders:

```bat
mkdir data
mkdir media\movies
mkdir media\series
mkdir media\downloads
```

On Linux/macOS:

```sh
mkdir -p data media/movies media/series media/downloads
```

3. Start the stack:

```sh
docker compose up --build -d
```

4. Open the UI:

```text
http://localhost:8765/
```

Local `docker-compose.yml` exposes:

- API/UI: `8765:8765`
- Redis: `6380:6379`

Do not expose Redis publicly in production. Keep Redis inside the Docker network or a private network.

## Local Development Without Docker

The Python package uses a `src/` layout. Tests automatically get `pythonpath = src` from `pytest.ini`; manual commands should use `PYTHONPATH=src` or the `run_local.py` launcher.

Create a Python environment:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
```

Linux/macOS:

```sh
python -m venv venv
. venv/bin/activate
pip install -r requirements-dev.txt
```

Install and build the frontend if you want FastAPI to serve the production UI locally:

```sh
cd frontend
npm install
npm run build
cd ..
```

Run API and worker from one terminal:

```sh
python run_local.py
```

Useful launcher options:

```sh
python run_local.py --api-only
python run_local.py --worker-only
python run_local.py --host 127.0.0.1 --port 8765
python run_local.py --no-browser
python run_local.py --storage redis
python run_local.py --skip-redis-check
python run_local.py --rebuild-frontend
```

The launcher binds to `127.0.0.1` by default. It skips `npm ci` when installed
dependencies match the lockfile and skips the frontend build when `dist/` is
newer than its sources. `--worker-only` never runs npm; use
`--rebuild-frontend` to force a clean install and build.

Default local settings used by `run_local.py` when environment variables are not set:

```env
VIDEO_CONVERTER_STORAGE=local
REDIS_URL=redis://localhost:6380/0
DATA_ROOT=./data
MEDIA_MOUNTS=Movies=./media/Movies;Series=./media/Series
```

In `local` mode, Redis is not required. Job records and queue state are stored in a SQLite-backed file such as `DATA_ROOT/data/local_queue.sqlite3`. FFmpeg and ffprobe are still required for real conversions.

## Frontend Development

The frontend lives in `frontend/` and is a Vite + TypeScript + React app.

```sh
cd frontend
npm install
npm run dev
```

- Vite dev server listens on port `5173`.
- `frontend/vite.config.ts` proxies API calls to `http://localhost:8765`.
- Run the FastAPI API separately when using the Vite dev server.
- The Vite base is `/ui/`; FastAPI serves the built SPA under `/ui` and returns the SPA entry for `/`.

Production frontend build:

```sh
cd frontend
npm run build
```

`frontend/dist/` is generated output and should not be committed. Docker builds it automatically in the Node build stage.

## Docker and Coolify

### Docker Image

`Dockerfile` has two stages:

- `frontend-builder`: installs frontend dependencies and runs `npm run build`.
- `backend-final`: installs FFmpeg, Python dependencies, copies `src/`, and copies only `frontend/dist/` into the final image.

The final image runs as an unprivileged `app` user under `tini`, includes an
OCI healthcheck, and defaults to:

```env
PYTHONPATH=/app/src
VIDEO_CONVERTER_STORAGE=redis
DATA_ROOT=/data
MEDIA_MOUNTS=Media=/data/input
```

### Unified Compose

The repository has a single `docker-compose.yml` for local development,
Coolify/Portainer, VPS deployments, and Raspberry Pi:

- `app` supervises Uvicorn and the Python worker through `entrypoint.sh`.
- `redis` runs `redis:7.2-alpine` with append-only persistence.
- `APP_DATA_SOURCE` can be a bind path such as `./data` or the `app-data`
  named volume. It is mounted at `/app-data` inside the container to avoid
  conflicts with platform-managed `/data` storage. On startup, the container
  prepares the runtime subdirectories and then runs the application as the
  unprivileged `app` user.
- Redis persistence uses the separate `redis-data` named volume.
- All media mounts are read-only and their host paths are configured with
  `MEDIA_MOVIES_SOURCE`, `MEDIA_SERIES_SOURCE`, and
  `MEDIA_DOWNLOADS_SOURCE`.
- The app healthcheck uses `/health/ready`, and Compose allows up to ten minutes
  for active FFmpeg work to drain during shutdown.
- Startup healthchecks use short, broadly compatible intervals so Redis and the
  app can become healthy quickly without requiring Docker 25's `start_interval`.
- Docker BuildKit keeps separate npm and pip download caches. Unchanged
  dependency layers are reused normally; when a lockfile or requirements file
  changes, unchanged packages do not need to be downloaded again. Coolify uses
  BuildKit; if an older local Docker installation reports that `--mount`
  requires BuildKit, run the build with `DOCKER_BUILDKIT=1`.

Copy `.env.example` to `.env`, customize it, and always start the same file:

```sh
docker compose up --build -d
```

On Raspberry Pi 4, set `V4L2_DEVICE=/dev/video11` in `.env`. Leave it unset on
Pi 5, which has no H.264 hardware encoder. For constrained systems, keep
`WORKER_CONCURRENCY=1` and reduce `APP_MEMORY_LIMIT` if needed.

When adding media roots, update both places together:

1. Add `Label=/container/path` to `MEDIA_MOUNTS` in `.env`.
2. Point the corresponding `MEDIA_*_SOURCE` variable at its host directory.

### Coolify

Coolify uses the same `docker-compose.yml`. Configure its environment variables
in the Coolify UI; a second env or Compose file is not required.

Recommended Coolify settings:

- Source: Git repository.
- Build/deploy type: Docker Compose.
- Compose file: `docker-compose.yml`.
- Public service: `app`.
- Container port: `8765`.
- Healthcheck path: `/health/ready`.

In Coolify's advanced build settings, leave `Include Source Commit in Build`
disabled and avoid forced/no-cache rebuilds unless troubleshooting. Including a
different commit hash in every build invalidates otherwise reusable Docker
layers.

Recommended Coolify variables include `APP_DATA_SOURCE=app-data`,
`APP_PORT=8765`, and host paths for the three `MEDIA_*_SOURCE` variables.
`MEDIA_MOUNTS` continues to use the fixed container paths. Redis host access is
bound to loopback only; application traffic uses the private Compose network.

## Environment Variables

| Variable | Default / example | Description |
| --- | --- | --- |
| `APP_IMAGE` | `batch-video-converter:local` | Image name/tag. Set a pinned GHCR tag when deploying a prebuilt release. |
| `APP_PORT` | `8765` | Host port mapped to container port 8765. |
| `APP_DATA_SOURCE` | `./data` | Host data path or the `app-data` named volume; mounted at `/app-data`. |
| `MEDIA_MOVIES_SOURCE` | `./media/movies` | Host path mounted read-only at `/media/movies`. |
| `MEDIA_SERIES_SOURCE` | `./media/series` | Host path mounted read-only at `/media/series`. |
| `MEDIA_DOWNLOADS_SOURCE` | `./media/downloads` | Host path mounted read-only at `/media/downloads`. |
| `V4L2_DEVICE` | `/dev/null` | Set to `/dev/video11` only on Raspberry Pi 4. |
| `VIDEO_CONVERTER_STORAGE` | `redis` | Storage backend. Use `redis` for Docker/production and `local` for single-machine development. |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL when `VIDEO_CONVERTER_STORAGE=redis`. Local Redis usually uses `redis://localhost:6380/0`. |
| `DATA_ROOT` | `/data` | Writable application root. The app creates `input`, `outputs`, `temp`, `logs`, and `data` under it. |
| `MEDIA_MOUNTS` | `Label=/container/path;Label2=/container/path2` | Read-only media roots shown in the UI. If empty, the app falls back to `DATA_ROOT/input`. |
| `WORKER_CONCURRENCY` | `1` | Number of jobs processed concurrently by the worker. Increase only when CPU/RAM/IO capacity is sufficient. |
| `FFMPEG_THREADS` | `1` | Maximum encoder threads per FFmpeg process (1–32). Approximate CPU pressure is `WORKER_CONCURRENCY × FFMPEG_THREADS`. |
| `APP_USERNAME` | `admin` | Username for accessing the web UI and API (only used together with `APP_PASSWORD`). |
| `APP_PASSWORD` | *(unset)* | Optional pre-provisioned password. There is no default: if unset, the UI shows a one-time first-run setup screen to create the admin account. |
| `JWT_SECRET` | *(auto-generated)* | Secret key used to sign JWT authentication tokens. If not set, a secret is generated once and persisted under `DATA_ROOT/data/jwt_secret`. |

Important rules:

- `MEDIA_MOUNTS` paths are container paths, not host paths.
- Each `MEDIA_MOUNTS` path must match a read-only mount on the combined `app` service.
- Source media mounts should be read-only (`:ro`).
- Runtime output, logs, temp files, and local SQLite data belong under `DATA_ROOT`.

## API Summary

Health and readiness (Unauthenticated):

- `GET /health/live`
- `GET /health/ready`

Authentication:

- `POST /api/v1/auth/login` is the canonical login endpoint. It accepts OAuth2 form data (`username`, `password`) and returns `{ "access_token": "...", "token_type": "bearer" }`. Invalid credentials return HTTP 401 with `Invalid username or password`.
- `POST /api/v1/auth/token` remains available as a backward-compatible alias for older clients, but new integrations should use `/login`.

Jobs and batches (Require Bearer Token):

- `POST /api/v1/jobs` creates a single job.
- `POST /api/v1/jobs/validate` validates a batch payload without enqueueing jobs.
- `POST /api/v1/jobs/batch` creates multiple jobs; supports `Idempotency-Key`.
- `GET /api/v1/jobs` lists jobs with filters and cursor header support.
- `GET /api/v1/jobs/{job_id}` returns one job.
- `GET /api/v1/batches` returns batch summaries.
- `GET /api/v1/jobs/stream` streams job updates.
- `POST /api/v1/jobs/{job_id}/cancel` requests cancellation for one job.
- `POST /api/v1/jobs/bulk/cancel` cancels multiple jobs.
- `POST /api/v1/jobs/bulk/start` requeues/start eligible jobs.
- `POST /api/v1/jobs/bulk/archive` archives jobs.
- `POST /api/v1/jobs/bulk/delete` deletes jobs.

Media and outputs (Require Bearer Token):

- `GET /api/v1/media/roots` lists configured media roots.
- `GET /api/v1/media/browse?root_key=...&path=...&q=...` browses a root-relative path.
- `GET /api/v1/media/subtitles?root_key=...&path=...` probes subtitle streams with ffprobe.
- `GET /api/v1/outputs` lists generated output files.
- `GET /api/v1/outputs/{filename}/download` downloads a sanitized output file.
- `GET /api/v1/worker/health` reports queue depth, running job count, and storage health.

System settings (Require Bearer Token):

- `GET /api/v1/settings` returns persisted system settings with safe defaults for missing legacy fields.
- `POST /api/v1/settings` persists `worker_concurrency`, `default_export`, `auto_cleanup`, and `ui` preferences. Older clients may still send only `worker_concurrency`.

Typical settings payload:

```json
{
  "worker_concurrency": 1,
  "default_export": {
    "profile": "h264_mp4",
    "video_export": "mp4",
    "audio_export": "copy",
    "subtitle_export": "none",
    "subtitle_language": null
  },
  "auto_cleanup": {
    "enabled": false,
    "retention_days": 30,
    "keep_minimum_outputs": 10
  },
  "ui": {
    "theme": "dark",
    "density": "comfortable"
  }
}
```

Typical job payload:

```json
{
  "source_root_key": "movies",
  "source_path": "Example/Movie.mkv",
  "profile": "h264_mp4",
  "video_export": "mp4",
  "audio_export": "copy",
  "subtitle_export": "embedded",
  "subtitle_language": "eng"
}
```

Export option values used by the UI:

- `video_export`: `mp4`, `mkv`, `webm`
- `audio_export`: `copy`, `aac`, `mp3`, `opus`
- `subtitle_export`: `none`, `embedded`, `separate_srt`

Supported source video extensions are `mp4`, `mov`, `mkv`, `avi`, `webm`, `m4v`, `mpg`, and `mpeg`.

## Frontend Usage

1. Open `http://localhost:8765/` for the built UI or the Vite dev URL during frontend development.
2. On first run, create the admin account in the setup screen (or log in with the pre-provisioned `APP_USERNAME` / `APP_PASSWORD` if set — there are no default credentials).
3. Use the media browser to select files from configured server roots.
4. Add selected files to the staging list and remove or select entries as needed.
5. Choose export settings:
   - Video output container: MP4, MKV, or WebM.
   - Audio mode/codec: copy, AAC, MP3, or Opus.
   - Subtitle mode: none, embedded, or separate SRT.
   - Subtitle language: detected dynamically from selected media when available.
6. Create jobs and monitor queue status, progress, batch summaries, worker health, and recent outputs.
7. Use bulk actions for cancel, start/requeue, archive, or delete where eligible.
8. Download completed outputs from the outputs panel.

## Test Commands

Install development dependencies first:

```sh
pip install -r requirements-dev.txt
```

Run Python lint and format checks:

```sh
ruff check .
black --check .
```

Format Python code automatically when needed:

```sh
ruff check . --fix
black .
```

Run all backend tests:

```sh
pytest
```

Run targeted backend tests:

```sh
pytest tests/api
pytest tests/core
pytest tests/worker
pytest tests/worker/test_ffmpeg_command.py
pytest tests/core/test_job_repository.py tests/core/test_local_storage.py
```

Run the frontend quality gate:

```sh
cd frontend
npm run build
```

Check Docker Compose configuration:

```sh
docker compose config
```

For Docker changes, also run a smoke test when possible:

```sh
docker compose up --build
```

## Directory Structure

```text
.
|-- AGENTS.md                         # Developer/agent project guide
|-- Dockerfile                        # Multi-stage frontend + backend image
|-- docker-compose.yml                # Unified local/Coolify/Pi stack
|-- .env.example                      # Unified deployment environment template
|-- pyproject.toml                    # Python tool config for Black and Ruff
|-- pytest.ini                        # pytest config with pythonpath=src
|-- requirements.txt                  # Runtime Python dependencies
|-- requirements-dev.txt              # Runtime + test dependencies
|-- run_local.py                      # Local API/worker launcher
|-- frontend/                         # Vite + React + TypeScript app
|   |-- package.json
|   |-- vite.config.ts
|   `-- src/
|-- src/video_converter/
|   |-- api/main.py                   # FastAPI app and routes
|   |-- core/config.py                # Settings and media root parsing
|   |-- core/job_repository.py        # Job persistence and queue operations
|   |-- core/models.py                # Pydantic API models
|   |-- core/path_validation.py       # Source path security checks
|   |-- core/storage.py               # Redis/local storage backends
|   `-- worker/main.py                # FFmpeg worker
`-- tests/
    |-- api/
    |-- core/
    `-- worker/
```

## Security and Path Notes

- The API never trusts `source_path` as a raw filesystem path.
- Media input is resolved as `source_root_key + source_path` and must remain inside the configured media root after `Path.resolve()`.
- Path traversal, absolute-path escape attempts, missing files, invalid roots, and unsupported extensions are rejected.
- Host paths should not leak into API payloads or frontend state; the UI deals with root keys and root-relative paths.
- Docker media mounts should be read-only. The application does not modify source media.
- The only writable application area should be `DATA_ROOT`: `input`, `outputs`, `temp`, `logs`, and `data`.
- Output downloads use filename sanitization and must not escape `DATA_ROOT/outputs`.
- Do not commit real `.env` files, media archives, generated outputs, SQLite/Redis data, logs, `frontend/node_modules/`, `frontend/dist/`, `.pytest_cache/`, `.coverage*`, `venv/`, or similar local artifacts.

## Troubleshooting

- UI opens but media roots are empty: verify `MEDIA_MOUNTS` and the matching `MEDIA_*_SOURCE` host paths.
- `GET /health/ready` returns 503: Redis is unavailable or `REDIS_URL` is wrong when using `VIDEO_CONVERTER_STORAGE=redis`.
- Local run complains about Redis: use default local storage or run `python run_local.py --storage local`; only `--storage redis` requires a local Redis-compatible service.
- Jobs stay queued: check that the worker service/process is running and can reach the same Redis/local store and media mounts as the API.
- Job fails before FFmpeg starts: check source path validation, file existence, extension support, and whether the media file is mounted read-only in the expected container path.
- Job fails during conversion: ensure `ffmpeg` and `ffprobe` are installed, inspect the job `log_tail`, and verify codec/container compatibility.
- WebM with `audio_export=copy` behaves differently: the worker normalizes WebM output for container compatibility and may fall back to Opus.
- Built UI is missing locally: run `cd frontend && npm run build` or use the Vite dev server while the API runs separately.
- Docker build fails in frontend stage: run `cd frontend && npm install && npm run build` locally to see TypeScript/Vite errors.
- Coolify cannot route the app: ensure public service is `app`, container port is `8765`, compose file is `docker-compose.yml`, and healthcheck path is `/health/ready`.
