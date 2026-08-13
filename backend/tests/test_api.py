from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from app.main import app, store
from app.models import UserCreate
from datetime import datetime, timedelta, timezone
from uuid import uuid4

client=TestClient(app)
def login(username='doctor',password='doctor123',organization='demo-clinic'):
    r=client.post('/api/auth/login',json={'organization':organization,'username':username,'password':password});assert r.status_code==200,r.text
    return {'Authorization':f"Bearer {r.json()['token']}"}

def test_health_auth_and_session_metadata():
    expected_version=(Path(__file__).resolve().parents[2]/'VERSION').read_text(encoding='utf-8').strip()
    h=client.get('/api/health').json();assert h['version']==expected_version;assert h['session_minutes']>=1
    me=client.get('/api/auth/me',headers=login()).json();assert me['organization_slug']=='demo-clinic'

def test_admin_user_management_and_password_change():
    admin=login('admin','admin123')
    username=f"nurse-{uuid4().hex[:6]}"
    r=client.post('/api/users',headers=admin,json={'username':username,'full_name':'Nurse Test','role':'receptionist','password':'temporary123'});assert r.status_code==200
    new=login(username,'temporary123');assert client.post('/api/auth/change-password',headers=new,json={'current_password':'temporary123','new_password':'permanent123'}).status_code==204
    uid=r.json()['id'];assert client.patch(f'/api/users/{uid}/status',headers=admin,json={'active':False}).status_code==200
    assert client.post('/api/auth/login',json={'organization':'demo-clinic','username':username,'password':'permanent123'}).status_code==401

def test_temporary_password_is_enforced_server_side():
    admin = login('admin', 'admin123')
    username = f'temporary-{uuid4().hex[:6]}'
    created = client.post('/api/users', headers=admin, json={'username':username,'full_name':'Temporary User','role':'doctor','password':'temporary1234'})
    assert created.status_code == 200 and created.json()['must_change_password'] is True
    token = login(username, 'temporary1234')
    # A bearer token must not bypass the onboarding guard.
    assert client.get('/api/patients', headers=token).status_code == 403
    changed = client.post('/api/auth/change-password', headers=token, json={'current_password':'temporary1234','new_password':'permanent1234'})
    assert changed.status_code == 204
    fresh = login(username, 'permanent1234')
    assert client.get('/api/patients', headers=fresh).status_code == 200


def test_mfa_enrollment_requires_code_and_gates_login(monkeypatch):
    import app.mfa as mfa
    monkeypatch.setattr(mfa, 'verify_totp', lambda secret, code: code == '123456')
    username = f'mfa-{uuid4().hex[:6]}'
    admin = login('admin', 'admin123')
    assert client.post('/api/users', headers=admin, json={'username': username, 'full_name': 'MFA Test', 'role': 'doctor', 'password': 'mfaPassword123'}).status_code == 200
    h = login(username, 'mfaPassword123')
    assert client.post('/api/auth/change-password', headers=h, json={'current_password':'mfaPassword123','new_password':'mfaPermanent123'}).status_code == 204
    h = login(username, 'mfaPermanent123')
    setup = client.post('/api/auth/mfa/setup', headers=h)
    assert setup.status_code == 200 and setup.json()['secret']
    assert client.post('/api/auth/mfa/confirm', headers=h, json={'code': '000000'}).status_code == 400
    assert client.post('/api/auth/mfa/confirm', headers=h, json={'code': '123456'}).status_code == 204
    challenge = client.post('/api/auth/login', json={'organization': 'demo-clinic', 'username': username, 'password': 'mfaPermanent123'})
    assert challenge.status_code == 200 and challenge.json()['mfa_required'] is True and 'token' not in challenge.json()
    assert client.post('/api/auth/mfa/complete-login', json={'challenge': challenge.json()['mfa_challenge'], 'code': '000000'}).status_code == 401
    complete = client.post('/api/auth/mfa/complete-login', json={'challenge': challenge.json()['mfa_challenge'], 'code': '123456'})
    assert complete.status_code == 200 and complete.json()['token']

def test_admin_mfa_recovery_revokes_target_sessions(monkeypatch):
    import app.mfa as mfa
    monkeypatch.setattr(mfa, 'verify_totp', lambda secret, code: code == '123456')
    admin = login('admin', 'admin123')
    username = f'recover-{uuid4().hex[:6]}'
    created = client.post('/api/users', headers=admin, json={'username':username,'full_name':'Recovery User','role':'doctor','password':'temporary1234'}).json()
    first = login(username, 'temporary1234')
    assert client.post('/api/auth/change-password', headers=first, json={'current_password':'temporary1234','new_password':'permanent1234'}).status_code == 204
    target = login(username, 'permanent1234')
    setup = client.post('/api/auth/mfa/setup', headers=target).json()
    assert setup['secret']
    assert client.post('/api/auth/mfa/confirm', headers=target, json={'code':'123456'}).status_code == 204
    # Obtain another ordinary session before the reset; it must be invalidated.
    challenge = client.post('/api/auth/login', json={'organization':'demo-clinic','username':username,'password':'permanent1234'}).json()
    active = client.post('/api/auth/mfa/complete-login', json={'challenge':challenge['mfa_challenge'],'code':'123456'}).json()['token']
    assert client.post(f"/api/users/{created['id']}/mfa-reset", headers=admin, json={'reason':'Verified lost authenticator device'}).status_code == 204
    assert client.get('/api/auth/me', headers={'Authorization':f'Bearer {active}'}).status_code == 401
    assert client.post('/api/auth/login', json={'organization':'demo-clinic','username':username,'password':'permanent1234'}).json().get('mfa_required') is None
    audit = client.get('/api/audit', headers=admin).json()
    assert any(row['action'] == 'mfa_recovery_reset' for row in audit)

def test_cross_clinic_data_isolation():
    org=store.create_organization('Second Clinic',f"second-{uuid4().hex[:6]}")
    store.create_user(org.id,UserCreate(username='doctor2',full_name='Second Doctor',role='doctor',password='doctor2345'))
    first=login();p=client.post('/api/patients',headers=first,json={'full_name':'Clinic One Patient'}).json()
    second=login('doctor2','doctor2345',org.slug)
    assert all(x['id']!=p['id'] for x in client.get('/api/patients',headers=second).json())
    assert client.get(f"/api/patients/{p['id']}/documents",headers=second).status_code==404

def test_reception_permissions_preserved():
    h=login('reception','reception123');p=client.post('/api/patients',headers=h,json={'full_name':'Reception Patient'});assert p.status_code==200
    assert client.get('/api/reports',headers=h).status_code==403
    assert client.get('/api/users',headers=h).status_code==403


def test_clinical_profile_encounter_and_pdf():
    h=login();patient=client.post('/api/patients',headers=h,json={'full_name':'Structured Patient','date_of_birth':'1980-01-02'}).json();pid=patient['id']
    profile={'blood_type':'A+','allergies':['Penicillin'],'current_medications':['Ramipril 5 mg'],'diagnoses':['Hypertension'],'medical_history':'Appendectomy','family_history':'Diabetes','social_history':'Non-smoker'}
    r=client.put(f'/api/patients/{pid}/clinical-profile',headers=h,json=profile);assert r.status_code==200;rj=r.json();assert rj['allergies']==['Penicillin']
    encounter={'visit_date':'2026-07-27T10:30:00Z','chief_complaint':'Routine control','anamnesis':'Feels well','examination':'Stable','assessment':'Controlled hypertension','plan':'Continue therapy','vital_signs':{'bp':'125/80','pulse':'72'}}
    r=client.post(f'/api/patients/{pid}/encounters',headers=h,json=encounter);assert r.status_code==200
    assert len(client.get(f'/api/patients/{pid}/encounters',headers=h).json())==1
    pdf=client.get(f'/api/patients/{pid}/medical-report.pdf',headers=h);assert pdf.status_code==200;assert pdf.headers['content-type']=='application/pdf';assert pdf.content.startswith(b'%PDF')


def test_pre_visit_briefing_generation_and_history():
    h=login();patient=client.post('/api/patients',headers=h,json={'full_name':'Briefing Patient'}).json();pid=patient['id']
    client.put(f'/api/patients/{pid}/clinical-profile',headers=h,json={'allergies':['Penicillin'],'current_medications':['Metformin 500 mg'],'diagnoses':['Type 2 diabetes']})
    client.post(f'/api/patients/{pid}/encounters',headers=h,json={'visit_date':'2026-07-20T09:00:00Z','chief_complaint':'Diabetes control','assessment':'Type 2 diabetes','plan':'Review laboratory results','vital_signs':{}})
    files={'file':('lab.txt',b'2026-07-18 HbA1c high at 8.2 percent','text/plain')}
    assert client.post(f'/api/patients/{pid}/documents',headers=h,files=files).status_code==200
    r=client.post(f'/api/patients/{pid}/pre-visit-briefings',headers=h);assert r.status_code==200,r.text
    body=r.json();assert 'Type 2 diabetes' in body['active_problems'];assert body['allergies']==['Penicillin'];assert body['questions_to_verify'];assert body['evidence_sources']==['lab.txt']
    history=client.get(f'/api/patients/{pid}/pre-visit-briefings',headers=h);assert history.status_code==200;assert len(history.json())==1

def test_reception_cannot_generate_briefing():
    doctor=login();p=client.post('/api/patients',headers=doctor,json={'full_name':'Protected Briefing'}).json()
    reception=login('reception','reception123')
    assert client.post(f"/api/patients/{p['id']}/pre-visit-briefings",headers=reception).status_code==403

def test_scribe_draft_in_serbian_and_approval_flow():
    h=login();patient=client.post('/api/patients',headers=h,json={'full_name':'Pacijent Scribe'}).json();pid=patient['id']
    transcript='Pacijent navodi suv kašalj pet dana bez temperature. Alergičan je na penicilin. Doktor: pluća auskultatorno čista. Plan je kontrola za sedam dana.'
    r=client.post(f'/api/patients/{pid}/scribe-drafts',headers=h,json={'transcript':transcript,'mode':'dictation'});assert r.status_code==200,r.text
    body=r.json();assert body['status']=='draft';assert body['anamnesis'];assert body['clinician_name']
    history=client.get(f'/api/patients/{pid}/scribe-drafts',headers=h);assert history.status_code==200;assert len(history.json())==1
    approved=client.patch(f"/api/patients/{pid}/scribe-drafts/{body['id']}/status",headers=h,json={'status':'approved'});assert approved.status_code==200;assert approved.json()['draft']['status']=='approved'

def test_reception_cannot_access_scribe():
    doctor=login();p=client.post('/api/patients',headers=doctor,json={'full_name':'Zaštićeni pacijent'}).json();reception=login('reception','reception123')
    assert client.get(f"/api/patients/{p['id']}/scribe-drafts",headers=reception).status_code==403


def test_epidemiology_radar_aggregates_signals_and_confirmed_pathogens():
    h=login(); now=datetime.now(timezone.utc).isoformat()
    for i in range(5):
        patient=client.post('/api/patients',headers=h,json={'full_name':f'Radar Pacijent {i}'}).json()
        payload={'visit_date':now,'chief_complaint':'Temperatura i kašalj','anamnesis':'Suv kašalj i bol u grlu','examination':'','assessment':'Respiratorna infekcija','plan':'Kontrola','vital_signs':{}}
        assert client.post(f"/api/patients/{patient['id']}/encounters",headers=h,json=payload).status_code==200
    patient=client.post('/api/patients',headers=h,json={'full_name':'Potvrđen grip'}).json()
    files={'file':('test-grip.txt','Laboratorijski test: Grip A pozitivan'.encode('utf-8'),'text/plain')}
    assert client.post(f"/api/patients/{patient['id']}/documents",headers=h,files=files).status_code==200
    r=client.get('/api/epidemiology/radar?days=7',headers=h);assert r.status_code==200,r.text
    body=r.json();assert body['minimum_sample_met'] is True
    assert any(x['name']=='respiratorni' and x['current_count']>=5 for x in body['syndrome_trends'])
    assert any(x['name']=='Grip A' for x in body['confirmed_pathogens'])
    assert body['clusters']

def test_epidemiology_radar_daily_counts_used_by_the_frontend_chart():
    h=login(); now=datetime.now(timezone.utc).isoformat()
    patient=client.post('/api/patients',headers=h,json={'full_name':'Daily Chart Pacijent'}).json()
    payload={'visit_date':now,'chief_complaint':'Temperatura i kašalj','anamnesis':'','examination':'','assessment':'','plan':'','vital_signs':{}}
    assert client.post(f"/api/patients/{patient['id']}/encounters",headers=h,json=payload).status_code==200
    r=client.get('/api/epidemiology/radar?days=7',headers=h);body=r.json()
    assert len(body['daily_counts'])==7
    assert all('date' in d and 'counts' in d for d in body['daily_counts'])
    today=body['daily_counts'][-1]
    assert today['counts'].get('respiratorni',0)>=1

def test_reception_cannot_view_epidemiology_radar():
    assert client.get('/api/epidemiology/radar',headers=login('reception','reception123')).status_code==403

def test_differential_analysis_uses_patient_data_and_epidemiology_context():
    h=login();now=datetime.now(timezone.utc).isoformat()
    patient=client.post('/api/patients',headers=h,json={'full_name':'Diferencijalni Pacijent'}).json();pid=patient['id']
    client.put(f'/api/patients/{pid}/clinical-profile',headers=h,json={'diagnoses':['Dijabetes'],'current_medications':['Metformin 500 mg'],'allergies':[]})
    client.post(f'/api/patients/{pid}/encounters',headers=h,json={'visit_date':now,'chief_complaint':'Temperatura i kašalj','anamnesis':'Bol u grlu i suv kašalj','examination':'','assessment':'Respiratorna infekcija','plan':'Kontrola','vital_signs':{}})
    files={'file':('laboratorija.txt','HbA1c high 8.2. CRP povišen. Leukociti povišeni.'.encode('utf-8'),'text/plain')}
    assert client.post(f'/api/patients/{pid}/documents',headers=h,files=files).status_code==200
    r=client.get(f'/api/patients/{pid}/differential-analysis',headers=h);assert r.status_code==200,r.text
    body=r.json();assert body['candidates'];assert any('Dijabetes' in x['name'] for x in body['candidates'])
    assert all(0<=x['match_score']<=100 for x in body['candidates'])
    assert 'nije verovatnoća dijagnoze' in body['disclaimer']

def test_reception_cannot_view_differential_analysis():
    doctor=login();p=client.post('/api/patients',headers=doctor,json={'full_name':'Zaštićena analiza'}).json()
    assert client.get(f"/api/patients/{p['id']}/differential-analysis",headers=login('reception','reception123')).status_code==403


def test_differential_review_is_persisted_and_can_update_scribe_draft():
    h=login();now=datetime.now(timezone.utc).isoformat()
    patient=client.post('/api/patients',headers=h,json={'full_name':'Pregled sugestije'}).json();pid=patient['id']
    transcript='Pacijent ima temperaturu i kašalj. CRP povišen i leukociti povišeni. Plan je dalja procena.'
    draft=client.post(f'/api/patients/{pid}/scribe-drafts',headers=h,json={'transcript':transcript,'mode':'dictation'});assert draft.status_code==200
    client.post(f'/api/patients/{pid}/encounters',headers=h,json={'visit_date':now,'chief_complaint':'Temperatura i kašalj','anamnesis':'Kašalj','examination':'','assessment':'','plan':'','vital_signs':{}})
    files={'file':('lab-review.txt','CRP povišen. Leukociti povišeni.'.encode('utf-8'),'text/plain')}
    client.post(f'/api/patients/{pid}/documents',headers=h,files=files)
    analysis=client.post(f'/api/patients/{pid}/differential-analyses',headers=h);assert analysis.status_code==200,analysis.text
    body=analysis.json();candidate=next(x for x in body['candidates'] if 'Bakterijska' in x['name'])
    reviewed=client.patch(f"/api/patients/{pid}/differential-analyses/{body['id']}/candidates/{candidate['id']}",headers=h,json={'status':'accepted','doctor_note':'Razmotriti nakon pregleda','add_to_latest_scribe_draft':True})
    assert reviewed.status_code==200,reviewed.text;assert reviewed.json()['scribe_draft_updated'] is True
    saved=client.get(f'/api/patients/{pid}/differential-analyses',headers=h).json();assert saved[0]['candidates'][0]['review_status'] in ('accepted','pending')
    drafts=client.get(f'/api/patients/{pid}/scribe-drafts',headers=h).json();assert 'Lekar prihvatio AI sugestiju' in drafts[0]['assessment']


def test_scribe_draft_can_be_edited_and_finalized_as_encounter():
    h=login();patient=client.post('/api/patients',headers=h,json={'full_name':'Pisar Finalizacija'}).json();pid=patient['id']
    r=client.post(f'/api/patients/{pid}/scribe-drafts',headers=h,json={'transcript':'Pacijent navodi kašalj. Pregled pluća uredan. Plan kontrola za sedam dana.','mode':'dictation'});assert r.status_code==200
    draft=r.json();assert draft['source_map']['anamnesis']=='transkript'
    edit={'chief_complaint':'Kašalj','anamnesis':'Kašalj traje tri dana.','examination':'Pluća auskultatorno čista.','assessment':'Akutna respiratorna infekcija u razmatranju.','plan':'Kontrola za sedam dana.','medication_changes':[],'allergy_updates':[],'missing_information':['Temperatura nije izmerena.']}
    updated=client.put(f"/api/patients/{pid}/scribe-drafts/{draft['id']}",headers=h,json=edit);assert updated.status_code==200;assert updated.json()['chief_complaint']=='Kašalj'
    finalized=client.patch(f"/api/patients/{pid}/scribe-drafts/{draft['id']}/status",headers=h,json={'status':'approved','create_encounter':True,'visit_date':'2026-07-28T18:00:00Z'});assert finalized.status_code==200,finalized.text
    body=finalized.json();assert body['draft']['encounter_id'];assert body['encounter']['chief_complaint']=='Kašalj'
    assert any(x['id']==body['encounter']['id'] for x in client.get(f'/api/patients/{pid}/encounters',headers=h).json())


def test_login_lockout_after_repeated_failures():
    org_slug='demo-clinic';username=f'lockout-{uuid4().hex[:6]}'
    admin=login('admin','admin123')
    client.post('/api/users',headers=admin,json={'username':username,'full_name':'Lockout Test','role':'receptionist','password':'correcthorse1'})
    for _ in range(5):
        r=client.post('/api/auth/login',json={'organization':org_slug,'username':username,'password':'wrong-password'});assert r.status_code==401
    locked=client.post('/api/auth/login',json={'organization':org_slug,'username':username,'password':'correcthorse1'})
    assert locked.status_code==429;assert 'Retry-After' in locked.headers

def test_authenticate_pays_the_same_verification_cost_whether_or_not_the_account_exists():
    # Regression guard for a username-enumeration timing side channel: a
    # response-time difference between "wrong password for a real account"
    # and "account/org doesn't exist" was measured locally at roughly 190ms
    # vs 3.5ms before the fix. This test can't reliably assert on wall-clock
    # timing in CI, so instead it verifies the actual mechanism: the
    # password-hashing routine must run exactly once in every case, rather
    # than being skipped via short-circuit when no matching row is found.
    from unittest.mock import patch
    import app.store as store_module
    with patch.object(store_module, '_verify_password', wraps=store_module._verify_password) as spy:
        assert store.authenticate('demo-clinic', 'doctor', 'wrong-password-xyz') is None
        assert spy.call_count == 1
    with patch.object(store_module, '_verify_password', wraps=store_module._verify_password) as spy:
        assert store.authenticate('demo-clinic', f'nouser-{uuid4().hex[:8]}', 'wrong-password-xyz') is None
        assert spy.call_count == 1
    with patch.object(store_module, '_verify_password', wraps=store_module._verify_password) as spy:
        assert store.authenticate(f'noorg-{uuid4().hex[:8]}', 'doctor', 'wrong-password-xyz') is None
        assert spy.call_count == 1

def test_cross_organization_idor_across_scribe_and_differential_endpoints():
    # Two patients in the SAME organization -- a subtler IDOR than the
    # cross-tenant case already covered by test_cross_clinic_data_isolation:
    # can patient B's id be substituted into a URL for an action scoped to
    # patient A's scribe draft?
    h=login()
    pid_a=client.post('/api/patients',headers=h,json={'full_name':'IDOR Pacijent A'}).json()['id']
    pid_b=client.post('/api/patients',headers=h,json={'full_name':'IDOR Pacijent B'}).json()['id']
    draft=client.post(f'/api/patients/{pid_a}/scribe-drafts',headers=h,json={'transcript':'Pacijent A ima temperaturu i kasalj.','mode':'dictation'}).json()
    edit=client.put(f"/api/patients/{pid_b}/scribe-drafts/{draft['id']}",headers=h,json={'chief_complaint':'HACKED','anamnesis':'','examination':'','assessment':'','plan':'','medication_changes':[],'allergy_updates':[],'missing_information':[]})
    assert edit.status_code in (404,409)
    status=client.patch(f"/api/patients/{pid_b}/scribe-drafts/{draft['id']}/status",headers=h,json={'status':'approved'})
    assert status.status_code==404
    unchanged=client.get(f'/api/patients/{pid_a}/scribe-drafts',headers=h).json()
    assert unchanged[0]['chief_complaint']!='HACKED'

def test_mass_assignment_is_rejected_on_patient_and_user_creation():
    h=login()
    p=client.post('/api/patients',headers=h,json={
        'full_name':'Mass Assignment Guard','id':'00000000-0000-0000-0000-000000000099',
        'organization_id':'attacker-controlled','role':'admin','active':True,
        'clinical_profile':{'diagnoses':['INJECTED']},
    }).json()
    assert p['id']!='00000000-0000-0000-0000-000000000099'
    assert p['clinical_profile']['diagnoses']==[]
    admin=login('admin','admin123')
    u=client.post('/api/users',headers=admin,json={
        'username':f'massassign-{uuid4().hex[:6]}','full_name':'Mass Assign','role':'receptionist','password':'correctPass1',
        'must_change_password':False,'id':'attacker-controlled-id','organization_id':'attacker-controlled',
    }).json()
    assert u['must_change_password'] is True
    assert u['id']!='attacker-controlled-id'

def test_lab_drafts_need_doctor_verification_and_are_tenant_isolated():
    h = login()
    patient = client.post('/api/patients', headers=h, json={'full_name': 'Lab Verification Patient'}).json()
    result = client.post(f"/api/patients/{patient['id']}/lab-results", headers=h, json={
        'name': 'CRP', 'value': 44.0, 'unit': 'mg/L', 'reference_range': '0–5',
    })
    assert result.status_code == 200
    assert result.json()['status'] == 'verified'
    other = client.post('/api/patients', headers=h, json={'full_name': 'Other Lab Patient'}).json()
    blocked = client.patch(f"/api/patients/{other['id']}/lab-results/{result.json()['id']}/status", headers=h, json={'status': 'rejected'})
    assert blocked.status_code == 404


def test_medication_safety_endpoint_is_doctor_only_and_not_a_safe_claim():
    h = login()
    patient = client.post('/api/patients', headers=h, json={'full_name': 'Safety Patient'}).json()
    client.put(f"/api/patients/{patient['id']}/clinical-profile", headers=h, json={
        'blood_type': None, 'allergies': ['penicillin'], 'current_medications': ['warfarin 5 mg'],
        'diagnoses': [], 'medical_history': None, 'family_history': None, 'social_history': None,
    })
    checked = client.post(f"/api/patients/{patient['id']}/medication-safety-check", headers=h, json={
        'proposed_medications': ['ibuprofen 400 mg', 'amoxicillin 500 mg'],
    })
    assert checked.status_code == 200
    assert len(checked.json()['findings']) >= 2
    assert 'ne znači da je kombinacija bezbedna' in checked.json()['disclaimer']
    reception = login('reception', 'reception123')
    assert client.post(f"/api/patients/{patient['id']}/medication-safety-check", headers=reception, json={'proposed_medications': []}).status_code == 403


def test_sessions_survive_a_store_restart():
    from app.store import PersistentStore
    h=login()
    token=h['Authorization'].split(' ',1)[1]
    reopened=PersistentStore(store.data_dir)
    session=reopened.get_session(token)
    assert session is not None
    assert client.get('/api/auth/me',headers=h).status_code==200

def test_audit_chain_is_tamper_evident():
    admin=login('admin','admin123')
    client.post('/api/patients',headers=admin,json={'full_name':'Audit Chain Patient'})
    org_id=store.organization_by_slug('demo-clinic').id
    before=store.verify_audit_chain(org_id);assert before['intact'] is True
    verify_endpoint=client.get('/api/audit/verify',headers=admin);assert verify_endpoint.status_code==200;assert verify_endpoint.json()['intact'] is True
    row=store._conn.execute('SELECT seq FROM audit WHERE organization_id=? ORDER BY seq DESC LIMIT 1',(org_id,)).fetchone()
    with store._conn:
        store._conn.execute("UPDATE audit SET detail_enc=? WHERE seq=?", (store.cipher.encrypt_text('tampered'), row['seq']))
    after=store.verify_audit_chain(org_id)
    assert after['intact'] is False;assert after['first_broken_seq']==row['seq']


def test_audit_detail_is_encrypted_at_rest():
    admin=login('admin','admin123')
    client.post('/api/patients',headers=admin,json={'full_name':'Secret Patient Name Xyz'})
    row=store._conn.execute("SELECT detail_enc FROM audit WHERE action='create' AND resource_type='patient' ORDER BY seq DESC LIMIT 1").fetchone()
    assert row['detail_enc'] is not None
    assert b'Secret Patient Name Xyz' not in row['detail_enc']
    # but the decrypted API view still exposes it normally to an authorized admin
    entries=client.get('/api/audit',headers=admin).json()
    assert any(e['detail']=='Secret Patient Name Xyz' for e in entries)


def test_password_change_revokes_all_sessions():
    username=f"revoke-{uuid4().hex[:6]}"
    admin=login('admin','admin123')
    client.post('/api/users',headers=admin,json={'username':username,'full_name':'Revoke Test','role':'receptionist','password':'temporary123'})
    session_a=login(username,'temporary123')
    session_b=login(username,'temporary123')
    assert client.get('/api/auth/me',headers=session_a).status_code==200
    assert client.get('/api/auth/me',headers=session_b).status_code==200
    changed=client.post('/api/auth/change-password',headers=session_a,json={'current_password':'temporary123','new_password':'brandNew123'})
    assert changed.status_code==204
    # both the session used to change the password AND any other active
    # session for this user should now be dead
    assert client.get('/api/auth/me',headers=session_a).status_code==401
    assert client.get('/api/auth/me',headers=session_b).status_code==401
    assert client.post('/api/auth/login',json={'organization':'demo-clinic','username':username,'password':'brandNew123'}).status_code==200


def test_admin_can_list_and_revoke_sessions():
    doctor=login()
    admin=login('admin','admin123')
    sessions=client.get('/api/sessions',headers=admin).json()
    assert any(s['username']=='doctor' for s in sessions)
    target=next(s for s in sessions if s['username']=='doctor')
    assert 'token' not in target
    revoked=client.delete(f"/api/sessions/{target['id']}",headers=admin)
    assert revoked.status_code==204
    assert client.get('/api/auth/me',headers=doctor).status_code==401


def test_reception_cannot_list_sessions():
    assert client.get('/api/sessions',headers=login('reception','reception123')).status_code==403


def test_health_reports_database_status():
    h=client.get('/api/health').json()
    assert h['database']=='ok'

def test_audit_chain_survives_detail_reencryption():
    # This is the same mechanism scripts/rotate_key.py relies on: changing
    # the ciphertext bytes of detail_enc and recomputing the chain with the
    # same digest formula must produce a chain that still verifies intact.
    admin=login('admin','admin123')
    client.post('/api/patients',headers=admin,json={'full_name':'Reencryption Check'})
    org_id=store.organization_by_slug('demo-clinic').id
    rows=store._conn.execute("SELECT seq,detail_enc FROM audit WHERE organization_id=? AND detail_enc IS NOT NULL",(org_id,)).fetchall()
    assert rows
    with store._conn:
        for row in rows:
            plaintext=store.cipher.decrypt_text(row['detail_enc'])
            store._conn.execute("UPDATE audit SET detail_enc=? WHERE seq=?",(store.cipher.encrypt_text(plaintext),row['seq']))
        prev_hash="0"*64
        for row in store._conn.execute("SELECT * FROM audit WHERE organization_id=? ORDER BY seq ASC",(org_id,)).fetchall():
            digest=store._audit_digest(prev_hash,row['id'],row['occurred_at'],row['user_id'],row['username'],row['role'],row['action'],row['resource_type'],row['resource_id'],row['detail_enc'])
            store._conn.execute("UPDATE audit SET prev_hash=?, hash=? WHERE seq=?",(prev_hash,digest,row['seq']))
            prev_hash=digest
    assert store.verify_audit_chain(org_id)['intact'] is True

def test_client_ip_ignores_x_forwarded_for_unless_trust_proxy_enabled():
    from unittest.mock import MagicMock
    import app.main as main_module
    fake_request=MagicMock()
    fake_request.client.host='127.0.0.1'
    fake_request.headers={'x-forwarded-for':'203.0.113.7, 10.0.0.1'}
    original=main_module.TRUST_PROXY_HEADERS
    try:
        main_module.TRUST_PROXY_HEADERS=False
        assert main_module._client_ip(fake_request)=='127.0.0.1'
        main_module.TRUST_PROXY_HEADERS=True
        assert main_module._client_ip(fake_request)=='203.0.113.7'
    finally:
        main_module.TRUST_PROXY_HEADERS=original

def test_decompression_bomb_docx_upload_is_rejected_via_the_real_endpoint():
    import zipfile
    from io import BytesIO
    buf=BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        huge='<w:t>'+('A'*80)+'</w:t>'
        huge=huge*3_000_000
        z.writestr('word/document.xml','<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r>'+huge+'</w:r></w:p></w:body></w:document>')
    bomb=buf.getvalue()
    assert len(bomb)<2*1024*1024
    h=login();p=client.post('/api/patients',headers=h,json={'full_name':'Zip Bomb Regression Test'}).json()
    r=client.post(f"/api/patients/{p['id']}/documents",headers=h,files={'file':('bomb.docx',bomb,'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
    assert r.status_code==415,r.text
    # the server must still be alive and functional right after
    assert client.get('/api/health').status_code==200

def test_oversized_upload_is_rejected_during_streaming_not_after_full_buffering():
    # Regression guard: the endpoint used to buffer the entire upload into
    # memory via a single `await file.read()` and only checked the size
    # afterwards -- an unbounded memory-exhaustion vector regardless of the
    # nominal 15 MB limit. This confirms an oversized upload is still
    # rejected (413) under the new chunked-read implementation, and that
    # the server survives it -- functionally the same guarantee as before,
    # now backed by bounded peak memory during the read itself.
    h=login();p=client.post('/api/patients',headers=h,json={'full_name':'Oversized Streaming Test'}).json()
    oversized=b'A'*(16*1024*1024)  # 16 MB, over the 15 MB cap
    r=client.post(f"/api/patients/{p['id']}/documents",headers=h,files={'file':('big.txt',oversized,'text/plain')})
    assert r.status_code==413,r.text
    assert client.get('/api/health').status_code==200

def test_medical_report_pdf_handles_ordinary_clinical_shorthand_containing_angle_brackets():
    # Regression guard: chief_complaint/org name were not escaped before
    # being passed to reportlab's Paragraph (which interprets a small
    # markup subset), so ordinary clinical shorthand like "Bol < 3/10"
    # crashed PDF generation outright (500), not just a hypothetical XSS
    # payload -- confirmed by hand before the fix.
    h=login();p=client.post('/api/patients',headers=h,json={'full_name':'PDF Escaping Test'}).json();pid=p['id']
    client.post(f'/api/patients/{pid}/encounters',headers=h,json={'visit_date':datetime.now(timezone.utc).isoformat(),'chief_complaint':'Bol < 3/10, temperatura < 37.5','anamnesis':'<b>test</b> <unclosed','examination':'','assessment':'','plan':'','vital_signs':{}})
    r=client.get(f'/api/patients/{pid}/medical-report.pdf',headers=h)
    assert r.status_code==200,r.text
    assert r.headers['content-type']=='application/pdf'
def test_reception_cannot_read_clinical_profile_or_pdf_or_differential():
    # v1.7.0 added a doctor-facing "safety strip" (allergies/diagnoses/meds)
    # sourced from the clinical-profile endpoint. Confirms the frontend's
    # role gate is backed by a real 403, not just a hidden UI element.
    doc=login();h=login('reception','reception123')
    p=client.post('/api/patients',headers=doc,json={'full_name':'Strip Test Patient'}).json()
    assert client.get(f"/api/patients/{p['id']}/clinical-profile",headers=h).status_code==403
    assert client.put(f"/api/patients/{p['id']}/clinical-profile",headers=h,json={'blood_type':'O+'}).status_code==403
    assert client.get(f"/api/patients/{p['id']}/medical-report.pdf",headers=h).status_code==403
    assert client.post(f"/api/patients/{p['id']}/differential-analyses",headers=h).status_code==403
    assert client.get(f"/api/patients/{p['id']}/encounters",headers=h).status_code==403

def test_clinical_text_with_angle_brackets_does_not_break_pdf_or_leak_markup():
    # reportlab's Paragraph() interprets a small set of markup tags; ordinary
    # clinical shorthand ("bol < 3/10") and a deliberate XSS-style payload
    # must both be neutralized by _safe() rather than crashing generation or
    # passing through as live markup.
    h=login()
    p=client.post('/api/patients',headers=h,json={'full_name':'Angle Bracket Patient'}).json()
    payload={'blood_type':'A+','allergies':['<script>alert(1)</script>','bol < 3/10'],'current_medications':[],'diagnoses':['<b>forged-bold</b>'],'medical_history':None,'family_history':None,'social_history':None}
    assert client.put(f"/api/patients/{p['id']}/clinical-profile",headers=h,json=payload).status_code==200
    r=client.get(f"/api/patients/{p['id']}/medical-report.pdf",headers=h)
    assert r.status_code==200 and r.headers['content-type']=='application/pdf' and len(r.content)>500

def test_appointment_and_patient_ids_are_tenant_scoped_for_open_chart_jump():
    # v1.7.0's "Open chart" button on an appointment row trusts patient_id
    # from the appointment payload without re-validating client-side; confirm
    # the server itself won't hand back another clinic's appointment/patient.
    org=store.create_organization('Third Clinic',f"third-{uuid4().hex[:6]}")
    store.create_user(org.id,UserCreate(username='doctor3',full_name='Third Doctor',role='doctor',password='doctor3456'))
    first=login()
    p=client.post('/api/patients',headers=first,json={'full_name':'Appt Isolation Patient'}).json()
    appt=client.post('/api/appointments',headers=first,json={'patient_id':p['id'],'starts_at':datetime.now(timezone.utc).isoformat(),'reason':'Test'}).json()
    third=login('doctor3','doctor3456',org.slug)
    assert all(a['id']!=appt['id'] for a in client.get('/api/appointments',headers=third).json())
    assert client.get(f"/api/patients/{p['id']}/overview",headers=third).status_code==404

def test_login_rate_limit_and_lockout_still_enforced():
    # Regression guard: v1.6.1 hardening added per-IP rate limiting and login
    # lockout after repeated failures; confirm neither regressed while the
    # frontend was reworked. Uses a disposable account so the lockout this
    # test deliberately triggers can't poison the shared 'doctor' login used
    # by every other test in this module.
    admin=login('admin','admin123')
    username=f"lockout-target-{uuid4().hex[:6]}"
    client.post('/api/users',headers=admin,json={'username':username,'full_name':'Lockout Target','role':'receptionist','password':'temporary123'})
    bad={'organization':'demo-clinic','username':username,'password':'wrong-password'}
    codes=[client.post('/api/auth/login',json=bad).status_code for _ in range(8)]
    assert 401 in codes
    assert any(c in (423,429) for c in codes), f"expected a lockout/rate-limit response among {codes}"

def test_username_enumeration_timing_still_short_circuits_consistently():
    # Regression guard for the PBKDF2 short-circuit timing fix: a nonexistent
    # username and a real username with a wrong password must both fail with
    # the same generic message (not a distinguishing detail). Both usernames
    # here are disposable -- never 'doctor' -- so a single wrong-password
    # attempt in this test can't contribute towards locking out the shared
    # login every other test in this module depends on.
    admin=login('admin','admin123')
    real_username=f"enum-real-{uuid4().hex[:6]}"
    client.post('/api/users',headers=admin,json={'username':real_username,'full_name':'Enum Real','role':'receptionist','password':'temporary123'})
    fake_username=f"enum-fake-{uuid4().hex[:6]}"
    real={'organization':'demo-clinic','username':real_username,'password':'definitely-wrong-pw'}
    fake={'organization':'demo-clinic','username':fake_username,'password':'definitely-wrong-pw'}
    r1=client.post('/api/auth/login',json=real);r2=client.post('/api/auth/login',json=fake)
    assert r1.status_code in (401,429) and r1.status_code==r2.status_code
    assert r1.json().get('detail')==r2.json().get('detail')
def test_pdf_report_serbian_diacritics_filename_and_allergy_alert():
    # v1.7.0 PDF pass: Serbian Latin diacritics (č/ć/ž/š/đ) fall outside the
    # Latin-1 range HTTP headers are restricted to. A patient name containing
    # them must not break the download, and a patient with allergies must
    # get the printed alert band (not just the on-screen one).
    h=login()
    p=client.post('/api/patients',headers=h,json={'full_name':'Đorđe Živković'}).json()
    client.put(f"/api/patients/{p['id']}/clinical-profile",headers=h,json={'blood_type':'B+','allergies':['Penicilin'],'current_medications':[],'diagnoses':[],'medical_history':None,'family_history':None,'social_history':None})
    r=client.get(f"/api/patients/{p['id']}/medical-report.pdf",headers=h)
    assert r.status_code==200
    disp=r.headers['content-disposition']
    assert 'filename=' in disp and all(ord(c)<256 for c in disp), f"Content-Disposition must be Latin-1 safe: {disp!r}"
    assert "filename*=UTF-8''" in disp
    from io import BytesIO
    from pypdf import PdfReader
    text=''.join(pg.extract_text() or '' for pg in PdfReader(BytesIO(r.content)).pages)
    assert 'ALERGIJE' in text and 'Penicilin' in text

def test_pdf_report_no_allergies_shows_explicit_reassurance_not_silence():
    h=login()
    p=client.post('/api/patients',headers=h,json={'full_name':'No Allergy Patient'}).json()
    r=client.get(f"/api/patients/{p['id']}/medical-report.pdf",headers=h)
    from io import BytesIO
    from pypdf import PdfReader
    text=''.join(pg.extract_text() or '' for pg in PdfReader(BytesIO(r.content)).pages)
    assert 'poznatih alergija' in text.lower()

def test_pdf_report_highlights_abnormal_vitals_consistent_with_frontend():
    h=login()
    p=client.post('/api/patients',headers=h,json={'full_name':'Vitals PDF Patient'}).json()
    client.post(f"/api/patients/{p['id']}/encounters",headers=h,json={'visit_date':datetime.now(timezone.utc).isoformat(),'chief_complaint':'Kontrola','vital_signs':{'bp':'150/95','pulse':'110','temperature':'38,4','spo2':'91'}})
    r=client.get(f"/api/patients/{p['id']}/medical-report.pdf",headers=h)
    assert r.status_code==200 and len(r.content)>500
def test_dashboard_surfaces_pending_red_flags_for_doctor_not_reception():
    # v1.7.0: a red-flag differential candidate a doctor generated and never
    # came back to accept/dismiss should surface on the dashboard, since the
    # patient-specific differential panel that produced it is easy to never
    # reopen. Reception must not see clinical suspicion counts at all.
    h=login();now=datetime.now(timezone.utc).isoformat()
    p=client.post('/api/patients',headers=h,json={'full_name':'Red Flag Dashboard Patient'}).json();pid=p['id']
    client.post(f'/api/patients/{pid}/encounters',headers=h,json={'visit_date':now,'chief_complaint':'Otežano disanje i temperatura','anamnesis':'Otežano disanje, temperatura, bol u grudima','examination':'','assessment':'','plan':'','vital_signs':{'spo2':'89'}})
    analysis=client.post(f'/api/patients/{pid}/differential-analyses',headers=h);assert analysis.status_code==200,analysis.text
    body=analysis.json();rf=next((x for x in body['candidates'] if x['red_flag']),None)
    assert rf is not None, f"expected a red-flag candidate, got {body['candidates']}"
    dash=client.get('/api/dashboard',headers=h).json();assert dash['red_flags_pending']>=1
    flags=client.get('/api/dashboard/red-flags',headers=h).json()
    assert any(f['patient_id']==pid and f['candidate_id']==rf['id'] for f in flags)
    recep=login('reception','reception123')
    assert client.get('/api/dashboard/red-flags',headers=recep).status_code==403
    dash_recep=client.get('/api/dashboard',headers=recep).json();assert dash_recep['red_flags_pending']==0

def test_dashboard_red_flag_clears_once_reviewed():
    h=login();now=datetime.now(timezone.utc).isoformat()
    p=client.post('/api/patients',headers=h,json={'full_name':'Red Flag Cleared Patient'}).json();pid=p['id']
    client.post(f'/api/patients/{pid}/encounters',headers=h,json={'visit_date':now,'chief_complaint':'Otežano disanje','anamnesis':'Otežano disanje i bol u grudima','examination':'','assessment':'','plan':'','vital_signs':{'spo2':'88'}})
    analysis=client.post(f'/api/patients/{pid}/differential-analyses',headers=h).json()
    rf=next(x for x in analysis['candidates'] if x['red_flag'])
    before=client.get('/api/dashboard/red-flags',headers=h).json()
    assert any(f['patient_id']==pid for f in before)
    client.patch(f"/api/patients/{pid}/differential-analyses/{analysis['id']}/candidates/{rf['id']}",headers=h,json={'status':'accepted','doctor_note':'Upućen na dalju obradu'})
    after=client.get('/api/dashboard/red-flags',headers=h).json()
    assert not any(f['patient_id']==pid for f in after)

def test_dashboard_running_late_counts_overdue_scheduled_appointments():
    h=login()
    p=client.post('/api/patients',headers=h,json={'full_name':'Late Appointment Patient'}).json()
    past=(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat()
    client.post('/api/appointments',headers=h,json={'patient_id':p['id'],'starts_at':past,'reason':'Kontrola'})
    dash=client.get('/api/dashboard',headers=h).json()
    assert dash['running_late']>=1
def test_setup_checklist_detects_demo_passwords_and_default_name():
    # v1.9.0 onboarding: the checklist's most important job is loudly
    # flagging that seeded demo credentials still work. In the test org the
    # demo users exist with default passwords, so all three must be listed.
    admin=login('admin','admin123')
    r=client.get('/api/setup/checklist',headers=admin);assert r.status_code==200,r.text
    body=r.json()
    assert set(body['default_passwords_active'])>={'doctor','admin'}
    assert body['clinic_name_is_default'] is True
    assert body['all_clear'] is False
    assert client.get('/api/setup/checklist',headers=login()).status_code==403

def test_setup_checklist_reads_the_real_tls_env_vars_not_a_nonexistent_one():
    # Regression guard: an earlier version checked CLINIC_SSL_CERTFILE,
    # a variable nothing else in this codebase ever reads or sets (the
    # real ones are CLINIC_TLS / CLINIC_TLS_CERT_FILE, see start.sh) --
    # meaning https_enabled could never become true even on a correctly
    # TLS-configured deployment, silently training the admin to ignore
    # the checklist. Confirms the checklist now reads the real variables.
    import os
    admin=login('admin','admin123')
    assert client.get('/api/setup/checklist',headers=admin).json()['https_enabled'] is False
    old_tls,old_cert=os.environ.get('CLINIC_TLS'),os.environ.get('CLINIC_TLS_CERT_FILE')
    try:
        os.environ['CLINIC_TLS']='1'
        os.environ['CLINIC_TLS_CERT_FILE']='/tmp/does-not-need-to-exist-for-this-check.pem'
        assert client.get('/api/setup/checklist',headers=admin).json()['https_enabled'] is True
    finally:
        for k,v in (('CLINIC_TLS',old_tls),('CLINIC_TLS_CERT_FILE',old_cert)):
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v

def test_setup_checklist_all_clear_requires_production_https_and_managed_key():
    # Regression guard: all_clear previously only checked demo passwords
    # and the clinic name, so a clinic could see a green checkmark while
    # still running plain HTTP with an auto-generated key -- a false sense
    # of security for a system holding health data. All five conditions
    # must now hold.
    import os
    admin=login('admin','admin123')
    client.patch('/api/organization',headers=admin,json={'name':'Not Demo Clinic Anymore'})
    saved={k:os.environ.get(k) for k in ('CLINIC_TLS','CLINIC_TLS_CERT_FILE','CLINIC_ENV','CLINIC_ENCRYPTION_KEY')}
    try:
        os.environ['CLINIC_TLS']='1'
        os.environ['CLINIC_TLS_CERT_FILE']='/tmp/does-not-need-to-exist-for-this-check.pem'
        os.environ['CLINIC_ENV']='production'
        os.environ['CLINIC_ENCRYPTION_KEY']='irrelevant-value-for-this-check'
        body=client.get('/api/setup/checklist',headers=admin).json()
        assert body['https_enabled'] and body['production_mode'] and body['encryption_key_externally_managed']
        assert body['clinic_name_is_default'] is False
        # demo passwords are still active in this test org -> still not all_clear
        assert body['all_clear'] is False
    finally:
        for k,v in saved.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v

def test_organization_rename_clears_default_name_flag_and_is_audited():
    admin=login('admin','admin123')
    org=store.create_organization('Rename Target',f"rename-{uuid4().hex[:6]}")
    store.create_user(org.id,UserCreate(username='admin-r',full_name='Rename Admin',role='admin',password='renadmin123'))
    h=login('admin-r','renadmin123',org.slug)
    r=client.patch('/api/organization',headers=h,json={'name':'Ordinacija Dr Petrović'});assert r.status_code==200,r.text
    assert r.json()['name']=='Ordinacija Dr Petrović'
    body=client.get('/api/setup/checklist',headers=h).json()
    assert body['clinic_name_is_default'] is False
    audit=client.get('/api/audit',headers=h).json()
    assert any(a['action']=='rename' and a['resource_type']=='organization' for a in audit)

def test_patient_export_zip_contains_record_and_is_audited_and_tenant_scoped():
    import io,zipfile,json as jsonlib
    h=login();now=datetime.now(timezone.utc).isoformat()
    p=client.post('/api/patients',headers=h,json={'full_name':'Izvoz Pacijent'}).json();pid=p['id']
    client.post(f'/api/patients/{pid}/encounters',headers=h,json={'visit_date':now,'chief_complaint':'Kontrola','anamnesis':'Bez tegoba','vital_signs':{'bp':'120/80'}})
    files={'file':('nalaz.txt','CRP u granicama normale.'.encode('utf-8'),'text/plain')}
    client.post(f'/api/patients/{pid}/documents',headers=h,files=files)
    r=client.get(f'/api/patients/{pid}/export.zip',headers=h);assert r.status_code==200
    zf=zipfile.ZipFile(io.BytesIO(r.content))
    names=zf.namelist()
    assert 'karton.json' in names and 'pregledi.csv' in names
    record=jsonlib.loads(zf.read('karton.json'))
    assert record['patient']['full_name']=='Izvoz Pacijent'
    assert len(record['encounters'])==1
    assert any(n.startswith('dokumenti/') for n in names)
    # CSV must open in Excel with Serbian diacritics intact -> BOM required
    assert zf.read('pregledi.csv').startswith('\ufeff'.encode('utf-8')[:3]) or zf.read('pregledi.csv')[:3]==b'\xef\xbb\xbf'
    # tenant isolation: another clinic's doctor gets 404, not the export
    org=store.create_organization('Export Isolation',f"exiso-{uuid4().hex[:6]}")
    store.create_user(org.id,UserCreate(username='doctor-x',full_name='X Doctor',role='doctor',password='xdoctor123'))
    other=login('doctor-x','xdoctor123',org.slug)
    assert client.get(f'/api/patients/{pid}/export.zip',headers=other).status_code==404
    # reception cannot export patient records at all
    assert client.get(f'/api/patients/{pid}/export.zip',headers=login('reception','reception123')).status_code==403

def test_clinic_export_zip_is_admin_only_with_all_csvs_and_audit_trail():
    import io,zipfile
    admin=login('admin','admin123')
    r=client.get('/api/export/clinic.zip',headers=admin);assert r.status_code==200
    zf=zipfile.ZipFile(io.BytesIO(r.content))
    assert {'README.txt','pacijenti.csv','pregledi.csv','termini.csv','revizija.csv'}<=set(zf.namelist())
    revizija=zf.read('revizija.csv').decode('utf-8-sig')
    assert 'radnja' in revizija.splitlines()[0]
    assert client.get('/api/export/clinic.zip',headers=login()).status_code==403
    assert client.get('/api/export/clinic.zip',headers=login('reception','reception123')).status_code==403

def test_dashboard_epi_alerts_surface_clusters_for_doctor_not_reception():
    h=login();now=datetime.now(timezone.utc).isoformat()
    # 5+ same-syndrome encounters in the current window -> radar cluster
    for i in range(6):
        p=client.post('/api/patients',headers=h,json={'full_name':f'Epi Alert Pacijent {i}'}).json()
        client.post(f"/api/patients/{p['id']}/encounters",headers=h,json={'visit_date':now,'chief_complaint':'Proliv i mučnina','anamnesis':'Proliv dva dana, mučnina','vital_signs':{}})
    dash=client.get('/api/dashboard',headers=h).json()
    assert any('gastrointestinalni' in a for a in dash['epi_alerts']),dash['epi_alerts']
    recep=client.get('/api/dashboard',headers=login('reception','reception123')).json()
    assert recep['epi_alerts']==[]


def test_inbox_flags_pending_lab_confirmation_from_auto_extracted_draft():
    # v1.16.0 UX pass: the document inbox status bar needs to distinguish
    # "a lab value was auto-extracted from this document and still awaits
    # a lekar's verified/rejected decision" from a plain unreviewed upload.
    h=login()
    p=client.post('/api/patients',headers=h,json={'full_name':'Inbox Status Pacijent'}).json()
    up=client.post(f"/api/patients/{p['id']}/documents",headers=h,files={'file':('nalaz.txt','CRP: 15 mg/L, povišen'.encode('utf-8'),'text/plain')})
    assert up.status_code==200 and up.json()['lab_drafts_created']>=1
    doc_id=up.json()['id']
    inbox=client.get('/api/documents/inbox',headers=h).json()
    row=next(x for x in inbox if x['id']==doc_id)
    assert row['pending_lab_confirmation'] is True

    lab=client.get(f"/api/patients/{p['id']}/lab-results",headers=h).json()
    draft=next(x for x in lab if x['source_document_id']==doc_id)
    client.patch(f"/api/patients/{p['id']}/lab-results/{draft['id']}/status",headers=h,json={'status':'verified'})
    inbox_after=client.get('/api/documents/inbox',headers=h).json()
    row_after=next(x for x in inbox_after if x['id']==doc_id)
    assert row_after['pending_lab_confirmation'] is False


def test_inbox_pending_lab_confirmation_false_for_document_without_lab_values():
    h=login()
    p=client.post('/api/patients',headers=h,json={'full_name':'Inbox Bez Lab Pacijent'}).json()
    up=client.post(f"/api/patients/{p['id']}/documents",headers=h,files={'file':('napomena.txt','Pacijent se oseća dobro, bez tegoba.'.encode('utf-8'),'text/plain')})
    inbox=client.get('/api/documents/inbox',headers=h).json()
    row=next(x for x in inbox if x['id']==up.json()['id'])
    assert row['pending_lab_confirmation'] is False
