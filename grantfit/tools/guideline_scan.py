"""Deterministic backstop scan of pasted guidelines.

Finds a small set of high-confidence, high-stakes rules directly in the text, so a model that
is confused or manipulated into omitting them cannot hide a hard eligibility failure:
- operating-budget caps / minimums
- mandatory geographic restrictions naming US states or multi-state regions
- 501(c)(3) / fiscal sponsorship requirements
- invitation-only / no-unsolicited-proposals language

Each finding's quote is a sentence of the source text itself (verified by construction).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from grantfit.tools.eligibility import REGIONS, US_STATES
from grantfit.tools.quotes import _501C3, parse_amounts, sentence_spans

_BUDGET = re.compile(r"\b(operating\s+)?budgets?\b|\bannual (revenue|expenses)\b", re.IGNORECASE)
_MAX_WORDS = re.compile(
    r"\b(under|below|less than|no more than|not (to )?exceed(ing)?|up to|or less|or below|maximum|at most|capped at|does not exceed|do not exceed)\b",
    re.IGNORECASE)
_MIN_WORDS = re.compile(r"\b(at least|minimum( of)?|or more|or greater|exceeding|above|greater than|over)\b",
                        re.IGNORECASE)
_MANDATORY = re.compile(
    r"\b(must|required|requires|only|eligible|eligibility|limited to|restricted to|open to)\b", re.IGNORECASE)
_INVITATION = re.compile(
    r"\b(by invitation only|invitation[- ]only|invited (applicants|organizations) only|"
    r"(does|do|will) not (accept|consider|review) unsolicited|"
    r"unsolicited (proposals|applications|requests|inquiries) (are|will) not (be )?(accepted|considered|reviewed))\b",
    re.IGNORECASE)


_GEO_VERB = re.compile(r"\b(serve|serves|serving|based in|located in|headquartered in|residents|operate|operating|"
                       r"service area|communities in)\b", re.IGNORECASE)
_PLACES = sorted({*US_STATES.values(), *REGIONS.keys()} - {"united states", "nationwide"}, key=len, reverse=True)
_PLACE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _PLACES) + r")\b", re.IGNORECASE)


@dataclass
class ScanFinding:
    kind: str                 # budget_max | budget_min | legal_status | geography | invitation_only
    value: object
    quote: str
    span: tuple[int, int]


def scan_guidelines(source_norm: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for a, b in sentence_spans(source_norm):
        s = source_norm[a:b]
        if _INVITATION.search(s):
            findings.append(ScanFinding("invitation_only", True, s, (a, b)))
        if _BUDGET.search(s) and _MANDATORY.search(s):
            amounts = parse_amounts(s)
            if len(amounts) == 1:  # one unambiguous threshold; ranges are left to the model
                is_max, is_min = bool(_MAX_WORDS.search(s)), bool(_MIN_WORDS.search(s))
                if is_max and not is_min:
                    findings.append(ScanFinding("budget_max", amounts[0], s, (a, b)))
                elif is_min and not is_max:
                    findings.append(ScanFinding("budget_min", amounts[0], s, (a, b)))
        if _GEO_VERB.search(s) and re.search(r"\b(must|only|limited to|restricted to)\b", s, re.IGNORECASE):
            places = list(dict.fromkeys(m.group(0) for m in _PLACE_RE.finditer(s)))
            if places:
                findings.append(ScanFinding("geography", places, s, (a, b)))
        if _501C3.search(s) and _MANDATORY.search(s):
            allowed = ["501c3"] + (["fiscally_sponsored"] if "fiscal" in s.lower() else [])
            findings.append(ScanFinding("legal_status", allowed, s, (a, b)))
    return findings
