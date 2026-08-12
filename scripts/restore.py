#!/usr/bin/env python3
"""Restore a verified Clinic AI Assistant backup.

Usage:
    python scripts/restore.py BACKUP.tar.gz [data_dir] [--force]

The matching manifest is mandatory. The archive checksum and inventory are
verified before extraction; the staged SQLite database must also pass
``PRAGMA integrity_check``. Only then are ``clinic.db`` and ``uploads/``
replaced. The encryption key is deliberately not in a backup and is never
touched by this script.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def manifest_for(archive: Path) -> Path:
    return archive.parent / f"{archive.name.removesuffix('.tar.gz')}.manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest(archive: Path) -> dict:
    path = manifest_for(archive)
    if not path.is_file():
        fail("Integrity check FAILED: matching manifest is required next to the backup archive. Restore was not started.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Integrity check FAILED: manifest cannot be read ({exc}).")
    if not isinstance(manifest, dict) or manifest.get("format_version") != 2:
        fail("Integrity check FAILED: unsupported or incomplete backup manifest.")
    if sha256_file(archive) != manifest.get("sha256"):
        fail("Integrity check FAILED: archive does not match its manifest checksum. Do not restore it.")
    contents = manifest.get("contents")
    if not isinstance(contents, list) or "clinic.db" not in contents:
        fail("Integrity check FAILED: manifest does not contain a valid backup inventory.")
    print("Integrity check passed (required manifest and SHA-256 match).")
    return manifest


def stage_and_validate(archive: Path, manifest: dict, stage: Path) -> tuple[Path, Path]:
    expected = set(manifest["contents"])
    allowed = {"clinic.db"} | {name for name in expected if name.startswith("uploads/") and name.endswith(".enc")}
    if expected != allowed or any(".." in Path(name).parts or Path(name).is_absolute() for name in expected):
        fail("Integrity check FAILED: manifest has an invalid backup inventory.")
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or set(names) != expected:
                fail("Integrity check FAILED: archive contents do not exactly match the manifest inventory.")
            if any(not member.isfile() or member.issym() or member.islnk() for member in members):
                fail("Integrity check FAILED: archive contains a non-regular file.")
            tar.extractall(stage, filter="data")
    except (tarfile.TarError, OSError) as exc:
        fail(f"Restore failed before changing data: archive could not be safely extracted ({exc}).")

    staged_db, staged_uploads = stage / "clinic.db", stage / "uploads"
    if not staged_db.is_file():
        fail("Integrity check FAILED: required database is missing after extraction.")
    staged_uploads.mkdir(exist_ok=True)
    try:
        conn = sqlite3.connect(f"file:{staged_db}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        if not row or row[0] != "ok":
            fail("Integrity check FAILED: staged SQLite database failed integrity_check.")
    except sqlite3.DatabaseError as exc:
        fail(f"Integrity check FAILED: staged database is not valid SQLite ({exc}).")
    return staged_db, staged_uploads


def replace_atomically(data_dir: Path, staged_db: Path, staged_uploads: Path, force: bool) -> None:
    target_db, target_uploads = data_dir / "clinic.db", data_dir / "uploads"
    if target_db.exists() and not force:
        fail(f"{target_db} already exists. Re-run with --force to overwrite it after verification.")
    data_dir.mkdir(parents=True, exist_ok=True)
    rollback = Path(tempfile.mkdtemp(prefix="clinic-restore-rollback-", dir=data_dir.parent))
    moved: list[tuple[Path, Path]] = []
    try:
        for target in (target_db, target_uploads):
            if target.exists():
                saved = rollback / target.name
                os.replace(target, saved)
                moved.append((target, saved))
        os.replace(staged_db, target_db)
        os.replace(staged_uploads, target_uploads)
    except OSError as exc:
        target_db.unlink(missing_ok=True)
        if target_uploads.exists():
            shutil.rmtree(target_uploads)
        for target, saved in reversed(moved):
            if saved.exists():
                os.replace(saved, target)
        fail(f"Restore failed while replacing data; prior data was rolled back ({exc}).")
    finally:
        shutil.rmtree(rollback, ignore_errors=True)


def main() -> None:
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    if not args:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    archive = Path(args[0])
    data_dir = Path(args[1]) if len(args) > 1 else Path(__file__).resolve().parents[1] / "data"

    if not archive.is_file():
        fail(f"Backup archive not found: {archive}")
    manifest = verify_manifest(archive)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clinic-restore-stage-", dir=data_dir.parent) as temp:
        staged_db, staged_uploads = stage_and_validate(archive, manifest, Path(temp))
        replace_atomically(data_dir, staged_db, staged_uploads, force)
    print(f"Restored database to {data_dir / 'clinic.db'}")
    print(f"Restored uploads to {data_dir / 'uploads'}")
    print("IMPORTANT: set CLINIC_ENCRYPTION_KEY (or provide the separately stored key) before starting the app.")


if __name__ == "__main__":
    main()
