#!/bin/sh
# Sets up the Python environment on first run, then starts the app.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  PY=$(command -v python3.12 || command -v python3)
  "$PY" -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
echo "Open http://localhost:8765"
exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8765 --no-access-log
