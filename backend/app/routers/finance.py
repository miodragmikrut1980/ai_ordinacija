"""Finansijsko-administrativni modul: cenovnik, računi, plaćanja, dnevni
promet i dugovanja.

Fiskalizacija -- honestly scoped: Serbian fiscalization (e-fiskalizacija /
SUF) requires a certified ESIR (electronic fiscal cash register) or a
licensed virtual-fiscalization API integration approved by the Poreska
uprava. This module cannot self-certify or fake a fiscal receipt -- doing
so would be a real legal problem for a clinic that relied on it, the same
reasoning that keeps app/notifications.py from pretending an unconfigured
SMS gateway sent a message. What this module DOES provide, deliberately,
is the precursor a fiscalization integration needs: gapless sequential
invoice numbering per organization per year (see PersistentStore.
_next_invoice_number), structured line items with per-item price and
discount, and a stable place to plug in a real ESIR/virtual-fiskalizacija
client later (issue_invoice would call out to it after the local invoice
is durably recorded).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..deps import current_user, patient_or_404, require_roles
from ..models import (
    DailyFinanceSummary, InvoiceCreate, InvoiceRecord, InvoiceStatusUpdate,
    OutstandingInvoice, PaymentCreate, PaymentRecord, ServiceCreate,
    ServiceRecord, ServiceUpdate,
)
from ..state import store

router = APIRouter()


# -- cenovnik -------------------------------------------------------------

@router.post('/api/finance/services', response_model=ServiceRecord)
def create_service(payload: ServiceCreate, user=Depends(require_roles('admin'))):
    r = store.create_service(user.organization_id, payload)
    store.audit(user, 'create', 'service', r.id, f'{payload.name} — {payload.price_rsd} RSD')
    return r


@router.get('/api/finance/services', response_model=list[ServiceRecord])
def list_services(include_inactive: bool = False, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    return store.list_services(user.organization_id, include_inactive=include_inactive)


@router.patch('/api/finance/services/{service_id}', response_model=ServiceRecord)
def update_service(service_id: str, payload: ServiceUpdate, user=Depends(require_roles('admin'))):
    existing = store.get_service(service_id)
    if not existing or existing.organization_id != user.organization_id:
        raise HTTPException(404, 'Service not found')
    r = store.update_service(user.organization_id, service_id, payload.model_dump(exclude_unset=True))
    store.audit(user, 'update', 'service', service_id, r.name)
    return r


# -- računi -------------------------------------------------------------

@router.post('/api/finance/invoices', response_model=InvoiceRecord)
def create_invoice(payload: InvoiceCreate, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    patient_or_404(user, payload.patient_id)
    r = store.create_invoice(user.organization_id, payload, issued_by=user.id, issued_by_name=user.full_name)
    store.audit(user, 'create', 'invoice', r.id, f'{r.invoice_number} — {r.total_rsd} RSD')
    return r


@router.get('/api/finance/invoices', response_model=list[InvoiceRecord])
def list_invoices(patient_id: str | None = None, status: str | None = None, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    return store.list_invoices(user.organization_id, patient_id=patient_id, status=status)


@router.get('/api/finance/invoices/{invoice_id}', response_model=InvoiceRecord)
def get_invoice(invoice_id: str, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    r = store.get_invoice(user.organization_id, invoice_id)
    if not r:
        raise HTTPException(404, 'Invoice not found')
    return r


@router.patch('/api/finance/invoices/{invoice_id}/status', response_model=InvoiceRecord)
def cancel_invoice(invoice_id: str, payload: InvoiceStatusUpdate, user=Depends(require_roles('admin'))):
    r = store.cancel_invoice(user.organization_id, invoice_id, payload.cancellation_reason)
    if not r:
        raise HTTPException(404, 'Invoice not found')
    store.audit(user, 'cancel', 'invoice', invoice_id, payload.cancellation_reason)
    return r


@router.post('/api/finance/invoices/{invoice_id}/payments', response_model=PaymentRecord)
def record_payment(invoice_id: str, payload: PaymentCreate, user=Depends(require_roles('receptionist', 'admin'))):
    invoice = store.get_invoice(user.organization_id, invoice_id)
    if not invoice:
        raise HTTPException(404, 'Invoice not found')
    if invoice.status == 'cancelled':
        raise HTTPException(409, {'error': 'invoice_cancelled', 'message': 'Ne može se evidentirati uplata na otkazan račun.'})
    if payload.amount_rsd > invoice.balance_due_rsd:
        raise HTTPException(422, {'error': 'overpayment', 'message': f'Iznos prelazi preostali dug od {invoice.balance_due_rsd} RSD.'})
    r = store.record_payment(user.organization_id, invoice_id, payload, recorded_by=user.id, recorded_by_name=user.full_name)
    store.audit(user, 'record_payment', 'invoice', invoice_id, f'{payload.amount_rsd} RSD — {payload.method}')
    return r


@router.get('/api/finance/invoices/{invoice_id}/payments', response_model=list[PaymentRecord])
def list_invoice_payments(invoice_id: str, user=Depends(require_roles('doctor', 'receptionist', 'admin'))):
    invoice = store.get_invoice(user.organization_id, invoice_id)
    if not invoice:
        raise HTTPException(404, 'Invoice not found')
    return store.list_payments(user.organization_id, invoice_id=invoice_id)


# -- izveštaji -------------------------------------------------------------

@router.get('/api/finance/daily-summary', response_model=DailyFinanceSummary)
def daily_summary(date: str | None = None, user=Depends(require_roles('receptionist', 'admin'))):
    day = date or datetime.now(timezone.utc).date().isoformat()
    return store.daily_finance_summary(user.organization_id, day)


@router.get('/api/finance/outstanding', response_model=list[OutstandingInvoice])
def outstanding_invoices(user=Depends(require_roles('receptionist', 'admin'))):
    return store.list_outstanding_invoices(user.organization_id)
