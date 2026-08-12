"""A deliberately small, transparent medication safety screen.

It is a triage aid, not a drug database and never returns a "safe" result.
Unknown medicines are reported explicitly so a clinician knows the screen did
not evaluate them.  Every warning requires verification in the approved local
SmPC/ALIMS source and clinical context.
"""
from __future__ import annotations

import re

from .models import MedicationSafetyCheck, MedicationSafetyFinding

REFERENCE_VERSION = "Internal safety rules 2026.08 (limited coverage)"

ALIASES = {
    "warfarin": {"warfarin"}, "ibuprofen": {"ibuprofen", "brufen", "nurofen"},
    "naproxen": {"naproxen"}, "aspirin": {"aspirin", "acetilsalicilna"},
    "tramadol": {"tramadol"}, "sertraline": {"sertraline", "zoloft"},
    "escitalopram": {"escitalopram", "citalex"}, "nitroglycerin": {"nitroglycerin", "nitroglicerin"},
    "sildenafil": {"sildenafil", "tadalafil", "vardenafil"}, "enalapril": {"enalapril", "ramipril", "perindopril"},
    "potassium": {"kalijum", "potassium", "kcl"}, "diazepam": {"diazepam", "alprazolam", "lorazepam"},
    "oxycodone": {"oxycodone", "morphine", "fentanyl", "codeine"}, "methotrexate": {"methotrexate"},
    "trimethoprim": {"trimethoprim", "co-trimoxazole", "biseptol"}, "simvastatin": {"simvastatin", "atorvastatin"},
    "clarithromycin": {"clarithromycin", "klaritromicin"}, "amoxicillin": {"amoxicillin", "amoksicilin", "ampicillin", "penicillin"},
}


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _matched(medications: list[str]) -> tuple[dict[str, str], list[str]]:
    hits: dict[str, str] = {}
    unknown: list[str] = []
    for original in medications:
        raw = _normalise(original)
        key = next((canonical for canonical, aliases in ALIASES.items() if any(a in raw for a in aliases)), None)
        if key:
            hits.setdefault(key, original)
        elif original.strip():
            unknown.append(original.strip())
    return hits, unknown


def check_medication_safety(current: list[str], proposed: list[str], allergies: list[str]) -> MedicationSafetyCheck:
    all_meds = [*current, *proposed]
    hits, unknown = _matched(all_meds)
    findings: list[MedicationSafetyFinding] = []
    rules = [
        ("critical", "interaction", ("nitroglycerin", "sildenafil"), "PDE-5 inhibitor i nitrat mogu izazvati opasnu hipotenziju.", "Ne izdavati/prepisivati dok lekar ne proveri terapiju i kliničko stanje."),
        ("high", "interaction", ("warfarin", "ibuprofen"), "Antikoagulans i NSAID mogu povećati rizik od krvarenja.", "Proveriti indikaciju, rizik od krvarenja i odobreni izvor pre odluke."),
        ("high", "interaction", ("warfarin", "naproxen"), "Antikoagulans i NSAID mogu povećati rizik od krvarenja.", "Proveriti indikaciju, rizik od krvarenja i odobreni izvor pre odluke."),
        ("high", "interaction", ("tramadol", "sertraline"), "Serotonergična kombinacija može povećati rizik od serotoninskog sindroma.", "Lekar mora proveriti doze, trajanje i simptome; ne oslanjati se samo na ovaj ekran."),
        ("high", "interaction", ("tramadol", "escitalopram"), "Serotonergična kombinacija može povećati rizik od serotoninskog sindroma.", "Lekar mora proveriti doze, trajanje i simptome; ne oslanjati se samo na ovaj ekran."),
        ("high", "interaction", ("methotrexate", "trimethoprim"), "Kombinacija može povećati hematološku toksičnost.", "Pre odluke proveriti specijalistički plan, laboratoriju i odobreni izvor."),
        ("moderate", "interaction", ("enalapril", "potassium"), "ACE inhibitor i kalijum mogu povećati rizik od hiperkalemije.", "Proveriti bubrežnu funkciju, kalijum i kompletnu terapiju."),
        ("moderate", "interaction", ("simvastatin", "clarithromycin"), "Makrolid može povećati izloženost statinu i rizik od miopatije.", "Proveriti alternativu/privremeni plan u odobrenom izvoru pre odluke."),
        ("high", "interaction", ("oxycodone", "diazepam"), "Opioid i benzodiazepin mogu povećati respiratornu depresiju i sedaciju.", "Lekar mora proceniti potrebu, doze, nadzor i faktore rizika."),
    ]
    for severity, kind, required, message, action in rules:
        if all(key in hits for key in required):
            findings.append(MedicationSafetyFinding(severity=severity, type=kind, medications=[hits[key] for key in required], message=message, action=action))
    allergy_text = " ".join(_normalise(x) for x in allergies)
    if "penicillin" in allergy_text and "amoxicillin" in hits:
        findings.append(MedicationSafetyFinding(severity="high", type="allergy", medications=[hits["amoxicillin"]], message="Evidentirana je alergija na penicilin, a predloženi lek se poklapa sa penicilinskom grupom.", action="Ne potvrđivati terapiju dok lekar ne proveri vrstu i težinu ranije reakcije i odobreni izvor."))
    for canonical, original in hits.items():
        if sum(1 for med in all_meds if canonical in _matched([med])[0]) > 1:
            findings.append(MedicationSafetyFinding(severity="moderate", type="duplicate", medications=[original], message="Ista prepoznata aktivna supstanca/grupa je navedena više puta.", action="Proveriti da li je duplikat nameran ili greška u listi terapije."))
    return MedicationSafetyCheck(reference_version=REFERENCE_VERSION, checked_medications=all_meds, unrecognized_medications=unknown,
        findings=findings, disclaimer="Ovo je ograničena provera potencijalnih rizika, ne kompletna baza interakcija niti preporuka terapije. Odsustvo upozorenja ne znači da je kombinacija bezbedna. Lekar mora proveriti alergije, dozu, komorbiditete, laboratoriju i važeći odobreni izvor (npr. SmPC/ALIMS) pre odluke.")
