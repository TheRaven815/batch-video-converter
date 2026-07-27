#!/bin/bash
# Combined entrypoint: starts the worker and the API, supervises both, and
# propagates SIGTERM/SIGINT to both so each can shut down gracefully.

set -e

if [ "$(id -u)" = "0" ]; then
    DATA_DIR="${DATA_ROOT:-/app-data}"
    mkdir -p \
        "$DATA_DIR" \
        "$DATA_DIR/input" \
        "$DATA_DIR/outputs" \
        "$DATA_DIR/temp" \
        "$DATA_DIR/logs" \
        "$DATA_DIR/data"
    chown app:app \
        "$DATA_DIR" \
        "$DATA_DIR/input" \
        "$DATA_DIR/outputs" \
        "$DATA_DIR/temp" \
        "$DATA_DIR/logs" \
        "$DATA_DIR/data"
    exec gosu app "$0" "$@"
fi

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
