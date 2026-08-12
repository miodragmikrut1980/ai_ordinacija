"""Shared singletons and config used across main.py and the routers.

Kept in its own module (rather than in main.py) so that router modules can
import `store` / `ai` without creating a circular import with main.py, which
in turn imports and mounts the routers.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .ai import ClinicAI
from .store import PersistentStore

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web" / "static"
# Overridable so the test suite (see backend/tests/conftest.py) can point
# this at an isolated temp directory instead of the real clinic database.
# Without this, repeated local/CI test runs share persistent state (most
# importantly login-attempt history), so failed-login tests can accumulate
# towards actually locking out the real 'doctor' demo account between runs.
DATA_DIR = Path(os.getenv("CLINIC_DATA_DIR", str(ROOT / "data")))

try:
    APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    APP_VERSION = "0.0.0-unknown"  # VERSION file missing -- shouldn't happen outside a broken checkout

SESSION_MINUTES = int(os.getenv("CLINIC_SESSION_MINUTES", "60"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("CLINIC_RATE_LIMIT_PER_MINUTE", "240"))
# Off by default: only enable this when the app is reachable exclusively
# through a reverse proxy that you control, which overwrites/strips
# X-Forwarded-For for external clients before setting its own value. If this
# is on without such a proxy in front, any client can set an arbitrary
# X-Forwarded-For value and get a fresh rate-limit budget on every request.
TRUST_PROXY_HEADERS = os.getenv("CLINIC_TRUST_PROXY_HEADERS", "0") == "1"

# In production, refuse to silently fall back to an auto-generated key file
# sitting next to the database it protects. This must be checked before
# PersistentStore (and the FieldCipher it constructs) has a chance to create
# that file, so the check happens here rather than inside crypto.py.
if os.getenv("CLINIC_ENV", "").lower() != "demo" and not (os.getenv("CLINIC_ENCRYPTION_KEY") or os.getenv("CLINIC_ENCRYPTION_KEY_COMMAND")):
    sys.exit(
        "CLINIC_ENV other than explicit demo requires CLINIC_ENCRYPTION_KEY or CLINIC_ENCRYPTION_KEY_COMMAND.\n"
        "Refusing to auto-generate data/secret.key outside disposable demo mode -- "
        "supply the key from your secrets manager instead (see backend/app/crypto.py)."
    )

store = PersistentStore(DATA_DIR)
ai = ClinicAI()
