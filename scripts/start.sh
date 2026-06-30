#!/usr/bin/env bash
# Production startup script: runs migrations then starts the server.
set -euo pipefail

echo "Running database migrations..."
alembic upgrade head

echo "Starting FastAPI server..."
exec uvicorn app.main:app \
    --host "${API_HOST:-0.0.0.0}" \
    --port "${API_PORT:-8000}" \
    --workers "${API_WORKERS:-4}" \
    --log-config /dev/null  # structlog handles logging
