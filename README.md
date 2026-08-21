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
DATA_ROOT=/app-data
MEDIA_MOUNTS=Movies=/media/movies;Series=/media/series;Downloads=/media/downloads
```

### Unified Compose

The repository has a single `docker-compose.yml` for local development,
Coolify/Portainer, VPS deployments, and Raspberry Pi:

- `app` supervises Uvicorn and the Python worker through `entrypoint.sh`.
- `redis` runs `redis:7.2-alpine` with append-only persistence.
- `APP_DATA_SOURCE` can be a bind path such as `./data` (local) or the `app-data`
  named volume (Coolify). It is mounted at `/app-data:rw,z` (`compose:27`, quoted for spaces) to avoid
  conflicts with platform-managed `/data` storage. On startup, `entrypoint.sh:7` prepares `input/outputs/temp/logs/data` and drops to `app` uid 1000.
- Redis persistence uses the separate `redis-data` named volume (`compose:68` `redis-data:/data`) — **required**, do not remove; it holds the queue even if `APP_DATA_SOURCE` is a bind mount.
- All media mounts are read-only (`:ro`, `compose:28-30` quoted) and their host paths are configured with `MEDIA_MOVIES_SOURCE` etc. — container paths must match `MEDIA_MOUNTS`.
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
- Build/deploy type: **Docker Compose** (not `Dockerfile` — your earlier `Persistent storage` bug with `/data/coolify/.../media/*` was an `Application` deploy).
- Compose file: `docker-compose.yml`.
- Public service: `app`.
- Container port: `8765`.
- Healthcheck path: `/health/ready`.

> When `Docker Compose` is selected, Coolify shows `Docker Compose volume mounts are read-only here` in Persistent Storage. **Do not add manual bind mounts** — media mounts come from `docker-compose.yml:28-30` (`MEDIA_*_SOURCE -> /media/*:ro`). The only auto-created volume you should see is `redis-data -> /data` (`compose:68`), which is the Redis persistence volume and is required.

Fix from the `raspi` investigation (`docker inspect f153460c: Source /data/coolify/.../media/movies -> /media/movies` empty):

1. Rename `TV Series` to `TV_Series` on the host (`mv "/media/RAVEN/TV Series" /media/RAVEN/TV_Series`) — or quote the compose line (`compose:29` is now quoted for spaces).
2. In Coolify `Environment Variables`, set only the 4 required values from `.env.example` (see above).
3. Delete the stale `Persistent Storage` directories (`/data/coolify/.../media/*`, duplicate `/data`) — they are ignored in Compose mode.
4. `Redeploy`.

In Coolify's advanced build settings, leave `Include Source Commit in Build`
disabled and avoid forced/no-cache rebuilds unless troubleshooting. Including a
different commit hash in every build invalidates otherwise reusable Docker
layers.

Minimal Coolify env (paste into UI):

```env
MEDIA_MOUNTS=Movies=/media/movies;Series=/media/series;Downloads=/media/downloads
MEDIA_MOVIES_SOURCE=/media/RAVEN/Movies
MEDIA_SERIES_SOURCE=/media/RAVEN/TV_Series
MEDIA_DOWNLOADS_SOURCE=/media/RAVEN/Downloads
APP_DATA_SOURCE=app-data
```

All other `APP_*` / `JWT_*` / `REDIS_*` values work from defaults (`config.py:46-57`, `auth.py:121`, `config.py:163`). Set them only if you want to pre-provision credentials — otherwise use the first-run setup screen in the UI.

## Environment Variables

### Minimal required (Coolify / Docker Compose)

Copy `.env.example` — it already contains the only 4 values you must set:

```env
MEDIA_MOUNTS=Movies=/media/movies;Series=/media/series;Downloads=/media/downloads
MEDIA_MOVIES_SOURCE=/media/RAVEN/Movies
MEDIA_SERIES_SOURCE=/media/RAVEN/TV_Series
MEDIA_DOWNLOADS_SOURCE=/media/RAVEN/Downloads
APP_DATA_SOURCE=app-data   # local dev: ./data  ·  Coolify: app-data (named volume)
```

* `MEDIA_MOUNTS` — container paths shown in the UI (`src/video_converter/core/config.py:107`). Must match the `:/media/...:ro` targets in `docker-compose.yml:28-30`.
* `MEDIA_*_SOURCE` — **host** paths (the Raspi/VPS), not the laptop. They are only used by `docker-compose.yml` volume interpolation; the Python app never reads them directly (`extra="ignore"` in `config.py:43`).
* `APP_DATA_SOURCE` — writable `DATA_ROOT=/app-data` mount. Use the named volume `app-data` on Coolify, or `./data` locally.

> **Spaces:** host folders with spaces (`TV Series`) break the YAML volume line if unquoted (`docker-compose.yml:28` now quoted). Rename to `TV_Series` or keep the env quoted: `MEDIA_SERIES_SOURCE="/media/TV Series"`.

All other variables have safe defaults and can be omitted:

| Variable | Default (code) | When to set |
| --- | --- | --- |
| `VIDEO_CONVERTER_STORAGE` | `redis` (`config.py:47`) | Only for local dev without Redis: `local` |
| `REDIS_URL` | `redis://redis:6379/0` (`config.py:46`, `compose:9`) | Only if Redis is not on the compose network |
| `DATA_ROOT` | `/app-data` (`config.py:48`, `Dockerfile:18`) | Never — fixed to avoid Coolify's reserved `/data` |
| `WORKER_CONCURRENCY` | `1` | Increase only on strong hardware |
| `FFMPEG_THREADS` | `1` | 1–32, `WORKER × THREADS` = CPU pressure |
| `FFMPEG_STALL_TIMEOUT_SECONDS` | `300` | Only for very long encodes |
| `MIN_FREE_DISK_BYTES` / `MAX_UPLOAD_BYTES` | `512 MB` / `10 GB` | Rarely |
| `APP_USERNAME` / `APP_PASSWORD` | `admin` / *(none)* (`config.py:55`, `auth.py:121`) | **Optional.** If `APP_PASSWORD` is empty (default), the UI shows a first-run setup screen. `APP_USERNAME` is ignored unless `APP_PASSWORD` is set. |
| `JWT_SECRET` | *(auto-generated at `DATA_ROOT/data/jwt_secret` — `config.py:163`)* | Only to pin a secret across rebuilds |
| `APP_IMAGE` / `APP_PORT` / `V4L2_DEVICE` / `REDIS_HOST_PORT` | See compose | Only for custom ports or Pi 4 hardware encoder |

Important rules:

- `MEDIA_MOUNTS` paths are **container** paths; `MEDIA_*_SOURCE` are **host** paths — they must pair in `docker-compose.yml:28-30`.
- Do **not** add manual `Persistent Storage` directories in Coolify when using **Docker Compose** deploy type. The banner `Docker Compose volume mounts are read-only here` (your screenshot) is expected — volumes come from the compose file. Only the `redis-data` named volume (`volumes: redis-data:/data` in `compose:68`) is needed and is created automatically.
- Source media mounts must stay `:ro`; the app never writes there.
- Everything writable lives under `DATA_ROOT=/app-data` (`input`, `outputs`, `temp`, `logs`, `data`).

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

- UI opens but media roots are empty: verify `MEDIA_MOUNTS` and the matching `MEDIA_*_SOURCE` host paths. On `raspi`, run `docker inspect <app_id> --format '{{ json .Mounts }}' | python3 -m json.tool` — `Source` must be `/media/RAVEN/...`, not `/data/coolify/.../media/*` (stale `Application` deploy). `docker exec <id> ls -l /media/movies` must list host files; `total 0` means a Coolify `Persistent Storage` bind was created with a relative path. In **Docker Compose** mode (see Coolify section) leave Persistent Storage empty — `redis-data` volume is the only one needed.
- `GET /health/ready` returns 503: Redis is unavailable or `REDIS_URL` is wrong when using `VIDEO_CONVERTER_STORAGE=redis`.
- Local run complains about Redis: use default local storage or run `python run_local.py --storage local`; only `--storage redis` requires a local Redis-compatible service.
- Jobs stay queued: check that the worker service/process is running and can reach the same Redis/local store and media mounts as the API.
- Job fails before FFmpeg starts: check source path validation, file existence, extension support, and whether the media file is mounted read-only in the expected container path.
- Job fails during conversion: ensure `ffmpeg` and `ffprobe` are installed, inspect the job `log_tail`, and verify codec/container compatibility.
- WebM with `audio_export=copy` behaves differently: the worker normalizes WebM output for container compatibility and may fall back to Opus.
- Built UI is missing locally: run `cd frontend && npm run build` or use the Vite dev server while the API runs separately.
- Docker build fails in frontend stage: run `cd frontend && npm install && npm run build` locally to see TypeScript/Vite errors.
- Coolify cannot route the app: ensure public service is `app`, container port is `8765`, compose file is `docker-compose.yml`, and healthcheck path is `/health/ready`.
