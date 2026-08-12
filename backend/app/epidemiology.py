from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
import re

from .clinical_keywords import any_unnegated

SYNDROMES = {
    "respiratorni": ["kašalj", "kasalj", "bol u grlu", "curenje nosa", "zapušen nos", "otežano disanje", "otezano disanje", "temperatura"],
    "gastrointestinalni": ["povraćanje", "povracanje", "proliv", "dijareja", "mučnina", "mucnina", "bol u stomaku"],
    "febrilni": ["temperatura", "febril", "groznica", "drhtavica"],
    "osip": ["osip", "urtikarija", "promene na koži", "promene na kozi"],
    "urinarni": ["dizurija", "bol pri mokrenju", "pečenje pri mokrenju", "pecenje pri mokrenju", "učestalo mokrenje", "ucestalo mokrenje"],
}
PATHOGENS = {
    "Grip A": [r"grip\s*a", r"influenza\s*a"],
    "Grip B": [r"grip\s*b", r"influenza\s*b"],
    "COVID-19": [r"covid(?:-19)?", r"sars[- ]?cov[- ]?2"],
    "RSV": [r"\brsv\b", r"respiratorni sincicijalni"],
    "Streptokok grupa A": [r"streptokok(?:\s+grupa)?\s*a", r"strep\s*a"],
}
POSITIVE_WORDS = ("pozitivan", "pozitivna", "pozitivno", "detektovan", "dokazan", "potvrđen", "potvrdjen")

def _text_for_encounter(e) -> str:
    return " ".join([e.chief_complaint, e.anamnesis, e.examination, e.assessment, e.plan]).lower()

def _contains_any(text: str, terms: list[str]) -> bool:
    # Uses the same negation-aware matching as the AI differential analysis
    # (see clinical_keywords.py): plain substring matching cannot tell
    # "kašalj" (cough, present) from "bez kašlja" (no cough), which would
    # otherwise count an explicitly ruled-out symptom as a positive case in
    # the epidemiology radar and its trend chart -- the same class of bug
    # differential.py was hardened against, previously present here too.
    return any_unnegated(text, terms)

def _pathogens(text: str) -> list[str]:
    low=text.lower(); found=[]
    if not any(w in low for w in POSITIVE_WORDS): return found
    for name, patterns in PATHOGENS.items():
        if any(re.search(p, low) for p in patterns): found.append(name)
    return found

def build_radar(encounters, documents, days: int = 7) -> dict:
    now=datetime.now(timezone.utc); start=now-timedelta(days=days); previous_start=start-timedelta(days=days)
    current=[e for e in encounters if e.visit_date.astimezone(timezone.utc)>=start]
    previous=[e for e in encounters if previous_start<=e.visit_date.astimezone(timezone.utc)<start]
    def counts(items):
        result=Counter()
        for e in items:
            text=_text_for_encounter(e)
            for name,terms in SYNDROMES.items():
                if _contains_any(text,terms):result[name]+=1
        return result
    curr,prev=counts(current),counts(previous)
    syndrome_rows=[]
    for name,count in curr.most_common():
        old=prev.get(name,0); change=None if old==0 else round((count-old)*100/old)
        level="visok" if count>=5 and (old==0 or count>=old*1.5) else "srednji" if count>=3 else "nizak"
        syndrome_rows.append({"name":name,"current_count":count,"previous_count":old,"change_percent":change,"signal_level":level})
    pathogen=Counter()
    for d in documents:
        if d.uploaded_at>=start:
            pathogen.update(_pathogens(d.text))
    for e in current:pathogen.update(_pathogens(_text_for_encounter(e)))
    confirmed=[{"name":name,"confirmed_count":count} for name,count in pathogen.most_common()]
    clusters=[]
    for row in syndrome_rows:
        if row["current_count"]>=5 and row["signal_level"] in ("srednji","visok"):
            clusters.append({"title":f"Mogući {row['name']} klaster","case_count":row["current_count"],"window_days":days,"confidence":"srednja" if row["previous_count"] else "niska","note":"Signal zahteva pregled lekara; ne potvrđuje epidemiju ni uzročnika."})
    sample=len(current)
    # Daily breakdown per syndrome, aligned to calendar days, for a trend
    # chart in the UI. This is a coarser view than the current/previous
    # period totals above (it buckets by calendar day rather than an exact
    # rolling window), which is the right trade-off for a chart meant to
    # give a clinician a quick visual read, not for the precise period
    # comparison the other fields are used for.
    daily_counts=[]
    today_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    for i in range(days-1,-1,-1):
        day_start=today_start-timedelta(days=i); day_end=day_start+timedelta(days=1)
        day_items=[e for e in current if day_start<=e.visit_date.astimezone(timezone.utc)<day_end]
        daily_counts.append({"date":day_start.date().isoformat(),"counts":dict(counts(day_items))})
    return {
        "period_days":days,"generated_at":now,"encounter_count":sample,"minimum_sample_met":sample>=5,
        "syndrome_trends":syndrome_rows,"confirmed_pathogens":confirmed,"clusters":clusters,"daily_counts":daily_counts,
        "disclaimer":"Trendovi su zbirni signali iz podataka ordinacije. Simptomi ne potvrđuju uzročnika; imenovani patogeni prikazuju se samo kada je u dokumentaciji evidentiran pozitivan ili potvrđen nalaz."
    }
