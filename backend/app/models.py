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
    status: Literal["scheduled", "checked_in", "completed", "cancelled"] = "scheduled"; notes: str | None = None; created_at: datetime

class AppointmentCreate(BaseModel):
    patient_id: str; starts_at: datetime; reason: str = Field(min_length=2, max_length=240); notes: str | None = None

class AppointmentStatusUpdate(BaseModel): status: Literal["scheduled", "checked_in", "completed", "cancelled"]
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
