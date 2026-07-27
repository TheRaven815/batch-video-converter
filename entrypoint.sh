#!/bin/bash
# Combined entrypoint: starts the worker and the API, supervises both, and
# propagates SIGTERM/SIGINT to both so each can shut down gracefully.

set -e

echo "[entrypoint] Starting worker..."
python -m video_converter.worker.main &
WORKER_PID=$!

echo "[entrypoint] Starting API server..."
uvicorn video_converter.api.main:app --host 0.0.0.0 --port "${API_PORT:-8765}" &
API_PID=$!

shutdown() {
    echo "[entrypoint] Shutting down..."
    kill -TERM "$WORKER_PID" "$API_PID" 2>/dev/null || true
}
trap shutdown TERM INT

# Wait until either process exits, then bring the other one down too.
wait -n "$WORKER_PID" "$API_PID" || true

echo "[entrypoint] A process exited, shutting down remaining..."
kill -TERM "$WORKER_PID" "$API_PID" 2>/dev/null || true
wait "$WORKER_PID" 2>/dev/null || true
wait "$API_PID" 2>/dev/null || true
