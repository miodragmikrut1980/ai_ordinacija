"""Persistence layer for the clinic assistant.

Backed by a single SQLite database file (``data/clinic.db``) instead of a
JSON file that was rewritten in full on every write. This gives us:

- real transactions and a UNIQUE constraint that closes the username race
  condition at the database level, instead of relying only on an
  application lock;
- indexes on the columns we actually filter/sort by;
- persistent sessions and login attempts, so a server restart does not log
  every clinician out and login lockouts survive a restart;
- a tamper-evident audit log (hash chain, see ``verify_audit_chain``);
- field-level encryption at rest for everything that can contain patient
  health information (documents, clinical profiles, encounters, scribe
  drafts, differential analyses, briefings, reports, and uploaded file
  bytes). See ``crypto.py`` for the key-management trade-offs.

Concurrency model: a single sqlite3 connection (``check_same_thread=False``)
is shared across requests. FastAPI runs each synchronous endpoint in a
worker-thread pool, so *every* method on this class -- reads included --
acquires ``self._lock`` before touching ``self._conn``. Reads are not
excluded from the lock: letting reads run lock-free while writes hold the
lock still lets a read interleave with a write's SELECT-then-INSERT
sequence on the same connection object, which is exactly what caused an
intermittent bug in the audit hash chain during testing (a read landing
between "read previous hash" and "insert new row" could observe a
half-updated view depending on how the underlying SQLite build serializes
cross-thread access to one connection). Serializing everything removes the
ambiguity entirely. At the scale of a single clinic this is not a
bottleneck.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .clinical_keywords import CLINICAL_SIGNIFICANCE_KEYWORDS
from .crypto import FieldCipher
from .models import *  # noqa: F401,F403 - re-exported model names used below

LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260_000).hex()
    return f"{salt}${digest}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        salt, expected = encoded.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(_hash_password(password, salt).split("$", 1)[1], expected)


# A fixed, valid-looking hash to run _verify_password against when no user
# is found, so that "wrong password for a real account" and "account/org
# doesn't exist" cost the same ~260k-iteration PBKDF2 computation instead of
# one of them short-circuiting near-instantly. Without this, the two cases
# are trivially distinguishable by response time alone (measured at roughly
# 190ms vs 3.5ms locally), which lets an attacker enumerate valid usernames
# even though the HTTP response body is identical either way. The value
# itself is arbitrary and never matches any real password.
_DUMMY_HASH = _hash_password("not-a-real-password-just-for-timing", salt="00" * 16)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "clinic"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_slug ON organizations(slug);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, username TEXT NOT NULL, username_lower TEXT NOT NULL,
    full_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 0,
    UNIQUE(organization_id, username_lower)
);

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, created_at TEXT NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patients_org ON patients(organization_id);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, uploaded_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, status TEXT NOT NULL, attention INTEGER NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_org ON documents(organization_id);
CREATE INDEX IF NOT EXISTS idx_documents_patient ON documents(patient_id);

CREATE TABLE IF NOT EXISTS lab_results (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, created_at TEXT NOT NULL,
    collected_at TEXT, status TEXT NOT NULL, source_document_id TEXT, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lab_results_patient ON lab_results(patient_id, collected_at);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, starts_at TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appointments_org ON appointments(organization_id);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, generated_at TEXT NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_org ON reports(organization_id);

CREATE TABLE IF NOT EXISTS encounters (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, visit_date TEXT NOT NULL,
    created_at TEXT NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_encounters_org ON encounters(organization_id);
CREATE INDEX IF NOT EXISTS idx_encounters_patient ON encounters(patient_id);

CREATE TABLE IF NOT EXISTS briefings (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, generated_at TEXT NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_briefings_patient ON briefings(patient_id);

CREATE TABLE IF NOT EXISTS scribe_drafts (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, created_at TEXT NOT NULL,
    status TEXT NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scribe_patient ON scribe_drafts(patient_id);

CREATE TABLE IF NOT EXISTS differential_analyses (
    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, patient_id TEXT NOT NULL, generated_at TEXT NOT NULL, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_differential_patient ON differential_analyses(patient_id);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, organization_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
    user_id TEXT, username TEXT NOT NULL, role TEXT NOT NULL, action TEXT NOT NULL, resource_type TEXT NOT NULL,
    resource_id TEXT, detail_enc BLOB, prev_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit(organization_id);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT NOT NULL, token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, organization_slug TEXT NOT NULL, username_lower TEXT NOT NULL,
    occurred_at TEXT NOT NULL, success INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup ON login_attempts(organization_slug, username_lower, occurred_at);

CREATE TABLE IF NOT EXISTS user_mfa (
    user_id TEXT PRIMARY KEY, secret_enc BLOB, pending_secret_enc BLOB,
    enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mfa_login_challenges (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mfa_challenges_expiry ON mfa_login_challenges(expires_at);

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL, updated_at TEXT NOT NULL
);
"""

# Bump this whenever _migrate_schema gains a new step. The recorded version
# lets a future migration check "has this DB already been migrated past
# version N" with a single indexed lookup instead of re-running PRAGMA
# table_info introspection against every table on every startup.
CURRENT_SCHEMA_VERSION = 5


class PersistentStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.upload_dir = data_dir / "uploads"
        self.db_path = data_dir / "clinic.db"
        self.legacy_json_path = data_dir / "clinic-store.json"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        needs_migration = not self.db_path.exists() and self.legacy_json_path.exists()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self.cipher = FieldCipher(data_dir)
        self._migrate_schema()
        if needs_migration:
            self._migrate_from_json()
        self._bootstrap_initial_clinic()

    # -- in-place schema migration for DBs created by an earlier version of
    # -- this app (encrypts previously-plaintext audit detail, adds session
    # -- bookkeeping columns) -----------------------------------------------
    def _migrate_schema(self):
        with self._lock:
            row = self._conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        if row and row["version"] >= CURRENT_SCHEMA_VERSION:
            return  # already migrated -- skip the introspection below entirely
        needs_vacuum = False
        with self._lock, self._conn:
            audit_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(audit)").fetchall()}
            if "detail" in audit_cols and "detail_enc" not in audit_cols:
                self._conn.execute("ALTER TABLE audit ADD COLUMN detail_enc BLOB")
                rows = self._conn.execute("SELECT seq, detail FROM audit ORDER BY seq ASC").fetchall()
                for row in rows:
                    blob = self.cipher.encrypt_text(row["detail"]) if row["detail"] is not None else None
                    self._conn.execute("UPDATE audit SET detail_enc=? WHERE seq=?", (blob, row["seq"]))
                # The hash chain was computed over the old plaintext detail;
                # recompute it per organization using the now-encrypted
                # detail so inserts and verification use one consistent
                # formula going forward.
                for org_row in self._conn.execute("SELECT DISTINCT organization_id FROM audit").fetchall():
                    org_id = org_row["organization_id"]
                    prev_hash = "0" * 64
                    for row in self._conn.execute("SELECT * FROM audit WHERE organization_id=? ORDER BY seq ASC", (org_id,)).fetchall():
                        digest = self._audit_digest(prev_hash, row["id"], row["occurred_at"], row["user_id"], row["username"], row["role"], row["action"], row["resource_type"], row["resource_id"], row["detail_enc"])
                        self._conn.execute("UPDATE audit SET prev_hash=?, hash=? WHERE seq=?", (prev_hash, digest, row["seq"]))
                        prev_hash = digest
                # Drop the now-unused plaintext column outright (not just set
                # it to NULL) -- an UPDATE alone leaves the old plaintext
                # bytes sitting in freed SQLite pages until something
                # overwrites them. DROP COLUMN + VACUUM below is what
                # actually removes the plaintext PHI from the file on disk.
                try:
                    self._conn.execute("ALTER TABLE audit DROP COLUMN detail")
                except sqlite3.OperationalError:
                    pass  # SQLite < 3.35 (unlikely with a modern Python): leave the (now-empty-in-spirit) column
                needs_vacuum = True

            session_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
            if "id" not in session_cols:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN id TEXT")
                self._conn.execute("ALTER TABLE sessions ADD COLUMN created_at TEXT")
                now = _iso(_now())
                for row in self._conn.execute("SELECT rowid FROM sessions WHERE id IS NULL").fetchall():
                    self._conn.execute("UPDATE sessions SET id=?, created_at=? WHERE rowid=?", (str(uuid4()), now, row["rowid"]))
            self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_id ON sessions(id)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
            # These CREATE statements are intentionally repeated for older
            # installations: SQLite's IF NOT EXISTS makes this migration
            # safe, while fresh installations receive the same schema from
            # SCHEMA above.
            self._conn.execute("CREATE TABLE IF NOT EXISTS user_mfa (user_id TEXT PRIMARY KEY, secret_enc BLOB, pending_secret_enc BLOB, enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")
            self._conn.execute("CREATE TABLE IF NOT EXISTS mfa_login_challenges (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mfa_challenges_expiry ON mfa_login_challenges(expires_at)")
        if needs_vacuum:
            # VACUUM cannot run inside a transaction; the `with self._conn:`
            # block above has already committed and exited by this point.
            with self._lock:
                self._conn.execute("VACUUM")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO schema_version(id,version,updated_at) VALUES (1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at",
                (CURRENT_SCHEMA_VERSION, _iso(_now())),
            )

    # -- low-level connection access, always serialized -----------------------
    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def health_check(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    # -- encrypted blob helpers ------------------------------------------------
    def _seal(self, payload: dict) -> bytes:
        return self.cipher.encrypt_text(json.dumps(payload, ensure_ascii=False))

    def _unseal(self, blob: bytes) -> dict:
        return json.loads(self.cipher.decrypt_text(blob))

    # -- one-time migration from the old plaintext JSON store ------------------
    def _migrate_from_json(self):
        try:
            raw = json.loads(self.legacy_json_path.read_text(encoding="utf-8"))
        except Exception:
            return
        now = _iso(_now())
        with self._lock, self._conn:
            existing_orgs = raw.get("organizations") or []
            if existing_orgs:
                # Already-tenant-aware JSON store: keep organization ids as-is.
                for org in existing_orgs:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO organizations(id,name,slug,created_at,active) VALUES (?,?,?,?,?)",
                        (org["id"], org["name"], org["slug"], org.get("created_at", now), int(org.get("active", True))),
                    )
                default_org_id = existing_orgs[0]["id"]
            else:
                # Pre-multi-tenant flat dump: everything belongs to one new clinic.
                default_org_id = str(uuid4())
                self._conn.execute(
                    "INSERT INTO organizations(id,name,slug,created_at,active) VALUES (?,?,?,?,1)",
                    (default_org_id, "Demo Clinic", "demo-clinic", now),
                )

            def org_of(item):
                return item.get("organization_id") or default_org_id

            for item in raw.get("users", []):
                self._conn.execute(
                    "INSERT OR IGNORE INTO users(id,organization_id,username,username_lower,full_name,role,password_hash,created_at,active,must_change_password) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], org_of(item), item["username"], item["username"].lower(), item["full_name"], item["role"],
                     item["password_hash"], item.get("created_at", now), int(item.get("active", True)), int(item.get("must_change_password", False))),
                )
            for item in raw.get("patients", []):
                plain = {k: item.get(k) for k in ("full_name", "date_of_birth", "phone", "email", "clinical_profile")}
                self._conn.execute(
                    "INSERT OR IGNORE INTO patients(id,organization_id,created_at,data) VALUES (?,?,?,?)",
                    (item["id"], org_of(item), item.get("created_at", now), self._seal(plain)),
                )
            for item in raw.get("documents", []):
                plain = {k: item.get(k) for k in ("filename", "media_type", "text")}
                self._conn.execute(
                    "INSERT OR IGNORE INTO documents(id,organization_id,patient_id,uploaded_at,size_bytes,status,attention,data) VALUES (?,?,?,?,?,?,?,?)",
                    (item["id"], org_of(item), item["patient_id"], item.get("uploaded_at", now), item.get("size_bytes", 0),
                     item.get("status", "ready"), int(item.get("attention", False)), self._seal(plain)),
                )
            for item in raw.get("appointments", []):
                plain = {k: item.get(k) for k in ("reason", "notes")}
                self._conn.execute(
                    "INSERT OR IGNORE INTO appointments(id,organization_id,patient_id,starts_at,status,created_at,data) VALUES (?,?,?,?,?,?,?)",
                    (item["id"], org_of(item), item["patient_id"], item["starts_at"], item.get("status", "scheduled"),
                     item.get("created_at", now), self._seal(plain)),
                )
            for item in raw.get("reports", []):
                plain = {k: item.get(k) for k in ("title", "content", "status")}
                self._conn.execute(
                    "INSERT OR IGNORE INTO reports(id,organization_id,patient_id,generated_at,data) VALUES (?,?,?,?,?)",
                    (item["id"], org_of(item), item["patient_id"], item.get("generated_at", now), self._seal(plain)),
                )
            for item in raw.get("encounters", []):
                plain = {k: item.get(k) for k in (
                    "chief_complaint", "anamnesis", "examination", "assessment", "plan", "vital_signs",
                    "clinician_id", "clinician_name",
                ) if k in item}
                self._conn.execute(
                    "INSERT OR IGNORE INTO encounters(id,organization_id,patient_id,visit_date,created_at,data) VALUES (?,?,?,?,?,?)",
                    (item["id"], org_of(item), item["patient_id"], item["visit_date"], item.get("created_at", now), self._seal(plain)),
                )
            for item in raw.get("briefings", []):
                plain = {k: v for k, v in item.items() if k not in ("id", "organization_id", "patient_id", "generated_at")}
                self._conn.execute(
                    "INSERT OR IGNORE INTO briefings(id,organization_id,patient_id,generated_at,data) VALUES (?,?,?,?,?)",
                    (item["id"], org_of(item), item["patient_id"], item.get("generated_at", now), self._seal(plain)),
                )
            for item in raw.get("scribe_drafts", []):
                plain = {k: v for k, v in item.items() if k not in ("id", "organization_id", "patient_id", "created_at", "status")}
                self._conn.execute(
                    "INSERT OR IGNORE INTO scribe_drafts(id,organization_id,patient_id,created_at,status,data) VALUES (?,?,?,?,?,?)",
                    (item["id"], org_of(item), item["patient_id"], item.get("created_at", now), item.get("status", "draft"), self._seal(plain)),
                )
            for item in raw.get("differential_analyses", []):
                plain = {k: v for k, v in item.items() if k not in ("id", "organization_id", "patient_id", "generated_at")}
                self._conn.execute(
                    "INSERT OR IGNORE INTO differential_analyses(id,organization_id,patient_id,generated_at,data) VALUES (?,?,?,?,?)",
                    (item["id"], org_of(item), item["patient_id"], item.get("generated_at", now), self._seal(plain)),
                )
            for item in raw.get("audit", []):
                self._insert_audit_row_locked(
                    org_of(item), item.get("occurred_at", now), item.get("user_id"), item.get("username", "unknown"),
                    item.get("role", "unknown"), item.get("action", ""), item.get("resource_type", ""),
                    item.get("resource_id"), item.get("detail"),
                )
        # Re-encrypt any legacy plaintext upload files in place, then remove
        # the plaintext JSON store entirely -- keeping it around would leave a
        # full plaintext copy of patient data sitting next to the encrypted
        # database, defeating the point of encrypting it.
        for path in self.upload_dir.glob("*"):
            if path.is_file() and not path.name.endswith(".enc"):
                try:
                    raw_bytes = path.read_bytes()
                    path.with_name(path.name + ".enc").write_bytes(self.cipher.encrypt_bytes(raw_bytes))
                    path.unlink()
                except Exception:
                    continue
        try:
            self.legacy_json_path.unlink()
        except OSError:
            pass

    def _bootstrap_initial_clinic(self):
        """Create known demo accounts only after an explicit local-demo opt-in."""
        demo_mode = os.getenv("CLINIC_ENV", "").lower() == "demo"
        with self._lock, self._conn:
            row = self._conn.execute("SELECT id FROM organizations LIMIT 1").fetchone()
            if row is None:
                org_id = str(uuid4())
                if not demo_mode:
                    username = os.getenv("CLINIC_BOOTSTRAP_ADMIN_USERNAME", "").strip()
                    password = os.getenv("CLINIC_BOOTSTRAP_ADMIN_PASSWORD", "")
                    if len(username) < 2 or len(password) < 12 or not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
                        raise RuntimeError(
                            "First start requires CLINIC_BOOTSTRAP_ADMIN_USERNAME and a 12+ character "
                            "CLINIC_BOOTSTRAP_ADMIN_PASSWORD containing letters and digits. Set CLINIC_ENV=demo only for disposable local evaluation."
                        )
                    org_name = os.getenv("CLINIC_ORGANIZATION_NAME", "Private Clinic").strip() or "Private Clinic"
                    org_slug = _slug(os.getenv("CLINIC_ORGANIZATION_SLUG", org_name))
                else:
                    username, password, org_name, org_slug = "doctor", "doctor123", "Demo Clinic", "demo-clinic"
                self._conn.execute(
                    "INSERT INTO organizations(id,name,slug,created_at,active) VALUES (?,?,?,?,1)",
                    (org_id, org_name, org_slug, _iso(_now())),
                )
            else:
                org_id = row["id"]
            has_users = self._conn.execute("SELECT 1 FROM users WHERE organization_id=? LIMIT 1", (org_id,)).fetchone()
            if not has_users:
                if not demo_mode:
                    username = os.getenv("CLINIC_BOOTSTRAP_ADMIN_USERNAME", "").strip()
                    password = os.getenv("CLINIC_BOOTSTRAP_ADMIN_PASSWORD", "")
                    if len(username) < 2 or len(password) < 12:
                        raise RuntimeError("Database has no users. Supply the bootstrap admin variables to create the first administrator.")
                    self._conn.execute(
                        "INSERT INTO users(id,organization_id,username,username_lower,full_name,role,password_hash,created_at,active,must_change_password) VALUES (?,?,?,?,?,?,?,?,1,1)",
                        (str(uuid4()), org_id, username, username.lower(), os.getenv("CLINIC_BOOTSTRAP_ADMIN_NAME", "Clinic Administrator"), "admin", _hash_password(password), _iso(_now())),
                    )
                    return
                for username, name, role, password in [
                    ("doctor", "Dr. Demo", "doctor", "doctor123"),
                    ("reception", "Reception Demo", "receptionist", "reception123"),
                    ("admin", "Clinic Admin", "admin", "admin123"),
                ]:
                    self._conn.execute(
                        "INSERT INTO users(id,organization_id,username,username_lower,full_name,role,password_hash,created_at,active,must_change_password) VALUES (?,?,?,?,?,?,?,?,1,0)",
                        (str(uuid4()), org_id, username, username.lower(), name, role, _hash_password(password), _iso(_now())),
                    )

    # -- organizations -----------------------------------------------------
    def create_organization(self, name: str, slug: str | None = None) -> OrganizationRecord:
        with self._lock, self._conn:
            org_id = str(uuid4())
            self._conn.execute(
                "INSERT INTO organizations(id,name,slug,created_at,active) VALUES (?,?,?,?,1)",
                (org_id, name, slug or _slug(name), _iso(_now())),
            )
        return self.organization_by_id(org_id)

    def rename_organization(self, org_id: str, name: str) -> OrganizationRecord:
        with self._lock, self._conn:
            self._conn.execute("UPDATE organizations SET name=? WHERE id=?", (name.strip(), org_id))
        return self.organization_by_id(org_id)

    def organization_by_id(self, org_id: str) -> OrganizationRecord | None:
        row = self._one("SELECT * FROM organizations WHERE id=?", (org_id,))
        return self._org_from_row(row) if row else None

    def organization_by_slug(self, slug: str) -> OrganizationRecord | None:
        row = self._one("SELECT * FROM organizations WHERE lower(slug)=? AND active=1", (slug.lower(),))
        return self._org_from_row(row) if row else None

    @staticmethod
    def _org_from_row(row) -> OrganizationRecord:
        return OrganizationRecord(id=row["id"], name=row["name"], slug=row["slug"], created_at=row["created_at"], active=bool(row["active"]))

    # -- users / auth --------------------------------------------------------
    def authenticate(self, org_slug: str, username: str, password: str) -> UserRecord | None:
        org = self.organization_by_slug(org_slug)
        row = None
        if org:
            row = self._one(
                "SELECT * FROM users WHERE organization_id=? AND username_lower=? AND active=1",
                (org.id, username.lower()),
            )
        # Always pay the same PBKDF2 cost whether or not a matching org/user
        # was found -- see _DUMMY_HASH above for why.
        password_hash = row["password_hash"] if row else _DUMMY_HASH
        password_ok = _verify_password(password, password_hash)
        if not row or not password_ok:
            return None
        return self.get_user(row["id"])

    @staticmethod
    def _user_from_row(row) -> UserRecord:
        # MFA state is intentionally held in a separate table.  This keeps
        # the encrypted TOTP secret out of every regular user query and makes
        # disabling/removing it an explicit operation.
        # (This helper is static for legacy callers, so mfa_enabled is filled
        # by get_user/list_users below.)
        return UserRecord(
            id=row["id"], organization_id=row["organization_id"], username=row["username"], full_name=row["full_name"],
            role=row["role"], password_hash=row["password_hash"], created_at=row["created_at"],
            active=bool(row["active"]), must_change_password=bool(row["must_change_password"]),
        )

    def get_user(self, user_id: str) -> UserRecord | None:
        row = self._one("SELECT * FROM users WHERE id=?", (user_id,))
        if not row:
            return None
        user = self._user_from_row(row)
        return user.model_copy(update={"mfa_enabled": self.mfa_is_enabled(user.id)})

    def public_user(self, u: UserRecord) -> dict:
        org = self.organization_by_id(u.organization_id)
        return {
            "id": u.id, "organization_id": u.organization_id, "organization_name": org.name, "organization_slug": org.slug,
            "username": u.username, "full_name": u.full_name, "role": u.role, "active": u.active,
            "must_change_password": u.must_change_password,
            "mfa_enabled": u.mfa_enabled,
        }

    def change_password(self, u: UserRecord, current: str, new: str) -> bool:
        with self._lock, self._conn:
            if not _verify_password(current, u.password_hash):
                return False
            self._conn.execute(
                "UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                (_hash_password(new), u.id),
            )
            return True

    def create_user(self, org_id: str, payload: UserCreate, *, force_password_change: bool = False) -> UserRecord:
        user_id = str(uuid4())
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO users(id,organization_id,username,username_lower,full_name,role,password_hash,created_at,active,must_change_password) VALUES (?,?,?,?,?,?,?,?,1,?)",
                    (user_id, org_id, payload.username, payload.username.lower(), payload.full_name, payload.role,
                     _hash_password(payload.password), _iso(_now()), int(force_password_change)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Username already exists in this clinic") from exc
        return self.get_user(user_id)

    def reset_mfa_as_admin(self, org_id: str, target_user_id: str) -> UserRecord | None:
        """Remove a lost-device factor and revoke all existing sessions."""
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM users WHERE id=? AND organization_id=? AND active=1", (target_user_id, org_id)).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM user_mfa WHERE user_id=?", (target_user_id,))
            self._conn.execute("DELETE FROM mfa_login_challenges WHERE user_id=?", (target_user_id,))
            self._conn.execute("DELETE FROM sessions WHERE user_id=?", (target_user_id,))
        return self.get_user(target_user_id)

    def list_users(self, org: str) -> list[UserRecord]:
        rows = self._all("SELECT * FROM users WHERE organization_id=? ORDER BY lower(full_name)", (org,))
        return [self.get_user(r["id"]) for r in rows]

    # -- multi-factor authentication (RFC 6238 TOTP) ------------------------
    def mfa_is_enabled(self, user_id: str) -> bool:
        row = self._one("SELECT enabled FROM user_mfa WHERE user_id=?", (user_id,))
        return bool(row and row["enabled"])

    def begin_mfa_setup(self, user_id: str) -> str:
        from .mfa import generate_secret
        secret = generate_secret()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO user_mfa(user_id,secret_enc,pending_secret_enc,enabled,updated_at) VALUES (?,?,?,0,?) "
                "ON CONFLICT(user_id) DO UPDATE SET pending_secret_enc=excluded.pending_secret_enc,updated_at=excluded.updated_at",
                (user_id, None, self.cipher.encrypt_text(secret), _iso(_now())),
            )
        return secret

    def confirm_mfa_setup(self, user_id: str, code: str) -> bool:
        from .mfa import verify_totp
        with self._lock, self._conn:
            row = self._conn.execute("SELECT pending_secret_enc FROM user_mfa WHERE user_id=?", (user_id,)).fetchone()
            if not row or row["pending_secret_enc"] is None:
                return False
            secret = self.cipher.decrypt_text(row["pending_secret_enc"])
            if not verify_totp(secret, code):
                return False
            self._conn.execute("UPDATE user_mfa SET secret_enc=?,pending_secret_enc=NULL,enabled=1,updated_at=? WHERE user_id=?", (self.cipher.encrypt_text(secret), _iso(_now()), user_id))
        return True

    def disable_mfa(self, user_id: str, code: str) -> bool:
        from .mfa import verify_totp
        with self._lock, self._conn:
            row = self._conn.execute("SELECT secret_enc,enabled FROM user_mfa WHERE user_id=?", (user_id,)).fetchone()
            if not row or not row["enabled"] or row["secret_enc"] is None or not verify_totp(self.cipher.decrypt_text(row["secret_enc"]), code):
                return False
            self._conn.execute("DELETE FROM user_mfa WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM mfa_login_challenges WHERE user_id=?", (user_id,))
        return True

    def begin_mfa_login_challenge(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM mfa_login_challenges WHERE expires_at<=?", (_iso(_now()),))
            self._conn.execute("INSERT INTO mfa_login_challenges(token_hash,user_id,expires_at,attempts) VALUES (?,?,?,0)", (digest, user_id, _iso(_now() + timedelta(minutes=5))))
        return token

    def complete_mfa_login_challenge(self, token: str, code: str) -> UserRecord | None:
        from .mfa import verify_totp
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM mfa_login_challenges WHERE token_hash=?", (digest,)).fetchone()
            if not row or datetime.fromisoformat(row["expires_at"]) <= _now() or row["attempts"] >= 5:
                self._conn.execute("DELETE FROM mfa_login_challenges WHERE token_hash=?", (digest,))
                return None
            secret_row = self._conn.execute("SELECT secret_enc,enabled FROM user_mfa WHERE user_id=?", (row["user_id"],)).fetchone()
            valid = bool(secret_row and secret_row["enabled"] and secret_row["secret_enc"] and verify_totp(self.cipher.decrypt_text(secret_row["secret_enc"]), code))
            if not valid:
                self._conn.execute("UPDATE mfa_login_challenges SET attempts=attempts+1 WHERE token_hash=?", (digest,))
                return None
            self._conn.execute("DELETE FROM mfa_login_challenges WHERE token_hash=?", (digest,))
        return self.get_user(row["user_id"])

    def set_user_active(self, org: str, user_id: str, active: bool) -> UserRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM users WHERE id=? AND organization_id=?", (user_id, org)).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE users SET active=? WHERE id=?", (int(active), user_id))
        return self.get_user(user_id)

    # -- sessions (persisted, so a restart does not log everyone out) -------
    def create_session(self, user_id: str, minutes: int) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires_at = _now() + timedelta(minutes=minutes)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions(id,token,user_id,created_at,expires_at) VALUES (?,?,?,?,?)",
                (str(uuid4()), token, user_id, _iso(_now()), _iso(expires_at)),
            )
        return token, expires_at

    def get_session(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
            if not row:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= _now():
                with self._conn:
                    self._conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                return None
            return {"user_id": row["user_id"], "expires_at": expires_at}

    def touch_session(self, token: str, minutes: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET expires_at=? WHERE token=?",
                (_iso(_now() + timedelta(minutes=minutes)), token),
            )

    def delete_session(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE token=?", (token,))

    def delete_sessions_for_user(self, user_id: str) -> None:
        """Invalidates every active session for a user. Called after a
        password change so a stolen token stops working immediately instead
        of remaining valid until it naturally expires."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def purge_expired_sessions(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE expires_at<=?", (_iso(_now()),))



    # -- login rate limiting / lockout ---------------------------------------
    def record_login_attempt(self, org_slug: str, username: str, success: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO login_attempts(organization_slug,username_lower,occurred_at,success) VALUES (?,?,?,?)",
                (org_slug.lower(), username.lower(), _iso(_now()), int(success)),
            )
            # keep the table small: this identity does not need attempts older than a day
            self._conn.execute(
                "DELETE FROM login_attempts WHERE organization_slug=? AND username_lower=? AND occurred_at<?",
                (org_slug.lower(), username.lower(), _iso(_now() - timedelta(days=1))),
            )

    def is_locked_out(self, org_slug: str, username: str) -> tuple[bool, int]:
        rows = self._all(
            "SELECT occurred_at, success FROM login_attempts WHERE organization_slug=? AND username_lower=? ORDER BY occurred_at DESC LIMIT ?",
            (org_slug.lower(), username.lower(), LOCKOUT_THRESHOLD),
        )
        if len(rows) < LOCKOUT_THRESHOLD or any(r["success"] for r in rows):
            return False, 0
        oldest_in_streak = datetime.fromisoformat(rows[-1]["occurred_at"])
        unlock_at = oldest_in_streak + timedelta(minutes=LOCKOUT_MINUTES)
        remaining = (unlock_at - _now()).total_seconds()
        if remaining <= 0:
            return False, 0
        return True, int(remaining) + 1

    # -- audit log (tamper-evident hash chain) -------------------------------
    @staticmethod
    def _audit_digest(prev_hash, record_id, occurred_at, user_id, username, role, action, resource_type, resource_id, detail_enc) -> str:
        detail_repr = detail_enc.hex() if detail_enc is not None else "None"
        digest_input = "|".join([
            prev_hash, record_id, str(occurred_at), str(user_id), username, role, action, resource_type,
            str(resource_id), detail_repr,
        ])
        return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    def _insert_audit_row_locked(self, org_id, occurred_at, user_id, username, role, action, resource_type, resource_id, detail) -> AuditRecord:
        """Must be called with self._lock already held (and inside a
        `with self._conn:` transaction) by the caller."""
        prev = self._conn.execute(
            "SELECT hash FROM audit WHERE organization_id=? ORDER BY seq DESC LIMIT 1", (org_id,)
        ).fetchone()
        prev_hash = prev["hash"] if prev else "0" * 64
        record_id = str(uuid4())
        detail_enc = self.cipher.encrypt_text(detail) if detail is not None else None
        digest = self._audit_digest(prev_hash, record_id, occurred_at, user_id, username, role, action, resource_type, resource_id, detail_enc)
        self._conn.execute(
            "INSERT INTO audit(id,organization_id,occurred_at,user_id,username,role,action,resource_type,resource_id,detail_enc,prev_hash,hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, org_id, occurred_at, user_id, username, role, action, resource_type, resource_id, detail_enc, prev_hash, digest),
        )
        return AuditRecord(id=record_id, organization_id=org_id, occurred_at=occurred_at, user_id=user_id, username=username,
                            role=role, action=action, resource_type=resource_type, resource_id=resource_id, detail=detail)

    def audit(self, user, action, resource_type, resource_id=None, detail=None) -> AuditRecord:
        with self._lock, self._conn:
            return self._insert_audit_row_locked(
                user.organization_id, _iso(_now()), user.id, user.username, user.role, action, resource_type, resource_id, detail,
            )

    def _audit_from_row(self, row) -> AuditRecord:
        detail = self.cipher.decrypt_text(row["detail_enc"]) if row["detail_enc"] is not None else None
        return AuditRecord(id=row["id"], organization_id=row["organization_id"], occurred_at=row["occurred_at"],
                            user_id=row["user_id"], username=row["username"], role=row["role"], action=row["action"],
                            resource_type=row["resource_type"], resource_id=row["resource_id"], detail=detail)

    def list_audit(self, org: str) -> list[AuditRecord]:
        rows = self._all("SELECT * FROM audit WHERE organization_id=? ORDER BY seq DESC", (org,))
        return [self._audit_from_row(r) for r in rows]

    def verify_audit_chain(self, org: str) -> dict:
        """Recomputes the hash chain for an organization's audit log and
        reports whether it is intact. Returns the sequence number of the
        first broken link, if any -- everything from that point on can no
        longer be trusted as unmodified."""
        rows = self._all("SELECT * FROM audit WHERE organization_id=? ORDER BY seq ASC", (org,))
        prev_hash = "0" * 64
        for row in rows:
            expected = self._audit_digest(prev_hash, row["id"], row["occurred_at"], row["user_id"], row["username"], row["role"], row["action"], row["resource_type"], row["resource_id"], row["detail_enc"])
            if row["prev_hash"] != prev_hash or row["hash"] != expected:
                return {"intact": False, "checked_entries": len(rows), "first_broken_seq": row["seq"]}
            prev_hash = row["hash"]
        return {"intact": True, "checked_entries": len(rows), "first_broken_seq": None}

    # -- session management (used by admins to see/revoke active sessions) --
    def list_active_sessions(self, org_id: str) -> list[dict]:
        rows = self._all(
            "SELECT s.id,s.user_id,s.created_at,s.expires_at,u.username,u.full_name,u.role "
            "FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE u.organization_id=? AND s.expires_at>? ORDER BY s.created_at DESC",
            (org_id, _iso(_now())),
        )
        return [dict(r) for r in rows]

    def revoke_session(self, org_id: str, session_id: str) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT s.token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND u.organization_id=?",
                (session_id, org_id),
            ).fetchone()
            if not row:
                return False
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return True



    # -- patients -------------------------------------------------------------
    def create_patient(self, org: str, p: PatientCreate) -> PatientRecord:
        patient_id = str(uuid4())
        created_at = _iso(_now())
        blob = self._seal({
            "full_name": p.full_name, "date_of_birth": p.date_of_birth, "phone": p.phone, "email": p.email,
            "clinical_profile": ClinicalProfile().model_dump(mode="json"),
        })
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO patients(id,organization_id,created_at,data) VALUES (?,?,?,?)",
                (patient_id, org, created_at, blob),
            )
        return self.get_patient(org, patient_id)

    def _patient_from_row(self, row) -> PatientRecord:
        payload = self._unseal(row["data"])
        return PatientRecord(id=row["id"], organization_id=row["organization_id"], created_at=row["created_at"], **payload)

    def list_patients(self, org: str) -> list[PatientRecord]:
        rows = self._all("SELECT * FROM patients WHERE organization_id=? ORDER BY created_at DESC", (org,))
        return [self._patient_from_row(r) for r in rows]

    def get_patient(self, org: str, id: str) -> PatientRecord | None:
        row = self._one("SELECT * FROM patients WHERE id=? AND organization_id=?", (id, org))
        return self._patient_from_row(row) if row else None

    def update_clinical_profile(self, org: str, patient_id: str, payload: ClinicalProfileUpdate) -> PatientRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM patients WHERE id=? AND organization_id=?", (patient_id, org)).fetchone()
            if not row:
                return None
            data = self._unseal(row["data"])
            data["clinical_profile"] = ClinicalProfile.model_validate(payload.model_dump()).model_dump(mode="json")
            self._conn.execute("UPDATE patients SET data=? WHERE id=?", (self._seal(data), patient_id))
        return self.get_patient(org, patient_id)

    # -- documents --------------------------------------------------------------
    ATTENTION_KEYWORDS = CLINICAL_SIGNIFICANCE_KEYWORDS

    def add_document(self, org: str, patient_id: str, filename: str, media_type: str, text: str, raw: bytes, extraction_method: str = "text") -> DocumentRecord:
        doc_id = str(uuid4())
        safe_name = Path(filename).name.replace(" ", "_")
        encrypted_path = self.upload_dir / f"{org}-{doc_id}-{safe_name}.enc"
        encrypted_path.write_bytes(self.cipher.encrypt_bytes(raw))
        attention = any(w in text.lower() for w in self.ATTENTION_KEYWORDS)
        uploaded_at = _iso(_now())
        blob = self._seal({"filename": Path(filename).name, "media_type": media_type, "extraction_method": extraction_method, "text": text})
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO documents(id,organization_id,patient_id,uploaded_at,size_bytes,status,attention,data) VALUES (?,?,?,?,?,?,?,?)",
                (doc_id, org, patient_id, uploaded_at, len(raw), "ready", int(attention), blob),
            )
        return self._get_document(doc_id)

    def _document_from_row(self, row) -> DocumentRecord:
        payload = self._unseal(row["data"])
        return DocumentRecord(
            id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
            uploaded_at=row["uploaded_at"], size_bytes=row["size_bytes"], status=row["status"],
            attention=bool(row["attention"]), **payload,
        )

    def _get_document(self, doc_id: str) -> DocumentRecord | None:
        row = self._one("SELECT * FROM documents WHERE id=?", (doc_id,))
        return self._document_from_row(row) if row else None

    def list_documents(self, org: str, patient_id: str | None = None, *, include_archived: bool = False) -> list[DocumentRecord]:
        if patient_id:
            where, args = "organization_id=? AND patient_id=?", (org, patient_id)
        else:
            where, args = "organization_id=?", (org,)
        if not include_archived:
            where += " AND status != 'archived'"
        rows = self._all(f"SELECT * FROM documents WHERE {where} ORDER BY uploaded_at DESC", args)
        return [self._document_from_row(r) for r in rows]

    def get_document(self, org: str, patient_id: str, id: str) -> DocumentRecord | None:
        row = self._one("SELECT * FROM documents WHERE id=? AND organization_id=? AND patient_id=?", (id, org, patient_id))
        return self._document_from_row(row) if row else None

    def document_bytes(self, org: str, patient_id: str, id: str) -> tuple[DocumentRecord, bytes] | None:
        document = self.get_document(org, patient_id, id)
        if not document:
            return None
        files = sorted(self.upload_dir.glob(f"{org}-{id}-*.enc"))
        if len(files) != 1:
            return None
        return document, self.cipher.decrypt_bytes(files[0].read_bytes())

    def archive_document(self, org: str, patient_id: str, id: str, reason: str) -> DocumentRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE id=? AND organization_id=? AND patient_id=?", (id, org, patient_id)
            ).fetchone()
            if not row:
                return None
            if row["status"] == "archived":
                return self._document_from_row(row)
            payload = self._unseal(row["data"])
            payload["archived_at"] = _iso(_now())
            payload["archive_reason"] = reason.strip()
            self._conn.execute("UPDATE documents SET status='archived', attention=0, data=? WHERE id=?", (self._seal(payload), id))
        return self.get_document(org, patient_id, id)

    # -- laboratory results -----------------------------------------------------
    def add_lab_result(self, org: str, patient_id: str, payload: LabResultCreate, *, source_document_id: str | None = None,
                       status: str = "draft", abnormality: str = "unknown") -> LabResultRecord:
        result_id = str(uuid4())
        created_at = _iso(_now())
        data = payload.model_dump(mode="json")
        data.pop("collected_at", None)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO lab_results(id,organization_id,patient_id,created_at,collected_at,status,source_document_id,data) VALUES (?,?,?,?,?,?,?,?)",
                (result_id, org, patient_id, created_at, _iso(payload.collected_at) if payload.collected_at else None,
                 status, source_document_id, self._seal(data)),
            )
        return self._get_lab_result(result_id)

    def _lab_result_from_row(self, row) -> LabResultRecord:
        payload = self._unseal(row["data"])
        return LabResultRecord(id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
                               created_at=row["created_at"], collected_at=row["collected_at"], status=row["status"],
                               source_document_id=row["source_document_id"], **payload)

    def _get_lab_result(self, result_id: str) -> LabResultRecord | None:
        row = self._one("SELECT * FROM lab_results WHERE id=?", (result_id,))
        return self._lab_result_from_row(row) if row else None

    def list_lab_results(self, org: str, patient_id: str) -> list[LabResultRecord]:
        rows = self._all("SELECT * FROM lab_results WHERE organization_id=? AND patient_id=? ORDER BY COALESCE(collected_at, created_at) DESC", (org, patient_id))
        return [self._lab_result_from_row(row) for row in rows]

    def update_lab_result_status(self, org: str, patient_id: str, result_id: str, status: str) -> LabResultRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT 1 FROM lab_results WHERE id=? AND organization_id=? AND patient_id=?", (result_id, org, patient_id)).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE lab_results SET status=? WHERE id=?", (status, result_id))
        return self._get_lab_result(result_id)

    # -- appointments -------------------------------------------------------------
    def create_appointment(self, org: str, p: AppointmentCreate) -> AppointmentRecord:
        appt_id = str(uuid4())
        blob = self._seal({"reason": p.reason, "notes": p.notes})
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO appointments(id,organization_id,patient_id,starts_at,status,created_at,data) VALUES (?,?,?,?,?,?,?)",
                (appt_id, org, p.patient_id, _iso(p.starts_at), "scheduled", _iso(_now()), blob),
            )
        return self._get_appointment(appt_id)

    def _appointment_from_row(self, row) -> AppointmentRecord:
        payload = self._unseal(row["data"])
        return AppointmentRecord(
            id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
            starts_at=row["starts_at"], status=row["status"], created_at=row["created_at"], **payload,
        )

    def _get_appointment(self, appt_id: str) -> AppointmentRecord | None:
        row = self._one("SELECT * FROM appointments WHERE id=?", (appt_id,))
        return self._appointment_from_row(row) if row else None

    def list_appointments(self, org: str) -> list[AppointmentRecord]:
        rows = self._all("SELECT * FROM appointments WHERE organization_id=? ORDER BY starts_at", (org,))
        return [self._appointment_from_row(r) for r in rows]

    def update_appointment_status(self, org: str, id: str, status: str) -> AppointmentRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT 1 FROM appointments WHERE id=? AND organization_id=?", (id, org)).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE appointments SET status=? WHERE id=?", (status, id))
        return self._get_appointment(id)

    # -- reports -------------------------------------------------------------
    def add_report(self, org: str, patient_id: str, title: str, content: str) -> GeneratedReport:
        report_id = str(uuid4())
        blob = self._seal({"title": title, "content": content, "status": "draft"})
        generated_at = _iso(_now())
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO reports(id,organization_id,patient_id,generated_at,data) VALUES (?,?,?,?,?)",
                (report_id, org, patient_id, generated_at, blob),
            )
        return self._get_report(report_id)

    def _report_from_row(self, row) -> GeneratedReport:
        payload = self._unseal(row["data"])
        return GeneratedReport(id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
                                generated_at=row["generated_at"], **payload)

    def _get_report(self, report_id: str) -> GeneratedReport | None:
        row = self._one("SELECT * FROM reports WHERE id=?", (report_id,))
        return self._report_from_row(row) if row else None

    def list_reports(self, org: str, patient_id: str | None = None) -> list[GeneratedReport]:
        if patient_id:
            rows = self._all("SELECT * FROM reports WHERE organization_id=? AND patient_id=? ORDER BY generated_at DESC", (org, patient_id))
        else:
            rows = self._all("SELECT * FROM reports WHERE organization_id=? ORDER BY generated_at DESC", (org,))
        return [self._report_from_row(r) for r in rows]

    # -- encounters -----------------------------------------------------------
    def add_encounter(self, org: str, patient, user, payload: EncounterCreate) -> EncounterRecord:
        enc_id = str(uuid4())
        created_at = _iso(_now())
        data = payload.model_dump(mode="json")
        data["clinician_id"] = user.id
        data["clinician_name"] = user.full_name
        visit_date = data.pop("visit_date")
        blob = self._seal(data)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO encounters(id,organization_id,patient_id,visit_date,created_at,data) VALUES (?,?,?,?,?,?)",
                (enc_id, org, patient.id, visit_date, created_at, blob),
            )
        return self._get_encounter(enc_id)

    def _encounter_from_row(self, row) -> EncounterRecord:
        payload = self._unseal(row["data"])
        return EncounterRecord(id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
                                visit_date=row["visit_date"], created_at=row["created_at"], **payload)

    def _get_encounter(self, enc_id: str) -> EncounterRecord | None:
        row = self._one("SELECT * FROM encounters WHERE id=?", (enc_id,))
        return self._encounter_from_row(row) if row else None

    def list_encounters(self, org: str, patient_id: str) -> list[EncounterRecord]:
        rows = self._all("SELECT * FROM encounters WHERE organization_id=? AND patient_id=? ORDER BY visit_date DESC", (org, patient_id))
        return [self._encounter_from_row(r) for r in rows]

    def list_all_encounters(self, org: str) -> list[EncounterRecord]:
        """All encounters for an organization across every patient -- used by
        the epidemiology radar and differential analysis, which need
        clinic-wide aggregates rather than a single patient's history."""
        rows = self._all("SELECT * FROM encounters WHERE organization_id=? ORDER BY visit_date DESC", (org,))
        return [self._encounter_from_row(r) for r in rows]

    # -- pre-visit briefings ----------------------------------------------------
    def add_briefing(self, org: str, patient_id: str, user, payload: dict) -> PreVisitBriefing:
        b_id = str(uuid4())
        generated_at = _iso(_now())
        blob = self._seal({**payload, "generated_by": user.full_name})
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO briefings(id,organization_id,patient_id,generated_at,data) VALUES (?,?,?,?,?)",
                (b_id, org, patient_id, generated_at, blob),
            )
        return self._get_briefing(b_id)

    def _briefing_from_row(self, row) -> PreVisitBriefing:
        payload = self._unseal(row["data"])
        return PreVisitBriefing(id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
                                 generated_at=row["generated_at"], **payload)

    def _get_briefing(self, b_id: str) -> PreVisitBriefing | None:
        row = self._one("SELECT * FROM briefings WHERE id=?", (b_id,))
        return self._briefing_from_row(row) if row else None

    def list_briefings(self, org: str, patient_id: str) -> list[PreVisitBriefing]:
        rows = self._all("SELECT * FROM briefings WHERE organization_id=? AND patient_id=? ORDER BY generated_at DESC", (org, patient_id))
        return [self._briefing_from_row(r) for r in rows]

    # -- AI medical scribe drafts -------------------------------------------------
    def add_scribe_draft(self, organization_id: str, patient_id: str, user, mode: str, transcript: str, payload: dict) -> ScribeDraftRecord:
        draft_id = str(uuid4())
        created_at = _iso(_now())
        data = {**payload, "clinician_id": user.id, "clinician_name": user.full_name, "mode": mode,
                "transcript": transcript, "status": "draft", "updated_at": None, "approved_at": None, "encounter_id": None}
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO scribe_drafts(id,organization_id,patient_id,created_at,status,data) VALUES (?,?,?,?,?,?)",
                (draft_id, organization_id, patient_id, created_at, "draft", self._seal(data)),
            )
        return self._get_scribe_draft(draft_id)

    def _scribe_from_row(self, row) -> ScribeDraftRecord:
        payload = self._unseal(row["data"])
        return ScribeDraftRecord(id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
                                  created_at=row["created_at"], **payload)

    def _get_scribe_draft(self, draft_id: str) -> ScribeDraftRecord | None:
        row = self._one("SELECT * FROM scribe_drafts WHERE id=?", (draft_id,))
        return self._scribe_from_row(row) if row else None

    def list_scribe_drafts(self, organization_id: str, patient_id: str) -> list[ScribeDraftRecord]:
        rows = self._all("SELECT * FROM scribe_drafts WHERE organization_id=? AND patient_id=? ORDER BY created_at DESC", (organization_id, patient_id))
        return [self._scribe_from_row(r) for r in rows]

    def update_scribe_draft(self, organization_id: str, patient_id: str, draft_id: str, payload: ScribeDraftUpdate) -> ScribeDraftRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM scribe_drafts WHERE id=? AND organization_id=? AND patient_id=?", (draft_id, organization_id, patient_id)
            ).fetchone()
            if not row or row["status"] != "draft":
                return None
            data = self._unseal(row["data"])
            data.update(payload.model_dump())
            data["updated_at"] = _iso(_now())
            self._conn.execute("UPDATE scribe_drafts SET data=? WHERE id=?", (self._seal(data), draft_id))
        return self._get_scribe_draft(draft_id)

    def finalize_scribe_draft(self, organization_id: str, patient, draft_id: str, user, status: str, create_encounter: bool = False, visit_date=None):
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM scribe_drafts WHERE id=? AND organization_id=? AND patient_id=?", (draft_id, organization_id, patient.id)
            ).fetchone()
            if not row:
                return None, None
            if row["status"] != "draft":
                return self._scribe_from_row(row), None
            data = self._unseal(row["data"])
            data["status"] = status
            data["updated_at"] = _iso(_now())
            encounter = None
            if status == "approved":
                data["approved_at"] = _iso(_now())
                if create_encounter:
                    payload = EncounterCreate(
                        visit_date=visit_date or _now(), chief_complaint=data.get("chief_complaint") or "Pregled",
                        anamnesis=data.get("anamnesis", ""), examination=data.get("examination", ""),
                        assessment=data.get("assessment", ""), plan=data.get("plan", ""), vital_signs={},
                    )
                    enc_id = str(uuid4())
                    enc_created_at = _iso(_now())
                    enc_data = payload.model_dump(mode="json")
                    enc_data["clinician_id"] = user.id
                    enc_data["clinician_name"] = user.full_name
                    enc_visit_date = enc_data.pop("visit_date")
                    self._conn.execute(
                        "INSERT INTO encounters(id,organization_id,patient_id,visit_date,created_at,data) VALUES (?,?,?,?,?,?)",
                        (enc_id, organization_id, patient.id, enc_visit_date, enc_created_at, self._seal(enc_data)),
                    )
                    encounter = self._encounter_from_row(self._conn.execute("SELECT * FROM encounters WHERE id=?", (enc_id,)).fetchone())
                    data["encounter_id"] = encounter.id
            self._conn.execute("UPDATE scribe_drafts SET status=?, data=? WHERE id=?", (status, self._seal(data), draft_id))
        return self._get_scribe_draft(draft_id), encounter

    def update_scribe_status(self, organization_id: str, patient_id: str, draft_id: str, status: str) -> ScribeDraftRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM scribe_drafts WHERE id=? AND organization_id=? AND patient_id=?", (draft_id, organization_id, patient_id)
            ).fetchone()
            if not row:
                return None
            data = self._unseal(row["data"])
            data["status"] = status
            self._conn.execute("UPDATE scribe_drafts SET status=?, data=? WHERE id=?", (status, self._seal(data), draft_id))
        return self._get_scribe_draft(draft_id)

    def append_candidate_to_latest_scribe(self, organization_id: str, patient_id: str, candidate) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM scribe_drafts WHERE organization_id=? AND patient_id=? AND status='draft' ORDER BY created_at DESC LIMIT 1",
                (organization_id, patient_id),
            ).fetchone()
            if not row:
                return False
            data = self._unseal(row["data"])
            line = f"Lekar prihvatio AI sugestiju za razmatranje: {candidate.name}."
            if candidate.doctor_note:
                line += f" Napomena lekara: {candidate.doctor_note}"
            data["assessment"] = (data.get("assessment", "").strip() + "\n" + line).strip()
            self._conn.execute("UPDATE scribe_drafts SET data=? WHERE id=?", (self._seal(data), row["id"]))
            return True

    # -- AI differential analyses -------------------------------------------------
    def add_differential_analysis(self, organization_id: str, patient_id: str, user, payload: dict) -> DifferentialAnalysis:
        analysis_id = str(uuid4())
        generated_at = _iso(_now())
        blob = self._seal({**payload, "generated_by": user.full_name})
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO differential_analyses(id,organization_id,patient_id,generated_at,data) VALUES (?,?,?,?,?)",
                (analysis_id, organization_id, patient_id, generated_at, blob),
            )
        return self._get_differential(analysis_id)

    def _differential_from_row(self, row) -> DifferentialAnalysis:
        payload = self._unseal(row["data"])
        return DifferentialAnalysis(id=row["id"], organization_id=row["organization_id"], patient_id=row["patient_id"],
                                     generated_at=row["generated_at"], **payload)

    def _get_differential(self, analysis_id: str) -> DifferentialAnalysis | None:
        row = self._one("SELECT * FROM differential_analyses WHERE id=?", (analysis_id,))
        return self._differential_from_row(row) if row else None

    def list_differential_analyses(self, organization_id: str, patient_id: str) -> list[DifferentialAnalysis]:
        rows = self._all(
            "SELECT * FROM differential_analyses WHERE organization_id=? AND patient_id=? ORDER BY generated_at DESC",
            (organization_id, patient_id),
        )
        return [self._differential_from_row(r) for r in rows]

    def list_pending_red_flags(self, organization_id: str) -> list[dict]:
        """Every 'crvena zastavica' candidate still awaiting a lekar's
        accept/dismiss decision, across all patients in this clinic. A
        red-flag candidate a doctor generated once and then never came back
        to review is exactly the case worth surfacing on the dashboard --
        the differential panel that produced it lives on a specific
        patient's workspace tab, which nobody reopens without a reason.
        Only the most recent analysis per patient is considered, so an
        older, superseded red flag that a newer analysis re-evaluated
        doesn't keep nagging after the newer one already ran.
        """
        rows = self._all(
            "SELECT d.id, d.patient_id, d.generated_at, d.data FROM differential_analyses d "
            "INNER JOIN (SELECT patient_id, MAX(generated_at) AS max_gen FROM differential_analyses "
            "  WHERE organization_id=? GROUP BY patient_id) latest "
            "ON latest.patient_id=d.patient_id AND latest.max_gen=d.generated_at "
            "WHERE d.organization_id=? ORDER BY d.generated_at DESC",
            (organization_id, organization_id),
        )
        out = []
        for row in rows:
            data = self._unseal(row["data"])
            for c in data.get("candidates", []):
                if c.get("red_flag") and c.get("review_status", "pending") == "pending":
                    out.append({
                        "patient_id": row["patient_id"], "analysis_id": row["id"],
                        "candidate_id": c["id"], "candidate_name": c["name"],
                        "match_score": c["match_score"], "generated_at": row["generated_at"],
                    })
        return out

    def review_differential_candidate(self, organization_id: str, patient_id: str, analysis_id: str, candidate_id: str, user, status: str, doctor_note: str | None):
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM differential_analyses WHERE id=? AND organization_id=? AND patient_id=?",
                (analysis_id, organization_id, patient_id),
            ).fetchone()
            if not row:
                return None
            data = self._unseal(row["data"])
            candidate_dict = next((c for c in data.get("candidates", []) if c["id"] == candidate_id), None)
            if not candidate_dict:
                return None
            candidate_dict["review_status"] = status
            candidate_dict["reviewed_at"] = _iso(_now())
            candidate_dict["reviewed_by"] = user.full_name
            candidate_dict["doctor_note"] = doctor_note
            self._conn.execute("UPDATE differential_analyses SET data=? WHERE id=?", (self._seal(data), analysis_id))
            row2 = self._conn.execute("SELECT * FROM differential_analyses WHERE id=?", (analysis_id,)).fetchone()
        analysis = self._differential_from_row(row2)
        candidate = next(c for c in analysis.candidates if c.id == candidate_id)
        return analysis, candidate
