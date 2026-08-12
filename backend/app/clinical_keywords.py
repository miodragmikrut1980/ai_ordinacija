"""Shared keyword lists and lightweight negation detection for the
non-LLM clinical-significance heuristics used in a few places in this
codebase (the document "needs attention" flag, the pre-visit briefing's
abnormal-finding scan, the epidemiology radar's syndrome counting, and the
AI differential analysis).

These are deliberately simple substring checks, not real NLP -- see the
negation notes below for the more serious limitation this implies. Keeping
these lists and this logic in ONE place instead of duplicating them per
call site matters: this project shipped for a while with an English-only
version of the significance keyword list in store.py, which meant the
"needs attention" flag almost never fired for the Serbian-language
documents this app is built around. A second, independent copy of the same
list was later found in ai.py with the identical bug, never fixed when the
first one was. Importing from here instead of hand-copying the tuple is
what prevents that class of bug from recurring a third time.

The negation helpers below were originally written only for differential.py
(the AI diagnosis-candidate scorer). epidemiology.py's syndrome counter was
found to have the exact same class of bug the significance-keyword list
once had: plain substring matching cannot tell "kašalj" (cough, present)
from "bez kašlja" (no cough) or "test negativan" (test negative), so an
encounter note that explicitly rules out a symptom was silently counted as
a positive case for that syndrome in the epidemiology radar and its trend
chart. Centralizing negation handling here, rather than re-deriving it a
second time in epidemiology.py, is the same fix for the same reason.
"""
from __future__ import annotations

CLINICAL_SIGNIFICANCE_KEYWORDS: tuple[str, ...] = (
    "critical", "urgent", "abnormal", "elevated", "positive", "high", "low",
    "kritič", "kritic", "hitno", "povišen", "povisen", "snižen", "snizen",
    "pozitivan", "pozitivna", "pozitivno", "visok", "nizak", "odstupanje",
)

# A short list of common Serbian/English negation cues checked in the text
# immediately before a matched term (within NEGATION_WINDOW characters). This
# is a heuristic, not linguistic negation scope detection (it will not catch
# every phrasing, e.g. negation more than NEGATION_WINDOW characters before
# the term, or negation stated after the term as in "kašalj nije prisutan").
# Every feature built on this still carries a disclaimer that a clinician
# must verify against the source documentation -- see differential.py and
# the epidemiology radar's disclaimer text.
NEGATION_CUES: tuple[str, ...] = (
    'bez ', 'nema ', 'nije ', 'ne ', 'odsutan', 'odsutno', 'negira', 'negativan', 'negativna', 'negativno',
    'no ', 'not ', 'without ', 'denies', 'negative',
)
NEGATION_WINDOW = 18


def term_is_negated(text: str, position: int) -> bool:
    """True if a negation cue appears in the NEGATION_WINDOW characters of
    `text` immediately before index `position` (the start of a matched
    term)."""
    window_start = max(0, position - NEGATION_WINDOW)
    window = text[window_start:position]
    return any(cue in window for cue in NEGATION_CUES)


def find_unnegated(text: str, term: str) -> bool:
    """True if `term` occurs in `text` at least once without an immediately
    preceding negation cue. `text` must already be lowercased (callers
    lowercase once for a whole document/encounter rather than per term)."""
    start = 0
    while True:
        idx = text.find(term, start)
        if idx == -1:
            return False
        if not term_is_negated(text, idx):
            return True
        start = idx + len(term)


def any_unnegated(text: str, terms) -> bool:
    """True if any term in `terms` occurs in `text` without negation."""
    return any(find_unnegated(text, term) for term in terms)
