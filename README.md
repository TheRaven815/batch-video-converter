# Batch Video Converter

[![CI](https://github.com/TheRaven815/batch-video-converter/actions/workflows/ci.yml/badge.svg)](https://github.com/TheRaven815/batch-video-converter/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ready-blue?logo=docker)](Dockerfile)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Self-hosted batch video converter: browse server-side media folders and queue FFmpeg conversions from a web UI. Built for Raspberry Pi, small VPS, and Docker/Coolify — with a local dev mode that needs no Redis.

Source media stays read-only. All runtime data lives under a single `DATA_ROOT`. No personal data or host paths leak to the frontend.

## Table of Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Supported Formats](#supported-formats)
- [Requirements](#requirements)
- [Quick Start With Docker Compose](#quick-start-with-docker-compose)
- [Local Development Without Docker](#local-development-without-docker)
- [Frontend Development](#frontend-development)
- [Docker and Coolify](#docker-and-coolify)
- [Environment Variables](#environment-variables)
- [API Summary](#api-summary)
- [Frontend Usage](#frontend-usage)
- [Test Commands](#test-commands)
- [Directory Structure](#directory-structure)
- [Security and Path Notes](#security-and-path-notes)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Authentication**: JWT login with first-run setup screen, bcrypt passwords, short-lived tokens.
- **Dark Theme UI**: Responsive dark theme, settings panel, density/theme preferences, multilingual (EN/TR).
- Server-side media browser backed by configured `MEDIA_MOUNTS` roots (no client uploads required, but upload is also supported).
- Batch job creation with validation and idempotent `Idempotency-Key` support.
- FFmpeg worker with progress, telemetry (fps/speed/bitrate), log tail, timeline, graceful shutdown, and stale-job recovery.
- Export controls for video container, audio handling, subtitle handling, and subtitle language selection — including multi-stream selection.
- Queue dashboard with filters, SSE + poll fallback, batch summaries, bulk actions (cancel/start/archive/delete), and recent outputs with download/preview.
- MP4 repair tool (`ffmpeg -c copy -movflags +faststart`) for fragmented/corrupt MP4s.
- Redis-backed queue/storage for Docker/production; SQLite-backed local queue for single-machine dev.
- Docker multi-stage image that builds the frontend and ships FastAPI + FFmpeg.

## Screenshots

> Add screenshots to `docs/` and reference them here. Example:
>
> ```md
> ![Dashboard](docs/screenshots/dashboard.png)
> ![Convert](docs/screenshots/convert.png)
> ```
>
> Until images are added, see [Frontend Usage](#frontend-usage) for the UI flow.

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

- `src/video_converter/api/main.py`: FastAPI app factory, lifespan, static frontend serving.
- `src/video_converter/api/routers/*`: Modular routers (`jobs`, `batches`, `media`, `outputs`, `settings`, `health`, `tools`, `ui`).
- `src/video_converter/api/auth.py`: JWT issue/verify, password hashing, auth dependencies.
- `src/video_converter/worker/main.py`: Worker loop, queue consumption via `BLMOVE` reliable queue, input/output resolution, export normalization, FFmpeg command building, progress parsing, heartbeat, and shutdown handling.
- `src/video_converter/core/config.py`: Env parsing, `Settings`, `MEDIA_MOUNTS` parsing, `DATA_ROOT` subdirs, queue constants.
- `src/video_converter/core/models.py`: Pydantic models — backend contract source of truth.
- `src/video_converter/core/path_validation.py`: Root-relative validation and traversal protection.
- `src/video_converter/core/storage.py`: Redis or SQLite-backed `LocalFileStore` (WAL, `busy_timeout`, `BEGIN IMMEDIATE`).
- `frontend/src/`: React/Vite app, typed API client, models, pages, components, hooks.
- `tests/`: pytest suites for API, core/storage/path, and worker/FFmpeg logic.

## Technology Stack

- Backend: Python 3.11+, FastAPI, Uvicorn, Pydantic v2, redis-py, pydantic-settings.
- Worker: Python, FFmpeg, ffprobe, `WORKER_CONCURRENCY` thread pool, hardware accel probe (`h264_v4l2m2m` on Pi 4).
- Storage/queue: Redis for Docker/production; SQLite file for dev (`VIDEO_CONVERTER_STORAGE=local`).
- Frontend: React 19, TypeScript 6, Vite 8, TanStack Query, wouter, sonner, lucide-react.
- Testing: pytest, httpx/TestClient, fake/local storage helpers.
- Container: multi-stage `node:22-slim` (build) + `python:3.11-slim` (runtime) with FFmpeg, `tini`, non-root `app` user.

## Supported Formats

**Input** (probed via ffprobe, extensions checked in `path_validation.py`):

| Container | Extensions |
| --- | --- |
| MP4 / MOV / M4V | `.mp4`, `.mov`, `.m4v` |
| Matroska | `.mkv` |
| WebM | `.webm` |
| AVI / MPEG | `.avi`, `.mpg`, `.mpeg` |

**Output**:

| Video container (`video_export`) | Profile | Audio handling (`audio_export`) | Subtitle (`subtitle_export`) |
| --- | --- | --- | --- |
| `mp4` | `h264_mp4` | `copy` (fallback to `aac` if incompatible), `aac`, `mp3`, `opus` | `none`, `embedded` (`mov_text`), `separate_srt` |
| `mkv` | `h264_mkv` / `h265_mp4` | `copy`, `aac`, `mp3`, `opus` | `none`, `embedded` (copy), `separate_srt` |
| `webm` | `vp9_webm` | `copy` (fallback to `opus`), `opus`, `vorbis` | `none`, `embedded` (`webvtt`), `separate_srt` |

Notes:

- WebM `audio_export=copy` falls back to `opus` for stability (see `worker/main.py:_resolve_export_options`).
- Audio `copy` is validated per container; incompatible source codecs are transcoded.
- Subtitle languages are probed with `ffprobe`; `und` (undefined) is selectable.

## Requirements

For local development without Docker:

- Python 3.11+
- Node.js / npm compatible with `frontend/package-lock.json`
- `ffmpeg` and `ffprobe` on `PATH`
- Python deps from `requirements.txt` or `requirements-dev.txt`
- Redis only if `VIDEO_CONVERTER_STORAGE=redis`

For Docker / Coolify:

- Docker and Docker Compose (BuildKit recommended)
- Host media directories mounted read-only into the `app` container
- Redis via the Compose service (or external `REDIS_URL`)
- Enough CPU/RAM for FFmpeg; keep `WORKER_CONCURRENCY=1` on Pi / small VPS

## Quick Start With Docker Compose

1. Copy the environment template:

```bat
copy .env.example .env
```

On Linux/macOS:

```sh
cp .env.example .env
```

2. Edit `.env` — set your **host** media paths (examples are generic; use your server/NAS paths):

```env
MEDIA_MOUNTS=Movies=/media/movies;Series=/media/series;Downloads=/media/downloads
MEDIA_MOVIES_SOURCE=/srv/media/movies
MEDIA_SERIES_SOURCE=/srv/media/series
MEDIA_DOWNLOADS_SOURCE=/srv/media/downloads
APP_DATA_SOURCE=app-data   # local: ./data  ·  Coolify: app-data (named volume)
```

> If a host folder contains spaces, keep the quotes: `MEDIA_SERIES_SOURCE="/srv/media/TV Series"`. The compose file quotes the bind source so spaces are safe.

3. Create runtime folders for local dev (skip on Coolify where `app-data` volume is used):

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

4. Start the stack:

```sh
docker compose up --build -d
```

5. Open the UI:

```text
http://localhost:8765/
```

Local `docker-compose.yml` exposes:

- API/UI: `8765:8765`
- Redis: `127.0.0.1:6380:6379` (loopback only)

Do not expose Redis publicly in production. Keep it inside the Docker network or a private network.

## Local Development Without Docker

The Python package uses a `src/` layout. Tests get `pythonpath = src` from `pytest.ini`; manual commands should use `PYTHONPATH=src` or the `run_local.py` launcher.

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

The launcher binds to `127.0.0.1` by default. It skips `npm ci` when installed dependencies match the lockfile and skips the frontend build when `dist/` is newer than sources. `--worker-only` never runs npm; use `--rebuild-frontend` to force a clean install and build.

Default local settings used by `run_local.py` when env vars are not set:

```env
VIDEO_CONVERTER_STORAGE=local
REDIS_URL=redis://localhost:6380/0
DATA_ROOT=./data
MEDIA_MOUNTS=Movies=./media/movies;Series=./media/series
```

In `local` mode, Redis is not required. State is stored in `DATA_ROOT/data/local_queue.sqlite3`. FFmpeg/ffprobe are still required for real conversions.

## Frontend Development

The frontend lives in `frontend/` (Vite + TypeScript + React).

```sh
cd frontend
npm install
npm run dev
```

- Vite dev server: `http://localhost:5173` (proxies `/api` and `/health` to `http://localhost:8765`).
- Run the FastAPI API separately when using the Vite dev server.
- Vite `base` is `/ui/`; FastAPI serves the built SPA under `/ui` and at `/`.

Production build:

```sh
cd frontend
npm run build   # runs tsc -b && vite build
```

Checks (must pass in CI):

```sh
cd frontend
npm run lint          # eslint
npm run format:check  # prettier --check .
npm run build
```

Auto-fix formatting:

```sh
cd frontend
npm run format        # prettier --write .
```

`frontend/dist/` is generated and must not be committed. Docker builds it in the Node stage.

## Docker and Coolify

### Docker Image

`Dockerfile` has two stages:

- `frontend-builder`: installs frontend deps and runs `npm run build`.
- `backend-final`: installs FFmpeg, Python deps, copies `src/`, and copies only `frontend/dist/` into the final image.

The final image runs as unprivileged `app` (uid 1000) under `tini`, includes an OCI healthcheck, and defaults to:

```env
PYTHONPATH=/app/src
VIDEO_CONVERTER_STORAGE=redis
DATA_ROOT=/app-data
MEDIA_MOUNTS=Movies=/media/movies;Series=/media/series;Downloads=/media/downloads
```

### Unified Compose

Single `docker-compose.yml` for local, Coolify/Portainer, VPS, and Raspberry Pi:

- `app` supervises Uvicorn and the worker via `entrypoint.sh`.
- `redis` is `redis:7.2-alpine` with AOF persistence.
- `APP_DATA_SOURCE` is a bind path (`./data` locally) or named volume (`app-data` on Coolify), mounted at `/app-data:rw,z` to avoid conflicts with platform-managed `/data`. `entrypoint.sh` prepares `input/outputs/temp/logs/data` and drops to `app`.
- Redis persistence uses the separate `redis-data` named volume (`redis-data:/data`) — **required**, do not remove.
- Media mounts are read-only (`:ro`, long syntax for correct interpolation of paths with spaces). Host paths come from `MEDIA_*_SOURCE`; container paths must match `MEDIA_MOUNTS`.
- Healthcheck: `/health/ready`. `stop_grace_period: 10m` lets FFmpeg drain.
- BuildKit caches for npm and pip are used; Coolify uses BuildKit. On old Docker without BuildKit, build with `DOCKER_BUILDKIT=1`.

Always start the same file:

```sh
docker compose up --build -d
```

On Raspberry Pi 4, set `V4L2_DEVICE=/dev/video11` in `.env` for hardware H.264. Leave unset on Pi 5 (no H.264 HW encoder). Keep `WORKER_CONCURRENCY=1` on constrained devices.

When adding media roots, update both places:

1. `Label=/container/path` in `MEDIA_MOUNTS` (`.env`).
2. Host path variable (`MEDIA_*_SOURCE`) in `.env` / `docker-compose.yml` volume.

### Coolify

Coolify uses the same `docker-compose.yml`. Configure env vars in the Coolify UI; no second compose file needed.

Recommended Coolify settings:

- Source: Git repository.
- Build/deploy type: **Docker Compose** (not `Dockerfile`).
- Compose file: `docker-compose.yml`.
- Public service: `app`.
- Container port: `8765`.
- Healthcheck path: `/health/ready`.

> When `Docker Compose` is selected, Coolify shows `Docker Compose volume mounts are read-only here` in Persistent Storage. **Do not add manual bind mounts** — media mounts come from `docker-compose.yml` long-syntax volumes (`MEDIA_*_SOURCE -> /media/*:ro`). The only volume you need is `redis-data -> /data`, created automatically.

Common pitfall: if media appears empty (`docker exec <app> ls -l /media/movies` is empty while host has files), the stack was deployed as **Application** instead of **Docker Compose**. Switch the deploy type, remove stale Persistent Storage entries, and redeploy.

In advanced build settings, leave `Include Source Commit in Build` disabled unless troubleshooting — it invalidates Docker layer cache.

Minimal Coolify env (paste into UI and adjust host paths):

```env
MEDIA_MOUNTS=Movies=/media/movies;Series=/media/series;Downloads=/media/downloads
MEDIA_MOVIES_SOURCE=/srv/media/movies
MEDIA_SERIES_SOURCE=/srv/media/series
MEDIA_DOWNLOADS_SOURCE=/srv/media/downloads
APP_DATA_SOURCE=app-data
```

All other `APP_*` / `JWT_*` / `REDIS_*` values have safe defaults (`config.py:46-57`, `auth.py:121`, `config.py:163`). Set them only to pre-provision credentials — otherwise use the first-run setup screen.

## Environment Variables

### Minimal required (Coolify / Docker Compose)

Copy `.env.example` — it already contains the only values you must set:

```env
MEDIA_MOUNTS=Movies=/media/movies;Series=/media/series;Downloads=/media/downloads
MEDIA_MOVIES_SOURCE=/srv/media/movies
MEDIA_SERIES_SOURCE=/srv/media/series
MEDIA_DOWNLOADS_SOURCE=/srv/media/downloads
APP_DATA_SOURCE=app-data   # local dev: ./data  ·  Coolify: app-data (named volume)
```

- `MEDIA_MOUNTS` — container paths shown in the UI (`config.py:107`). Must match `:/media/...:ro` targets in `docker-compose.yml`.
- `MEDIA_*_SOURCE` — **host** paths (server/NAS). Used only by compose volume interpolation; the app never reads them (`extra="ignore"` in `config.py:43`).
- `APP_DATA_SOURCE` — writable `DATA_ROOT=/app-data` mount. Use named volume on Coolify, bind path locally.

> **Spaces:** host folders with spaces break YAML if unquoted. Either rename (`TV Series` → `TV_Series`) or keep the env quoted: `MEDIA_SERIES_SOURCE="/srv/media/TV Series"` (compose long syntax is already quoted).

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

Rules:

- `MEDIA_MOUNTS` are **container** paths; `MEDIA_*_SOURCE` are **host** paths — they must pair in `docker-compose.yml`.
- Do **not** add manual `Persistent Storage` directories in Coolify when using **Docker Compose** deploy type. Volumes come from the compose file; only `redis-data` is needed.
- Media mounts must stay `:ro`; the app never writes there.
- Everything writable lives under `DATA_ROOT=/app-data` (`input`, `outputs`, `temp`, `logs`, `data`).

## API Summary

Health and readiness (unauthenticated):

- `GET /health/live`
- `GET /health/ready`

Authentication:

- `POST /api/v1/auth/login` — canonical login, OAuth2 form (`username`, `password`) → `{ access_token, token_type: "bearer" }`. 401 on invalid credentials.
- `POST /api/v1/auth/token` — backward-compatible alias for `/login`.

Jobs and batches (Bearer token required):

- `POST /api/v1/jobs` — create one job.
- `POST /api/v1/jobs/validate` — validate a batch without enqueueing.
- `POST /api/v1/jobs/batch` — create many jobs; supports `Idempotency-Key`.
- `GET /api/v1/jobs` — list jobs with filters and cursor pagination.
- `GET /api/v1/jobs/{job_id}` — get one job.
- `GET /api/v1/batches` — batch summaries.
- `GET /api/v1/jobs/stream` — SSE job updates (ticket auth, `request:jobs:stream`).
- `POST /api/v1/jobs/{job_id}/cancel` — cancel one job.
- `POST /api/v1/jobs/bulk/cancel` — bulk cancel.
- `POST /api/v1/jobs/bulk/start` — requeue/start eligible jobs.
- `POST /api/v1/jobs/bulk/archive` — archive jobs.
- `POST /api/v1/jobs/bulk/delete` — delete jobs.

Media and outputs (Bearer token required):

- `GET /api/v1/media/roots` — configured roots.
- `GET /api/v1/media/browse?root_key=...&path=...&q=...` — browse root-relative path.
- `GET /api/v1/media/streams?root_key=...&path=...` — probe all streams (video/audio/subtitle) via ffprobe.
- `GET /api/v1/media/subtitles?root_key=...&path=...` — legacy subtitle probe (kept for compatibility).
- `GET /api/v1/outputs` — list generated outputs.
- `GET /api/v1/outputs/{filename}/download` — sanitized download.
- `GET /api/v1/worker/health` — queue depth, running count, storage health.
- `POST /api/v1/tools/mp4-fix` — repair MP4 (`+genpts` + `+faststart`, stream copy).

System settings (Bearer token required):

- `GET /api/v1/settings` — persisted settings with safe defaults.
- `POST /api/v1/settings` — persist `worker_concurrency`, `default_export`, `auto_cleanup`, `ui`. Older clients may send only `worker_concurrency`.

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

Export values used by the UI:

- `video_export`: `mp4`, `mkv`, `webm`
- `audio_export`: `copy`, `aac`, `mp3`, `opus`
- `subtitle_export`: `none`, `embedded`, `separate_srt`

Supported source extensions: `mp4`, `mov`, `mkv`, `avi`, `webm`, `m4v`, `mpg`, `mpeg`.

OpenAPI schema is served at `/openapi.json`; frontend types are generated via `npm run generate:api` (`openapi-typescript`).

## Frontend Usage

1. Open `http://localhost:8765/` (or Vite dev URL `http://localhost:5173`).
2. On first run, create the admin account in the setup screen (or log in with pre-provisioned `APP_USERNAME`/`APP_PASSWORD`).
3. Browse server media roots and select files.
4. Add selected files to the staging list; remove or re-select as needed.
5. Choose export settings:
   - Video container: MP4, MKV, or WebM.
   - Audio: copy, AAC, MP3, or Opus (with per-stream selection when available).
   - Subtitle: none, embedded, or separate SRT.
   - Subtitle language: auto-detected from selected media.
6. Use **Streams** to pick specific audio/subtitle indexes; apply to all staged files if desired.
7. Create jobs and monitor queue status, progress, batch summaries, worker health, and recent outputs.
8. Use bulk actions (cancel/start/archive/delete) where eligible.
9. Download completed outputs or use preview; repair broken MP4s via the MP4 Fix tool.

## Test Commands

Install dev deps first:

```sh
pip install -r requirements-dev.txt
```

Python lint and format:

```sh
ruff check .
black --check .
```

Auto-fix Python formatting:

```sh
ruff check . --fix
black .
```

Run all backend tests:

```sh
pytest
```

Targeted:

```sh
pytest tests/api
pytest tests/core
pytest tests/worker
pytest tests/worker/test_ffmpeg_command.py
pytest tests/core/test_job_repository.py tests/core/test_local_storage.py
```

Frontend quality gate (must pass in CI):

```sh
cd frontend
npm run lint
npm run format:check
npm run build
```

Docker compose config check:

```sh
docker compose config
```

Smoke test (if Docker available):

```sh
docker compose up --build
```

## Directory Structure

```text
.
|-- Dockerfile                        # Multi-stage frontend + backend image
|-- docker-compose.yml                # Unified local/Coolify/Pi stack
|-- .env.example                      # Deployment env template (generic paths)
|-- entrypoint.sh                     # Supervises Uvicorn + worker
|-- pyproject.toml                    # Black and Ruff config
|-- pytest.ini                        # pytest config (pythonpath=src)
|-- requirements.txt                  # Runtime Python deps
|-- requirements-dev.txt              # Runtime + test deps
|-- run_local.py                      # Local API/worker launcher
|-- frontend/                         # Vite + React + TypeScript app
|   |-- package.json
|   |-- vite.config.ts
|   `-- src/
|       |-- api.ts                    # Typed API client
|       |-- models.ts                 # Frontend types (mirrors Pydantic)
|       |-- pages/                    # Dashboard, Convert, Presets, Settings
|       |-- components/               # ui, LoginPage, SettingsPanel, etc.
|       |-- hooks/useServerState.ts   # React Query + SSE + polling
|       |-- context/AppContext.tsx
|       `-- utils/constants.ts, helpers.ts
|-- src/video_converter/
|   |-- api/main.py                   # FastAPI app factory
|   |-- api/routers/                  # jobs, batches, media, outputs, etc.
|   |-- api/auth.py                   # JWT + password handling
|   |-- core/config.py                # Settings + MEDIA_MOUNTS parsing
|   |-- core/job_repository.py        # Job persistence + queue ops
|   |-- core/models.py                # Pydantic API/job models
|   |-- core/path_validation.py       # Traversal protection
|   |-- core/storage.py               # Redis / LocalFileStore
|   `-- worker/main.py                # FFmpeg worker
`-- tests/
    |-- api/
    |-- core/
    `-- worker/
```

## Security and Path Notes

- The API never trusts `source_path` as a raw filesystem path. Inputs are resolved as `source_root_key + source_path` and must remain inside the configured root after `Path.resolve()` (symlinks resolved).
- Path traversal, absolute-path escapes, missing files, invalid roots, and unsupported extensions are rejected with structured errors (`StructuredErrorResponse`).
- Host paths never leak to API payloads or frontend state; the UI uses root keys + root-relative paths.
- Docker media mounts should be read-only (`:ro`). The app never modifies source media.
- The only writable area is `DATA_ROOT`: `input`, `outputs`, `temp`, `logs`, `data`.
- Output downloads are filename-sanitized and cannot escape `DATA_ROOT/outputs`. Range and preview endpoints also sanitize.
- Do not commit real `.env` files, media, outputs, SQLite/Redis dumps, logs, `frontend/node_modules/`, `frontend/dist/`, `.pytest_cache/`, `.coverage*`, `venv/`, etc. See `.gitignore`.
- Auth: bcrypt (with legacy SHA fallback for migration), rate limit on login, `JWT_SECRET` persisted at `DATA_ROOT/data/jwt_secret` with `0600`.

## Troubleshooting

- Media roots empty: verify `MEDIA_MOUNTS` and matching `MEDIA_*_SOURCE` host paths. Run `docker inspect <app_container> --format '{{ json .Mounts }}' | python3 -m json.tool` — `Source` must be your host path (e.g. `/srv/media/movies`), not `/data/coolify/...` (stale **Application** deploy). `docker exec <app> ls -l /media/movies` must list files; `total 0` means a wrong bind was created. In **Docker Compose** deploy mode, leave Coolify Persistent Storage empty — only `redis-data` volume is needed.
- Paths with spaces: quote the env value (`MEDIA_SERIES_SOURCE="/srv/media/TV Series"`) — compose long syntax is already quoted.
- `GET /health/ready` → 503: Redis unavailable or `REDIS_URL` wrong when `VIDEO_CONVERTER_STORAGE=redis`.
- Local run complains about Redis: use default (`--storage local`) or run `python run_local.py --storage local`; only `--storage redis` needs a local Redis.
- Jobs stay queued: worker not running or cannot reach the same Redis/local store and media mounts as the API. Check `GET /api/v1/worker/health`.
- Job fails before FFmpeg: check validation (path, extension, file exists, root valid) and that the media is mounted at the expected container path.
- Job fails during conversion: ensure `ffmpeg`/`ffprobe` are installed, check `log_tail` and `timeline` in job detail drawer, verify container/codec compatibility.
- WebM with `audio_export=copy` fallback: worker normalizes to `opus` for container compatibility — set explicitly to `opus` to avoid surprise.
- Built UI missing locally: `cd frontend && npm run build` or use Vite dev server with API running.
- Docker build fails in frontend stage: run `cd frontend && npm install && npm run build` locally for TypeScript/Vite errors.
- Coolify cannot route the app: ensure public service is `app`, container port `8765`, compose file `docker-compose.yml`, healthcheck `/health/ready`.

## Contributing

Contributions are welcome! Please:

1. Fork the repository and create a feature branch (`git checkout -b feat/my-change`).
2. Install dev deps and ensure checks pass:
   ```sh
   pip install -r requirements-dev.txt
   cd frontend && npm install
   pytest
   cd frontend && npm run lint && npm run format:check && npm run build
   ```
3. Keep backend and frontend types in sync: edit `src/video_converter/core/models.py` and `frontend/src/models.ts` together; regenerate `frontend/src/generated/api-schema.ts` via `npm run generate:api` if you change the API.
4. Add or update tests for new behavior (see `tests/api`, `tests/core`, `tests/worker`).
5. Run `ruff check .` and `black .` for Python, `npm run format` for frontend.
6. Open a pull request with a clear description and screenshots for UI changes.

Please do not commit secrets, media files, `data/`, `frontend/dist/`, or `frontend/node_modules/`. See `AGENTS.md` for detailed project conventions.

### Reporting Issues

Open a GitHub issue with steps to reproduce, expected vs actual behavior, logs (`log_tail` or `DATA_ROOT/logs/`), and your environment (Docker vs local, Pi vs VPS).

### Security

For security vulnerabilities, please open a private security advisory or contact the maintainers directly instead of filing a public issue.

## License

This project is released under the [MIT License](LICENSE). See `LICENSE` for details.
