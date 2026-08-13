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


def _child(h, name='Pedijatrijski Pacijent'):
    return client.post('/api/patients', headers=h, json={'full_name': name, 'date_of_birth': '2023-01-15'}).json()


def test_pediatric_profile_upsert_accessible_to_reception_for_guardian_contact():
    h = login()
    p = _child(h)
    r = client.put(f"/api/patients/{p['id']}/pediatric-profile", headers=login('reception', 'reception123'), json={
        'guardian_name': 'Ana Petrović', 'guardian_relationship': 'majka', 'guardian_phone': '+381 64 111 2222',
    })
    assert r.status_code == 200, r.text
    fetched = client.get(f"/api/patients/{p['id']}/pediatric-profile", headers=h).json()
    assert fetched['guardian_name'] == 'Ana Petrović'


def test_pediatric_profile_upsert_updates_not_duplicates():
    h = login()
    p = _child(h, 'Upsert Test Dete')
    client.put(f"/api/patients/{p['id']}/pediatric-profile", headers=h, json={'guardian_name': 'Prvo Ime'})
    client.put(f"/api/patients/{p['id']}/pediatric-profile", headers=h, json={'guardian_name': 'Drugo Ime'})
    fetched = client.get(f"/api/patients/{p['id']}/pediatric-profile", headers=h).json()
    assert fetched['guardian_name'] == 'Drugo Ime'


def test_growth_measurement_requires_at_least_one_metric():
    h = login()
    p = _child(h, 'Prazno Merenje Dete')
    r = client.post(f"/api/patients/{p['id']}/growth-measurements", headers=h, json={'measured_at': datetime.now(timezone.utc).isoformat()})
    assert r.status_code == 422


def test_growth_measurements_recorded_and_listed_chronologically():
    h = login()
    p = _child(h, 'Rast Dete')
    for weeks_ago, weight in [(8, 3.5), (4, 5.2), (0, 6.8)]:
        client.post(f"/api/patients/{p['id']}/growth-measurements", headers=h, json={
            'measured_at': (datetime.now(timezone.utc) - timedelta(weeks=weeks_ago)).isoformat(),
            'weight_kg': weight, 'height_cm': 50 + weeks_ago,
        })
    rows = client.get(f"/api/patients/{p['id']}/growth-measurements", headers=h).json()
    assert [r['weight_kg'] for r in rows] == [3.5, 5.2, 6.8]


def test_growth_measurement_response_never_claims_a_percentile():
    # Deliberate scope guard: this module must never fabricate a WHO
    # growth-percentile claim it can't verify or keep current.
    h = login()
    p = _child(h, 'Percentil Guard Dete')
    r = client.post(f"/api/patients/{p['id']}/growth-measurements", headers=h, json={
        'measured_at': datetime.now(timezone.utc).isoformat(), 'weight_kg': 4.2,
    })
    assert r.status_code == 200
    assert 'percentil' not in str(r.json()).lower()


def test_vaccination_log_records_who_administered_it():
    h = login()
    p = _child(h, 'Vakcina Dete')
    r = client.post(f"/api/patients/{p['id']}/vaccinations", headers=h, json={
        'vaccine_name': 'DTaP', 'administered_at': '2026-03-01', 'lot_number': 'LOT123',
    })
    assert r.status_code == 200, r.text
    assert r.json()['recorded_by_name']  # doctor's name, from the session, not client-supplied
    rows = client.get(f"/api/patients/{p['id']}/vaccinations", headers=h).json()
    assert any(v['vaccine_name'] == 'DTaP' for v in rows)


def test_reception_cannot_record_growth_or_vaccinations():
    h = login()
    p = _child(h, 'RBAC Pedijatrija Dete')
    recep = login('reception', 'reception123')
    assert client.post(f"/api/patients/{p['id']}/growth-measurements", headers=recep, json={'measured_at': datetime.now(timezone.utc).isoformat(), 'weight_kg': 5}).status_code == 403
    assert client.post(f"/api/patients/{p['id']}/vaccinations", headers=recep, json={'vaccine_name': 'X', 'administered_at': '2026-01-01'}).status_code == 403
    assert client.get(f"/api/patients/{p['id']}/growth-measurements", headers=recep).status_code == 403


def test_pediatric_data_is_tenant_isolated():
    h = login()
    p = _child(h, 'Izolacija Pedijatrija Dete')
    client.post(f"/api/patients/{p['id']}/vaccinations", headers=h, json={'vaccine_name': 'MMR', 'administered_at': '2026-02-01'})
    org = store.create_organization('Pediatrics Isolation Clinic', f"peds-iso-{uuid4().hex[:6]}")
    store.create_user(org.id, UserCreate(username='doctor-peds', full_name='Peds Doctor', role='doctor', password='pedsdoc123'))
    other = login('doctor-peds', 'pedsdoc123', org.slug)
    assert client.get(f"/api/patients/{p['id']}/vaccinations", headers=other).status_code == 404
