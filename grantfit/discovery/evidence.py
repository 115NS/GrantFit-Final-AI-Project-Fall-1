"""Deterministic evidence scan of retrieved page text.

Finds sentences that state a deadline, funding amount, eligibility rule, or geographic focus and
returns them as exact quotes. A field is filled only if such a sentence exists; the quote is a
verbatim span of the retrieved text (verified by construction). No model call, no inference.
"""

from __future__ import annotations

import re
from datetime import date

from grantfit.discovery.models import EvidenceField
from grantfit.tools.eligibility import REGIONS, US_STATES
from grantfit.tools.quotes import normalize, parse_amounts, sentence_spans
from grantfit.tools.safety import scan_injection

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_MON_ABBR = "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_RE = re.compile(
    rf"\b(({_MONTHS}|{_MON_ABBR})\.?\s+\d{{1,2}}(st|nd|rd|th)?,?\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})\b",
    re.IGNORECASE)
DEADLINE_WORDS = re.compile(r"\b(deadline|due|closes?|closing date|submit(ted)? by|applications? (are )?(accepted|received) (until|through|by)|no later than)\b", re.IGNORECASE)
ROLLING = re.compile(r"\brolling basis\b|\brolling (deadline|applications?)\b|\baccepted year[- ]round\b", re.IGNORECASE)
FUND_WORDS = re.compile(r"\b(grants?|awards?|funding|up to|range|ranging|maximum|request)\b", re.IGNORECASE)
ELIG_WORDS = re.compile(r"\b(eligib\w*|must be|must have|501\s*\(?c\)?\s*\(?3\)?|nonprofit organizations? (that|with|located)|only organizations|not eligible|ineligible)\b", re.IGNORECASE)
GEO_VERB = re.compile(r"\b(serv(e|es|ing)|located|based|residents|communit(y|ies) (in|of)|region|county|counties|towns?)\b", re.IGNORECASE)
_PLACES = sorted({*US_STATES.values(), *REGIONS.keys()} - {"united states", "nationwide"}, key=len, reverse=True)
# "<Place> Native" (e.g. "Alaska Native", "Native Hawaiian") names a population, not a service area.
PLACE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _PLACES) + r"|[A-Z][a-z]+ County)\b(?!\s+Natives?\b)",
                      re.IGNORECASE)
MIN_WORDS = 6   # evidence must be a statement, not a menu item or heading

MAX_QUOTE = 300
# Appended to 'could not read' notes; the UI states it once in its Limitations section instead.
UNREAD_NOTE = " Tables, PDFs, and linked pages are not interpreted; check the source."


def _sentences(text: str, flagged: list[tuple[int, int]] = ()) -> list[str]:
    """Sentences of the normalized text, excluding any that overlap injection-flagged spans."""
    norm = normalize(text)
    return [norm[a:b].strip() for a, b in sentence_spans(norm)
            if 12 <= b - a <= MAX_QUOTE * 2 and len(norm[a:b].split()) >= MIN_WORDS
            and not any(a < fb and fa < b for fa, fb in flagged)]


def parse_date(text: str) -> date | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    raw = re.sub(r"(st|nd|rd|th),?", ",", m.group(0), count=1) if re.search(r"\d(st|nd|rd|th)", m.group(0)) else m.group(0)
    raw = raw.replace(".", "").replace(",", ", ").replace("  ", " ")
    from datetime import datetime
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(re.sub(r"\s+", " ", raw.replace(" ,", ",")).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _clip(s: str) -> str:
    return s if len(s) <= MAX_QUOTE else s[:MAX_QUOTE].rsplit(" ", 1)[0] + " …"


def scan_page(text: str, today: date | None = None) -> dict:
    """Return evidence fields + parsed values + safety warnings for retrieved page text."""
    today = today or date.today()
    flags, flagged = scan_injection(normalize(text))
    sents = _sentences(text, flagged)   # flagged passages are never used as evidence
    unread = UNREAD_NOTE
    out: dict = {"deadline": EvidenceField(note="GrantFit could not read a deadline statement on this page." + unread),
                 "deadline_date": None,
                 "funding": EvidenceField(note="GrantFit could not read a grant amount on this page." + unread),
                 "award_min": None, "award_max": None,
                 "eligibility": EvidenceField(note="GrantFit could not read an eligibility statement on this page." + unread),
                 "geography": EvidenceField(note="GrantFit could not read a geographic focus on this page." + unread),
                 "safety_warnings": []}

    # Deadline: prefer a sentence with deadline wording and a parseable future date.
    candidates = [(s, parse_date(s)) for s in sents if DEADLINE_WORDS.search(s) and DATE_RE.search(s)]
    future = [(s, d) for s, d in candidates if d and d >= today]
    pick = future[0] if future else (candidates[0] if candidates else None)
    if pick:
        s, d = pick
        past = d is not None and d < today
        out["deadline"] = EvidenceField(
            status="quoted_from_page", value=(d.isoformat() if d else None), quote=_clip(s),
            note="This date appears to have passed; check for the next cycle." if past else None)
        out["deadline_date"] = d
    else:
        roll = next((s for s in sents if ROLLING.search(s)), None)
        if roll:
            out["deadline"] = EvidenceField(status="quoted_from_page", value="Rolling", quote=_clip(roll))

    # Funding: a sentence with funding wording and at least one dollar amount.
    for s in sents:
        if FUND_WORDS.search(s) and "$" in s:
            amounts = [a for a in parse_amounts(s) if a >= 100]
            if amounts:
                lo, hi = min(amounts), max(amounts)
                value = f"${hi:,.0f}" if lo == hi else f"${lo:,.0f} to ${hi:,.0f}"
                out["funding"] = EvidenceField(status="quoted_from_page", value=value, quote=_clip(s),
                                               note="Amounts as stated in the quoted sentence.")
                out["award_min"], out["award_max"] = (None, hi) if lo == hi else (lo, hi)
                break

    elig = [s for s in sents if ELIG_WORDS.search(s)]
    if elig:
        out["eligibility"] = EvidenceField(status="quoted_from_page", value="Stated on the page (see quote)",
                                           quote=_clip(elig[0]),
                                           note=f"{len(elig)} eligibility statement(s) found; first shown."
                                           if len(elig) > 1 else None)
    geo = [s for s in sents if GEO_VERB.search(s) and PLACE_RE.search(s)]
    if geo:
        places = list(dict.fromkeys(m.group(0) for m in PLACE_RE.finditer(geo[0])))
        out["geography"] = EvidenceField(status="quoted_from_page", value="Mentions " + ", ".join(places[:4]),
                                         quote=_clip(geo[0]),
                                         note="A place named in the source text; confirm whether it limits eligibility.")

    if flags:
        out["safety_warnings"].append(
            f"This page contains {len(flags)} passage(s) that look like instructions to an AI. They are "
            "treated as data, excluded from verification, and cannot change any result.")
    return out


STOPWORDS = {"and", "the", "for", "with", "from", "into", "our", "your", "their", "that", "this", "are", "was",
             "nonprofit", "nonprofits", "program", "programs", "grant", "grants", "funding", "organization"}


def keyword_terms(keywords: str) -> list[str]:
    return list(dict.fromkeys(w for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", keywords.lower()) if w not in STOPWORDS))


def match_level(found: list[str], keywords: str) -> str:
    terms = keyword_terms(keywords)
    if terms and len(found) == len(terms):
        return "full"
    return "partial" if found else "none"


def keyword_relevance(keywords: str, *texts: str) -> tuple[list[str], str]:
    """Which of the user's keywords (not mission prose) appear in the retrieved texts."""
    terms = keyword_terms(keywords)
    hay = " ".join(t.lower() for t in texts if t)
    found = [w for w in terms if re.search(rf"\b{re.escape(w)}", hay)]
    if not found:
        return found, "None of your keywords appear in the retrieved text."
    if len(found) == len(terms):
        return found, f"Mentions all of your keywords ({', '.join(found)})."
    missing = [w for w in terms if w not in found]
    return found, f"Mentions {', '.join(found)}; does not mention {', '.join(missing)}."
