# syntax=docker/dockerfile:1

FROM node:22-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci --prefer-offline --no-audit --fund=false
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS backend-final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    VIDEO_CONVERTER_STORAGE=redis \
    DATA_ROOT=/app-data \
    MEDIA_MOUNTS=Media=/app-data/input

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg gosu tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /app-data \
    && chown app:app /app-data

COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --disable-pip-version-check -r /app/requirements.txt

COPY src /app/src

# Only copy the built frontend static files (dist/) from the frontend-builder stage.
# Source files (src/, tsconfig.json, vite.config.ts, package.json, etc.) are NOT
# included in the final image — the backend only serves from frontend/dist/.
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health/live', timeout=4)"]

# tini reaps zombies and forwards signals to the entrypoint's process group.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default: run API + worker together. Override CMD in compose for separate services.
CMD ["/app/entrypoint.sh"]
