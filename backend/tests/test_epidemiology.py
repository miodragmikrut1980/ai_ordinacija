"""Direct unit tests for backend/app/epidemiology.py.

Mirrors test_differential.py's approach: construct encounter objects
directly to test the syndrome-counting logic itself (negation handling,
the new "urinarni" syndrome), not the HTTP endpoint.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.epidemiology import build_radar
from app.models import EncounterRecord


def _encounter(**kwargs) -> EncounterRecord:
    defaults = dict(
        id="e1", organization_id="org1", patient_id="p1", visit_date=datetime.now(timezone.utc),
        chief_complaint="Pregled", anamnesis="", examination="", assessment="", plan="", vital_signs={},
        clinician_id="u1", clinician_name="Dr Test", created_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return EncounterRecord(**defaults)


def test_negated_symptom_does_not_count_toward_syndrome_trend():
    # Regression guard for the gap found in v1.8.0: epidemiology.py's
    # syndrome counter used plain substring matching with no negation
    # handling at all, so "bez kašlja" (no cough) was silently counted the
    # same as an actual, present cough -- the same class of bug already
    # fixed once in differential.py.
    encounters = [_encounter(id=f"e{i}", anamnesis="Pacijent je bez kašlja i bez temperature.") for i in range(5)]
    radar = build_radar(encounters, [], days=7)
    names = {row["name"] for row in radar["syndrome_trends"]}
    assert "respiratorni" not in names
    assert "febrilni" not in names


def test_unnegated_symptom_still_counts_toward_syndrome_trend():
    # Regression guard: the negation check must not suppress genuine,
    # unnegated matches in the radar either.
    encounters = [_encounter(id=f"e{i}", anamnesis="Pacijent ima kašalj i temperaturu.") for i in range(5)]
    radar = build_radar(encounters, [], days=7)
    resp = next(row for row in radar["syndrome_trends"] if row["name"] == "respiratorni")
    assert resp["current_count"] == 5


def test_urinary_syndrome_is_tracked_and_negation_aware():
    # v1.8.0: new "urinarni" syndrome category in the epidemiology radar.
    encounters = [_encounter(id=f"e{i}", anamnesis="Dizurija i učestalo mokrenje.") for i in range(4)]
    encounters.append(_encounter(id="e-neg", anamnesis="Pacijent je bez dizurije."))
    radar = build_radar(encounters, [], days=7)
    urinary = next((row for row in radar["syndrome_trends"] if row["name"] == "urinarni"), None)
    assert urinary is not None, [row["name"] for row in radar["syndrome_trends"]]
    assert urinary["current_count"] == 4
