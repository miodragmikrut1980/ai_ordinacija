#!/usr/bin/env python3
"""Create a consistent backup of the clinic database and uploaded files.

Usage:
    python scripts/backup.py [data_dir] [--out DIR]

What it does:
    1. Uses SQLite's online backup API to copy clinic.db to a temp file while
       the app can keep running (WAL mode makes this safe: readers/writers
       are not blocked by the backup, and the backup is a consistent
       point-in-time snapshot, not a torn copy of a file mid-write).
    2. Bundles that snapshot together with data/uploads/*.enc into a single
       timestamped tar.gz under data/backups/ (or --out).
    3. Writes a manifest.json recording what went in, when, and a SHA-256 of
       the archive for integrity checking later.

What it deliberately does NOT do:
    - It does not include data/secret.key (or reference CLINIC_ENCRYPTION_KEY).
      A backup containing both the encrypted data and the key that unlocks it
      is not meaningfully encrypted at rest -- anyone who gets the backup
      gets everything. Back up the key separately, through whatever secrets
      manager or process you use to protect it, and store it apart from
      these archives. restore.py will refuse to guess at a key for you.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    args = sys.argv[1:]
    out_dir = None
    if "--out" in args:
        i = args.index("--out")
        out_dir = Path(args[i + 1])
        del args[i:i + 2]
    data_dir = Path(args[0]) if args else Path(__file__).resolve().parents[1] / "data"
    db_path = data_dir / "clinic.db"
    uploads_dir = data_dir / "uploads"
    if not db_path.exists():
        print(f"No database found at {db_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = out_dir or (data_dir / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = out_dir / f"clinic-backup-{timestamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "clinic.db"
        # Online backup API: consistent snapshot even if the app is running.
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_db)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()

        enc_files = sorted(uploads_dir.glob("*.enc")) if uploads_dir.exists() else []

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(tmp_db, arcname="clinic.db")
            for f in enc_files:
                tar.add(f, arcname=f"uploads/{f.name}")

    digest = sha256_file(archive_path)
    manifest = {
        "format_version": 2,
        "created_at": timestamp,
        "archive": archive_path.name,
        "sha256": digest,
        "uploaded_files": len(enc_files),
        "contents": ["clinic.db", *[f"uploads/{f.name}" for f in enc_files]],
        "note": "Encryption key is NOT included. Back it up separately.",
    }
    manifest_path = out_dir / f"{archive_path.stem.replace('.tar', '')}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Backup written to {archive_path}")
    print(f"Manifest written to {manifest_path}")
    print(f"SHA-256: {digest}")
    print("Reminder: the encryption key was NOT included in this backup. Back it up separately.")


if __name__ == "__main__":
    main()
