#!/usr/bin/env bash
# Starts clinic-ai-assistant against a disposable, isolated data directory
# so E2E runs never touch a real clinic's data/ folder (same reasoning as
# backend/tests/conftest.py's CLINIC_DATA_DIR isolation for pytest).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2E_DATA_DIR="$(mktemp -d -t clinic-e2e-data-XXXXXX)"
trap 'rm -rf "$E2E_DATA_DIR"' EXIT

export CLINIC_ENV=demo
export CLINIC_DATA_DIR="$E2E_DATA_DIR"
export CLINIC_PORT=8899
export PYTHONPATH="$REPO_ROOT/backend"

# Uses whichever `python` is on PATH -- CI sets this up via actions/setup-python
# before invoking this script (see .github/workflows/tests.yml's e2e job).
exec python -m uvicorn app.main:app --app-dir "$REPO_ROOT/backend" --host 127.0.0.1 --port 8899
