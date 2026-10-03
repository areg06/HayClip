#!/usr/bin/env bash
# Start everything for the local operator app: Postgres (socket-only), the worker and the web app.
# Ctrl-C stops the web app and the worker (the worker finishes its current job first).
#   scripts/dev-up.sh [port]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8765}"
scripts/dev-postgres.sh init >/dev/null
scripts/dev-postgres.sh start
.venv/bin/python -m hayclips.worker --pools io,cpu,paid &
WORKER=$!
trap 'kill -TERM $WORKER 2>/dev/null; wait $WORKER 2>/dev/null' EXIT
.venv/bin/python -m hayclips.web --port "$PORT"
