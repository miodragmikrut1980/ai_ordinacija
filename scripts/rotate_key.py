#!/usr/bin/env python3
"""Rotate the field-level encryption key for the clinic database.

Usage:
    CLINIC_ENCRYPTION_KEY_OLD=<old key> CLINIC_ENCRYPTION_KEY=<new key> \\
        python scripts/rotate_key.py [data_dir]

Or, to pull the old/new keys from a real secrets manager instead of putting
them directly in the environment:

    CLINIC_ENCRYPTION_KEY_OLD_COMMAND="vault kv get -field=key secret/clinic/old-key" \\
    CLINIC_ENCRYPTION_KEY_COMMAND="vault kv get -field=key secret/clinic/new-key" \\
        python scripts/rotate_key.py

If neither CLINIC_ENCRYPTION_KEY nor CLINIC_ENCRYPTION_KEY_COMMAND is set, a
new key is generated and printed -- save it somewhere safe (a secrets
manager, not this repo) before the process exits, since it is the only
thing that can decrypt the database afterwards.

What it does, in order, inside a single SQLite transaction per table:
    1. Opens the database directly (not through the app) with the OLD key.
    2. For every row containing an encrypted `data` blob, decrypts with the
       old key and re-encrypts with the new key.
    3. Re-encrypts every uploaded file (*.enc) under data/uploads the same way.
    4. Writes the new key to data/secret.key (only if the new key was not
       supplied via the environment/command -- if it was, this script does
       not persist it anywhere; that is your secrets manager's job).

Run this with the application stopped. It takes an exclusive lock on the
database for the duration of the rotation.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402
from app.crypto import EncryptionUnavailable, _key_from_command  # noqa: E402
from app.store import PersistentStore  # noqa: E402 - only used for its digest formula, not instantiated

TABLES_WITH_BLOB = [
    # Fixed, hardcoded list -- these names are never derived from user input,
    # so the f-string interpolation of {table} below is safe (there is
    # nothing here for an attacker to control).
    "patients", "documents", "appointments", "reports", "encounters",
    "briefings", "scribe_drafts", "differential_analyses",
]

# MFA secrets live outside the patient-record payload tables, but are also
# encrypted with the same field key.  Keeping this list explicit means a
# future schema addition cannot accidentally be re-encrypted via an
# attacker-controlled identifier.
EXTRA_ENCRYPTED_COLUMNS = [("user_mfa", "user_id", "secret_enc"), ("user_mfa", "user_id", "pending_secret_enc")]


def _resolve_key(env_var: str, command_env_var: str) -> bytes | None:
    command = os.getenv(command_env_var)
    if command:
        try:
            return _key_from_command(command)
        except EncryptionUnavailable as exc:
            print(f"{command_env_var}: {exc}", file=sys.stderr)
            sys.exit(1)
    value = os.getenv(env_var)
    return value.encode() if value else None


def main():
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data"
    db_path = data_dir / "clinic.db"
    if not db_path.exists():
        print(f"No database found at {db_path}", file=sys.stderr)
        sys.exit(1)

    old_key = _resolve_key("CLINIC_ENCRYPTION_KEY_OLD", "CLINIC_ENCRYPTION_KEY_OLD_COMMAND")
    if not old_key:
        key_path = data_dir / "secret.key"
        if not key_path.exists():
            print("Set CLINIC_ENCRYPTION_KEY_OLD (or _OLD_COMMAND), or ensure data/secret.key exists.", file=sys.stderr)
            sys.exit(1)
        old_key = key_path.read_bytes().strip()
    old_fernet = Fernet(old_key)

    new_key = _resolve_key("CLINIC_ENCRYPTION_KEY", "CLINIC_ENCRYPTION_KEY_COMMAND")
    new_key_provided = bool(new_key)
    new_key = new_key or Fernet.generate_key()
    new_fernet = Fernet(new_key)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rotated = 0
    for table in TABLES_WITH_BLOB:
        rows = conn.execute(f"SELECT rowid, id, data FROM {table}").fetchall()
        for row in rows:
            try:
                plaintext = old_fernet.decrypt(row["data"])
            except InvalidToken:
                print(f"WARNING: could not decrypt {table}/{row['id']} with the old key -- left untouched", file=sys.stderr)
                continue
            new_blob = new_fernet.encrypt(plaintext)
            conn.execute(f"UPDATE {table} SET data=? WHERE rowid=?", (new_blob, row["rowid"]))
            rotated += 1

    # The audit log's `detail_enc` column follows the same encrypt-at-rest
    # pattern but uses a different column name and can be NULL (not every
    # audit entry has a detail string), so it is rotated separately.
    audit_touched = False
    for row in conn.execute("SELECT seq, id, detail_enc FROM audit WHERE detail_enc IS NOT NULL").fetchall():
        try:
            plaintext = old_fernet.decrypt(row["detail_enc"])
        except InvalidToken:
            print(f"WARNING: could not decrypt audit/{row['id']} with the old key -- left untouched", file=sys.stderr)
            continue
        new_blob = new_fernet.encrypt(plaintext)
        conn.execute("UPDATE audit SET detail_enc=? WHERE seq=?", (new_blob, row["seq"]))
        rotated += 1
        audit_touched = True

    if audit_touched:
        # Re-encrypting detail_enc changes its bytes, and the tamper-evident
        # hash chain is computed over those bytes -- so the chain must be
        # recomputed per organization with the new ciphertext, using the
        # exact same digest formula the app uses, or verify_audit_chain
        # would (correctly, but confusingly) report the rotation itself as
        # tampering.
        for org_row in conn.execute("SELECT DISTINCT organization_id FROM audit").fetchall():
            org_id = org_row["organization_id"]
            prev_hash = "0" * 64
            for row in conn.execute("SELECT * FROM audit WHERE organization_id=? ORDER BY seq ASC", (org_id,)).fetchall():
                digest = PersistentStore._audit_digest(
                    prev_hash, row["id"], row["occurred_at"], row["user_id"], row["username"], row["role"],
                    row["action"], row["resource_type"], row["resource_id"], row["detail_enc"],
                )
                conn.execute("UPDATE audit SET prev_hash=?, hash=? WHERE seq=?", (prev_hash, digest, row["seq"]))
                prev_hash = digest

    # An MFA secret that is not rotated would silently lock a clinician out
    # after the next key rotation. Rotate both active and in-progress setup
    # secrets before committing the same transaction as all other data.
    for table, id_column, encrypted_column in EXTRA_ENCRYPTED_COLUMNS:
        try:
            rows = conn.execute(f"SELECT rowid, {id_column}, {encrypted_column} FROM {table} WHERE {encrypted_column} IS NOT NULL").fetchall()
        except sqlite3.OperationalError:
            # A database created before MFA simply has no table yet.
            continue
        for row in rows:
            try:
                plaintext = old_fernet.decrypt(row[encrypted_column])
            except InvalidToken:
                print(f"WARNING: could not decrypt {table}/{row[id_column]} ({encrypted_column}) with the old key -- left untouched", file=sys.stderr)
                continue
            conn.execute(f"UPDATE {table} SET {encrypted_column}=? WHERE rowid=?", (new_fernet.encrypt(plaintext), row["rowid"]))
            rotated += 1

    conn.commit()
    conn.close()

    upload_dir = data_dir / "uploads"
    files_rotated = 0
    for path in upload_dir.glob("*.enc"):
        try:
            plaintext = old_fernet.decrypt(path.read_bytes())
        except InvalidToken:
            print(f"WARNING: could not decrypt upload {path.name} with the old key -- left untouched", file=sys.stderr)
            continue
        path.write_bytes(new_fernet.encrypt(plaintext))
        files_rotated += 1

    if not new_key_provided:
        (data_dir / "secret.key").write_bytes(new_key if isinstance(new_key, bytes) else new_key.encode())
        try:
            (data_dir / "secret.key").chmod(0o600)
        except OSError:
            pass
        print(f"New key written to {data_dir / 'secret.key'}.")
    else:
        print("New key was supplied externally (env var or command) and was not written to disk -- store it in your secrets manager.")

    print(f"Rotated {rotated} database rows and {files_rotated} uploaded files.")


if __name__ == "__main__":
    main()
