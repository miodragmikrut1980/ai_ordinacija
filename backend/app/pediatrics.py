"""Specijalistički modul: pedijatrija.

Namerno NE sadrži WHO percentile rasta niti zvaničan raspored vakcinacije.
Oba zahtevaju autoritativne, ažurne tabele/kalendare koje ovaj sistem ne
može pouzdano da proveri niti da garantuje da su ažurne (raspored
vakcinacije se povremeno menja; pogrešan raspored u kliničkom alatu je
opasniji nego da ga uopšte nema). Umesto toga:

- Merenja rasta se evidentiraju i prikazuju kao trend kroz vreme (isti
  princip kao trend laboratorijskih nalaza) -- lekar vizuelno prati
  putanju rasta bez da mu sistem tvrdi na kom je percentilu dete.
- Vakcinacije se evidentiraju kao dnevnik onoga što je DATO (naziv, datum,
  serija) -- ne kao podsetnik za ono što bi TREBALO da bude dato.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .deps import current_user, patient_or_404, require_roles
from .models import (
    GrowthMeasurementCreate, GrowthMeasurementRecord, PediatricProfileRecord,
    PediatricProfileUpdate, VaccinationCreate, VaccinationRecord,
)
from .state import store

router = APIRouter()


@router.put('/api/patients/{patient_id}/pediatric-profile', response_model=PediatricProfileRecord)
def update_pediatric_profile(patient_id: str, payload: PediatricProfileUpdate, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    patient_or_404(user, patient_id)
    r = store.upsert_pediatric_profile(user.organization_id, patient_id, payload)
    store.audit(user, 'update', 'pediatric_profile', patient_id, payload.guardian_name or '')
    return r


@router.get('/api/patients/{patient_id}/pediatric-profile', response_model=PediatricProfileRecord | None)
def get_pediatric_profile(patient_id: str, user=Depends(current_user)):
    patient_or_404(user, patient_id)
    return store.get_pediatric_profile(user.organization_id, patient_id)


@router.post('/api/patients/{patient_id}/growth-measurements', response_model=GrowthMeasurementRecord)
def add_growth_measurement(patient_id: str, payload: GrowthMeasurementCreate, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    if payload.height_cm is None and payload.weight_kg is None and payload.head_circumference_cm is None:
        raise HTTPException(422, 'At least one of height_cm, weight_kg, or head_circumference_cm is required')
    r = store.add_growth_measurement(user.organization_id, patient_id, payload)
    store.audit(user, 'create', 'growth_measurement', r.id, patient_id)
    return r


@router.get('/api/patients/{patient_id}/growth-measurements', response_model=list[GrowthMeasurementRecord])
def list_growth_measurements(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_growth_measurements(user.organization_id, patient_id)


@router.post('/api/patients/{patient_id}/vaccinations', response_model=VaccinationRecord)
def add_vaccination(patient_id: str, payload: VaccinationCreate, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    r = store.add_vaccination(user.organization_id, patient_id, payload, recorded_by_name=user.full_name)
    store.audit(user, 'create', 'vaccination', r.id, payload.vaccine_name)
    return r


@router.get('/api/patients/{patient_id}/vaccinations', response_model=list[VaccinationRecord])
def list_vaccinations(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_vaccinations(user.organization_id, patient_id)
