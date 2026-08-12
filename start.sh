#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -e .

check_port_free() {
  local port="$1"
  python3 - "$port" <<'EOF'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.5)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)  # port is in use
finally:
    s.close()
sys.exit(0)  # port is free
EOF
}

fail_port_in_use() {
  local port="$1"
  echo "" >&2
  echo "Port $port is already in use -- something is already listening there." >&2
  echo "" >&2
  echo "Likely cause: a previous ./start.sh is still running in another terminal (Ctrl+C stops it)." >&2
  echo "" >&2
  echo "To find and stop whatever is using it:" >&2
  echo "  lsof -i :$port          # shows the process (macOS/Linux)" >&2
  echo "  kill -9 <PID>           # stop it, using the PID from the line above" >&2
  echo "" >&2
  echo "Or run this instance on a different port instead:" >&2
  echo "  CLINIC_PORT=$((port + 1)) ./start.sh" >&2
  echo "" >&2
  exit 1
}

if [ "${CLINIC_TLS:-0}" = "1" ]; then
  PORT="${CLINIC_PORT:-8443}"
  if [ -n "${CLINIC_TLS_CERT_FILE:-}" ] && [ -n "${CLINIC_TLS_KEY_FILE:-}" ]; then
    # A real certificate (e.g. from Let's Encrypt / your CA) was supplied --
    # use it instead of generating a self-signed one.
    CERT_PATH="$CLINIC_TLS_CERT_FILE"
    KEY_PATH="$CLINIC_TLS_KEY_FILE"
    echo "Starting with the supplied TLS certificate on https://127.0.0.1:$PORT"
    echo "(bound to loopback only, same as the self-signed path -- for external reachability, set --host or use the Docker/systemd deployment instead)"
  else
    # Opt-in local HTTPS using a self-signed certificate (generated once
    # into data/tls/). Browsers will show a "not trusted" warning for a
    # self-signed cert -- that is expected locally. For anything reachable
    # by real users, set CLINIC_TLS_CERT_FILE/CLINIC_TLS_KEY_FILE to a
    # certificate from a real CA instead (or terminate TLS at a reverse
    # proxy in front of this app, which amounts to the same thing).
    read CERT_PATH KEY_PATH <<< "$(python -c "
from pathlib import Path
from app.tls import ensure_self_signed_cert
c, k = ensure_self_signed_cert(Path('data'))
print(c, k)
")"
    echo "Starting with a self-signed certificate on https://127.0.0.1:$PORT"
  fi
  check_port_free "$PORT" || fail_port_in_use "$PORT"
  exec uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT" --ssl-certfile "$CERT_PATH" --ssl-keyfile "$KEY_PATH" --reload
else
  PORT="${CLINIC_PORT:-8080}"
  check_port_free "$PORT" || fail_port_in_use "$PORT"
  exec uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT" --reload
fi
