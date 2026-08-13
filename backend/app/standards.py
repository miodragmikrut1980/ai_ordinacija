"""Standardni kodovi i opšte referentne vrednosti — namerno malen, tačan
skup, ne pokušaj pune LOINC/MKB-10 terminološke baze.

LOINC ima preko 100.000 kodova i MKB-10 preko 70.000; ni jedan se ne može
odgovorno "generisati" ovde bez licenciranog terminološkog servisa. Ovaj
modul umesto toga daje ispravne, dobro poznate kodove tačno za onaj skup
analiza (laboratory.py:TEST_ALIASES) i dijagnoza (differential.py:RULES)
koje sistem već prepoznaje — isti princip kao kod SMS/Viber podsetnika i
fiskalizacije: bolje malo i tačno nego veliko i nepouzdano.

Referentni opsezi ovde su opšti (iz literature), NE zvanični opseg
laboratorije ordinacije, koji zavisi od analizatora i metode. Uvek se
prikazuju sa napomenom i ne menjaju već postojeću logiku isticanja
odstupanja (koja daje prednost opsegu navedenom u samom izveštaju kada
postoji — videti laboratory.py:_abnormality).
"""
from __future__ import annotations

from pydantic import BaseModel


class LabStandard(BaseModel):
    test_name: str
    loinc_code: str
    loinc_display: str
    general_reference_range: str
    reference_caveat: str = (
        "Opšta referenca iz literature — laboratorija ordinacije može imati "
        "drugačiji opseg u zavisnosti od analizatora i metode. Uvek proveriti "
        "prema izveštaju laboratorije."
    )


# Testovi identični ključevima u laboratory.py:TEST_ALIASES.values()
LAB_STANDARDS: dict[str, LabStandard] = {
    "CRP": LabStandard(
        test_name="CRP", loinc_code="1988-5",
        loinc_display="C reactive protein [Mass/volume] in Serum or Plasma",
        general_reference_range="< 5 mg/L",
    ),
    "Glukoza": LabStandard(
        test_name="Glukoza", loinc_code="2345-7",
        loinc_display="Glucose [Mass/volume] in Serum or Plasma",
        general_reference_range="3.9–6.1 mmol/L (našte)",
    ),
    "Hemoglobin": LabStandard(
        test_name="Hemoglobin", loinc_code="718-7",
        loinc_display="Hemoglobin [Mass/volume] in Blood",
        general_reference_range="120–170 g/L (zavisi od pola)",
    ),
    "Leukociti": LabStandard(
        test_name="Leukociti", loinc_code="6690-2",
        loinc_display="Leukocytes [#/volume] in Blood",
        general_reference_range="4.0–10.0 ×10⁹/L",
    ),
    "Trombociti": LabStandard(
        test_name="Trombociti", loinc_code="777-3",
        loinc_display="Platelets [#/volume] in Blood",
        general_reference_range="150–400 ×10⁹/L",
    ),
    "Kreatinin": LabStandard(
        test_name="Kreatinin", loinc_code="2160-0",
        loinc_display="Creatinine [Mass/volume] in Serum or Plasma",
        general_reference_range="44–106 µmol/L (zavisi od pola)",
    ),
    "TSH": LabStandard(
        test_name="TSH", loinc_code="3016-3",
        loinc_display="Thyrotropin [Units/volume] in Serum or Plasma",
        general_reference_range="0.4–4.0 mIU/L",
    ),
    "HbA1c": LabStandard(
        test_name="HbA1c", loinc_code="4548-4",
        loinc_display="Hemoglobin A1c/Hemoglobin.total in Blood",
        general_reference_range="20–42 mmol/mol (4.0–6.0 %)",
    ),
}

# Ključevi identični Rule.name u differential.py:RULES
ICD10_CODES: dict[str, str] = {
    "Anemija usled nedostatka gvožđa": "D50.9",
    "Dijabetes melitus / nedovoljna glikoregulacija": "E11.9",
    "Bakterijska respiratorna infekcija": "J20.9",
    "Virusna respiratorna infekcija": "J06.9",
    "Pneumonija — potrebno razmotriti/isključiti": "J18.9",
    "Infekcija urinarnog trakta": "N39.0",
}

# Ključevi identični kanonskim ključevima u medication_safety.py:ALIASES.
# WHO ATC klasifikacija -- isti princip kao LOINC/MKB-10 iznad: mali,
# tačan, provériv skup za lekove koje ovaj sistem već prepoznaje, ne
# pokušaj pune ATC/RxNorm terminološke baze. Za lekove sa više relevantnih
# klasifikacija (npr. metotreksat kao imunosupresiv naspram citostatika,
# acetilsalicilna kiselina kao antiagregans naspram analgetika) izabrana
# je klasifikacija najbliža kontekstu u kom se lek pojavljuje u ovom
# sistemu — uvek proveriti prema aktuelnom ATC indeksu za tačnu indikaciju.
ATC_CODES: dict[str, str] = {
    "warfarin": "B01AA03",
    "ibuprofen": "M01AE01",
    "naproxen": "M01AE02",
    "aspirin": "N02BA01",
    "tramadol": "N02AX02",
    "sertraline": "N06AB06",
    "escitalopram": "N06AB10",
    "nitroglycerin": "C01DA02",
    "sildenafil": "G04BE03",
    "enalapril": "C09AA02",
    "potassium": "A12BA01",
    "diazepam": "N05BA01",
    "oxycodone": "N02AA05",
    "methotrexate": "L04AX03",
    "trimethoprim": "J01EA01",
    "simvastatin": "C10AA01",
    "clarithromycin": "J01FA09",
    "amoxicillin": "J01CA04",
    "metformin": "A10BA02",
}
