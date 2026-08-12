from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import require_roles
from ..epidemiology import build_radar
from ..state import store

router = APIRouter()


@router.get('/api/epidemiology/radar')
def epidemiology_radar(days: int = 7, user=Depends(require_roles('doctor', 'admin'))):
    if days not in (7, 14, 30):
        raise HTTPException(422, 'Period mora biti 7, 14 ili 30 dana')
    encounters = store.list_all_encounters(user.organization_id)
    documents = store.list_documents(user.organization_id)
    result = build_radar(encounters, documents, days)
    store.audit(user, 'view', 'epidemiology_radar', detail=f'{days} dana')
    return result
