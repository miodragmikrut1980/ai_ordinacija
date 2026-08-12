"""Kalendar ordinacije: termini sa lekarom/sobom/trajanjem, sprečavanje
duplog zakazivanja, otkazivanje/no-show, lista čekanja i podsetnici.

Split out of patients.py once appointments grew from "a list with a status"
into an actual scheduling system -- conflict checking, reschedule, waitlist
promotion and reminder lifecycle are a distinct domain from patient-record
management, and patients.py was already large.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..deps import current_user, patient_or_404, require_roles
from ..models import (
    AppointmentCreate, AppointmentReschedule, AppointmentStatusUpdate,
    ClinicianSummary, WaitlistCreate, WaitlistEntry, WaitlistPromote,
)
from ..notifications import dispatch as dispatch_reminder
from ..state import store

router = APIRouter()

REMINDER_LEAD = timedelta(hours=24)


def _conflict_detail(existing) -> dict:
    return {
        'error': 'appointment_conflict',
        'message': 'Termin se poklapa sa postojećim zakazanim terminom za istog lekara ili istu sobu.',
        'conflicting_appointment_id': existing.id,
        'conflicting_starts_at': existing.starts_at.isoformat(),
    }


def _schedule_default_reminder(org: str, appointment) -> None:
    """Auto-schedules one reminder per new/rescheduled appointment, timed
    24h before the visit (or ~1 minute from now if the appointment is
    already less than 24h out, so same-day bookings still get a heads-up
    instead of silently getting none). Skipped entirely if the appointment
    is too imminent for a reminder to be useful."""
    send_at = appointment.starts_at - REMINDER_LEAD
    now = datetime.now(timezone.utc)
    if send_at <= now:
        send_at = now + timedelta(minutes=1)
    if send_at >= appointment.starts_at:
        return
    channel = os.getenv('CLINIC_DEFAULT_REMINDER_CHANNEL', 'email')
    store.create_reminder(org, appointment.id, channel, send_at)


@router.get('/api/clinicians', response_model=list[ClinicianSummary])
def clinicians(user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    return store.list_clinicians(user.organization_id)


@router.post('/api/appointments')
def create_appointment(payload: AppointmentCreate, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    patient_or_404(user, payload.patient_id)
    conflict = store.find_appointment_conflict(user.organization_id, payload.clinician_id, payload.room, payload.starts_at, payload.duration_minutes)
    if conflict:
        raise HTTPException(409, _conflict_detail(conflict))
    clinician_name = None
    if payload.clinician_id:
        clinician_name = next((c['full_name'] for c in store.list_clinicians(user.organization_id) if c['id'] == payload.clinician_id), None)
    r = store.create_appointment(user.organization_id, payload, clinician_name=clinician_name)
    store.audit(user, 'create', 'appointment', r.id, payload.reason)
    _schedule_default_reminder(user.organization_id, r)
    return r


@router.get('/api/appointments')
def appointments(from_: str | None = None, to: str | None = None, clinician_id: str | None = None, user=Depends(current_user)):
    """Optional ?from=&to= (ISO datetimes) narrows to a calendar window --
    the week/month view sends its visible range rather than fetching the
    whole clinic's history every time it renders."""
    names = {p.id: p.full_name for p in store.list_patients(user.organization_id)}
    rows = store.list_appointments(user.organization_id)
    if from_:
        start = datetime.fromisoformat(from_)
        rows = [a for a in rows if a.starts_at >= start]
    if to:
        end = datetime.fromisoformat(to)
        rows = [a for a in rows if a.starts_at <= end]
    if clinician_id:
        rows = [a for a in rows if a.clinician_id == clinician_id]
    return [{**a.model_dump(mode='json'), 'patient_name': names.get(a.patient_id, 'Unknown patient')} for a in rows]


@router.patch('/api/appointments/{appointment_id}')
def reschedule_appointment(appointment_id: str, payload: AppointmentReschedule, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    existing = store.get_appointment(appointment_id)
    if not existing or existing.organization_id != user.organization_id:
        raise HTTPException(404, 'Appointment not found')
    if existing.status not in ('scheduled', 'checked_in'):
        raise HTTPException(409, {'error': 'not_reschedulable', 'message': 'Samo zakazan ili prijavljen termin može da se pomera.'})
    new_starts_at = payload.starts_at or existing.starts_at
    new_duration = payload.duration_minutes or existing.duration_minutes
    new_clinician = payload.clinician_id if payload.clinician_id is not None else existing.clinician_id
    new_room = payload.room if payload.room is not None else existing.room
    conflict = store.find_appointment_conflict(user.organization_id, new_clinician, new_room, new_starts_at, new_duration, exclude_id=appointment_id)
    if conflict:
        raise HTTPException(409, _conflict_detail(conflict))
    patch = payload.model_dump(exclude_unset=True)
    if 'clinician_id' in patch and patch['clinician_id']:
        patch['clinician_name'] = next((c['full_name'] for c in store.list_clinicians(user.organization_id) if c['id'] == patch['clinician_id']), None)
    r = store.reschedule_appointment(user.organization_id, appointment_id, patch)
    store.audit(user, 'reschedule', 'appointment', appointment_id, f'-> {new_starts_at.isoformat()}')
    if payload.starts_at:
        store.cancel_pending_reminders(user.organization_id, appointment_id)
        _schedule_default_reminder(user.organization_id, r)
    return r


@router.patch('/api/appointments/{appointment_id}/status')
def appointment_status(appointment_id: str, payload: AppointmentStatusUpdate, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    r = store.update_appointment_status(user.organization_id, appointment_id, payload.status, payload.cancellation_reason)
    if not r:
        raise HTTPException(404, 'Appointment not found')
    store.audit(user, 'update_status', 'appointment', appointment_id, payload.status)
    return r


# -- lista čekanja -------------------------------------------------------------

@router.post('/api/waitlist', response_model=WaitlistEntry)
def add_to_waitlist(payload: WaitlistCreate, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    patient_or_404(user, payload.patient_id)
    r = store.create_waitlist_entry(user.organization_id, payload)
    store.audit(user, 'create', 'waitlist_entry', r.id, payload.desired_service or 'termin')
    return r


@router.get('/api/waitlist', response_model=list[WaitlistEntry])
def list_waitlist(status: str = 'waiting', user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    return store.list_waitlist(user.organization_id, status=status or None)


@router.post('/api/waitlist/{entry_id}/promote')
def promote_waitlist_entry(entry_id: str, payload: WaitlistPromote, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    entry = store.get_waitlist_entry(entry_id)
    if not entry or entry.organization_id != user.organization_id:
        raise HTTPException(404, 'Waitlist entry not found')
    if entry.status != 'waiting':
        raise HTTPException(409, {'error': 'already_resolved', 'message': 'Ova stavka liste čekanja je već zakazana ili otkazana.'})
    conflict = store.find_appointment_conflict(user.organization_id, payload.clinician_id, payload.room, payload.starts_at, payload.duration_minutes)
    if conflict:
        raise HTTPException(409, _conflict_detail(conflict))
    clinician_name = None
    if payload.clinician_id:
        clinician_name = next((c['full_name'] for c in store.list_clinicians(user.organization_id) if c['id'] == payload.clinician_id), None)
    create_payload = AppointmentCreate(
        patient_id=entry.patient_id, starts_at=payload.starts_at, reason=payload.service_type or entry.desired_service or 'Termin sa liste čekanja',
        clinician_id=payload.clinician_id, room=payload.room, service_type=payload.service_type, duration_minutes=payload.duration_minutes,
    )
    appt = store.create_appointment(user.organization_id, create_payload, clinician_name=clinician_name)
    store.update_waitlist_status(user.organization_id, entry_id, 'scheduled', appointment_id=appt.id)
    store.audit(user, 'promote', 'waitlist_entry', entry_id, f'-> appointment {appt.id}')
    _schedule_default_reminder(user.organization_id, appt)
    return appt


@router.patch('/api/waitlist/{entry_id}/status', response_model=WaitlistEntry)
def update_waitlist_entry_status(entry_id: str, status: str, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    if status not in ('waiting', 'cancelled'):
        raise HTTPException(422, 'Invalid status')
    r = store.update_waitlist_status(user.organization_id, entry_id, status)
    if not r:
        raise HTTPException(404, 'Waitlist entry not found')
    store.audit(user, 'update_status', 'waitlist_entry', entry_id, status)
    return r


# -- podsetnici -------------------------------------------------------------

@router.get('/api/reminders')
def list_reminders(appointment_id: str | None = None, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    return store.list_reminders(user.organization_id, appointment_id=appointment_id)
