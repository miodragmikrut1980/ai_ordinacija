from __future__ import annotations

from datetime import datetime, date
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..deps import patient_or_404, require_roles
from ..differential import build_differential
from ..epidemiology import build_radar
from ..models import (
    DifferentialAnalysis, DifferentialCandidateReview, DifferentialReviewResult,
    ScribeDraftRequest, ScribeDraftUpdate, ScribeStatusUpdate,
)
from ..state import ai, store

router = APIRouter()


def _generate_differential(patient_id: str, user):
    patient = patient_or_404(user, patient_id)
    documents = store.list_documents(user.organization_id, patient_id)
    encounters = store.list_encounters(user.organization_id, patient_id)
    radar = build_radar(store.list_all_encounters(user.organization_id), store.list_documents(user.organization_id), 30)
    payload = build_differential(patient, documents, encounters, radar)
    payload.pop('patient_id', None)
    record = store.add_differential_analysis(user.organization_id, patient_id, user, payload)
    store.audit(user, 'generate', 'differential_analysis', record.id, f"{len(record.candidates)} kandidata")
    return record


@router.get('/api/patients/{patient_id}/differential-analysis', response_model=DifferentialAnalysis)
def differential_analysis(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    return _generate_differential(patient_id, user)


@router.post('/api/patients/{patient_id}/differential-analyses', response_model=DifferentialAnalysis)
def create_differential_analysis(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    return _generate_differential(patient_id, user)


@router.get('/api/patients/{patient_id}/differential-analyses', response_model=list[DifferentialAnalysis])
def differential_history(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_differential_analyses(user.organization_id, patient_id)


@router.patch('/api/patients/{patient_id}/differential-analyses/{analysis_id}/candidates/{candidate_id}', response_model=DifferentialReviewResult)
def review_differential_candidate(patient_id: str, analysis_id: str, candidate_id: str, payload: DifferentialCandidateReview, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    reviewed = store.review_differential_candidate(user.organization_id, patient_id, analysis_id, candidate_id, user, payload.status, payload.doctor_note)
    if not reviewed:
        raise HTTPException(404, 'Analiza ili kandidat nisu pronađeni')
    analysis, candidate = reviewed
    updated = False
    if payload.status == 'accepted' and payload.add_to_latest_scribe_draft:
        updated = store.append_candidate_to_latest_scribe(user.organization_id, patient_id, candidate)
    store.audit(user, payload.status, 'differential_candidate', candidate.id, f'{candidate.name} · dodat u nacrt: {updated}')
    return DifferentialReviewResult(analysis=analysis, scribe_draft_updated=updated)


@router.post('/api/patients/{patient_id}/pre-visit-briefings')
async def generate_pre_visit_briefing(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient = patient_or_404(user, patient_id)
    payload = await ai.pre_visit_briefing(patient, store.list_documents(user.organization_id, patient_id), store.list_encounters(user.organization_id, patient_id))
    record = store.add_briefing(user.organization_id, patient_id, user, payload)
    store.audit(user, 'generate', 'pre_visit_briefing', record.id, patient.full_name)
    return record


@router.get('/api/patients/{patient_id}/pre-visit-briefings')
def pre_visit_briefings(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_briefings(user.organization_id, patient_id)


def _calc_age(dob: str | None) -> int | None:
    """Mirrors the frontend's calcAge() in web/static/app.js so the printed
    report and the on-screen banner never disagree about a patient's age."""
    if not dob:
        return None
    try:
        b = date.fromisoformat(dob)
    except ValueError:
        return None
    today = datetime.now().date()
    years = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return years if 0 <= years < 130 else None


def _now_local_label() -> str:
    return datetime.now().strftime('%d.%m.%Y. %H:%M')


_TRANSLIT = str.maketrans({'č': 'c', 'ć': 'c', 'ž': 'z', 'š': 's', 'đ': 'dj', 'Č': 'C', 'Ć': 'C', 'Ž': 'Z', 'Š': 'S', 'Đ': 'Dj'})


def _ascii_filename(name: str, fallback: str) -> str:
    """HTTP headers are Latin-1 only, and Serbian Latin diacritics
    (č/ć/ž/š/đ) fall outside that range -- passing them straight into
    Content-Disposition would raise an encoding error and break the download
    outright, not just show mojibake. Ship a transliterated ASCII filename
    as the fallback and the real UTF-8 name via RFC 5987 filename* for
    browsers that use it."""
    ascii_name = name.translate(_TRANSLIT)
    ascii_name = ''.join(c for c in ascii_name if c.isascii() and (c.isalnum() or c in '-.')).strip('-')
    return ascii_name or fallback


def _vital_flag(key: str, raw: str) -> bool:
    """Mirrors the frontend's vitalsBadges() abnormal thresholds (see
    web/static/app.js) so the printed report highlights the same values the
    lekar already sees flagged on screen -- a PDF that silently dropped the
    highlighting a doctor already trusts would be a regression, not a
    simplification."""
    import re
    m = re.search(r'-?\d+(?:[.,]\d+)?', raw or '')
    if not m:
        return False
    val = float(m.group(0).replace(',', '.'))
    if key == 'pulse':
        return val > 100 or val < 50
    if key == 'temperature':
        return val >= 37.5 or val < 35
    if key == 'spo2':
        return val < 94
    if key == 'bp':
        bp = re.match(r'(\d{2,3})\s*/\s*(\d{2,3})', raw or '')
        if not bp:
            return False
        sys_, dia = int(bp.group(1)), int(bp.group(2))
        return sys_ >= 140 or dia >= 90 or sys_ < 90
    return False


@router.get('/api/patients/{patient_id}/medical-report.pdf')
def medical_report_pdf(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    def _safe(value) -> str:
        # reportlab's Paragraph interprets a small set of markup tags
        # ('<b>', '<font ...>', etc). Any user-entered clinical text can
        # contain a bare '<' as ordinary punctuation (e.g. "Bol < 3/10"),
        # which is not valid markup and crashes PDF generation outright
        # rather than degrading gracefully -- this happened in testing with
        # completely ordinary-looking clinical shorthand, not just a
        # deliberately crafted payload. Every value that reaches a
        # Paragraph() must go through this first.
        return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    VITAL_LABELS = {'bp': 'TA (mmHg)', 'pulse': 'Puls (/min)', 'temperature': 'Temperatura (\u00b0C)', 'spo2': 'SpO\u2082 (%)'}

    patient = patient_or_404(user, patient_id)
    org = store.organization_by_id(user.organization_id)
    encounters = sorted(store.list_encounters(user.organization_id, patient_id), key=lambda e: e.visit_date, reverse=True)
    cp = patient.clinical_profile
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=42)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ClinicTitle', parent=styles['Title'], alignment=TA_CENTER, fontSize=18, spaceAfter=2))
    styles.add(ParagraphStyle(name='ReportSubtitle', parent=styles['Normal'], alignment=TA_CENTER, fontSize=9, textColor=colors.HexColor('#5a7a77'), spaceAfter=10))
    styles.add(ParagraphStyle(name='SectionHead', parent=styles['Heading2'], fontSize=12, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor('#102d32')))
    styles.add(ParagraphStyle(name='AllergyAlert', parent=styles['BodyText'], fontSize=11, textColor=colors.HexColor('#8c2f26'), leading=15))
    styles.add(ParagraphStyle(name='Meta', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#7c918e'), alignment=TA_RIGHT))

    generated_at = _now_local_label()
    story = [
        Paragraph(_safe(org.name), styles['ClinicTitle']),
        Paragraph('Sažetak medicinskog kartona &mdash; zahteva proveru lekara', styles['ReportSubtitle']),
    ]

    # Allergy alert band: printed medical summaries get handed to other
    # clinicians, pharmacies, or the patient themselves -- allergies are the
    # single fact most worth making impossible to miss on a printed page,
    # the same reasoning behind the on-screen safety strip in v1.7.0.
    if cp.allergies:
        alert_table = Table([[Paragraph(f"\u26a0 ALERGIJE: {_safe(', '.join(cp.allergies))}", styles['AllergyAlert'])]], colWidths=[517])
        alert_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fbeaea')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e0a39c')),
            ('PADDING', (0, 0), (-1, -1), 9),
        ]))
        story += [alert_table, Spacer(1, 12)]
    else:
        story += [Paragraph('Bez poznatih alergija.', styles['BodyText']), Spacer(1, 10)]

    age = _calc_age(patient.date_of_birth)
    dob_line = patient.date_of_birth or 'Nije evidentirano'
    if age is not None:
        dob_line = f'{dob_line} ({age} god.)'
    demographics = [
        ['Pacijent', patient.full_name],
        ['Datum rođenja', dob_line],
        ['Krvna grupa', cp.blood_type or 'Nije evidentirano'],
        ['Telefon', patient.phone or 'Nije evidentirano'],
        ['E-mail', patient.email or 'Nije evidentirano'],
    ]
    t = Table(demographics, colWidths=[110, 407])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#eef3f7')), ('GRID', (0, 0), (-1, -1), .4, colors.grey), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('PADDING', (0, 0), (-1, -1), 7), ('FONTSIZE', (0, 0), (-1, -1), 9)]))
    story += [t, Spacer(1, 10)]

    story.append(Paragraph('Klinički profil', styles['SectionHead']))
    for title, value in [('Trenutne terapije', ', '.join(cp.current_medications) or 'Nije evidentirano'), ('Dijagnoze', ', '.join(cp.diagnoses) or 'Nije evidentirano'), ('Lična anamneza', cp.medical_history or 'Nije evidentirano'), ('Porodična anamneza', cp.family_history or 'Nije evidentirano'), ('Socijalna anamneza', cp.social_history or 'Nije evidentirano')]:
        story += [Paragraph(f'<b>{title}:</b> {_safe(value)}', styles['BodyText']), Spacer(1, 4)]

    story.append(Paragraph(f'Strukturisani pregledi ({len(encounters)})', styles['SectionHead']))
    if not encounters:
        story.append(Paragraph('Nema evidentiranih strukturisanih pregleda.', styles['BodyText']))
    for e in encounters:
        block = [Paragraph(f"{e.visit_date.strftime('%d.%m.%Y. %H:%M')} &middot; {_safe(e.chief_complaint)}", styles['Heading3']),
                 Paragraph(f'Lekar: {_safe(e.clinician_name)}', styles['Meta'])]
        if e.vital_signs:
            vt_row1, vt_row2 = [], []
            for k in ('bp', 'pulse', 'temperature', 'spo2'):
                if k in e.vital_signs:
                    vt_row1.append(VITAL_LABELS[k])
                    val = _safe(e.vital_signs[k])
                    vt_row2.append(Paragraph(f'<font color="#a1352a"><b>{val}</b></font>' if _vital_flag(k, e.vital_signs[k]) else val, styles['BodyText']))
            if vt_row1:
                vt = Table([vt_row1, vt_row2], colWidths=[517 / max(len(vt_row1), 1)] * len(vt_row1))
                vt.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef4f2')), ('FONTSIZE', (0, 0), (-1, -1), 8), ('PADDING', (0, 0), (-1, -1), 5), ('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#dfe9e6')), ('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
                block += [Spacer(1, 3), vt]
        for label, val in [('Anamneza', e.anamnesis), ('Objektivni pregled', e.examination), ('Procena', e.assessment), ('Plan lečenja', e.plan)]:
            if val:
                block += [Spacer(1, 4), Paragraph(f'<b>{label}:</b> {_safe(val)}', styles['BodyText'])]
        block.append(Spacer(1, 10))
        story.append(KeepTogether(block))

    story += [Spacer(1, 14), Paragraph(
        'Ovaj dokument je pomoćni sažetak generisan iz elektronskog kartona ordinacije. '
        'Ne predstavlja zamenu za original dokumentacije i mora ga pregledati lekar pre upotrebe.',
        styles['Meta'])]

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(colors.HexColor('#8ca39f'))
        canvas.drawString(36, 22, f'Generisano: {generated_at} \u00b7 {org.name}')
        canvas.drawRightString(A4[0] - 36, 22, f'Strana {doc_.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    store.audit(user, 'export', 'medical_report_pdf', patient_id, patient.full_name)
    slug = _ascii_filename(patient.full_name.strip().lower().replace(' ', '-'), patient_id[:8])
    ascii_filename = f"medicinski-izvestaj-{slug}.pdf"
    utf8_filename = f"medicinski-izveštaj-{patient.full_name.strip().lower().replace(' ', '-')}.pdf"
    disposition = f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(utf8_filename)}"
    return Response(out.getvalue(), media_type='application/pdf', headers={'Content-Disposition': disposition})


@router.post('/api/patients/{patient_id}/scribe-drafts')
async def create_scribe_draft(patient_id: str, payload: ScribeDraftRequest, user=Depends(require_roles('doctor', 'admin'))):
    patient = patient_or_404(user, patient_id)
    draft = await ai.scribe_draft(patient, payload.transcript, payload.mode)
    record = store.add_scribe_draft(user.organization_id, patient_id, user, payload.mode, payload.transcript, draft)
    store.audit(user, 'generate', 'scribe_draft', record.id, f'{payload.mode} · nacrt')
    return record


@router.get('/api/patients/{patient_id}/scribe-drafts')
def list_scribe_drafts(patient_id: str, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    return store.list_scribe_drafts(user.organization_id, patient_id)


@router.put('/api/patients/{patient_id}/scribe-drafts/{draft_id}')
def edit_scribe_draft(patient_id: str, draft_id: str, payload: ScribeDraftUpdate, user=Depends(require_roles('doctor', 'admin'))):
    patient_or_404(user, patient_id)
    draft = store.update_scribe_draft(user.organization_id, patient_id, draft_id, payload)
    if not draft:
        raise HTTPException(409, 'Nacrt nije pronađen ili više nije moguće menjati ga')
    store.audit(user, 'edit', 'scribe_draft', draft.id, 'Lekar izmenio AI nacrt')
    return draft


@router.patch('/api/patients/{patient_id}/scribe-drafts/{draft_id}/status')
def update_scribe_draft_status(patient_id: str, draft_id: str, payload: ScribeStatusUpdate, user=Depends(require_roles('doctor', 'admin'))):
    patient = patient_or_404(user, patient_id)
    draft, encounter = store.finalize_scribe_draft(user.organization_id, patient, draft_id, user, payload.status, payload.create_encounter, payload.visit_date)
    if not draft:
        raise HTTPException(404, 'Nacrt nije pronađen')
    store.audit(user, payload.status, 'scribe_draft', draft.id, 'Kreiran strukturisani pregled' if encounter else None)
    return {'draft': draft, 'encounter': encounter}
