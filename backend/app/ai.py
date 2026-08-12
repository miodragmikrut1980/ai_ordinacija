from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import httpx

from .models import TimelineItem
from .clinical_keywords import CLINICAL_SIGNIFICANCE_KEYWORDS


class ClinicAI:
    def __init__(self) -> None:
        self.base_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:4b")
        self.enabled = os.getenv("CLINIC_AI_PROVIDER", "local").lower() == "ollama"

    async def _ollama(self, prompt: str) -> str | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "stream": False},
                )
                response.raise_for_status()
                return response.json().get("response", "").strip() or None
        except Exception:
            return None

    @staticmethod
    def _combined(documents: list) -> str:
        return "\n\n".join(f"[{d.filename}]\n{d.text}" for d in documents if d.text.strip())

    async def summarize(self, patient_name: str, documents: list) -> str:
        text = self._combined(documents)
        if not text:
            return "No readable medical documentation has been uploaded for this patient."
        prompt = (
            "You are a medical documentation assistant. Do not diagnose. Summarize only facts found in the records. "
            "Use sections: Key history, Current problems, Therapies, Important findings, Missing information. "
            f"Patient: {patient_name}\nRecords:\n{text[:24000]}"
        )
        generated = await self._ollama(prompt)
        if generated:
            return generated
        lines = [x.strip() for x in re.split(r"[\n\r]+", text) if len(x.strip()) > 20]
        selected = lines[:10]
        return "Clinical record summary\n\n" + "\n".join(f"• {line[:300]}" for line in selected)

    async def answer(self, patient_name: str, documents: list, question: str) -> str:
        text = self._combined(documents)
        if not text:
            return "I cannot answer because there are no readable documents for this patient."
        prompt = (
            "Answer the clinician's question using only the supplied records. Cite filenames in brackets. "
            "State clearly when the answer is not present. Do not provide a diagnosis or invent details. "
            f"Patient: {patient_name}\nQuestion: {question}\nRecords:\n{text[:24000]}"
        )
        generated = await self._ollama(prompt)
        if generated:
            return generated
        terms = [w.lower() for w in re.findall(r"\w+", question) if len(w) > 3]
        scored: list[tuple[int, str, str]] = []
        for doc in documents:
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", doc.text):
                score = sum(1 for term in terms if term in sentence.lower())
                if score:
                    scored.append((score, doc.filename, sentence.strip()))
        scored.sort(reverse=True)
        if not scored:
            return "The uploaded records do not contain enough information to answer this question reliably."
        return "\n".join(f"[{name}] {sentence[:500]}" for _, name, sentence in scored[:5])

    @staticmethod
    def _source_for_line(documents: list, line: str) -> str | None:
        needle = line[:80].strip().lower()
        for doc in documents:
            if needle and needle in doc.text.lower():
                return doc.filename
        return None

    async def timeline(self, documents: list) -> list[TimelineItem]:
        text = self._combined(documents)
        if not text:
            return []
        date_pattern = re.compile(r"\b(?:\d{1,2}[./-]){2}\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
        items: list[TimelineItem] = []
        for line in [x.strip() for x in text.splitlines() if len(x.strip()) > 15]:
            match = date_pattern.search(line)
            lowered = line.lower()
            category = "other"
            if any(k in lowered for k in ["diagnos", "assessment"]): category = "diagnosis"
            elif any(k in lowered for k in ["therapy", "terap", "medication", "lek"]): category = "therapy"
            elif any(k in lowered for k in ["lab", "hemoglobin", "crp", "glucose"]): category = "lab"
            elif any(k in lowered for k in ["procedure", "operat", "surgery"]): category = "procedure"
            elif any(k in lowered for k in ["visit", "pregled", "control"]): category = "visit"
            if match or category != "other":
                items.append(TimelineItem(date=match.group(0) if match else None, title=line[:90], detail=line[:500], category=category, source=self._source_for_line(documents, line)))
        return items[:30]

    async def report(self, patient_name: str, documents: list) -> str:
        summary = await self.summarize(patient_name, documents)
        return (
            f"CLINICAL DOCUMENTATION REVIEW\nPatient: {patient_name}\nGenerated: "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n{summary}\n\n"
            "Note: This draft is generated from uploaded documentation and requires clinician review and approval."
        )


    async def pre_visit_briefing(self, patient, documents: list, encounters: list) -> dict:
        profile = patient.clinical_profile
        sources = sorted({d.filename for d in documents})
        combined = self._combined(documents)
        encounter_text = "\n".join(
            f"{e.visit_date.date()}: {e.chief_complaint}; assessment={e.assessment}; plan={e.plan}"
            for e in encounters[:8]
        )
        prompt = (
            "Prepare a concise pre-visit briefing for a clinician. Use only supplied facts. Do not diagnose or recommend treatment. "
            "Return strict JSON with keys active_problems, recent_findings, questions_to_verify, missing_information. "
            "Each value must be an array of short strings. Questions should help verify safety, adherence, symptom change, and follow-up. "
            f"Patient: {patient.full_name}\nKnown diagnoses: {profile.diagnoses}\nAllergies: {profile.allergies}\n"
            f"Medications: {profile.current_medications}\nEncounters:\n{encounter_text}\nDocuments:\n{combined[:18000]}"
        )
        generated = await self._ollama(prompt)
        parsed = {}
        if generated:
            try:
                match = re.search(r"\{.*\}", generated, re.S)
                parsed = json.loads(match.group(0) if match else generated)
            except Exception:
                parsed = {}
        lowered = combined.lower()
        abnormal_lines = []
        for doc in documents:
            for line in [x.strip() for x in doc.text.splitlines() if len(x.strip()) > 12]:
                if any(k in line.lower() for k in CLINICAL_SIGNIFICANCE_KEYWORDS):
                    abnormal_lines.append(f"[{doc.filename}] {line[:220]}")
        problems = list(profile.diagnoses)
        if not problems:
            problems = [e.assessment.strip() for e in encounters[:5] if e.assessment.strip()]
        questions = parsed.get("questions_to_verify") or []
        if not questions:
            questions = [
                "Have symptoms changed since the last documented encounter?",
                "Are all listed medications still being taken at the recorded dose?",
                "Have there been any new allergies, adverse reactions, admissions, or specialist visits?",
            ]
        missing = parsed.get("missing_information") or []
        if not profile.allergies: missing.append("Allergy status is not recorded.")
        if not profile.current_medications: missing.append("Current medication list is not recorded.")
        if not encounters: missing.append("No structured prior encounter is available.")
        return {
            "active_problems": (parsed.get("active_problems") or problems)[:8],
            "allergies": profile.allergies[:12],
            "medications": profile.current_medications[:15],
            "recent_findings": (parsed.get("recent_findings") or abnormal_lines)[:8],
            "questions_to_verify": questions[:8],
            "missing_information": list(dict.fromkeys(missing))[:8],
            "evidence_sources": sources[:20],
        }

    async def scribe_draft(self, patient, transcript: str, mode: str) -> dict:
        prompt = (
            "Ti si medicinski pisar za ordinaciju u Srbiji. Od transkripta napravi nacrt medicinskog pregleda na srpskom jeziku, latinicom. "
            "Koristi samo izgovorene činjenice. Ne postavljaj novu dijagnozu i ne izmišljaj podatke. "
            "Vrati strogi JSON sa ključevima chief_complaint, anamnesis, examination, assessment, plan, medication_changes, allergy_updates, missing_information. "
            "Poslednja tri polja su nizovi kratkih stavki. Ako nešto nije izgovoreno, ostavi prazno ili dodaj u missing_information. "
            f"Pacijent: {patient.full_name}. Režim: {mode}. Transkript:\n{transcript[:30000]}"
        )
        generated = await self._ollama(prompt)
        parsed = {}
        if generated:
            try:
                match = re.search(r"\{.*\}", generated, re.S)
                parsed = json.loads(match.group(0) if match else generated)
            except Exception:
                parsed = {}
        if not parsed:
            text = transcript.strip()
            sentences = [x.strip() for x in re.split(r'(?<=[.!?])\s+', text) if x.strip()]
            parsed = {
                'chief_complaint': sentences[0][:500] if sentences else '',
                'anamnesis': text[:5000],
                'examination': '', 'assessment': '', 'plan': '',
                'medication_changes': [], 'allergy_updates': [],
                'missing_information': ['Objektivni nalaz nije jasno izdvojen.', 'Procena i plan moraju biti potvrđeni od strane lekara.']
            }
        return {
            'chief_complaint': str(parsed.get('chief_complaint') or '')[:500],
            'anamnesis': str(parsed.get('anamnesis') or '')[:8000],
            'examination': str(parsed.get('examination') or '')[:8000],
            'assessment': str(parsed.get('assessment') or '')[:4000],
            'plan': str(parsed.get('plan') or '')[:4000],
            'medication_changes': list(parsed.get('medication_changes') or [])[:12],
            'allergy_updates': list(parsed.get('allergy_updates') or [])[:12],
            'missing_information': list(parsed.get('missing_information') or [])[:12],
            'source_map': {
                'chief_complaint': 'transkript', 'anamnesis': 'transkript',
                'examination': 'transkript' if parsed.get('examination') else 'nije navedeno',
                'assessment': 'transkript' if parsed.get('assessment') else 'zahteva unos lekara',
                'plan': 'transkript' if parsed.get('plan') else 'zahteva unos lekara'
            },
        }
