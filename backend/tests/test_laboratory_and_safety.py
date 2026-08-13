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


def test_lab_parser_traces_matches_back_to_source_page_via_offsets():
    # v1.17.0 document-viewer citation: given the page_offsets extractors.py
    # would produce for a multi-page PDF, each parsed value must resolve to
    # the correct 1-indexed page.
    page1 = "Uvod u nalaz.\nBez relevantnih vrednosti ovde."
    page2 = "CRP: 45 mg/L [0-5]\nOstali nalazi u redu."
    text = "\n".join([page1, page2])
    page_offsets = [0, len(page1) + 1]  # +1 for the join separator
    parsed = extract_lab_candidates(text, page_offsets=page_offsets)
    assert len(parsed) == 1
    item, _flag = parsed[0]
    assert item.name == "CRP" and item.source_page == 2


def test_lab_parser_source_page_is_none_without_offsets():
    parsed = extract_lab_candidates("CRP: 45 mg/L [0-5]")
    assert parsed[0][0].source_page is None


def test_medication_finding_carries_rule_id_and_source_note():
    checked = check_medication_safety(["warfarin"], ["ibuprofen"], [])
    finding = next(f for f in checked.findings if f.rule_id == "INT-002")
    assert finding.severity == "high"
    assert "SmPC/ALIMS" in finding.source_note


def test_renal_impairment_flags_nephrotoxic_medication():
    checked = check_medication_safety(
        current=["ibuprofen"], proposed=[], allergies=[],
        diagnoses=["Hronična bubrežna bolest stadijum 3"],
    )
    finding = next(f for f in checked.findings if f.rule_id == "REN-001")
    assert finding.type == "organ_function" and finding.severity == "high"
    assert "bubrežna" in finding.message.lower()


def test_renal_impairment_does_not_flag_unrelated_medication():
    checked = check_medication_safety(
        current=["sertraline"], proposed=[], allergies=[],
        diagnoses=["Hronična bubrežna bolest stadijum 3"],
    )
    assert not any(f.type == "organ_function" for f in checked.findings)


def test_hepatic_impairment_flags_opioid_dose_concern():
    checked = check_medication_safety(
        current=["oxycodone"], proposed=[], allergies=[],
        medical_history="Pacijent ima poznatu cirozu jetre.",
    )
    finding = next(f for f in checked.findings if f.rule_id == "HEP-002")
    assert finding.type == "organ_function"


def test_metformin_renal_warning_is_critical_severity():
    checked = check_medication_safety(
        current=["metformin"], proposed=[], allergies=[],
        diagnoses=["Dijabetes melitus tip 2", "Renalna insuficijencija"],
    )
    finding = next(f for f in checked.findings if f.rule_id == "REN-003")
    assert finding.severity == "critical"


def test_no_organ_findings_without_impairment_diagnosis():
    checked = check_medication_safety(current=["ibuprofen", "metformin"], proposed=[], allergies=[], diagnoses=["Sezonske alergije"])
    assert not any(f.type == "organ_function" for f in checked.findings)


def test_rule_catalog_lists_every_rule_with_version():
    from app.medication_safety import rule_catalog
    catalog = rule_catalog()
    assert catalog["module_version"] == "2026.08"
    ids = {r["rule_id"] for r in catalog["interaction_rules"]} | {r["rule_id"] for r in catalog["organ_function_rules"]}
    assert "INT-001" in ids and "REN-003" in ids and "HEP-002" in ids
    assert "metformin" in catalog["recognized_medications"]


def test_organ_function_cues_match_across_serbian_grammatical_cases():
    # Regression guard: an earlier version matched only nominative-case
    # diagnosis phrasing ("ciroza", "bubrežna insuficijencija"), so a real
    # clinical note using a different case ("cirozu jetre", "bubrežnu
    # insuficijenciju") silently produced NO warning -- the dangerous
    # failure mode for a safety screen. Stem-based matching must catch both.
    nominative = check_medication_safety(["metformin"], [], [], diagnoses=["Renalna insuficijencija"])
    accusative = check_medication_safety(["metformin"], [], [], diagnoses=["Ima renalnu insuficijenciju"])
    assert any(f.rule_id == "REN-003" for f in nominative.findings)
    assert any(f.rule_id == "REN-003" for f in accusative.findings)

    hep_nominative = check_medication_safety(["oxycodone"], [], [], medical_history="Ciroza jetre.")
    hep_accusative = check_medication_safety(["oxycodone"], [], [], medical_history="Poznata ciroza, prati se zbog ciroze jetre.")
    assert any(f.rule_id == "HEP-002" for f in hep_nominative.findings)
    assert any(f.rule_id == "HEP-002" for f in hep_accusative.findings)
