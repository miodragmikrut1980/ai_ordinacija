from __future__ import annotations
from uuid import uuid4
from dataclasses import dataclass

from .clinical_keywords import any_unnegated
from .standards import ICD10_CODES

@dataclass(frozen=True)
class Rule:
    name: str
    category: str
    positive: tuple[tuple[str, tuple[str, ...], int], ...]
    missing: tuple[str, ...]
    red_flag: bool = False

RULES = (
    Rule('Anemija usled nedostatka gvožđa','hematologija',(
        ('Snižen hemoglobin',('hemoglobin nizak','hemoglobin low','hgb low','hgb snižen'),28),
        ('Snižen MCV',('mcv nizak','mcv low','mikrocit'),22),
        ('Snižen feritin',('feritin nizak','ferritin low','feritin 7','feritin 8','feritin 9'),32),
        ('Umor ili malaksalost',('umor','malaksal','fatigue'),12),
    ),('Feritin, gvožđe i TIBC nisu kompletno evidentirani.','Potrebno je proveriti mogući izvor gubitka krvi.')),
    Rule('Dijabetes melitus / nedovoljna glikoregulacija','metabolizam',(
        ('Povišen HbA1c',('hba1c high','hba1c povišen','hba1c 7','hba1c 8','hba1c 9'),38),
        ('Povišena glukoza',('glucose high','glukoza poviš','hiperglik'),24),
        ('Ranija dijagnoza dijabetesa',('diabetes','dijabetes'),24),
        ('Terapija metforminom',('metformin',),10),
    ),('Nedostaje novija vrednost HbA1c ili glukoze natašte.','Potrebno je proveriti simptome hiperglikemije i adherenciju.')),
    Rule('Bakterijska respiratorna infekcija','infektologija',(
        ('Povišen CRP',('crp high','crp povišen','crp povisen'),24),
        ('Leukocitoza',('leukociti poviš','leukociti povis','leukocytosis'),22),
        ('Temperatura',('temperatura','febril','fever'),14),
        ('Kašalj',('kašalj','kasalj','cough'),12),
        ('Pozitivan bakterijski test',('streptokok pozitivan','bakterija potvrđena','bakterija potvrdjena'),24),
    ),('Nisu evidentirani saturacija i detaljan nalaz pluća.','Etiologija se ne može potvrditi bez kliničkog pregleda i po potrebi mikrobiologije.')),
    Rule('Virusna respiratorna infekcija','infektologija',(
        ('Kašalj',('kašalj','kasalj','cough'),20),
        ('Curenje nosa ili bol u grlu',('curenje nosa','bol u grlu','rinitis','sore throat'),18),
        ('Temperatura',('temperatura','febril','fever'),16),
        ('Potvrđen virusni test',('grip a pozitivan','grip b pozitivan','covid pozitivan','rsv pozitivan'),34),
    ),('Nije evidentiran virusološki test.','Nedostaju trajanje simptoma i objektivni nalaz.')),
    Rule('Pneumonija — potrebno razmotriti/isključiti','crvena zastavica',(
        ('Kašalj i temperatura',('kašalj','kasalj','temperatura','febril'),18),
        ('Otežano disanje',('otežano disanje','otezano disanje','dispneja','shortness of breath'),34),
        ('Niska saturacija',('spo2 8','saturacija 8','saturacija niska'),34),
        ('Bol u grudima',('bol u grudima','chest pain'),18),
    ),('Nisu evidentirani saturacija, auskultacija i snimanje pluća.','Potrebna je hitnija procena ako postoje dispneja, hipoksija ili pogoršanje.'),True),
    Rule('Infekcija urinarnog trakta','urologija',(
        ('Dizurija / bol pri mokrenju',('dizurija','bol pri mokrenju','pečenje pri mokrenju','pecenje pri mokrenju','burning urination'),26),
        ('Učestalo mokrenje',('učestalo mokrenje','ucestalo mokrenje','urinary frequency','poliurija'),18),
        ('Bol u leđima / slabinama',('bol u ledjima','bol u leđima','bol u slabinama','flank pain'),14),
        ('Zamućen ili neprijatan miris urina',('zamućen urin','zamucen urin','neprijatan miris urina','cloudy urine'),12),
        ('Pozitivan nalaz urina',('leukociti u urinu','nitriti pozitivni','urin kultura pozitivna','e coli pozitivan','urine culture positive'),30),
    ),('Nije evidentirana urinokultura ili analiza urina.','Kod muškaraca, dece ili ponovljenih epizoda razmotriti dodatnu obradu.')),
)

# Deliberately simple substring matching, not real NLP -- but plain substring
# matching cannot tell "kašalj" (cough, present) from "bez kašlja" (no cough)
# or "test negativan" (test negative). Without this, a negated finding could
# count as supporting evidence for a diagnosis, which is a real, clinically
# meaningful false positive, not a cosmetic bug. Negation detection itself
# lives in clinical_keywords.py (shared with the epidemiology radar, which
# had the identical gap) -- see that module's docstring for the heuristic's
# scope and limits. Every candidate this module produces still carries the
# disclaimer that a clinician must verify against the source documentation.

def _combined(patient, documents, encounters) -> str:
    profile=patient.clinical_profile
    parts=[patient.full_name, ' '.join(profile.diagnoses), ' '.join(profile.current_medications), ' '.join(profile.allergies), profile.medical_history or '']
    parts += [f'{e.chief_complaint} {e.anamnesis} {e.examination} {e.assessment} {e.plan} {e.vital_signs}' for e in encounters]
    parts += [f'{d.filename} {d.text}' for d in documents]
    return '\n'.join(parts).lower()

def build_differential(patient, documents, encounters, radar: dict) -> dict:
    text=_combined(patient,documents,encounters)
    candidates=[]
    for rule in RULES:
        evidence=[]; score=0
        for label,terms,weight in rule.positive:
            if any_unnegated(text, terms):
                evidence.append(label);score+=weight
        if score < 18: continue
        score=min(score,95)
        level='visoko' if score>=70 else 'srednje' if score>=45 else 'niže'
        candidates.append({'id':str(uuid4()),'name':rule.name,'category':rule.category,'match_score':score,'match_level':level,'supporting_evidence':evidence,'contradicting_evidence':[],'missing_information':list(rule.missing),'red_flag':rule.red_flag,'icd10_code':ICD10_CODES.get(rule.name)})
    candidates.sort(key=lambda x:(not x['red_flag'],-x['match_score']))
    epi=[]
    for trend in radar.get('syndrome_trends',[]):
        current=trend.get('current_count',0)
        previous=trend.get('previous_count',0)
        change=trend.get('change_percent')
        # A trend rising from zero prior cases has change_percent=None (you
        # can't compute a percent change from zero), which used to make it
        # silently fall through this check entirely -- exactly the case
        # (a brand-new cluster appearing) that's most worth surfacing here.
        is_rising = (change is not None and change > 0) or (previous == 0 and current > 0)
        if current>=3 and is_rising:
            epi.append(f"U ordinaciji je zabeležen porast sindroma „{trend['name']}“ ({current} slučajeva u tekućem periodu).")
    # The radar's own `clusters` output is its highest-confidence, already-
    # filtered signal (count>=5 AND signal_level in srednji/visok) -- it was
    # computed but never actually read here, so the differential analysis's
    # epidemiological context was missing the most significant findings the
    # radar itself was capable of producing.
    for cluster in radar.get('clusters',[]):
        epi.append(f"{cluster['title']} ({cluster['case_count']} slučajeva u poslednjih {cluster['window_days']} dana).")
    for pathogen in radar.get('confirmed_pathogens',[]):
        epi.append(f"Laboratorijski potvrđen trend u ordinaciji: {pathogen['name']} ({pathogen['confirmed_count']} potvrđenih nalaza).")
    epi = list(dict.fromkeys(epi))  # clusters/syndrome_trends can restate the same syndrome; de-duplicate identical lines
    return {
        'patient_id':patient.id,
        'generated_from':{'documents':len(documents),'encounters':len(encounters),'diagnoses':len(patient.clinical_profile.diagnoses)},
        'candidates':candidates[:6],
        'epidemiology_context':epi[:4],
        'disclaimer':'Stepen podudaranja nije verovatnoća dijagnoze. Rezultat je pomoć lekaru i mora se proveriti kliničkim pregledom i izvornom dokumentacijom. Podudaranje se zasniva na jednostavnom pretraživanju ključnih reči i ne prepoznaje pouzdano svaki oblik negacije ili kontekst (npr. porodičnu anamnezu) -- uvek proveriti izvornu dokumentaciju.'
    }
