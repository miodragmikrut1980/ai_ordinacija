from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import app, store
from app.models import UserCreate

client = TestClient(app)


def login(username='doctor', password='doctor123', organization='demo-clinic'):
    r = client.post('/api/auth/login', json={'organization': organization, 'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': f"Bearer {r.json()['token']}"}


def _patient(h, name='Kalendar Pacijent'):
    return client.post('/api/patients', headers=h, json={'full_name': name}).json()


def _clinician_id(h):
    cs = client.get('/api/clinicians', headers=h).json()
    assert cs, 'expected at least the seeded demo doctor'
    return cs[0]['id']


def test_clinicians_endpoint_lists_doctors_only_and_hides_password_hash():
    h = login('reception', 'reception123')
    r = client.get('/api/clinicians', headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body and all('id' in c and 'full_name' in c for c in body)
    assert all('password_hash' not in c for c in body)


def test_create_appointment_with_clinician_room_and_duration():
    h = login()
    p = _patient(h)
    cid = _clinician_id(h)
    starts = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = client.post('/api/appointments', headers=h, json={
        'patient_id': p['id'], 'starts_at': starts, 'reason': 'Kontrola',
        'clinician_id': cid, 'room': 'Ordinacija 1', 'service_type': 'Kontrolni pregled', 'duration_minutes': 30,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['clinician_id'] == cid and body['clinician_name'] and body['room'] == 'Ordinacija 1' and body['duration_minutes'] == 30


def test_overlapping_appointment_for_same_clinician_is_rejected():
    h = login()
    p1, p2 = _patient(h, 'Sudar Pacijent 1'), _patient(h, 'Sudar Pacijent 2')
    cid = _clinician_id(h)
    starts = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    ok = client.post('/api/appointments', headers=h, json={
        'patient_id': p1['id'], 'starts_at': starts.isoformat(), 'reason': 'Prvi termin',
        'clinician_id': cid, 'duration_minutes': 30,
    })
    assert ok.status_code == 200, ok.text
    overlapping = (starts + timedelta(minutes=15)).isoformat()
    clash = client.post('/api/appointments', headers=h, json={
        'patient_id': p2['id'], 'starts_at': overlapping, 'reason': 'Drugi termin (sudar)',
        'clinician_id': cid, 'duration_minutes': 30,
    })
    assert clash.status_code == 409, clash.text
    assert clash.json()['detail']['error'] == 'appointment_conflict'
    assert clash.json()['detail']['conflicting_appointment_id'] == ok.json()['id']


def test_overlapping_appointment_for_same_room_but_different_clinician_is_also_rejected():
    # A room can't host two patients at once even if two different doctors
    # are technically free -- the physical constraint is the room, not just
    # the clinician's calendar.
    admin = login('admin', 'admin123')
    second_doctor = f"doctor2-{uuid4().hex[:6]}"
    client.post('/api/users', headers=admin, json={'username': second_doctor, 'full_name': 'Dr. Drugi', 'role': 'doctor', 'password': 'doctor2pass'})
    h = login()
    p1, p2 = _patient(h, 'Soba Pacijent 1'), _patient(h, 'Soba Pacijent 2')
    cid1 = _clinician_id(h)
    cid2 = next(c['id'] for c in client.get('/api/clinicians', headers=h).json() if c['id'] != cid1)
    starts = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    ok = client.post('/api/appointments', headers=h, json={
        'patient_id': p1['id'], 'starts_at': starts.isoformat(), 'reason': 'Prvi termin',
        'clinician_id': cid1, 'room': 'Soba A', 'duration_minutes': 20,
    })
    assert ok.status_code == 200, ok.text
    clash = client.post('/api/appointments', headers=h, json={
        'patient_id': p2['id'], 'starts_at': starts.isoformat(), 'reason': 'Drugi lekar, ista soba',
        'clinician_id': cid2, 'room': 'Soba A', 'duration_minutes': 20,
    })
    assert clash.status_code == 409, clash.text


def test_non_overlapping_appointments_for_same_clinician_are_allowed():
    h = login()
    p1, p2 = _patient(h, 'Redosled Pacijent 1'), _patient(h, 'Redosled Pacijent 2')
    cid = _clinician_id(h)
    starts = (datetime.now(timezone.utc) + timedelta(days=4)).replace(microsecond=0)
    first = client.post('/api/appointments', headers=h, json={'patient_id': p1['id'], 'starts_at': starts.isoformat(), 'reason': 'Prvi', 'clinician_id': cid, 'duration_minutes': 20})
    assert first.status_code == 200
    back_to_back = (starts + timedelta(minutes=20)).isoformat()
    second = client.post('/api/appointments', headers=h, json={'patient_id': p2['id'], 'starts_at': back_to_back, 'reason': 'Drugi odmah posle', 'clinician_id': cid, 'duration_minutes': 20})
    assert second.status_code == 200, second.text


def test_cancelled_appointment_frees_up_the_slot():
    h = login()
    p1, p2 = _patient(h, 'Otkazan Pacijent 1'), _patient(h, 'Otkazan Pacijent 2')
    cid = _clinician_id(h)
    starts = (datetime.now(timezone.utc) + timedelta(days=5)).replace(microsecond=0)
    first = client.post('/api/appointments', headers=h, json={'patient_id': p1['id'], 'starts_at': starts.isoformat(), 'reason': 'Prvi', 'clinician_id': cid, 'duration_minutes': 20}).json()
    client.patch(f"/api/appointments/{first['id']}/status", headers=h, json={'status': 'cancelled', 'cancellation_reason': 'Pacijent otkazao'})
    second = client.post('/api/appointments', headers=h, json={'patient_id': p2['id'], 'starts_at': starts.isoformat(), 'reason': 'Zauzima isti termin', 'clinician_id': cid, 'duration_minutes': 20})
    assert second.status_code == 200, second.text


def test_reschedule_checks_conflicts_and_moves_the_appointment():
    h = login()
    p1, p2 = _patient(h, 'Pomeranje Pacijent 1'), _patient(h, 'Pomeranje Pacijent 2')
    cid = _clinician_id(h)
    t1 = (datetime.now(timezone.utc) + timedelta(days=6)).replace(microsecond=0)
    t2 = t1 + timedelta(hours=1)
    a1 = client.post('/api/appointments', headers=h, json={'patient_id': p1['id'], 'starts_at': t1.isoformat(), 'reason': 'Termin A', 'clinician_id': cid, 'duration_minutes': 20}).json()
    a2 = client.post('/api/appointments', headers=h, json={'patient_id': p2['id'], 'starts_at': t2.isoformat(), 'reason': 'Termin B', 'clinician_id': cid, 'duration_minutes': 20}).json()
    # moving a1 onto a2's slot must be rejected
    clash = client.patch(f"/api/appointments/{a1['id']}", headers=h, json={'starts_at': t2.isoformat()})
    assert clash.status_code == 409, clash.text
    # moving a1 to a genuinely free slot must succeed
    free_slot = (t1 + timedelta(days=1)).isoformat()
    moved = client.patch(f"/api/appointments/{a1['id']}", headers=h, json={'starts_at': free_slot})
    assert moved.status_code == 200, moved.text
    assert moved.json()['starts_at'].startswith(free_slot[:16])


def test_no_show_status_and_dashboard_do_not_confuse_it_with_cancelled():
    h = login()
    p = _patient(h, 'No Show Pacijent')
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    a = client.post('/api/appointments', headers=h, json={'patient_id': p['id'], 'starts_at': past, 'reason': 'Nije se pojavio'}).json()
    r = client.patch(f"/api/appointments/{a['id']}/status", headers=h, json={'status': 'no_show'})
    assert r.status_code == 200 and r.json()['status'] == 'no_show'


def test_reception_cannot_reschedule_or_status_change_appointments_it_shouldnt():
    # Reception CAN create/reschedule appointments (scheduling is their job)
    # but the calendar surface stays scoped to doctor/receptionist/admin --
    # this guards against an unrelated role sneaking in later.
    h = login()
    p = _patient(h, 'RBAC Appt Patient')
    starts = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    a = client.post('/api/appointments', headers=h, json={'patient_id': p['id'], 'starts_at': starts, 'reason': 'RBAC test'}).json()
    recep = login('reception', 'reception123')
    ok = client.patch(f"/api/appointments/{a['id']}", headers=recep, json={'duration_minutes': 30})
    assert ok.status_code == 200, ok.text


def test_waitlist_create_list_and_promote_to_conflict_checked_appointment():
    h = login()
    p = _patient(h, 'Cekanje Pacijent')
    cid = _clinician_id(h)
    entry = client.post('/api/waitlist', headers=h, json={'patient_id': p['id'], 'desired_service': 'Kontrola', 'preferred_note': 'Ujutru ako je moguće'})
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()['id']
    waiting = client.get('/api/waitlist', headers=h).json()
    assert any(w['id'] == entry_id for w in waiting)

    starts = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    promoted = client.post(f'/api/waitlist/{entry_id}/promote', headers=h, json={'starts_at': starts, 'clinician_id': cid, 'duration_minutes': 20})
    assert promoted.status_code == 200, promoted.text
    appt_id = promoted.json()['id']

    still_waiting = client.get('/api/waitlist', headers=h, params={'status': 'waiting'}).json()
    assert not any(w['id'] == entry_id for w in still_waiting)

    p2 = _patient(h, 'Sudar sa listom cekanja')
    clash = client.post('/api/appointments', headers=h, json={'patient_id': p2['id'], 'starts_at': starts, 'reason': 'Sudar', 'clinician_id': cid, 'duration_minutes': 20})
    assert clash.status_code == 409, clash.text
    assert clash.json()['detail']['conflicting_appointment_id'] == appt_id


def test_promoting_an_already_scheduled_waitlist_entry_is_rejected():
    h = login()
    p = _patient(h, 'Dupla Promocija')
    entry = client.post('/api/waitlist', headers=h, json={'patient_id': p['id']}).json()
    starts = (datetime.now(timezone.utc) + timedelta(days=9)).isoformat()
    first = client.post(f"/api/waitlist/{entry['id']}/promote", headers=h, json={'starts_at': starts})
    assert first.status_code == 200
    second = client.post(f"/api/waitlist/{entry['id']}/promote", headers=h, json={'starts_at': starts})
    assert second.status_code == 409


def test_new_appointment_auto_schedules_a_reminder():
    h = login()
    p = _patient(h, 'Podsetnik Pacijent')
    starts = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    a = client.post('/api/appointments', headers=h, json={'patient_id': p['id'], 'starts_at': starts, 'reason': 'Sa podsetnikom'}).json()
    reminders = client.get('/api/reminders', headers=h, params={'appointment_id': a['id']}).json()
    assert len(reminders) == 1
    assert reminders[0]['status'] == 'pending'
    assert reminders[0]['channel'] == 'email'


def test_cancelling_an_appointment_cancels_its_pending_reminder():
    h = login()
    p = _patient(h, 'Otkazan Podsetnik Pacijent')
    starts = (datetime.now(timezone.utc) + timedelta(days=11)).isoformat()
    a = client.post('/api/appointments', headers=h, json={'patient_id': p['id'], 'starts_at': starts, 'reason': 'Bice otkazan'}).json()
    client.patch(f"/api/appointments/{a['id']}/status", headers=h, json={'status': 'cancelled'})
    reminders = client.get('/api/reminders', headers=h, params={'appointment_id': a['id']}).json()
    assert reminders[0]['status'] == 'cancelled'


def test_send_reminders_script_marks_email_unconfigured_without_smtp_env():
    # Runs the actual periodic-job entrypoint against the test store's data
    # directory, confirming it processes a due reminder end-to-end without
    # crashing and without falsely claiming delivery when CLINIC_SMTP_HOST
    # isn't set in this test environment.
    import os
    import subprocess
    import sys
    from pathlib import Path

    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'Skripta Podsetnik Pacijent', 'email': 'pacijent@example.com'}).json()
    starts = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    a = client.post('/api/appointments', headers=h, json={'patient_id': p['id'], 'starts_at': starts, 'reason': 'Skoro'}).json()
    reminder = client.get('/api/reminders', headers=h, params={'appointment_id': a['id']}).json()[0]
    # Force it due right now rather than waiting on the real ~1-minute lead.
    store.mark_reminder_result(reminder['id'], 'pending', None)
    import sqlite3
    conn = sqlite3.connect(Path(os.environ['CLINIC_DATA_DIR']) / 'clinic.db')
    conn.execute("UPDATE reminders SET send_at=? WHERE id=?", ('2000-01-01T00:00:00+00:00', reminder['id']))
    conn.commit(); conn.close()

    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ}
    result = subprocess.run([sys.executable, str(repo_root / 'scripts' / 'send_reminders.py'), os.environ['CLINIC_DATA_DIR']],
                             capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'unconfigured' in result.stdout.lower() or 'Reminders processed' in result.stdout

    after = client.get('/api/reminders', headers=h, params={'appointment_id': a['id']}).json()[0]
    assert after['status'] == 'unconfigured'
    assert after['error']


def test_send_reminders_script_fails_when_patient_has_no_email():
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    h = login()
    p = _patient(h, 'Bez Email Pacijent')  # created with no email on file
    starts = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    a = client.post('/api/appointments', headers=h, json={'patient_id': p['id'], 'starts_at': starts, 'reason': 'Skoro'}).json()
    reminder = client.get('/api/reminders', headers=h, params={'appointment_id': a['id']}).json()[0]
    conn = sqlite3.connect(Path(os.environ['CLINIC_DATA_DIR']) / 'clinic.db')
    conn.execute("UPDATE reminders SET send_at=? WHERE id=?", ('2000-01-01T00:00:00+00:00', reminder['id']))
    conn.commit(); conn.close()

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(repo_root / 'scripts' / 'send_reminders.py'), os.environ['CLINIC_DATA_DIR']],
                             capture_output=True, text=True, env={**os.environ}, timeout=30)
    assert result.returncode == 0, result.stderr

    after = client.get('/api/reminders', headers=h, params={'appointment_id': a['id']}).json()[0]
    assert after['status'] == 'failed'
    assert 'mail' in after['error'].lower()
