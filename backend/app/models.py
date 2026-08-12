from __future__ import annotations
import re
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

Role = Literal["doctor", "receptionist", "admin"]

def _check_password_strength(password: str) -> str:
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one letter and one digit")
    if password.lower() in ("password", "password1", "12345678", "qwertyui"):
        raise ValueError("This password is too common")
    return password

class OrganizationRecord(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime
    active: bool = True

class UserRecord(BaseModel):
    id: str
    organization_id: str
    username: str
    full_name: str
    role: Role
    password_hash: str = Field(repr=False)
    created_at: datetime
    active: bool = True
    must_change_password: bool = False
    mfa_enabled: bool = False

class LoginRequest(BaseModel):
    organization: str = Field(min_length=2, max_length=80)
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=4, max_length=200)

class PasswordChange(BaseModel):
    current_password: str = Field(min_length=4, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)
    _strength = field_validator("new_password")(lambda cls, v: _check_password_strength(v))


class MfaCode(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class MfaLoginComplete(MfaCode):
    challenge: str = Field(min_length=32, max_length=200)

class MfaAdminReset(BaseModel):
    """Deliberate, auditable lost-device recovery by a different admin."""
    reason: str = Field(min_length=12, max_length=500)


class MfaSetupResponse(BaseModel):
    secret: str = Field(repr=False)
    otpauth_uri: str = Field(repr=False)
    issuer: str
    account_name: str

class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=120)
    role: Role
    password: str = Field(min_length=8, max_length=200)
    _strength = field_validator("password")(lambda cls, v: _check_password_strength(v))

    @model_validator(mode="after")
    def _password_not_username(self):
        if self.password.lower() == self.username.lower():
            raise ValueError("Password cannot be the same as the username")
        return self

class UserStatusUpdate(BaseModel):
    active: bool

class AuditRecord(BaseModel):
    id: str; organization_id: str; occurred_at: datetime; user_id: str | None = None
    username: str; role: str; action: str; resource_type: str; resource_id: str | None = None; detail: str | None = None

class DocumentRecord(BaseModel):
    id: str; organization_id: str; patient_id: str; filename: str; media_type: str; uploaded_at: datetime
    size_bytes: int = 0; status: Literal["ready", "failed", "archived"] = "ready"; attention: bool = False
    extraction_method: Literal["text", "ocr"] = "text"; text: str = Field(repr=False)
    archived_at: datetime | None = None; archive_reason: str | None = None

class DocumentArchiveRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

class ClinicalProfile(BaseModel):
    blood_type: str | None = None
    allergies: list[str] = Field(default_factory=list)
    current_medications: list[str] = Field(default_factory=list)
    diagnoses: list[str] = Field(default_factory=list)
    medical_history: str | None = None
    family_history: str | None = None
    social_history: str | None = None

class PatientRecord(BaseModel):
    id: str; organization_id: str; full_name: str; date_of_birth: str | None = None; phone: str | None = None; email: str | None = None; created_at: datetime
    clinical_profile: ClinicalProfile = Field(default_factory=ClinicalProfile)

class ClinicalProfileUpdate(ClinicalProfile):
    pass

class EncounterCreate(BaseModel):
    visit_date: datetime
    chief_complaint: str = Field(min_length=2, max_length=500)
    anamnesis: str = Field(default='', max_length=8000)
    examination: str = Field(default='', max_length=8000)
    assessment: str = Field(default='', max_length=4000)
    plan: str = Field(default='', max_length=4000)
    vital_signs: dict[str, str] = Field(default_factory=dict)

class EncounterRecord(EncounterCreate):
    id: str
    organization_id: str
    patient_id: str
    clinician_id: str
    clinician_name: str
    created_at: datetime

class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120); date_of_birth: str | None = None; phone: str | None = None; email: str | None = None

class AppointmentRecord(BaseModel):
    id: str; organization_id: str; patient_id: str; starts_at: datetime; reason: str = Field(min_length=2, max_length=240)
    status: Literal["scheduled", "checked_in", "completed", "cancelled", "no_show"] = "scheduled"; notes: str | None = None; created_at: datetime
    clinician_id: str | None = None
    clinician_name: str | None = None
    room: str | None = None
    service_type: str | None = None
    duration_minutes: int = 20
    cancellation_reason: str | None = None

class AppointmentCreate(BaseModel):
    patient_id: str; starts_at: datetime; reason: str = Field(min_length=2, max_length=240); notes: str | None = None
    clinician_id: str | None = None
    room: str | None = Field(default=None, max_length=80)
    service_type: str | None = Field(default=None, max_length=80)
    duration_minutes: int = Field(default=20, ge=5, le=240)

class AppointmentReschedule(BaseModel):
    """Partial update for moving/resizing/reassigning an appointment. Every
    field is optional so the frontend calendar can send just what changed
    (e.g. a drag-to-reschedule only sends starts_at)."""
    starts_at: datetime | None = None
    clinician_id: str | None = None
    room: str | None = Field(default=None, max_length=80)
    service_type: str | None = Field(default=None, max_length=80)
    duration_minutes: int | None = Field(default=None, ge=5, le=240)

class AppointmentStatusUpdate(BaseModel):
    status: Literal["scheduled", "checked_in", "completed", "cancelled", "no_show"]
    cancellation_reason: str | None = Field(default=None, max_length=240)

class ClinicianSummary(BaseModel):
    id: str
    full_name: str

class WaitlistCreate(BaseModel):
    patient_id: str
    desired_service: str | None = Field(default=None, max_length=80)
    clinician_id: str | None = None
    preferred_note: str | None = Field(default=None, max_length=240)

class WaitlistEntry(BaseModel):
    id: str
    organization_id: str
    patient_id: str
    desired_service: str | None = None
    clinician_id: str | None = None
    preferred_note: str | None = None
    status: Literal["waiting", "scheduled", "cancelled"] = "waiting"
    created_at: datetime
    appointment_id: str | None = None

class WaitlistPromote(BaseModel):
    """Turns a waiting-list entry into a real, conflict-checked appointment."""
    starts_at: datetime
    duration_minutes: int = Field(default=20, ge=5, le=240)
    clinician_id: str | None = None
    room: str | None = Field(default=None, max_length=80)
    service_type: str | None = Field(default=None, max_length=80)

ReminderChannel = Literal["email", "sms", "viber"]
ReminderStatus = Literal["pending", "sent", "failed", "unconfigured", "cancelled"]

class Reminder(BaseModel):
    id: str
    organization_id: str
    appointment_id: str
    channel: ReminderChannel
    send_at: datetime
    status: ReminderStatus = "pending"
    created_at: datetime
    last_attempt_at: datetime | None = None
    error: str | None = None

class ChatRequest(BaseModel): question: str = Field(min_length=2, max_length=2000)

class TimelineItem(BaseModel):
    date: str | None = None; title: str; detail: str; category: Literal["diagnosis", "therapy", "lab", "procedure", "visit", "other"] = "other"; source: str | None = None

class GeneratedReport(BaseModel):
    id: str; organization_id: str; patient_id: str; title: str; generated_at: datetime; content: str; status: Literal["draft", "reviewed"] = "draft"

class PatientOverview(BaseModel):
    document_count: int; timeline_count: int; source_count: int; lab_result_count: int = 0; latest_document_at: datetime | None = None; readiness: Literal["empty", "limited", "ready"]


class LabResultCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    value: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    reference_range: str | None = Field(default=None, max_length=100)
    collected_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=1000)


class LabResultRecord(LabResultCreate):
    id: str
    organization_id: str
    patient_id: str
    created_at: datetime
    source_document_id: str | None = None
    status: Literal["draft", "verified", "rejected"] = "draft"
    abnormality: Literal["low", "high", "normal", "unknown"] = "unknown"


class LabResultStatusUpdate(BaseModel):
    status: Literal["verified", "rejected"]


class MedicationSafetyRequest(BaseModel):
    proposed_medications: list[str] = Field(default_factory=list, max_length=40)


class MedicationSafetyFinding(BaseModel):
    severity: Literal["critical", "high", "moderate"]
    type: Literal["allergy", "interaction", "duplicate"]
    medications: list[str] = Field(default_factory=list)
    message: str
    action: str


class MedicationSafetyCheck(BaseModel):
    reference_version: str
    checked_medications: list[str] = Field(default_factory=list)
    unrecognized_medications: list[str] = Field(default_factory=list)
    findings: list[MedicationSafetyFinding] = Field(default_factory=list)
    disclaimer: str

class DashboardOverview(BaseModel):
    appointments_today: int; checked_in: int; needs_attention: int; reports_this_week: int; total_patients: int
    red_flags_pending: int = 0
    running_late: int = 0
    epi_alerts: list[str] = Field(default_factory=list)

class PendingRedFlag(BaseModel):
    patient_id: str
    patient_name: str
    analysis_id: str
    candidate_id: str
    candidate_name: str
    match_score: int
    generated_at: datetime


class BriefingSection(BaseModel):
    title: str
    items: list[str] = Field(default_factory=list)

class PreVisitBriefing(BaseModel):
    id: str
    organization_id: str
    patient_id: str
    generated_at: datetime
    generated_by: str
    active_problems: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    recent_findings: list[str] = Field(default_factory=list)
    questions_to_verify: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    disclaimer: str = "AI-generated preparation only. Verify all items against source records and the patient interview."

class ScribeDraftRequest(BaseModel):
    transcript: str = Field(min_length=10, max_length=50000)
    mode: Literal['consultation', 'dictation'] = 'dictation'

class ScribeDraftRecord(BaseModel):
    id: str
    organization_id: str
    patient_id: str
    clinician_id: str
    clinician_name: str
    created_at: datetime
    mode: Literal['consultation', 'dictation']
    transcript: str = Field(repr=False)
    chief_complaint: str = ''
    anamnesis: str = ''
    examination: str = ''
    assessment: str = ''
    plan: str = ''
    medication_changes: list[str] = Field(default_factory=list)
    allergy_updates: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    source_map: dict[str, str] = Field(default_factory=dict)
    status: Literal['draft', 'approved', 'rejected'] = 'draft'
    updated_at: datetime | None = None
    approved_at: datetime | None = None
    encounter_id: str | None = None

class ScribeDraftUpdate(BaseModel):
    chief_complaint: str = Field(default='', max_length=500)
    anamnesis: str = Field(default='', max_length=8000)
    examination: str = Field(default='', max_length=8000)
    assessment: str = Field(default='', max_length=4000)
    plan: str = Field(default='', max_length=4000)
    medication_changes: list[str] = Field(default_factory=list)
    allergy_updates: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

class ScribeStatusUpdate(BaseModel):
    status: Literal['approved', 'rejected']
    create_encounter: bool = False
    visit_date: datetime | None = None


class DifferentialCandidate(BaseModel):
    id: str
    name: str
    category: str
    match_score: int = Field(ge=0, le=100)
    match_level: Literal["visoko", "srednje", "niže"]
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    red_flag: bool = False
    review_status: Literal["pending", "accepted", "dismissed"] = "pending"
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    doctor_note: str | None = None

class DifferentialAnalysis(BaseModel):
    id: str
    organization_id: str
    patient_id: str
    generated_at: datetime
    generated_by: str
    generated_from: dict[str, int]
    candidates: list[DifferentialCandidate] = Field(default_factory=list)
    epidemiology_context: list[str] = Field(default_factory=list)
    disclaimer: str


class DifferentialCandidateReview(BaseModel):
    status: Literal["accepted", "dismissed"]
    doctor_note: str | None = Field(default=None, max_length=1000)
    add_to_latest_scribe_draft: bool = False

class DifferentialReviewResult(BaseModel):
    analysis: DifferentialAnalysis
    scribe_draft_updated: bool = False


# -- finansijsko-administrativni modul ------------------------------------
# Sve novčane vrednosti su cele dinare (RSD nema fraktalne kovanice u
# svakodnevnom prometu već godinama), čuvane kao int da se izbegnu greške
# zaokruživanja koje bi float unosio u račune i dnevni promet.

PaymentMethod = Literal["gotovina", "kartica", "prenos"]
InvoiceStatus = Literal["draft", "issued", "paid", "cancelled"]

class ServiceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=140)
    price_rsd: int = Field(ge=0, le=10_000_000)
    category: str | None = Field(default=None, max_length=80)
    default_duration_minutes: int | None = Field(default=None, ge=5, le=240)

class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=140)
    price_rsd: int | None = Field(default=None, ge=0, le=10_000_000)
    category: str | None = Field(default=None, max_length=80)
    default_duration_minutes: int | None = Field(default=None, ge=5, le=240)
    active: bool | None = None

class ServiceRecord(BaseModel):
    id: str
    organization_id: str
    name: str
    price_rsd: int
    category: str | None = None
    default_duration_minutes: int | None = None
    active: bool = True
    created_at: datetime

class InvoiceLineItemInput(BaseModel):
    service_id: str | None = None
    description: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=100)
    unit_price_rsd: int = Field(ge=0, le=10_000_000)
    discount_percent: int = Field(default=0, ge=0, le=100)

class InvoiceLineItem(InvoiceLineItemInput):
    line_total_rsd: int

class InvoiceCreate(BaseModel):
    patient_id: str
    appointment_id: str | None = None
    line_items: list[InvoiceLineItemInput] = Field(min_length=1, max_length=50)
    discount_percent: int = Field(default=0, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=500)

class InvoiceRecord(BaseModel):
    id: str
    organization_id: str
    patient_id: str
    appointment_id: str | None = None
    invoice_number: str
    issued_by: str
    issued_by_name: str
    issued_at: datetime
    status: InvoiceStatus = "issued"
    line_items: list[InvoiceLineItem]
    subtotal_rsd: int
    discount_percent: int = 0
    total_rsd: int
    paid_rsd: int = 0
    balance_due_rsd: int
    notes: str | None = None
    cancellation_reason: str | None = None

class PaymentCreate(BaseModel):
    amount_rsd: int = Field(gt=0, le=10_000_000)
    method: PaymentMethod
    note: str | None = Field(default=None, max_length=200)

class PaymentRecord(BaseModel):
    id: str
    organization_id: str
    invoice_id: str
    amount_rsd: int
    method: PaymentMethod
    paid_at: datetime
    recorded_by: str
    recorded_by_name: str
    note: str | None = None

class InvoiceStatusUpdate(BaseModel):
    status: Literal["cancelled"]
    cancellation_reason: str = Field(min_length=2, max_length=300)

class DailyFinanceSummary(BaseModel):
    date: str
    invoices_issued: int
    revenue_collected_rsd: int
    revenue_by_method: dict[str, int]
    outstanding_new_rsd: int

class OutstandingInvoice(BaseModel):
    invoice_id: str
    invoice_number: str
    patient_id: str
    patient_name: str
    issued_at: datetime
    total_rsd: int
    paid_rsd: int
    balance_due_rsd: int
    days_outstanding: int
