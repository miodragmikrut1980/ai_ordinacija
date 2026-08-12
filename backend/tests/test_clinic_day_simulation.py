"""A synthetic, end-to-end day in one clinic.

This is deliberately an API-level workflow rather than a unit test. It uses
no real patient data and verifies that ordinary clinical work, document
review, archival, audit evidence, and role boundaries stay connected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from test_api import client, login


def test_synthetic_clinic_day_document_lifecycle_and_permissions():
    doctor = login()
    patient = client.post(
        "/api/patients", headers=doctor,
        json={"full_name": "SINTETICKI Pacijent — ne koristiti za lečenje", "date_of_birth": "1980-01-02"},
    )
    assert patient.status_code == 200, patient.text
    patient_id = patient.json()["id"]

    # Reception creates the scheduling entry; the doctor records care.
    starts_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    receptionist = login("reception", "reception123")
    scheduled = client.post("/api/appointments", headers=receptionist, json={
        "patient_id": patient_id, "starts_at": starts_at, "reason": "Kontrolni pregled",
    })
    assert scheduled.status_code == 200, scheduled.text
    assert client.patch(
        f"/api/appointments/{scheduled.json()['id']}/status", headers=receptionist,
        json={"status": "checked_in"},
    ).status_code == 200

    visit = client.post(f"/api/patients/{patient_id}/encounters", headers=doctor, json={
        "visit_date": datetime.now(timezone.utc).isoformat(), "chief_complaint": "Kontrola laboratorije",
        "anamnesis": "Sintetički testni podatak", "assessment": "Lekar proverava original nalaza",
        "plan": "Potvrditi rezultat prema originalu", "vital_signs": {"bp": "120/80"},
    })
    assert visit.status_code == 200, visit.text

    raw = "CRP 4 mg/L (referentno < 5) — sintetički nalaz".encode("utf-8")
    uploaded = client.post(
        f"/api/patients/{patient_id}/documents", headers=doctor,
        files={"file": ("synthetic-lab.txt", raw, "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["id"]

    original = client.get(f"/api/patients/{patient_id}/documents/{document_id}/original", headers=doctor)
    assert original.status_code == 200 and original.content == raw
    assert original.headers["cache-control"] == "no-store"
    assert client.get(f"/api/patients/{patient_id}/documents/{document_id}/original", headers=receptionist).status_code == 403

    archived = client.post(
        f"/api/patients/{patient_id}/documents/{document_id}/archive", headers=doctor,
        json={"reason": "Duplikat testnog nalaza; zadržan radi revizionog traga"},
    )
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert client.get(f"/api/patients/{patient_id}/documents?include_archived=false", headers=doctor).json() == []
    listed = client.get(f"/api/patients/{patient_id}/documents", headers=doctor).json()
    assert len(listed) == 1 and listed[0]["archive_reason"].startswith("Duplikat")
    # Archiving does not remove the source file; a clinician can still review it.
    assert client.get(f"/api/patients/{patient_id}/documents/{document_id}/original", headers=doctor).content == raw
    audit = client.get("/api/audit", headers=doctor).json()
    assert any(row["action"] == "archive" and row["resource_id"] == document_id for row in audit)
