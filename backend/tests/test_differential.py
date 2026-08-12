"""Direct unit tests for backend/app/differential.py.

These construct model objects directly rather than going through the full
HTTP API, since what's being tested here is the rule-matching logic itself
(negation handling, epidemiology context assembly), not the endpoint.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.differential import build_differential
from app.models import ClinicalProfile, DocumentRecord, EncounterRecord, PatientRecord


def _patient(**profile_kwargs) -> PatientRecord:
    return PatientRecord(
        id="p1", organization_id="org1", full_name="Test Pacijent",
        created_at=datetime.now(timezone.utc), clinical_profile=ClinicalProfile(**profile_kwargs),
    )


def _document(text: str, filename: str = "doc.txt") -> DocumentRecord:
    return DocumentRecord(
        id="d1", organization_id="org1", patient_id="p1", filename=filename, media_type="text/plain",
        uploaded_at=datetime.now(timezone.utc), text=text,
    )


def _encounter(**kwargs) -> EncounterRecord:
    defaults = dict(
        id="e1", organization_id="org1", patient_id="p1", visit_date=datetime.now(timezone.utc),
        chief_complaint="Pregled", anamnesis="", examination="", assessment="", plan="", vital_signs={},
    )
    defaults.update(kwargs)
    return EncounterRecord(**defaults)


EMPTY_RADAR = {"syndrome_trends": [], "confirmed_pathogens": [], "clusters": []}


def test_negated_symptom_does_not_count_as_supporting_evidence():
    # "bez kašlja" (without cough) must not count toward a respiratory
    # infection candidate the way an actual, unnegated "kašalj" would.
    patient = _patient()
    doc = _document("Pacijent je bez kašlja i bez temperature. Opšte stanje dobro.")
    result = build_differential(patient, [doc], [], EMPTY_RADAR)
    names = [c["name"] for c in result["candidates"]]
    assert "Virusna respiratorna infekcija" not in names
    assert "Bakterijska respiratorna infekcija" not in names


def test_unnegated_symptom_still_counts_normally():
    # Regression guard: the negation check must not suppress genuine,
    # unnegated matches (i.e. it should not be over-broad).
    patient = _patient()
    doc = _document("Pacijent ima kašalj, temperaturu i bol u grlu tri dana.")
    result = build_differential(patient, [doc], [], EMPTY_RADAR)
    names = [c["name"] for c in result["candidates"]]
    assert "Virusna respiratorna infekcija" in names


def test_negation_only_suppresses_the_negated_term_not_the_whole_document():
    # "bez kašlja" should not count, but a genuinely present, unrelated
    # positive finding later in the same document still should.
    patient = _patient()
    doc = _document("Pacijent je bez kašlja. Laboratorija: CRP povišen, leukociti povišeni.")
    result = build_differential(patient, [doc], [], EMPTY_RADAR)
    bacterial = next((c for c in result["candidates"] if c["name"] == "Bakterijska respiratorna infekcija"), None)
    assert bacterial is not None
    assert "Povišen CRP" in bacterial["supporting_evidence"]
    assert "Kašalj" not in bacterial["supporting_evidence"]


def test_epidemiology_context_includes_a_cluster_newly_emerging_from_zero():
    # A syndrome trend with previous_count=0 has change_percent=None (you
    # can't compute a percent change from zero) -- this must still be
    # treated as a rising signal, not silently dropped.
    patient = _patient()
    radar = {
        "syndrome_trends": [{"name": "respiratorni", "current_count": 6, "previous_count": 0, "change_percent": None, "signal_level": "visok"}],
        "confirmed_pathogens": [], "clusters": [],
    }
    result = build_differential(patient, [], [], radar)
    assert any("respiratorni" in line for line in result["epidemiology_context"])


def test_epidemiology_context_includes_radar_clusters():
    # radar['clusters'] is the radar's own highest-confidence output and was
    # previously never read by the differential analysis at all.
    patient = _patient()
    radar = {
        "syndrome_trends": [], "confirmed_pathogens": [],
        "clusters": [{"title": "Mogući respiratorni klaster", "case_count": 7, "window_days": 7, "confidence": "srednja"}],
    }
    result = build_differential(patient, [], [], radar)
    assert any("respiratorni klaster" in line for line in result["epidemiology_context"])


def test_disclaimer_present_and_score_bounded():
    patient = _patient(diagnoses=["Dijabetes"], current_medications=["Metformin"])
    doc = _document("HbA1c povišen 8.5. Glukoza povišena.")
    result = build_differential(patient, [doc], [], EMPTY_RADAR)
    assert result["candidates"]
    assert all(0 <= c["match_score"] <= 100 for c in result["candidates"])
    assert "nije verovatnoća dijagnoze" in result["disclaimer"]


def test_urinary_tract_infection_rule_matches_and_respects_negation():
    # v1.8.0: new UTI rule. A patient with real dysuria + a positive urine
    # finding should match; explicitly negated dysuria should not.
    patient = _patient()
    doc = _document("Pacijent navodi dizuriju i pečenje pri mokrenju tri dana. Urin: leukociti u urinu, nitriti pozitivni.")
    result = build_differential(patient, [doc], [], EMPTY_RADAR)
    uti = next((c for c in result["candidates"] if c["name"] == "Infekcija urinarnog trakta"), None)
    assert uti is not None, [c["name"] for c in result["candidates"]]
    assert "Dizurija / bol pri mokrenju" in uti["supporting_evidence"]

    patient2 = _patient()
    doc2 = _document("Pacijent je bez dizurije. Opšte stanje dobro.")
    result2 = build_differential(patient2, [doc2], [], EMPTY_RADAR)
    names2 = [c["name"] for c in result2["candidates"]]
    assert "Infekcija urinarnog trakta" not in names2
