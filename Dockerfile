FROM node:22-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS backend-final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    VIDEO_CONVERTER_STORAGE=redis \
    DATA_ROOT=/data \
    MEDIA_MOUNTS=Media=/data/input

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src

# Only copy the built frontend static files (dist/) from the frontend-builder stage.
# Source files (src/, tsconfig.json, vite.config.ts, package.json, etc.) are NOT
# included in the final image — the backend only serves from frontend/dist/.
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8765

# Default: run API + worker together. Override CMD in compose for separate services.
CMD ["/app/entrypoint.sh"]
