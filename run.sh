#!/bin/bash
# Waypoint local development launcher
set -eu
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON:-python3}"

# Install deps if needed
if ! "$PYTHON_BIN" -c "import fastapi" 2>/dev/null; then
    echo "Installing dependencies..."
    "$PYTHON_BIN" -m pip install -r requirements.txt
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
echo "Starting Waypoint on http://${HOST}:${PORT}"
exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
