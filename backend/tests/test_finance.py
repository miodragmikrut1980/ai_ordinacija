from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import app, store
from app.models import UserCreate

client = TestClient(app)


def login(username='doctor', password='doctor123', organization='demo-clinic'):
    r = client.post('/api/auth/login', json={'organization': organization, 'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': f"Bearer {r.json()['token']}"}


def _patient(h, name='Finansije Pacijent'):
    return client.post('/api/patients', headers=h, json={'full_name': name}).json()


def _service(h, name='Kontrolni pregled', price=2500):
    r = client.post('/api/finance/services', headers=h, json={'name': name, 'price_rsd': price})
    assert r.status_code == 200, r.text
    return r.json()


def test_only_admin_can_create_or_update_services():
    doctor, recep = login(), login('reception', 'reception123')
    admin = login('admin', 'admin123')
    for h in (doctor, recep):
        assert client.post('/api/finance/services', headers=h, json={'name': 'X', 'price_rsd': 100}).status_code == 403
    svc = _service(admin)
    assert svc['price_rsd'] == 2500 and svc['active'] is True
    for h in (doctor, recep):
        assert client.patch(f"/api/finance/services/{svc['id']}", headers=h, json={'price_rsd': 3000}).status_code == 403
    updated = client.patch(f"/api/finance/services/{svc['id']}", headers=admin, json={'price_rsd': 3000})
    assert updated.status_code == 200 and updated.json()['price_rsd'] == 3000


def test_deactivated_service_is_hidden_by_default_but_visible_with_include_inactive():
    admin = login('admin', 'admin123')
    svc = _service(admin, name='Usluga koja nestaje', price=1000)
    client.patch(f"/api/finance/services/{svc['id']}", headers=admin, json={'active': False})
    active_only = client.get('/api/finance/services', headers=admin).json()
    assert not any(s['id'] == svc['id'] for s in active_only)
    with_inactive = client.get('/api/finance/services', headers=admin, params={'include_inactive': True}).json()
    assert any(s['id'] == svc['id'] for s in with_inactive)


def test_invoice_totals_computed_from_line_item_and_invoice_discounts():
    admin = login('admin', 'admin123')
    h = login()
    p = _patient(h)
    svc = _service(admin, name='Laboratorijska analiza', price=4000)
    r = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'],
        'line_items': [
            {'service_id': svc['id'], 'description': svc['name'], 'quantity': 2, 'unit_price_rsd': 4000, 'discount_percent': 10},
            {'description': 'Materijal', 'quantity': 1, 'unit_price_rsd': 500, 'discount_percent': 0},
        ],
        'discount_percent': 5,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # line 1: 2*4000=8000, -10% => 7200; line 2: 500 => subtotal 7700; -5% invoice discount => 7315
    assert body['subtotal_rsd'] == 7700
    assert body['total_rsd'] == 7315
    assert body['balance_due_rsd'] == 7315
    assert body['status'] == 'issued'
    assert body['invoice_number'].startswith(str(datetime.now(timezone.utc).year))


def test_invoice_numbers_are_sequential_and_gapless_per_year():
    h = login()
    p = _patient(h, 'Numeracija Pacijent')
    numbers = []
    for _ in range(3):
        r = client.post('/api/finance/invoices', headers=h, json={
            'patient_id': p['id'], 'line_items': [{'description': 'Usluga', 'unit_price_rsd': 100}],
        })
        numbers.append(r.json()['invoice_number'])
    seqs = [int(n.split('-')[1]) for n in numbers]
    assert seqs == [seqs[0], seqs[0] + 1, seqs[0] + 2]


def test_payments_update_balance_and_status_transitions_to_paid():
    h = login()
    recep = login('reception', 'reception123')
    p = _patient(h, 'Placanje Pacijent')
    invoice = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Pregled', 'unit_price_rsd': 3000}],
    }).json()
    assert invoice['status'] == 'issued' and invoice['balance_due_rsd'] == 3000

    partial = client.post(f"/api/finance/invoices/{invoice['id']}/payments", headers=recep, json={'amount_rsd': 1000, 'method': 'gotovina'})
    assert partial.status_code == 200, partial.text
    after_partial = client.get(f"/api/finance/invoices/{invoice['id']}", headers=h).json()
    assert after_partial['paid_rsd'] == 1000 and after_partial['balance_due_rsd'] == 2000 and after_partial['status'] == 'issued'

    final = client.post(f"/api/finance/invoices/{invoice['id']}/payments", headers=recep, json={'amount_rsd': 2000, 'method': 'kartica'})
    assert final.status_code == 200
    after_full = client.get(f"/api/finance/invoices/{invoice['id']}", headers=h).json()
    assert after_full['paid_rsd'] == 3000 and after_full['balance_due_rsd'] == 0 and after_full['status'] == 'paid'

    payments = client.get(f"/api/finance/invoices/{invoice['id']}/payments", headers=h).json()
    assert len(payments) == 2 and {pm['method'] for pm in payments} == {'gotovina', 'kartica'}


def test_overpayment_is_rejected():
    h = login()
    p = _patient(h, 'Preplata Pacijent')
    invoice = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Usluga', 'unit_price_rsd': 1000}],
    }).json()
    recep = login('reception', 'reception123')
    r = client.post(f"/api/finance/invoices/{invoice['id']}/payments", headers=recep, json={'amount_rsd': 5000, 'method': 'gotovina'})
    assert r.status_code == 422


def test_doctor_cannot_record_payments_but_can_create_invoices():
    h = login()
    p = _patient(h, 'Doktor Racun Pacijent')
    invoice = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Pregled', 'unit_price_rsd': 2000}],
    })
    assert invoice.status_code == 200
    r = client.post(f"/api/finance/invoices/{invoice.json()['id']}/payments", headers=h, json={'amount_rsd': 500, 'method': 'gotovina'})
    assert r.status_code == 403


def test_cancelling_invoice_requires_admin_and_reason_and_blocks_further_payments():
    h = login()
    admin = login('admin', 'admin123')
    p = _patient(h, 'Otkazan Racun Pacijent')
    invoice = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Pregled', 'unit_price_rsd': 1500}],
    }).json()
    forbidden = client.patch(f"/api/finance/invoices/{invoice['id']}/status", headers=h, json={'status': 'cancelled', 'cancellation_reason': 'Test'})
    assert forbidden.status_code == 403
    cancelled = client.patch(f"/api/finance/invoices/{invoice['id']}/status", headers=admin, json={'status': 'cancelled', 'cancellation_reason': 'Pacijent je platio gotovinom van sistema'})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    blocked_payment = client.post(f"/api/finance/invoices/{invoice['id']}/payments", headers=admin, json={'amount_rsd': 500, 'method': 'gotovina'})
    assert blocked_payment.status_code == 409


def test_daily_summary_counts_payments_by_method_for_the_day():
    h = login()
    recep = login('reception', 'reception123')
    p = _patient(h, 'Dnevni Promet Pacijent')
    invoice = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Pregled', 'unit_price_rsd': 6000}],
    }).json()
    client.post(f"/api/finance/invoices/{invoice['id']}/payments", headers=recep, json={'amount_rsd': 4000, 'method': 'kartica'})
    client.post(f"/api/finance/invoices/{invoice['id']}/payments", headers=recep, json={'amount_rsd': 2000, 'method': 'gotovina'})
    today = datetime.now(timezone.utc).date().isoformat()
    summary = client.get('/api/finance/daily-summary', headers=recep, params={'date': today}).json()
    assert summary['revenue_by_method']['kartica'] >= 4000
    assert summary['revenue_by_method']['gotovina'] >= 2000
    assert summary['revenue_collected_rsd'] >= 6000
    assert client.get('/api/finance/daily-summary', headers=h).status_code == 403  # doctor can't see till


def test_outstanding_invoices_lists_unpaid_balance_and_excludes_cancelled():
    h = login()
    recep = login('reception', 'reception123')
    admin = login('admin', 'admin123')
    p = _patient(h, 'Dug Pacijent')
    unpaid = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Pregled', 'unit_price_rsd': 5000}],
    }).json()
    cancelled = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Otkazana usluga', 'unit_price_rsd': 1000}],
    }).json()
    client.patch(f"/api/finance/invoices/{cancelled['id']}/status", headers=admin, json={'status': 'cancelled', 'cancellation_reason': 'Greška'})
    outstanding = client.get('/api/finance/outstanding', headers=recep).json()
    ids = [o['invoice_id'] for o in outstanding]
    assert unpaid['id'] in ids
    assert cancelled['id'] not in ids


def test_invoice_and_payment_data_are_tenant_isolated():
    h = login()
    p = _patient(h, 'Izolacija Racun Pacijent')
    invoice = client.post('/api/finance/invoices', headers=h, json={
        'patient_id': p['id'], 'line_items': [{'description': 'Pregled', 'unit_price_rsd': 1000}],
    }).json()
    org = store.create_organization('Finance Isolation Clinic', f"fin-iso-{uuid4().hex[:6]}")
    store.create_user(org.id, UserCreate(username='doctor-fin', full_name='Fin Doctor', role='doctor', password='docfin1234'))
    other = login('doctor-fin', 'docfin1234', org.slug)
    assert client.get(f"/api/finance/invoices/{invoice['id']}", headers=other).status_code == 404
