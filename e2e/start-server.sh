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

# Locate a Python interpreter that actually has this project's
# dependencies installed. Playwright spawns this script as its own child
# process, which does NOT inherit an activated virtualenv from your shell
# even if `python` normally resolves correctly when you run commands by
# hand -- so this can't just assume `python` (or even `python3`) is on
# PATH and ready to go. Preference order:
#   1. This repo's own .venv, if you've set one up (see README.md/CHANGELOG)
#   2. python3 on PATH (the common case on macOS/Linux, where bare
#      `python` often doesn't exist at all)
#   3. python on PATH (CI runners via actions/setup-python provide this)
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  echo "e2e/start-server.sh: no Python interpreter found (checked $REPO_ROOT/.venv/bin/python, python3, python)." >&2
  echo "Run 'python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\"' in the repo root first." >&2
  exit 1
fi

# Fail fast with a clear message rather than a cryptic ModuleNotFoundError
# if the chosen interpreter doesn't actually have the app installed.
if ! "$PYTHON" -c "import fastapi" >/dev/null 2>&1; then
  echo "e2e/start-server.sh: $PYTHON does not have this project's dependencies installed." >&2
  echo "Run 'python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\"' in the repo root, or activate the venv you already use for pytest." >&2
  exit 1
fi

exec "$PYTHON" -m uvicorn app.main:app --app-dir "$REPO_ROOT/backend" --host 127.0.0.1 --port 8899
