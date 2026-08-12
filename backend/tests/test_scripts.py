"""Tests for scripts/backup.py, scripts/restore.py, and scripts/rotate_key.py.

These run the scripts as real subprocesses against an isolated temporary
data directory (not the shared `store` fixture used in test_api.py), since
they are meant to operate on a stopped application's data directory.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, timeout=60,
    )


def _make_store(data_dir: Path):
    sys.path.insert(0, str(BACKEND_DIR))
    from app.store import PersistentStore
    from app.models import PatientCreate

    store = PersistentStore(data_dir)
    org = store.organization_by_slug("demo-clinic")
    patient = store.create_patient(org.id, PatientCreate(full_name="Backup Test Patient"))
    store.add_document(org.id, patient.id, "lab.txt", "text/plain", "CRP povišen, kontrola za 5 dana.", b"CRP povisen")
    return store, org, patient


@pytest.fixture()
def seeded_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    store, org, patient = _make_store(data_dir)
    return data_dir, org.id, patient.id


def test_backup_then_restore_round_trip(seeded_data_dir):
    data_dir, org_id, patient_id = seeded_data_dir
    key = (data_dir / "secret.key").read_bytes()

    result = _run(str(REPO_ROOT / "scripts" / "backup.py"), str(data_dir))
    assert result.returncode == 0, result.stderr
    backups = list((data_dir / "backups").glob("*.tar.gz"))
    assert len(backups) == 1
    archive = backups[0]
    manifest_path = data_dir / "backups" / f"{archive.stem.replace('.tar', '')}.manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["format_version"] == 2 and "sha256" in manifest and manifest["uploaded_files"] == 1
    assert manifest["contents"] == ["clinic.db", *[f"uploads/{path.name}" for path in sorted((data_dir / "uploads").glob("*.enc"))]]

    restore_dir = data_dir.parent / "restored"
    result = _run(str(REPO_ROOT / "scripts" / "restore.py"), str(archive), str(restore_dir), "--force")
    assert result.returncode == 0, result.stderr
    assert "Integrity check passed" in result.stdout
    (restore_dir / "secret.key").write_bytes(key)  # backup deliberately excludes the key -- supply it back for the test

    sys.path.insert(0, str(BACKEND_DIR))
    from app.store import PersistentStore
    restored = PersistentStore(restore_dir)
    patients = restored.list_patients(org_id)
    assert len(patients) == 1 and patients[0].full_name == "Backup Test Patient"
    docs = restored.list_documents(org_id, patient_id)
    assert len(docs) == 1 and docs[0].attention is True


def test_restore_refuses_a_tampered_archive(seeded_data_dir):
    data_dir, _, _ = seeded_data_dir
    result = _run(str(REPO_ROOT / "scripts" / "backup.py"), str(data_dir))
    assert result.returncode == 0, result.stderr
    archive = next((data_dir / "backups").glob("*.tar.gz"))
    with open(archive, "ab") as f:
        f.write(b"tampered-bytes")

    restore_dir = data_dir.parent / "restored_tampered"
    result = _run(str(REPO_ROOT / "scripts" / "restore.py"), str(archive), str(restore_dir), "--force")
    assert result.returncode == 1
    assert "Integrity check FAILED" in result.stderr
    assert not (restore_dir / "clinic.db").exists()


def test_restore_requires_the_matching_manifest_and_leaves_existing_data_unchanged(seeded_data_dir):
    data_dir, _, _ = seeded_data_dir
    result = _run(str(REPO_ROOT / "scripts" / "backup.py"), str(data_dir))
    assert result.returncode == 0, result.stderr
    archive = next((data_dir / "backups").glob("*.tar.gz"))
    manifest = data_dir / "backups" / f"{archive.stem.replace('.tar', '')}.manifest.json"
    manifest.unlink()

    restore_dir = data_dir.parent / "restore_without_manifest"
    restore_dir.mkdir()
    original_db = restore_dir / "clinic.db"
    original_db.write_bytes(b"do-not-overwrite")
    result = _run(str(REPO_ROOT / "scripts" / "restore.py"), str(archive), str(restore_dir), "--force")
    assert result.returncode == 1
    assert "matching manifest is required" in result.stderr
    assert original_db.read_bytes() == b"do-not-overwrite"


def test_backup_excludes_the_encryption_key(seeded_data_dir):
    data_dir, _, _ = seeded_data_dir
    key = (data_dir / "secret.key").read_bytes()
    result = _run(str(REPO_ROOT / "scripts" / "backup.py"), str(data_dir))
    assert result.returncode == 0, result.stderr
    archive = next((data_dir / "backups").glob("*.tar.gz"))
    assert key not in archive.read_bytes()


def test_rotate_key_reencrypts_and_preserves_data(seeded_data_dir):
    data_dir, org_id, patient_id = seeded_data_dir
    old_key = (data_dir / "secret.key").read_text().strip()

    sys.path.insert(0, str(BACKEND_DIR))
    from cryptography.fernet import Fernet
    new_key = Fernet.generate_key().decode()

    import os
    env = {**os.environ, "CLINIC_ENCRYPTION_KEY_OLD": old_key, "CLINIC_ENCRYPTION_KEY": new_key}
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "rotate_key.py"), str(data_dir)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "Rotated" in result.stdout

    # the on-disk secret.key must NOT have been overwritten with the new key
    # (it was supplied externally via CLINIC_ENCRYPTION_KEY, so persisting it
    # to disk would defeat the point of supplying it externally)
    assert (data_dir / "secret.key").read_text().strip() == old_key

    os.environ["CLINIC_ENCRYPTION_KEY"] = new_key
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        from app.store import PersistentStore
        rotated_store = PersistentStore(data_dir)
        patients = rotated_store.list_patients(org_id)
        assert len(patients) == 1 and patients[0].full_name == "Backup Test Patient"
        assert rotated_store.verify_audit_chain(org_id)["intact"] is True
    finally:
        del os.environ["CLINIC_ENCRYPTION_KEY"]


def test_rotate_key_refuses_wrong_old_key(seeded_data_dir):
    data_dir, _, _ = seeded_data_dir
    sys.path.insert(0, str(BACKEND_DIR))
    from cryptography.fernet import Fernet
    wrong_old_key = Fernet.generate_key().decode()

    import os
    env = {**os.environ, "CLINIC_ENCRYPTION_KEY_OLD": wrong_old_key}
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "rotate_key.py"), str(data_dir)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert result.returncode == 0  # the script completes but rotates nothing it can't decrypt
    assert "WARNING: could not decrypt" in result.stderr
    assert "Rotated 0 database rows" in result.stdout
