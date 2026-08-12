"""Minimal, dependency-free RFC 6238 TOTP support for clinician MFA.

The secret is generated locally and encrypted by ``PersistentStore`` before
it reaches disk.  Codes are accepted only in a one-step clock window to
allow small device drift; enrollment and login challenges remain separate.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code(secret: str, counter: int) -> str:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret.upper() + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str, code: str, now: float | None = None) -> bool:
    if not code or len(code) != 6 or not code.isdigit():
        return False
    counter = int((time.time() if now is None else now) // 30)
    return any(hmac.compare_digest(_code(secret, counter + drift), code) for drift in (-1, 0, 1))
