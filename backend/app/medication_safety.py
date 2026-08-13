"""A deliberately small, transparent medication safety screen.

It is a triage aid, not a drug database and never returns a "safe" result.
Unknown medicines are reported explicitly so a clinician knows the screen did
not evaluate them. Every warning requires verification in the approved local
SmPC/ALIMS source and clinical context.

Versioning: every rule has a stable `rule_id` and an `added_version` (this
module's own version, not an external database release). This is the
honest scope of "verziona baza" this project can offer without a licensed
drug-interaction data feed: not a real external database with its own
release numbers, but a small, auditable, version-controlled ruleset where
every change is a diffable commit to this file, and every finding traces
back to exactly which rule produced it. `source_note` on every rule states
plainly that these are well-established, textbook-level interactions --
not sourced from a live external database this app doesn't have access to
-- and must still be verified against the clinic's approved formulary.
"""
from __future__ import annotations

import re

from .models import MedicationSafetyCheck, MedicationSafetyFinding

MODULE_VERSION = "2026.08"
_TEXTBOOK_SOURCE = (
    "Opšte poznata farmakološka interakcija (nivo osnovnih udžbenika "
    "farmakologije); nije preuzeto iz spoljne baze podataka. Proveriti u "
    "važećem SmPC/ALIMS izvoru pre odluke."
)

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
    "metformin": {"metformin", "glucophage"},
}

# (rule_id, added_version, severity, kind, required_meds, message, action)
INTERACTION_RULES = [
    ("INT-001", "2026.08", "critical", "interaction", ("nitroglycerin", "sildenafil"), "PDE-5 inhibitor i nitrat mogu izazvati opasnu hipotenziju.", "Ne izdavati/prepisivati dok lekar ne proveri terapiju i kliničko stanje."),
    ("INT-002", "2026.08", "high", "interaction", ("warfarin", "ibuprofen"), "Antikoagulans i NSAID mogu povećati rizik od krvarenja.", "Proveriti indikaciju, rizik od krvarenja i odobreni izvor pre odluke."),
    ("INT-003", "2026.08", "high", "interaction", ("warfarin", "naproxen"), "Antikoagulans i NSAID mogu povećati rizik od krvarenja.", "Proveriti indikaciju, rizik od krvarenja i odobreni izvor pre odluke."),
    ("INT-004", "2026.08", "high", "interaction", ("tramadol", "sertraline"), "Serotonergična kombinacija može povećati rizik od serotoninskog sindroma.", "Lekar mora proveriti doze, trajanje i simptome; ne oslanjati se samo na ovaj ekran."),
    ("INT-005", "2026.08", "high", "interaction", ("tramadol", "escitalopram"), "Serotonergična kombinacija može povećati rizik od serotoninskog sindroma.", "Lekar mora proveriti doze, trajanje i simptome; ne oslanjati se samo na ovaj ekran."),
    ("INT-006", "2026.08", "high", "interaction", ("methotrexate", "trimethoprim"), "Kombinacija može povećati hematološku toksičnost.", "Pre odluke proveriti specijalistički plan, laboratoriju i odobreni izvor."),
    ("INT-007", "2026.08", "moderate", "interaction", ("enalapril", "potassium"), "ACE inhibitor i kalijum mogu povećati rizik od hiperkalemije.", "Proveriti bubrežnu funkciju, kalijum i kompletnu terapiju."),
    ("INT-008", "2026.08", "moderate", "interaction", ("simvastatin", "clarithromycin"), "Makrolid može povećati izloženost statinu i rizik od miopatije.", "Proveriti alternativu/privremeni plan u odobrenom izvoru pre odluke."),
    ("INT-009", "2026.08", "high", "interaction", ("oxycodone", "diazepam"), "Opioid i benzodiazepin mogu povećati respiratornu depresiju i sedaciju.", "Lekar mora proceniti potrebu, doze, nadzor i faktore rizika."),
]

# Keyword cues checked against ClinicalProfile.diagnoses + medical_history
# (free text, so this is a heuristic match, not a coded problem list --
# same limitation as clinical_keywords.py elsewhere in this app).
# Keyword cues checked against ClinicalProfile.diagnoses + medical_history
# (free text, so this is a heuristic match, not a coded problem list --
# same limitation as clinical_keywords.py elsewhere in this app). Each cue
# is a tuple of word STEMS that must ALL appear somewhere in the text --
# stems rather than full words so Serbian grammatical case endings
# ("bubrežna" vs "bubrežnu" vs "bubrežne") don't cause a missed match,
# which would be the dangerous failure mode here.
_RENAL_IMPAIRMENT_CUES = (
    ("bubrežn", "insuficijenc"), ("bubrežn", "bolest"), ("hbb",), ("dijaliz",), ("renaln", "insuficijenc"),
)
_HEPATIC_IMPAIRMENT_CUES = (
    ("ciroz",), ("hepataln", "insuficijenc"), ("insuficijenc", "jetr"), ("ošteć", "jetr"), ("hepatitis",),
)

# (rule_id, added_version, severity, organ, med_key, message, action)
ORGAN_FUNCTION_RULES = [
    ("REN-001", "2026.08", "high", "bubrežna", "ibuprofen", "NSAID kod smanjene bubrežne funkcije povećava rizik daljeg pogoršanja funkcije bubrega.", "Razmotriti alternativu ili prilagođavanje doze; proveriti aktuelnu bubrežnu funkciju."),
    ("REN-002", "2026.08", "high", "bubrežna", "naproxen", "NSAID kod smanjene bubrežne funkcije povećava rizik daljeg pogoršanja funkcije bubrega.", "Razmotriti alternativu ili prilagođavanje doze; proveriti aktuelnu bubrežnu funkciju."),
    ("REN-003", "2026.08", "critical", "bubrežna", "metformin", "Metformin kod značajno smanjene bubrežne funkcije nosi rizik od laktatne acidoze.", "Proveriti aktuelnu eGFR/kreatinin pre nastavka terapije; razmotriti prilagođavanje ili obustavu."),
    ("REN-004", "2026.08", "high", "bubrežna", "methotrexate", "Metotreksat se eliminiše bubrezima; smanjena funkcija povećava rizik toksičnosti.", "Proveriti aktuelnu bubrežnu funkciju i dozu pre nastavka terapije."),
    ("HEP-001", "2026.08", "moderate", "jetrena", "simvastatin", "Statini zahtevaju oprez i praćenje jetrenih enzima kod oštećenja jetre.", "Proveriti aktuelne jetrene enzime pre nastavka ili uvođenja terapije."),
    ("HEP-002", "2026.08", "high", "jetrena", "oxycodone", "Opioidi se metabolišu u jetri; oštećenje jetre menja dozu i rizik od nagomilavanja.", "Razmotriti prilagođavanje doze i pojačan klinički nadzor."),
]


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


def check_medication_safety(
    current: list[str], proposed: list[str], allergies: list[str],
    diagnoses: list[str] | None = None, medical_history: str | None = None,
) -> MedicationSafetyCheck:
    all_meds = [*current, *proposed]
    hits, unknown = _matched(all_meds)
    findings: list[MedicationSafetyFinding] = []

    for rule_id, added_version, severity, kind, required, message, action in INTERACTION_RULES:
        if all(key in hits for key in required):
            findings.append(MedicationSafetyFinding(
                rule_id=rule_id, severity=severity, type=kind, medications=[hits[key] for key in required],
                message=message, action=action, source_note=f"{_TEXTBOOK_SOURCE} (pravilo dodato u v{added_version})",
            ))

    allergy_text = " ".join(_normalise(x) for x in allergies)
    if "penicillin" in allergy_text and "amoxicillin" in hits:
        findings.append(MedicationSafetyFinding(
            rule_id="ALG-001", severity="high", type="allergy", medications=[hits["amoxicillin"]],
            message="Evidentirana je alergija na penicilin, a predloženi lek se poklapa sa penicilinskom grupom.",
            action="Ne potvrđivati terapiju dok lekar ne proveri vrstu i težinu ranije reakcije i odobreni izvor.",
            source_note=f"{_TEXTBOOK_SOURCE} (pravilo dodato u v2026.08)",
        ))

    for canonical, original in hits.items():
        if sum(1 for med in all_meds if canonical in _matched([med])[0]) > 1:
            findings.append(MedicationSafetyFinding(
                rule_id="DUP-001", severity="moderate", type="duplicate", medications=[original],
                message="Ista prepoznata aktivna supstanca/grupa je navedena više puta.",
                action="Proveriti da li je duplikat nameran ili greška u listi terapije.",
                source_note="Provera internog dosledovanja liste terapije, ne farmakološki izvor.",
            ))

    # Bubrežna/jetrena funkcija: heuristička pretraga slobodnog teksta
    # dijagnoza/anamneze (isto ograničenje kao clinical_keywords.py --
    # ne zamenjuje strukturisan podatak o funkciji organa poput eGFR).
    # NAPOMENA: _normalise() iznad je napravljen za nazive lekova (uglavnom
    # ASCII) i regex-om uništava srpske dijakritike (č/ž bi razdvojili
    # "hronična" na "hroni na"), pa se ovde koristi običan lowercase.
    context_text = " ".join([*(diagnoses or []), medical_history or ""]).lower()
    has_renal = any(all(stem in context_text for stem in cue) for cue in _RENAL_IMPAIRMENT_CUES)
    has_hepatic = any(all(stem in context_text for stem in cue) for cue in _HEPATIC_IMPAIRMENT_CUES)
    for rule_id, added_version, severity, organ, med_key, message, action in ORGAN_FUNCTION_RULES:
        applies = (organ == "bubrežna" and has_renal) or (organ == "jetrena" and has_hepatic)
        if applies and med_key in hits:
            findings.append(MedicationSafetyFinding(
                rule_id=rule_id, severity=severity, type="organ_function", medications=[hits[med_key]],
                message=f"[{organ} funkcija] {message}", action=action,
                source_note=f"{_TEXTBOOK_SOURCE} (pravilo dodato u v{added_version}). Zasnovano na tekstu dijagnoza/anamneze pacijenta -- nije zamena za laboratorijski nalaz funkcije organa.",
            ))

    return MedicationSafetyCheck(
        reference_version=f"Internal safety rules {MODULE_VERSION} (limited coverage)",
        checked_medications=all_meds, unrecognized_medications=unknown, findings=findings,
        disclaimer="Ovo je ograničena provera potencijalnih rizika, ne kompletna baza interakcija niti preporuka terapije. Odsustvo upozorenja ne znači da je kombinacija bezbedna. Lekar mora proveriti alergije, dozu, komorbiditete, laboratoriju i važeći odobreni izvor (npr. SmPC/ALIMS) pre odluke.",
    )


def rule_catalog() -> dict:
    """Full, auditable list of every rule this module can trigger -- what
    'verziona baza' honestly means here: not a licensed external database,
    but a small versioned ruleset an admin/pharmacist can review in full."""
    from .standards import ATC_CODES
    return {
        "module_version": MODULE_VERSION,
        "interaction_rules": [
            {"rule_id": r[0], "added_version": r[1], "severity": r[2], "medications": list(r[4]), "message": r[5]}
            for r in INTERACTION_RULES
        ],
        "organ_function_rules": [
            {"rule_id": r[0], "added_version": r[1], "severity": r[2], "organ": r[3], "medication": r[4], "message": r[5]}
            for r in ORGAN_FUNCTION_RULES
        ],
        "recognized_medications": [
            {"name": name, "atc_code": ATC_CODES.get(name)} for name in sorted(ALIASES.keys())
        ],
    }
