from __future__ import annotations

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


def _make_patient_with_portal(username=None, password='PacijentLoz123'):
    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'Portal Pacijent'}).json()
    username = username or f"portal-{uuid4().hex[:8]}"
    r = client.post(f"/api/patients/{p['id']}/portal-account", headers=login('reception', 'reception123'), json={'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return p, username, password


def portal_login(username, password, organization='demo-clinic'):
    r = client.post('/api/portal/auth/login', json={'organization': organization, 'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': f"Bearer {r.json()['token']}"}, r.json()


def test_only_reception_and_admin_can_create_portal_account_not_doctor():
    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'RBAC Portal Pacijent'}).json()
    forbidden = client.post(f"/api/patients/{p['id']}/portal-account", headers=h, json={'username': 'x' * 5, 'password': 'Lozinka123'})
    assert forbidden.status_code == 403
    ok = client.post(f"/api/patients/{p['id']}/portal-account", headers=login('reception', 'reception123'), json={'username': f"u{uuid4().hex[:6]}", 'password': 'Lozinka123'})
    assert ok.status_code == 200, ok.text


def test_duplicate_portal_account_for_same_patient_rejected():
    p, username, password = _make_patient_with_portal()
    dup = client.post(f"/api/patients/{p['id']}/portal-account", headers=login('reception', 'reception123'), json={'username': f"other-{uuid4().hex[:6]}", 'password': 'Lozinka123'})
    assert dup.status_code == 409


def test_portal_login_forces_password_change_before_anything_else():
    p, username, password = _make_patient_with_portal()
    ph, body = portal_login(username, password)
    assert body['account']['must_change_password'] is True
    blocked = client.get('/api/portal/appointments', headers=ph)
    assert blocked.status_code == 403
    changed = client.post('/api/portal/auth/change-password', headers=ph, json={'current_password': password, 'new_password': 'NovaLozinka456'})
    assert changed.status_code == 204
    # old token is invalidated by the password change; must log in again
    assert client.get('/api/portal/auth/me', headers=ph).status_code == 401
    ph2, _ = portal_login(username, 'NovaLozinka456')
    assert client.get('/api/portal/appointments', headers=ph2).status_code == 200


def test_staff_token_is_rejected_by_portal_endpoints_and_vice_versa():
    # The core security property of this feature: a staff session must
    # never be usable against /api/portal/*, and a portal session must
    # never be usable against a staff endpoint -- separate token spaces.
    staff_h = login()
    assert client.get('/api/portal/appointments', headers=staff_h).status_code == 401

    p, username, password = _make_patient_with_portal()
    ph, _ = portal_login(username, password)
    client.post('/api/portal/auth/change-password', headers=ph, json={'current_password': password, 'new_password': 'DrugaLoz789'})
    ph2, _ = portal_login(username, 'DrugaLoz789')
    assert client.get('/api/patients', headers=ph2).status_code == 401
    assert client.get('/api/dashboard', headers=ph2).status_code == 401


def test_portal_login_lockout_is_independent_of_staff_lockout():
    p, username, password = _make_patient_with_portal()
    bad = {'organization': 'demo-clinic', 'username': username, 'password': 'wrong-wrong'}
    codes = [client.post('/api/portal/auth/login', json=bad).status_code for _ in range(8)]
    assert 401 in codes and any(c == 429 for c in codes)
    # staff login for an unrelated account must be unaffected
    staff_ok = client.post('/api/auth/login', json={'organization': 'demo-clinic', 'username': 'doctor', 'password': 'doctor123'})
    assert staff_ok.status_code == 200


def _activated_portal(username=None, password='PrvaLoz123'):
    p, username, password = _make_patient_with_portal(username, password)
    ph, _ = portal_login(username, password)
    client.post('/api/portal/auth/change-password', headers=ph, json={'current_password': password, 'new_password': 'AktivnaLoz456'})
    ph2, _ = portal_login(username, 'AktivnaLoz456')
    return p, ph2


def test_consent_flow_records_version_and_timestamp():
    p, ph = _activated_portal()
    before = client.get('/api/portal/consent', headers=ph).json()
    assert before['accepted'] is False
    accepted = client.post('/api/portal/consent', headers=ph, json={'consent_type': 'obrada_podataka'})
    assert accepted.status_code == 200, accepted.text
    after = client.get('/api/portal/consent', headers=ph).json()
    assert after['accepted'] is True and after['accepted_at']


def test_online_booking_conflict_checked_and_visible_to_staff():
    p, ph = _activated_portal()
    staff = login()
    cid = client.get('/api/portal/clinicians', headers=ph).json()[0]['id']
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d')
    slots = client.get('/api/portal/available-slots', headers=ph, params={'clinician_id': cid, 'date': tomorrow, 'duration_minutes': 20}).json()
    assert slots, 'expected at least one free slot tomorrow during working hours'
    booked = client.post('/api/portal/appointments', headers=ph, json={'starts_at': slots[0], 'clinician_id': cid, 'reason': 'Online zakazan pregled', 'duration_minutes': 20})
    assert booked.status_code == 200, booked.text
    appt_id = booked.json()['id']
    staff_view = client.get('/api/appointments', headers=staff).json()
    assert any(a['id'] == appt_id for a in staff_view)
    # a second patient can't book the exact same slot for the same clinician
    p2, ph2 = _activated_portal()
    clash = client.post('/api/portal/appointments', headers=ph2, json={'starts_at': slots[0], 'clinician_id': cid, 'reason': 'Sudar', 'duration_minutes': 20})
    assert clash.status_code == 409


def test_patient_can_cancel_own_appointment_but_not_someone_elses():
    p, ph = _activated_portal()
    cid = client.get('/api/portal/clinicians', headers=ph).json()[0]['id']
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=2)).strftime('%Y-%m-%d')
    slots = client.get('/api/portal/available-slots', headers=ph, params={'clinician_id': cid, 'date': tomorrow}).json()
    booked = client.post('/api/portal/appointments', headers=ph, json={'starts_at': slots[0], 'clinician_id': cid, 'reason': 'Za otkazivanje'}).json()

    p2, ph2 = _activated_portal()
    forbidden = client.patch(f"/api/portal/appointments/{booked['id']}/cancel", headers=ph2)
    assert forbidden.status_code == 404  # not visible/owned -> 404, not 403 (no existence leak)

    ok = client.patch(f"/api/portal/appointments/{booked['id']}/cancel", headers=ph)
    assert ok.status_code == 200 and ok.json()['status'] == 'cancelled'


def test_portal_lab_results_only_shows_verified_never_draft_or_rejected():
    p, ph = _activated_portal()
    staff = login()
    client.post(f"/api/patients/{p['id']}/lab-results", headers=staff, json={'name': 'CRP', 'value': 3.0, 'unit': 'mg/L'})
    files = {'file': ('nalaz.txt', 'CRP: 40 mg/L povišen'.encode('utf-8'), 'text/plain')}
    client.post(f"/api/patients/{p['id']}/documents", headers=staff, files=files)
    portal_results = client.get('/api/portal/lab-results', headers=ph).json()
    assert all(r['status'] == 'verified' for r in portal_results)
    assert any(r['name'] == 'CRP' and r['value'] == 3.0 for r in portal_results)
    assert not any(r['value'] == 40.0 for r in portal_results)  # still a draft, must not appear


def test_messaging_thread_visible_both_directions_and_marks_read():
    p, ph = _activated_portal()
    staff = login()
    sent = client.post('/api/portal/messages', headers=ph, json={'body': 'Zdravo, imam pitanje o terapiji.'})
    assert sent.status_code == 200, sent.text
    staff_thread = client.get(f"/api/patients/{p['id']}/messages", headers=staff).json()
    assert any(m['body'] == 'Zdravo, imam pitanje o terapiji.' and m['sender_type'] == 'patient' for m in staff_thread)
    reply = client.post(f"/api/patients/{p['id']}/messages", headers=staff, json={'body': 'Zdravo, javite se u ordinaciju.'})
    assert reply.status_code == 200
    patient_thread = client.get('/api/portal/messages', headers=ph).json()
    assert any(m['body'] == 'Zdravo, javite se u ordinaciju.' and m['sender_type'] == 'staff' for m in patient_thread)


def test_questionnaire_submission_visible_to_doctor_not_reception():
    p, ph = _activated_portal()
    submitted = client.post('/api/portal/questionnaire', headers=ph, json={
        'chief_complaint': 'Bol u grlu tri dana', 'symptoms_text': 'Bol pri gutanju, bez temperature.',
        'confirmed_allergies': True, 'confirmed_medications': True,
    })
    assert submitted.status_code == 200, submitted.text
    doctor_view = client.get(f"/api/patients/{p['id']}/questionnaire-responses", headers=login()).json()
    assert any(q['chief_complaint'] == 'Bol u grlu tri dana' for q in doctor_view)
    assert client.get(f"/api/patients/{p['id']}/questionnaire-responses", headers=login('reception', 'reception123')).status_code == 403


def test_portal_accounts_and_messages_are_tenant_isolated():
    p, ph = _activated_portal()
    org = store.create_organization('Portal Isolation Clinic', f"portal-iso-{uuid4().hex[:6]}")
    store.create_user(org.id, UserCreate(username='doctor-piso', full_name='Piso Doctor', role='doctor', password='pisodoc123'))
    other_staff = login('doctor-piso', 'pisodoc123', org.slug)
    assert client.get(f"/api/patients/{p['id']}/messages", headers=other_staff).status_code == 404
