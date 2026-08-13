from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def login(username='doctor', password='doctor123', organization='demo-clinic'):
    r = client.post('/api/auth/login', json={'organization': organization, 'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': f"Bearer {r.json()['token']}"}


def _two_page_pdf(page1_text: str, page2_text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet
    styles = getSampleStyleSheet()
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4)
    doc.build([Paragraph(page1_text, styles['Normal']), PageBreak(), Paragraph(page2_text, styles['Normal'])])
    return out.getvalue()


def test_lab_standards_endpoint_returns_seed_loinc_codes():
    h = login()
    r = client.get('/api/lab-standards', headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['CRP']['loinc_code'] == '1988-5'
    assert body['HbA1c']['loinc_code'] == '4548-4'
    assert 'referenc' in body['CRP']['reference_caveat'].lower()


def test_icd10_codes_endpoint_returns_seed_codes():
    h = login()
    r = client.get('/api/icd10-codes', headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['Infekcija urinarnog trakta'] == 'N39.0'
    assert body['Pneumonija — potrebno razmotriti/isključiti'] == 'J18.9'


def test_atc_codes_endpoint_returns_seed_codes():
    h = login()
    r = client.get('/api/atc-codes', headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['metformin'] == 'A10BA02'
    assert body['warfarin'] == 'B01AA03'
    assert body['amoxicillin'] == 'J01CA04'


def test_differential_candidate_carries_matching_icd10_code():
    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'MKB Test Pacijent'}).json()
    client.post(f"/api/patients/{p['id']}/encounters", headers=h, json={
        'visit_date': '2026-08-12T09:00:00Z', 'chief_complaint': 'Dizurija',
        'anamnesis': 'Dizurija i pečenje pri mokrenju, učestalo mokrenje.', 'vital_signs': {},
    })
    analysis = client.post(f"/api/patients/{p['id']}/differential-analyses", headers=h)
    assert analysis.status_code == 200, analysis.text
    uti = next(c for c in analysis.json()['candidates'] if c['name'] == 'Infekcija urinarnog trakta')
    assert uti['icd10_code'] == 'N39.0'


def test_differential_evidence_citation_traces_back_to_uploaded_document_via_api():
    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'Citat Diferencijala Pacijent'}).json()
    up = client.post(f"/api/patients/{p['id']}/documents", headers=h,
                      files={'file': ('nalaz-urina.txt', 'Dizurija i pečenje pri mokrenju, učestalo mokrenje.'.encode('utf-8'), 'text/plain')})
    assert up.status_code == 200, up.text
    analysis = client.post(f"/api/patients/{p['id']}/differential-analyses", headers=h)
    assert analysis.status_code == 200, analysis.text
    uti = next(c for c in analysis.json()['candidates'] if c['name'] == 'Infekcija urinarnog trakta')
    assert uti['evidence_citations']
    citation = uti['evidence_citations'][0]
    assert citation['document_id'] == up.json()['id']
    assert citation['filename'] == 'nalaz-urina.txt'
    # the citation must resolve to a real, fetchable original document
    original = client.get(f"/api/patients/{p['id']}/documents/{citation['document_id']}/original", headers=h)
    assert original.status_code == 200


def test_document_viewer_citation_source_page_survives_through_lab_results_api():
    # v1.17.0: a lab value recognized on page 2 of a real multi-page PDF
    # must carry that page number all the way through to the lab-results
    # API response, so the frontend can link straight to
    # /original#page=2 instead of the whole document.
    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'Citat Pacijent'}).json()
    pdf_bytes = _two_page_pdf('Uvod u nalaz, bez laboratorijskih vrednosti.', 'CRP: 45 mg/L [0-5]')
    up = client.post(f"/api/patients/{p['id']}/documents", headers=h,
                      files={'file': ('nalaz.pdf', pdf_bytes, 'application/pdf')})
    assert up.status_code == 200, up.text
    assert up.json()['lab_drafts_created'] >= 1
    results = client.get(f"/api/patients/{p['id']}/lab-results", headers=h).json()
    crp = next(r for r in results if r['name'] == 'CRP')
    assert crp['source_page'] == 2
    assert crp['source_document_id'] == up.json()['id']
    # the actual PDF bytes must be fetchable so the frontend link resolves
    original = client.get(f"/api/patients/{p['id']}/documents/{up.json()['id']}/original", headers=h)
    assert original.status_code == 200 and original.headers['content-type'] == 'application/pdf'


def test_lab_results_trend_endpoint_returns_chronological_history():
    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'Trend Pacijent'}).json()
    for value, date in [(5.2, '2026-06-01T09:00:00Z'), (6.8, '2026-07-01T09:00:00Z'), (4.9, '2026-08-01T09:00:00Z')]:
        client.post(f"/api/patients/{p['id']}/lab-results", headers=h, json={
            'name': 'HbA1c', 'value': value, 'unit': '%', 'collected_at': date,
        })
    trend = client.get(f"/api/patients/{p['id']}/lab-results/trend", headers=h, params={'name': 'HbA1c'})
    assert trend.status_code == 200, trend.text
    body = trend.json()
    assert [row['value'] for row in body] == [5.2, 6.8, 4.9]
    assert body == sorted(body, key=lambda r: r['date'])


def test_reception_cannot_view_lab_results_or_trend():
    h = login()
    p = client.post('/api/patients', headers=h, json={'full_name': 'RBAC Lab Pacijent'}).json()
    recep = login('reception', 'reception123')
    assert client.get(f"/api/patients/{p['id']}/lab-results", headers=recep).status_code == 403
    assert client.get(f"/api/patients/{p['id']}/lab-results/trend", headers=recep, params={'name': 'CRP'}).status_code == 403
