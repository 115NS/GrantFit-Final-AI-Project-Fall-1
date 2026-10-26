"""Deterministic quote verification against the original guideline text.

A quote is verified only if it is an exact span of the source after typography/whitespace
normalization (no paraphrase, no ellipses, no case changes), is long enough to be meaningful,
and does not overlap text flagged as a possible prompt injection.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

MIN_QUOTE_CHARS = 12
MIN_QUOTE_WORDS = 3

_TYPO = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
})


def normalize(text: str) -> str:
    """NFKC + straight quotes/dashes; whitespace runs -> one space, or one newline if the run
    contained a line break (kept as a sentence boundary; same length, so indices line up with
    the space-only form used for quote matching)."""
    text = unicodedata.normalize("NFKC", text or "").translate(_TYPO)
    text = re.sub(r"[^\S\n]*\n\s*", "\n", text)
    return re.sub(r"[^\S\n]+", " ", text).strip()


def _flat(text: str) -> str:
    return text.replace("\n", " ")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Spans of sentences/lines in already-normalized text. A line break ends a segment only
    when the next line starts like a new item (capital, digit, bullet), so hard-wrapped
    sentences stay whole while headings are separated."""
    spans, start = [], 0
    for m in re.finditer(r"(?<=[.!?;:])\s+|\n(?=[A-Z0-9\"'(\-*•])|\s+(?=[-*•]\s)", text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if b > a]


def verify_quote(
    quote: str | None, source_norm: str, flagged_spans: list[tuple[int, int]] = ()
) -> tuple[bool, str]:
    """Returns (verified, reason). `source_norm` must already be normalize()d."""
    if not quote or not quote.strip():
        return False, "No quote supplied."
    q = _flat(normalize(quote))
    if len(q) < MIN_QUOTE_CHARS or len(q.split()) < MIN_QUOTE_WORDS:
        return False, "Quote too short to verify."
    if "..." in q or "…" in q:
        return False, "Quote contains an ellipsis (not an exact span)."
    idx = _flat(source_norm).find(q)
    if idx < 0:
        return False, "Quote not found verbatim in the guideline text."
    end = idx + len(q)
    for a, b in flagged_spans:
        if idx < b and a < end:
            return False, "Quote overlaps text flagged as a possible prompt injection."
    return True, "Exact quote found in guideline text."


# ---------------------------------------------------------------------------
# Value-vs-quote consistency: an extracted value must be visible in its own quote.
# ---------------------------------------------------------------------------

_MONEY = re.compile(
    r"(\$)?\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(million|billion|thousand|mm|m|k|b)?\b",
    re.IGNORECASE,
)
_MULT = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mm": 1e6, "billion": 1e9, "b": 1e9}


def parse_amounts(text: str) -> list[float]:
    """Dollar amounts and large numbers in text ('$3 million' -> 3e6, '$250,000' -> 250000)."""
    out = []
    for m in _MONEY.finditer(text or ""):
        dollar, num, mult = m.group(1), m.group(2), (m.group(3) or "").lower()
        value = float(num.replace(",", ""))
        if mult:
            value *= _MULT[mult]
        elif not dollar and value < 1000:
            continue  # skip small bare numbers like '501(c)(3)' or '5 pages'
        out.append(value)
    return out


def parse_small_numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", text or "")]


def amount_in_quote(value: float | None, quote: str) -> bool:
    if value is None:
        return False
    return any(abs(a - value) <= max(1.0, 0.005 * value) for a in parse_amounts(quote))


_501C3 = re.compile(r"501\s*\(?\s*c\s*\)?\s*\(?\s*3\s*\)?", re.IGNORECASE)


def legal_status_in_quote(status: str, quote: str) -> bool:
    q = quote.lower()
    if status == "501c3":
        return bool(_501C3.search(quote))
    if status == "fiscally_sponsored":
        return "fiscal" in q
    return False


def date_in_quote(d: date, quote: str) -> bool:
    q = _flat(normalize(quote)).lower()
    month = d.strftime("%B").lower()
    candidates = {
        d.isoformat(), f"{d.month}/{d.day}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{month} {d.day}, {d.year}", f"{month} {d.day} {d.year}", f"{d.day} {month} {d.year}",
        f"{month[:3]} {d.day}, {d.year}", f"{month[:3]}. {d.day}, {d.year}",
    }
    return any(c in q for c in candidates)
