#!/bin/sh
# Combined entrypoint: starts the worker in the background, then runs the API
# in the foreground. Graceful shutdown propagates SIGTERM to both processes.

set -e

# Start worker in background
echo "[entrypoint] Starting worker..."
python -m video_converter.worker.main &
WORKER_PID=$!

# Graceful shutdown handler
cleanup() {
    echo "[entrypoint] Shutting down..."
    kill -TERM "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

# Start API in foreground
echo "[entrypoint] Starting API server..."
uvicorn video_converter.api.main:app --host 0.0.0.0 --port "${API_PORT:-8765}" &
API_PID=$!

# Wait for either process to exit
wait -n "$WORKER_PID" "$API_PID" 2>/dev/null || true

# If one process died, kill the other and exit
echo "[entrypoint] A process exited, shutting down remaining..."
kill -TERM "$WORKER_PID" "$API_PID" 2>/dev/null || true
wait "$WORKER_PID" "$API_PID" 2>/dev/null || true
