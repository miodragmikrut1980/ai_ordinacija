"""Pacijentski portal — potpuno odvojen tok autentifikacije od osoblja.

Sve na ovoj ruti koristi isključivo `current_portal_account` (deps.py),
nikad `current_user`/`require_roles` koji su za osoblje. Ova granica je
namerna i strogo se poštuje: pacijentov nalog vidi samo svoje podatke, i
to samo podskup koji je bezbedno pokazati pacijentu direktno (potvrđeni
laboratorijski nalazi, ne AI diferencijalna analiza; sopstveni termini,
ne cela evidencija ordinacije).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from .deps import current_portal_account
from .models import (
    AppointmentCreate, ConsentAccept, PortalAccountCreate, PortalAppointmentRequest,
    PortalChangePassword, PortalLoginRequest, PortalMessageCreate, QuestionnaireSubmit,
)
from .state import PORTAL_SESSION_MINUTES, PORTAL_WORK_END_HOUR, PORTAL_WORK_START_HOUR, store

router = APIRouter()

CONSENT_TEXT_VERSION = "2026-08-v1"
CONSENT_TEXT = (
    "Saglasan/na sam da ordinacija obrađuje moje lične i zdravstvene podatke "
    "u svrhu zakazivanja, vođenja medicinske dokumentacije i komunikacije u "
    "vezi sa lečenjem, u skladu sa Zakonom o zaštiti podataka o ličnosti "
    "Republike Srbije. Podaci se čuvaju lokalno, u ordinaciji, i ne dele se "
    "sa trećim licima bez zakonskog osnova."
)


def _public_account(a) -> dict:
    return {"id": a.id, "patient_id": a.patient_id, "username": a.username, "must_change_password": a.must_change_password}


# -- autentifikacija -------------------------------------------------------------

@router.post('/api/portal/auth/login')
def portal_login(payload: PortalLoginRequest):
    locked, retry_after = store.is_locked_out(payload.organization, payload.username, realm='portal')
    if locked:
        raise HTTPException(429, f'Too many failed login attempts. Try again in {retry_after} seconds.', headers={'Retry-After': str(retry_after)})
    account = store.authenticate_portal(payload.organization, payload.username, payload.password)
    store.record_login_attempt(payload.organization, payload.username, success=bool(account), realm='portal')
    if not account:
        raise HTTPException(401, 'Invalid clinic, username or password')
    token, _ = store.create_portal_session(account.id, PORTAL_SESSION_MINUTES)
    store.audit_portal(account, 'login', 'portal_session')
    return {'token': token, 'expires_in_minutes': PORTAL_SESSION_MINUTES, 'account': _public_account(account)}


@router.get('/api/portal/auth/me')
def portal_me(account=Depends(current_portal_account)):
    return _public_account(account)


@router.post('/api/portal/auth/logout', status_code=204)
def portal_logout(authorization: str | None = Header(default=None), account=Depends(current_portal_account)):
    store.delete_portal_session(authorization.split(' ', 1)[1])
    store.audit_portal(account, 'logout', 'portal_session')


@router.post('/api/portal/auth/change-password', status_code=204)
def portal_change_password(payload: PortalChangePassword, account=Depends(current_portal_account)):
    full = store.get_portal_account(account.id)
    row_ok = store.authenticate_portal(
        store.organization_by_id(account.organization_id).slug, account.username, payload.current_password,
    )
    if not row_ok:
        raise HTTPException(401, 'Current password is incorrect')
    store.portal_change_password(account.id, payload.new_password)
    store.audit_portal(account, 'change_password', 'portal_account', account.id)


# -- pristanak za obradu podataka -------------------------------------------------------------

@router.get('/api/portal/consent')
def get_consent(account=Depends(current_portal_account)):
    latest = store.latest_consent(account.organization_id, account.patient_id, 'obrada_podataka')
    return {'text': CONSENT_TEXT, 'text_version': CONSENT_TEXT_VERSION, 'accepted': latest is not None and latest.consent_text_version == CONSENT_TEXT_VERSION, 'accepted_at': latest.accepted_at if latest else None}


@router.post('/api/portal/consent')
def accept_consent(payload: ConsentAccept, account=Depends(current_portal_account)):
    r = store.accept_consent(account.organization_id, account.patient_id, payload.consent_type, CONSENT_TEXT_VERSION)
    store.audit_portal(account, 'accept_consent', 'consent', r.id, payload.consent_type)
    return r


# -- online zakazivanje -------------------------------------------------------------

@router.get('/api/portal/clinicians')
def portal_clinicians(account=Depends(current_portal_account)):
    return store.list_clinicians(account.organization_id)


@router.get('/api/portal/available-slots')
def portal_available_slots(clinician_id: str, date: str, duration_minutes: int = 20, account=Depends(current_portal_account)):
    """`date` is an ISO date (YYYY-MM-DD). A clinician must be chosen --
    booking with 'any available doctor' would need real assignment logic
    this pass doesn't attempt (see store.py:available_slots note)."""
    slots = store.available_slots(account.organization_id, clinician_id, date, duration_minutes, PORTAL_WORK_START_HOUR, PORTAL_WORK_END_HOUR)
    return [s.isoformat() for s in slots]


@router.post('/api/portal/appointments')
def portal_book_appointment(payload: PortalAppointmentRequest, account=Depends(current_portal_account)):
    conflict = store.find_appointment_conflict(account.organization_id, payload.clinician_id, None, payload.starts_at, payload.duration_minutes)
    if conflict:
        raise HTTPException(409, {'error': 'appointment_conflict', 'message': 'Izabrani termin više nije slobodan. Osvežite dostupne termine.'})
    clinician_name = None
    if payload.clinician_id:
        clinician_name = next((c['full_name'] for c in store.list_clinicians(account.organization_id) if c['id'] == payload.clinician_id), None)
    create_payload = AppointmentCreate(
        patient_id=account.patient_id, starts_at=payload.starts_at, reason=payload.reason,
        clinician_id=payload.clinician_id, service_type=payload.service_type, duration_minutes=payload.duration_minutes,
    )
    appt = store.create_appointment(account.organization_id, create_payload, clinician_name=clinician_name)
    store.audit_portal(account, 'book', 'appointment', appt.id, payload.reason)
    return appt


@router.get('/api/portal/appointments')
def portal_list_appointments(account=Depends(current_portal_account)):
    rows = store.list_appointments(account.organization_id)
    return [a for a in rows if a.patient_id == account.patient_id]


@router.patch('/api/portal/appointments/{appointment_id}/cancel')
def portal_cancel_appointment(appointment_id: str, account=Depends(current_portal_account)):
    existing = store.get_appointment(appointment_id)
    if not existing or existing.organization_id != account.organization_id or existing.patient_id != account.patient_id:
        raise HTTPException(404, 'Appointment not found')
    if existing.status not in ('scheduled', 'checked_in'):
        raise HTTPException(409, {'error': 'not_cancellable', 'message': 'Ovaj termin se više ne može otkazati onlajn.'})
    r = store.update_appointment_status(account.organization_id, appointment_id, 'cancelled', 'Otkazao pacijent putem portala')
    store.audit_portal(account, 'cancel', 'appointment', appointment_id)
    return r


# -- laboratorijski nalazi (samo potvrđeni) -------------------------------------------------------------

@router.get('/api/portal/lab-results')
def portal_lab_results(account=Depends(current_portal_account)):
    """Only 'verified' results -- a draft is an unconfirmed AI/OCR guess and
    a rejected one was determined wrong; showing either to a patient
    directly (without a clinician's framing) risks real, needless alarm."""
    rows = store.list_lab_results(account.organization_id, account.patient_id)
    return [r for r in rows if r.status == 'verified']


# -- bezbedne poruke -------------------------------------------------------------

@router.get('/api/portal/messages')
def portal_list_messages(account=Depends(current_portal_account)):
    store.mark_portal_messages_read(account.organization_id, account.patient_id, 'staff')
    return store.list_portal_messages(account.organization_id, account.patient_id)


@router.post('/api/portal/messages')
def portal_send_message(payload: PortalMessageCreate, account=Depends(current_portal_account)):
    patient = store.get_patient(account.organization_id, account.patient_id)
    r = store.create_portal_message(account.organization_id, account.patient_id, 'patient', patient.full_name if patient else account.username, payload.body)
    store.audit_portal(account, 'send_message', 'portal_message', r.id)
    return r


# -- digitalni upitnik pre pregleda -------------------------------------------------------------

@router.post('/api/portal/questionnaire')
def portal_submit_questionnaire(payload: QuestionnaireSubmit, account=Depends(current_portal_account)):
    r = store.create_questionnaire_response(account.organization_id, account.patient_id, payload)
    store.audit_portal(account, 'submit_questionnaire', 'questionnaire_response', r.id)
    return r


@router.get('/api/portal/questionnaire')
def portal_list_questionnaire(account=Depends(current_portal_account)):
    return store.list_questionnaire_responses(account.organization_id, account.patient_id)
