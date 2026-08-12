from __future__ import annotations

from app.laboratory import extract_lab_candidates
from app.medication_safety import check_medication_safety


def test_lab_parser_is_conservative_and_flags_reference_range():
    parsed = extract_lab_candidates("CRP: 45 mg/L [0-5]\nGlukoza 5.1 mmol/L (3.9-5.6)\nRandom number 123")
    assert [(item.name, item.value, flag) for item, flag in parsed] == [
        ("CRP", 45.0, "high"), ("Glukoza", 5.1, "normal"),
    ]


def test_medication_screen_never_claims_unknown_medicine_is_safe():
    checked = check_medication_safety([], ["mystery medicine"], [])
    assert checked.unrecognized_medications == ["mystery medicine"]
    assert "ne znači da je kombinacija bezbedna" in checked.disclaimer


def test_medication_screen_detects_potential_allergy_and_interaction():
    checked = check_medication_safety(["Warfarin 5 mg"], ["Ibuprofen 400 mg", "Amoxicillin 500 mg"], ["penicillin rash"])
    assert {item.type for item in checked.findings} == {"interaction", "allergy"}
    assert any(item.severity == "high" for item in checked.findings)
