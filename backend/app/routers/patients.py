from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from ..deps import current_user, patient_or_404, require_roles
from ..extractors import UnsupportedDocumentError, extract_text_with_method
from ..epidemiology import build_radar
from ..laboratory import extract_lab_candidates
from ..medication_safety import check_medication_safety, rule_catalog
from ..standards import ATC_CODES, ICD10_CODES, LAB_STANDARDS
from ..models import (
    ChatRequest, ClinicalProfileUpdate,
    DashboardOverview, DocumentArchiveRequest, EncounterCreate, LabResultCreate, LabResultStatusUpdate,
    MedicationSafetyRequest, PatientCreate, PatientOverview, PendingRedFlag,
    PortalAccountCreate, PortalMessageCreate, QuestionnaireSubmit,
)
from ..state import ai, store

router = APIRouter()

UPLOAD_MAX_BYTES = 15 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB


async def _read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Reads the upload in bounded chunks, rejecting it as soon as the cap
    is exceeded -- NOT after buffering the whole thing into memory first.
    The previous `await file.read()` read an arbitrarily large upload
    fully into memory before the size was ever checked, so a large or
    malicious upload could exhaust memory regardless of the 15 MB limit
    the code claimed to enforce. Peak memory here is bounded to roughly
    max_bytes + one chunk.

    This still holds the (capped) file fully in memory afterwards, since
    extract_text_with_method() and the rest of the pipeline operate on an
    in-memory bytes object -- a true disk-streaming pipeline would need
    that extraction layer reworked too, which is a larger change than this
    fix. What this closes is the specific gap: an upload can no longer
    consume unbounded memory before the size limit is ever applied.

    Malware/antivirus scanning of uploads is a separate, still-open gap:
    doing that safely needs a real AV engine (e.g. ClamAV) integrated at
    deployment time, which this environment doesn't provide and can't
    responsibly fake.
    """
    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f'Maximum file size is {max_bytes // (1024 * 1024)} MB')
        chunks.append(chunk)
    return b''.join(chunks)


@router.get('/api/lab-standards')
def lab_standards(user=Depends(current_user)):
    """Seed LOINC codes + general reference ranges for the tests
    laboratory.py already recognizes. See standards.py for scope notes --
    this is a small, correct, extensible set, not a full LOINC service."""
    return {name: std.model_dump() for name, std in LAB_STANDARDS.items()}


@router.get('/api/icd10-codes')
def icd10_codes(user=Depends(current_user)):
    """Seed ICD-10/MKB-10 codes for the conditions differential.py already
    recognizes. See standards.py for scope notes."""
    return ICD10_CODES


@router.get('/api/atc-codes')
def atc_codes(user=Depends(current_user)):
    """Seed WHO ATC codes for the medications medication_safety.py already
    recognizes. See standards.py for scope notes -- same 'small, correct,
    extensible seed set' principle as LOINC/ICD-10 above."""
    return ATC_CODES


@router.post('/api/patients')
def create_patient(payload: PatientCreate, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    r = store.create_patient(user.organization_id, payload)
    store.audit(user, 'create', 'patient', r.id, r.full_name)
    return r


@router.get('/api/patients')
def patients(user=Depends(current_user)):
    return store.list_patients(user.organization_id)


@router.get('/api/dashboard', response_model=DashboardOverview)
def dashboard(user=Depends(current_user)):
    now = datetime.now(timezone.utc)
    apps = store.list_appointments(user.organization_id)
    docs = store.list_documents(user.organization_id)
    reports = store.list_reports(user.organization_id)
    today = [a for a in apps if a.starts_at.date() == now.date() and a.status != 'cancelled']
    # A "running late" appointment is today's scheduled slot whose start time
    # has already passed without the patient being checked in -- the doctor-
    # facing signal that the day's actual pace has drifted from the plan.
    running_late = sum(a.status == 'scheduled' and a.starts_at <= now for a in today)
    # Red-flag visibility is a clinical-judgement feature gated the same way
    # the differential analysis itself is (doctor/admin); a receptionist's
    # dashboard should not surface un-triaged clinical suspicions.
    red_flags = len(store.list_pending_red_flags(user.organization_id)) if user.role in ('doctor', 'admin') else 0
    # Epidemiology highlight: the radar's clusters are its already-filtered,
    # highest-confidence output (see epidemiology.py). Surfacing them on the
    # dashboard -- one line each, doctor/admin only -- is what makes the
    # radar something a lekar actually sees during a working day instead of
    # a tab nobody opens. Same role gate as the radar endpoint itself.
    epi_alerts: list[str] = []
    if user.role in ('doctor', 'admin'):
        radar = build_radar(store.list_all_encounters(user.organization_id), docs, 7)
        epi_alerts = [f"{c['title']} — {c['case_count']} slučajeva u poslednjih {c['window_days']} dana" for c in radar['clusters']]
        epi_alerts += [f"Laboratorijski potvrđeno: {p['name']} ({p['confirmed_count']}×)" for p in radar['confirmed_pathogens'][:3]]
    return DashboardOverview(
        appointments_today=len(today),
        checked_in=sum(a.status == 'checked_in' for a in today),
        needs_attention=sum(d.attention for d in docs),
        reports_this_week=sum(r.generated_at >= now - timedelta(days=7) for r in reports),
        total_patients=len(store.list_patients(user.organization_id)),
        red_flags_pending=red_flags,
        running_late=running_late,
        epi_alerts=epi_alerts[:4],
    )


@router.get('/api/dashboard/red-flags', response_model=list[PendingRedFlag])
def dashboard_red_flags(user=Depends(require_roles('doctor', 'admin'))):
    names = {p.id: p.full_name for p in store.list_patients(user.organization_id)}
    return [
        PendingRedFlag(
            patient_id=r['patient_id'], patient_name=names.get(r['patient_id'], 'Nepoznat pacijent'),
            analysis_id=r['analysis_id'], candidate_id=r['candidate_id'], candidate_name=r['candidate_name'],
            match_score=r['match_score'], generated_at=r['generated_at'],
        )
        for r in store.list_pending_red_flags(user.organization_id)
    ]


@router.post('/api/patients/{patient_id}/documents')
async def upload(patient_id: str, file: UploadFile = File(...), user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    raw = await _read_upload_capped(file, UPLOAD_MAX_BYTES)
    if not raw:
        raise HTTPException(422, 'The uploaded file is empty')
    try:
        text, extraction_method, page_offsets = extract_text_with_method(file.filename or 'document', raw)
    except UnsupportedDocumentError as e:
        raise HTTPException(415, str(e))
    except Exception as e:
        raise HTTPException(422, 'The document could not be read') from e
    if not text:
        raise HTTPException(422, 'No readable text was found in the document')
    r = store.add_document(user.organization_id, patient_id, file.filename or 'document', file.content_type or 'application/octet-stream', text, raw, extraction_method)
    candidates = extract_lab_candidates(text, page_offsets=page_offsets)
    for candidate, abnormality in candidates:
        store.add_lab_result(user.organization_id, patient_id, candidate, source_document_id=r.id, status='draft', abnormality=abnormality)
    store.audit(user, 'upload', 'document', r.id, f'{r.filename}; OCR={extraction_method}; lab drafts={len(candidates)}')
    return {**r.model_dump(mode='json'), 'lab_drafts_created': len(candidates)}


@router.get('/api/documents/inbox')
def inbox(user=Depends(current_user)):
    names = {p.id: p.full_name for p in store.list_patients(user.organization_id)}
    pending_lab_doc_ids = store.documents_with_pending_lab_results(user.organization_id)
    return [
        {'id': d.id, 'patient_id': d.patient_id, 'patient_name': names.get(d.patient_id, 'Unknown patient'),
         'filename': d.filename, 'uploaded_at': d.uploaded_at, 'size_bytes': d.size_bytes, 'status': d.status,
         'attention': d.attention, 'pending_lab_confirmation': d.id in pending_lab_doc_ids}
        for d in store.list_documents(user.organization_id)
    ]


@router.get('/api/patients/{patient_id}/documents')
def documents(patient_id: str, include_archived: bool = True, user=Depends(current_user)):
    patient_or_404(user, patient_id)
    return [
        {'id': d.id, 'filename': d.filename, 'media_type': d.media_type, 'uploaded_at': d.uploaded_at,
         'size_bytes': d.size_bytes, 'status': d.status, 'attention': d.attention, 'extraction_method': d.extraction_method,
         'archived_at': d.archived_at, 'archive_reason': d.archive_reason}
        for d in store.list_documents(user.organization_id, patient_id, include_archived=include_archived)
    ]


@router.get('/api/patients/{patient_id}/documents/{document_id}/original')
def original_document(patient_id: str, document_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    result = store.document_bytes(user.organization_id, patient_id, document_id)
    if not result:
        raise HTTPException(404, 'Document not found')
    document, raw = result
    store.audit(user, 'view_original', 'document', document_id, document.filename)
    # Response headers must be Latin-1; RFC 5987 gives browsers the real UTF-8
    # filename without letting a patient-uploaded filename break the response.
    filename_star = quote(document.filename.replace('\r', '').replace('\n', ''), safe='')
    return Response(raw, media_type=document.media_type, headers={
        'Content-Disposition': f"inline; filename=\"document\"; filename*=UTF-8''{filename_star}",
        'Cache-Control': 'no-store',
    })


@router.post('/api/patients/{patient_id}/documents/{document_id}/archive')
def archive_document(patient_id: str, document_id: str, payload: DocumentArchiveRequest, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    document = store.archive_document(user.organization_id, patient_id, document_id, payload.reason)
    if not document:
        raise HTTPException(404, 'Document not found')
    store.audit(user, 'archive', 'document', document_id, payload.reason.strip())
    return {'id': document.id, 'status': document.status, 'archived_at': document.archived_at, 'archive_reason': document.archive_reason}


@router.get('/api/patients/{patient_id}/overview', response_model=PatientOverview)
async def overview(patient_id: str, user=Depends(current_user)):
    patient_or_404(user, patient_id)
    docs = store.list_documents(user.organization_id, patient_id)
    timeline = await ai.timeline(docs)
    readiness = 'empty' if not docs else 'limited' if len(docs) == 1 else 'ready'
    return PatientOverview(
        document_count=len(docs), timeline_count=len(timeline), source_count=len({d.filename for d in docs}),
        lab_result_count=len(store.list_lab_results(user.organization_id, patient_id)),
        latest_document_at=docs[0].uploaded_at if docs else None, readiness=readiness,
    )


@router.get('/api/patients/{patient_id}/lab-results')
def lab_results(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_lab_results(user.organization_id, patient_id)


@router.get('/api/patients/{patient_id}/lab-results/trend')
def lab_results_trend(patient_id: str, name: str, user=Depends(require_roles('doctor', 'admin'))):
    """Chronological history of one named test for the trend chart in the
    Laboratorija tab. Only verified/rejected results are meaningless to
    exclude here -- a doctor tracking a trend wants to see everything,
    including still-unconfirmed drafts, clearly labeled as such by the
    frontend (same 'draft' status already surfaced in the regular list)."""
    patient_or_404(user, patient_id)
    rows = [r for r in store.list_lab_results(user.organization_id, patient_id) if r.name == name and r.value is not None]
    rows.sort(key=lambda r: r.collected_at or r.created_at)
    return [{'date': (r.collected_at or r.created_at).isoformat(), 'value': r.value, 'unit': r.unit, 'status': r.status, 'abnormality': r.abnormality} for r in rows]


@router.post('/api/patients/{patient_id}/lab-results')
def create_lab_result(patient_id: str, payload: LabResultCreate, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    result = store.add_lab_result(user.organization_id, patient_id, payload, status='verified')
    store.audit(user, 'create', 'lab_result', result.id, f'{result.name} · ručni unos')
    return result


@router.patch('/api/patients/{patient_id}/lab-results/{result_id}/status')
def update_lab_result_status(patient_id: str, result_id: str, payload: LabResultStatusUpdate, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    result = store.update_lab_result_status(user.organization_id, patient_id, result_id, payload.status)
    if not result:
        raise HTTPException(404, 'Laboratorijski rezultat nije pronađen')
    store.audit(user, payload.status, 'lab_result', result.id, result.name)
    return result


@router.post('/api/patients/{patient_id}/medication-safety-check')
def medication_safety_check(patient_id: str, payload: MedicationSafetyRequest, user=Depends(require_roles('doctor', 'admin'))):
    patient = patient_or_404(user, patient_id)
    profile = patient.clinical_profile
    result = check_medication_safety(profile.current_medications, payload.proposed_medications, profile.allergies,
                                      diagnoses=profile.diagnoses, medical_history=profile.medical_history)
    store.audit(user, 'check', 'medication_safety', patient_id, f'potencijalna upozorenja={len(result.findings)}')
    return result


@router.get('/api/medication-safety/rules')
def medication_safety_rules(user=Depends(require_roles('doctor', 'admin'))):
    """Full auditable rule catalog for the medication safety screen -- see
    medication_safety.py:rule_catalog for what 'versioned' honestly means
    here (a small, diffable ruleset, not a licensed external database)."""
    return rule_catalog()


@router.get('/api/patients/{patient_id}/summary')
async def summary(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    p = patient_or_404(user, patient_id)
    return {'summary': await ai.summarize(p.full_name, store.list_documents(user.organization_id, patient_id))}


@router.post('/api/patients/{patient_id}/chat')
async def chat(patient_id: str, payload: ChatRequest, user=Depends(require_roles('doctor', 'admin'))):
    p = patient_or_404(user, patient_id)
    return {'answer': await ai.answer(p.full_name, store.list_documents(user.organization_id, patient_id), payload.question)}


@router.get('/api/patients/{patient_id}/timeline')
async def timeline(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return await ai.timeline(store.list_documents(user.organization_id, patient_id))


@router.post('/api/patients/{patient_id}/reports')
async def report(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    p = patient_or_404(user, patient_id)
    content = await ai.report(p.full_name, store.list_documents(user.organization_id, patient_id))
    r = store.add_report(user.organization_id, patient_id, 'Clinical documentation review', content)
    store.audit(user, 'generate', 'report', r.id, p.full_name)
    return r


@router.get('/api/reports')
def reports(user=Depends(require_roles('doctor', 'admin'))):
    names = {p.id: p.full_name for p in store.list_patients(user.organization_id)}
    return [{**r.model_dump(mode='json'), 'patient_name': names.get(r.patient_id, 'Unknown patient')} for r in store.list_reports(user.organization_id)]


@router.get('/api/patients/{patient_id}/clinical-profile')
def clinical_profile(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    return patient_or_404(user, patient_id).clinical_profile


@router.put('/api/patients/{patient_id}/clinical-profile')
def update_clinical_profile(patient_id: str, payload: ClinicalProfileUpdate, user=Depends(require_roles('doctor', 'admin'))):
    patient = patient_or_404(user, patient_id)
    updated = store.update_clinical_profile(user.organization_id, patient_id, payload)
    store.audit(user, 'update', 'clinical_profile', patient_id, patient.full_name)
    return updated.clinical_profile


@router.post('/api/patients/{patient_id}/encounters')
def create_encounter(patient_id: str, payload: EncounterCreate, user=Depends(require_roles('doctor', 'admin'))):
    patient = patient_or_404(user, patient_id)
    record = store.add_encounter(user.organization_id, patient, user, payload)
    store.audit(user, 'create', 'encounter', record.id, patient.full_name)
    return record


@router.get('/api/patients/{patient_id}/encounters')
def encounters(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_encounters(user.organization_id, patient_id)


# -- pacijentski portal: upravljanje od strane osoblja -------------------------------------------------------------

@router.post('/api/patients/{patient_id}/portal-account')
def create_portal_account(patient_id: str, payload: PortalAccountCreate, user=Depends(require_roles('receptionist', 'admin'))):
    """Osoblje bira korisničko ime i početnu lozinku (isti obrazac kao
    kreiranje naloga osoblja preko /api/users) -- pacijent je dobija lično
    ili telefonom i menja je pri prvoj prijavi (must_change_password=True).
    Ovo namerno izbegava potrebu za e-mail/SMS infrastrukturom za slanje
    pozivnica, koju ova instalacija ne garantuje da ima podešenu."""
    patient_or_404(user, patient_id)
    if store.get_portal_account_for_patient(user.organization_id, patient_id):
        raise HTTPException(409, 'This patient already has a portal account')
    try:
        account = store.create_portal_account(user.organization_id, patient_id, payload)
    except ValueError as e:
        raise HTTPException(409, str(e))
    store.audit(user, 'create', 'portal_account', account.id, payload.username)
    return {'id': account.id, 'patient_id': account.patient_id, 'username': account.username}


@router.get('/api/patients/{patient_id}/portal-account')
def get_portal_account(patient_id: str, user=Depends(require_roles('receptionist', 'admin'))):
    patient_or_404(user, patient_id)
    account = store.get_portal_account_for_patient(user.organization_id, patient_id)
    if not account:
        raise HTTPException(404, 'No portal account for this patient')
    return {'id': account.id, 'patient_id': account.patient_id, 'username': account.username, 'active': account.active}


@router.get('/api/patients/{patient_id}/messages')
def list_patient_messages(patient_id: str, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    patient_or_404(user, patient_id)
    store.mark_portal_messages_read(user.organization_id, patient_id, 'patient')
    return store.list_portal_messages(user.organization_id, patient_id)


@router.post('/api/patients/{patient_id}/messages')
def send_patient_message(patient_id: str, payload: PortalMessageCreate, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    patient_or_404(user, patient_id)
    r = store.create_portal_message(user.organization_id, patient_id, 'staff', user.full_name, payload.body)
    store.audit(user, 'send_message', 'portal_message', r.id, patient_id)
    return r


@router.get('/api/patients/{patient_id}/questionnaire-responses')
def list_patient_questionnaire(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_questionnaire_responses(user.organization_id, patient_id)
