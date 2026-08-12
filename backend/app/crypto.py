"""Encryption at rest for patient data.

Sensitive fields (documents, clinical profiles, encounters, scribe drafts,
differential analyses, briefings, reports) are encrypted with a symmetric
Fernet key before they are written to the database or the filesystem, and
decrypted only in memory when read back.

Key management (read this before deploying for real patients):

- Preferred: CLINIC_ENCRYPTION_KEY_COMMAND -- a shell command whose stdout
  (first line, trimmed) is the key. This is the integration point for a real
  secrets manager without this project needing an SDK dependency for every
  vendor: e.g.
    CLINIC_ENCRYPTION_KEY_COMMAND="aws secretsmanager get-secret-value --secret-id clinic/encryption-key --query SecretString --output text"
    CLINIC_ENCRYPTION_KEY_COMMAND="vault kv get -field=key secret/clinic/encryption-key"
  The command's exit code is checked; a non-zero exit or empty output is a
  startup failure, not a silent fallback.
- CLINIC_ENCRYPTION_KEY -- the key value directly in an environment
  variable. Simpler, but the key then sits in plaintext in the process
  environment / your orchestrator's secret-injection mechanism instead of
  being fetched fresh from a KMS on each start.
- If neither is set, a key is generated on first run and stored in
  ``data/secret.key`` with owner-only permissions, so the demo works out of
  the box. Storing the key next to the database it protects is a practical
  trade-off for local evaluation, NOT a substitute for a real key
  management service -- anyone who can read the data directory can read
  both the ciphertext and the key. CLINIC_ENV=production (see state.py)
  refuses to start on this fallback at all.
- Losing the key means losing all encrypted data beyond recovery -- back it
  up separately from the database file (see scripts/backup.py, which
  deliberately excludes the key for this reason).
"""
from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class EncryptionUnavailable(RuntimeError):
    pass


def _key_from_command(command: str) -> bytes:
    try:
        result = subprocess.run(
            shlex.split(command), capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EncryptionUnavailable(f"CLINIC_ENCRYPTION_KEY_COMMAND could not be run: {exc}") from exc
    if result.returncode != 0:
        raise EncryptionUnavailable(
            f"CLINIC_ENCRYPTION_KEY_COMMAND exited with status {result.returncode}: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()[:500]}"
        )
    key = result.stdout.decode("utf-8", errors="replace").strip().splitlines()[:1]
    if not key or not key[0]:
        raise EncryptionUnavailable("CLINIC_ENCRYPTION_KEY_COMMAND produced no output")
    return key[0].encode()


def _load_or_create_key(data_dir: Path) -> bytes:
    key_command = os.getenv("CLINIC_ENCRYPTION_KEY_COMMAND")
    if key_command:
        return _key_from_command(key_command)
    env_key = os.getenv("CLINIC_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode() if isinstance(env_key, str) else env_key
    key_path = data_dir / "secret.key"
    if key_path.exists():
        return key_path.read_bytes().strip()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # best effort on platforms without POSIX permission bits
    return key


class FieldCipher:
    """Encrypts/decrypts UTF-8 text and raw bytes for storage."""

    def __init__(self, data_dir: Path):
        key = _load_or_create_key(data_dir)
        try:
            self._fernet = Fernet(key)
        except Exception as exc:  # noqa: BLE001 - surface a clear error
            raise EncryptionUnavailable(
                "CLINIC_ENCRYPTION_KEY is not a valid Fernet key"
            ) from exc

    def encrypt_text(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt_text(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode("utf-8")

    def encrypt_bytes(self, raw: bytes) -> bytes:
        return self._fernet.encrypt(raw)

    def decrypt_bytes(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)


__all__ = ["FieldCipher", "EncryptionUnavailable", "InvalidToken"]
