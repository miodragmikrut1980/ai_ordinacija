"""Izvoz podataka i onboarding checklist.

Two of the product's stated differentiators live here:

- Structured data export (per-patient and whole-clinic). A small private
  clinic gets asked for its records by inspectors, by patients exercising
  data-portability rights under Serbian data-protection law, and by its own
  accountant -- and the answer must not be "log in and screenshot things".
  Every export is a single audited action producing a ZIP a non-technical
  person can open. Exports deliberately reuse the same store accessors the
  UI uses (tenant-scoped, decrypted through the same path) so an export can
  never see more than the exporting user could on screen.

- Setup checklist. The "onboarding in 15 minutes without an IT person"
  promise fails if the clinic silently keeps running with demo credentials.
  The checklist's most important job is detecting that the seeded demo
  passwords still work and saying so loudly on the admin dashboard.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..deps import patient_or_404, require_roles
from ..state import store

router = APIRouter()

# The exact credential set seeded by store._seed_demo_clinic(). Kept as a
# module-level constant next to the check that uses it so a future change to
# the seed data has one obvious second place to update.
_DEMO_CREDENTIALS = (
    ("doctor", "doctor123"),
    ("reception", "reception123"),
    ("admin", "admin123"),
)


class SetupChecklist(BaseModel):
    default_passwords_active: list[str] = Field(default_factory=list)
    clinic_name_is_default: bool = False
    user_count: int = 0
    https_enabled: bool = False
    production_mode: bool = False
    encryption_key_externally_managed: bool = False
    all_clear: bool = False


class OrganizationRename(BaseModel):
    name: str = Field(min_length=2, max_length=120)


@router.get('/api/setup/checklist', response_model=SetupChecklist)
def setup_checklist(user=Depends(require_roles('admin'))):
    import os
    org = store.organization_by_id(user.organization_id)
    # authenticate() is rate-limit/lockout-free at the store layer (those
    # guards live in the auth router), so probing our own org's demo
    # credentials here doesn't consume the admin's login budget or generate
    # failed-attempt records against real users: record_login_attempt is
    # only called by the login endpoint, not by store.authenticate itself.
    still_default = [
        username for username, password in _DEMO_CREDENTIALS
        if store.authenticate(org.slug, username, password) is not None
    ]
    users = store.list_users(user.organization_id)
    # These must match the actual variables start.sh and crypto.py read --
    # a checklist that checks a variable nothing else in the app reads
    # (CLINIC_SSL_CERTFILE was never a real setting here) is worse than no
    # checklist: it can report "https_enabled: false" forever even on a
    # correctly-configured TLS deployment, training the admin to ignore it.
    https_enabled = os.getenv('CLINIC_TLS') == '1' and bool(os.getenv('CLINIC_TLS_CERT_FILE'))
    production_mode = os.getenv('CLINIC_ENV') == 'production'
    encryption_key_externally_managed = bool(os.getenv('CLINIC_ENCRYPTION_KEY') or os.getenv('CLINIC_ENCRYPTION_KEY_COMMAND'))
    checklist = SetupChecklist(
        default_passwords_active=still_default,
        clinic_name_is_default=org.name.strip().lower() == 'demo clinic',
        user_count=len(users),
        https_enabled=https_enabled,
        production_mode=production_mode,
        encryption_key_externally_managed=encryption_key_externally_managed,
    )
    # all_clear now means "this looks like a real production deployment",
    # not just "someone renamed the org and changed the passwords" -- a
    # clinic could previously see a green checkmark while still running
    # over plain HTTP with a demo-mode auto-generated key. Those are the
    # two flags that actually matter for a real deployment; CLINIC_ENV is
    # necessarily 'production' before crypto.py will even allow the app to
    # start without an explicit key (see crypto.py), so this check is
    # mostly a legibility signal for the admin, not the primary guard.
    checklist.all_clear = (
        not checklist.default_passwords_active
        and not checklist.clinic_name_is_default
        and checklist.https_enabled
        and checklist.production_mode
        and checklist.encryption_key_externally_managed
    )
    return checklist


@router.patch('/api/organization')
def rename_organization(payload: OrganizationRename, user=Depends(require_roles('admin'))):
    org = store.rename_organization(user.organization_id, payload.name)
    store.audit(user, 'rename', 'organization', org.id, org.name)
    return org


def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    # utf-8-sig: Excel (the tool an inspector or accountant will actually
    # open this with) mis-detects plain UTF-8 CSV and renders Serbian
    # diacritics as mojibake without the BOM.
    return buf.getvalue().encode('utf-8-sig')


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode('utf-8')


@router.get('/api/patients/{patient_id}/export.zip')
def export_patient(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    """Complete, portable record of one patient: JSON for machine use plus
    the same data flattened to CSV for humans. Ovo je odgovor na zahtev
    pacijenta za kopiju svojih podataka (pravo na prenosivost)."""
    patient = patient_or_404(user, patient_id)
    encounters = store.list_encounters(user.organization_id, patient_id)
    # A patient export is a complete record, therefore it contains archived
    # source documents as well as active ones. Clinical AI deliberately uses
    # only active documents (the default in PersistentStore).
    documents = store.list_documents(user.organization_id, patient_id, include_archived=True)
    cp = patient.clinical_profile
    exported_at = datetime.now(timezone.utc).isoformat()

    record = {
        'exported_at': exported_at,
        'patient': {
            'full_name': patient.full_name, 'date_of_birth': patient.date_of_birth,
            'phone': patient.phone, 'email': patient.email, 'created_at': patient.created_at,
        },
        'clinical_profile': cp.model_dump(),
        'encounters': [e.model_dump(exclude={'organization_id'}) for e in encounters],
        'documents': [d.model_dump(exclude={'organization_id'}) for d in documents],
    }
    enc_rows = [[e.visit_date, e.clinician_name, e.chief_complaint, e.anamnesis, e.examination,
                 e.assessment, e.plan, '; '.join(f'{k}={v}' for k, v in e.vital_signs.items())] for e in encounters]

    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('karton.json', _json_bytes(record))
        zf.writestr('pregledi.csv', _csv_bytes(
            ['datum', 'lekar', 'razlog dolaska', 'anamneza', 'objektivni pregled', 'procena', 'plan', 'vitalni znaci'], enc_rows))
        for d in documents:
            zf.writestr(f'dokumenti/{d.uploaded_at:%Y-%m-%d}-{d.filename}.txt', d.text.encode('utf-8'))
    store.audit(user, 'export', 'patient_record', patient_id, patient.full_name)
    return Response(out.getvalue(), media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="izvoz-kartona-{patient_id[:8]}.zip"'})


@router.get('/api/export/clinic.zip')
def export_clinic(user=Depends(require_roles('admin'))):
    """Whole-clinic structured export as CSVs an inspector or accountant
    can open directly. Admin-only and audited; the audit log itself is
    included so the export doubles as the inspection artifact."""
    org = store.organization_by_id(user.organization_id)
    patients = store.list_patients(user.organization_id)
    encounters = store.list_all_encounters(user.organization_id)
    appointments = store.list_appointments(user.organization_id)
    audit_rows = store.list_audit(user.organization_id)
    patient_names = {p.id: p.full_name for p in patients}
    exported_at = datetime.now(timezone.utc)

    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('README.txt', (
            f'Izvoz podataka ordinacije: {org.name}\n'
            f'Generisano: {exported_at:%d.%m.%Y. %H:%M} UTC\n\n'
            'Sadržaj:\n'
            '  pacijenti.csv  - demografski podaci\n'
            '  pregledi.csv   - strukturisani pregledi\n'
            '  termini.csv    - zakazani termini\n'
            '  revizija.csv   - kompletan revizioni dnevnik (tamper-evident)\n\n'
            'CSV fajlovi su UTF-8 sa BOM oznakom i otvaraju se direktno u Excel-u.\n'
        ).encode('utf-8'))
        zf.writestr('pacijenti.csv', _csv_bytes(
            ['ime i prezime', 'datum rođenja', 'telefon', 'e-mail', 'kreiran'],
            [[p.full_name, p.date_of_birth or '', p.phone or '', p.email or '', p.created_at] for p in patients]))
        zf.writestr('pregledi.csv', _csv_bytes(
            ['pacijent', 'datum', 'lekar', 'razlog dolaska', 'procena', 'plan'],
            [[patient_names.get(e.patient_id, '?'), e.visit_date, e.clinician_name, e.chief_complaint, e.assessment, e.plan] for e in encounters]))
        zf.writestr('termini.csv', _csv_bytes(
            ['pacijent', 'početak', 'razlog', 'status'],
            [[patient_names.get(a.patient_id, '?'), a.starts_at, a.reason, a.status] for a in appointments]))
        zf.writestr('revizija.csv', _csv_bytes(
            ['vreme', 'korisnik', 'uloga', 'radnja', 'resurs', 'detalj'],
            [[r.occurred_at, r.username, r.role, r.action, r.resource_type, r.detail or ''] for r in audit_rows]))
    store.audit(user, 'export', 'clinic_data', org.id, f'{len(patients)} pacijenata, {len(encounters)} pregleda')
    return Response(out.getvalue(), media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="izvoz-ordinacije-{exported_at:%Y-%m-%d}.zip"'})
