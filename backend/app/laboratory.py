"""Conservative laboratory-result parsing and trend support.

This module deliberately creates *draft* values only.  Laboratory reports
vary in units, reference ranges and layout, therefore a parsed value is not a
medical-record entry until a clinician verifies it in the UI.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import LabResultCreate


# Names are intentionally explicit.  A broad regex that guessed every number
# in a report would create unsafe false laboratory results.
TEST_ALIASES = {
    "crp": "CRP",
    "c-reactive protein": "CRP",
    "glukoza": "Glukoza",
    "glucose": "Glukoza",
    "hemoglobin": "Hemoglobin",
    "hgb": "Hemoglobin",
    "leukociti": "Leukociti",
    "wbc": "Leukociti",
    "trombociti": "Trombociti",
    "plt": "Trombociti",
    "kreatinin": "Kreatinin",
    "creatinine": "Kreatinin",
    "tsh": "TSH",
    "hba1c": "HbA1c",
}

_NUMBER = r"(?P<value>-?\d+(?:[.,]\d+)?)"
_UNIT = r"(?P<unit>[A-Za-zµ/%][A-Za-z0-9µ/%^ .-]{0,24})?"
_RANGE = r"(?:\s*(?:\[|\()\s*(?P<low>-?\d+(?:[.,]\d+)?)\s*[-–]\s*(?P<high>-?\d+(?:[.,]\d+)?)\s*(?:\]|\)))?"


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _abnormality(value: float | None, low: float | None, high: float | None) -> str:
    if value is None or (low is None and high is None):
        return "unknown"
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"


def extract_lab_candidates(text: str, collected_at: datetime | None = None, page_offsets: list[int] | None = None) -> list[tuple[LabResultCreate, str]]:
    """Return deduplicated, conservative parsed lab candidates.

    Only a recognized test name followed by a numeric value on the same line
    is accepted. The returned abnormality is informational and must still be
    checked against the original report by a clinician.

    `page_offsets` (see extractors.py:extract_text_with_method), if given,
    lets each candidate carry the 1-indexed page of the original document it
    was found on -- this is what backs the document-viewer citation feature
    (jump straight to the right page instead of re-reading the whole file).
    """
    import bisect
    seen: set[tuple[str, float, str | None]] = set()
    results: list[tuple[LabResultCreate, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = " ".join(raw_line.strip().split())
        line_offset = offset
        offset += len(raw_line)
        if not line or len(line) > 500:
            continue
        lowered = line.lower()
        for alias, name in TEST_ALIASES.items():
            start = lowered.find(alias)
            if start < 0:
                continue
            suffix = line[start + len(alias):]
            match = re.search(r"(?:\s*[:=]\s*|\s+)" + _NUMBER + r"\s*" + _UNIT + _RANGE, suffix, re.I)
            if not match:
                continue
            value = _number(match.group("value"))
            unit = (match.group("unit") or "").strip() or None
            low, high = _number(match.group("low")), _number(match.group("high"))
            reference_range = f"{match.group('low')}–{match.group('high')}" if low is not None or high is not None else None
            key = (name, value if value is not None else float("nan"), unit)
            if key in seen:
                break
            seen.add(key)
            source_page = None
            if page_offsets:
                # bisect_right - 1: the last page whose offset is <= this line's
                # offset is the page the line actually falls on.
                source_page = bisect.bisect_right(page_offsets, line_offset)
            result = LabResultCreate(name=name, value=value, unit=unit, reference_range=reference_range,
                                     collected_at=collected_at or datetime.now(timezone.utc), notes=f"Prepoznato iz dokumenta: {line[:260]}",
                                     source_page=source_page)
            results.append((result, _abnormality(value, low, high)))
            break
    return results[:80]
