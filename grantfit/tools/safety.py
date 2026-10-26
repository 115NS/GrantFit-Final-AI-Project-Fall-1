"""Deterministic safety scanners.

The injection scan is defense in depth only: critical rules (eligibility gate, quote
verification, review triggers) are enforced in code regardless of what the scan finds.
"""

from __future__ import annotations

import os
import re

from grantfit.models import SafetyFlag
from grantfit.tools.quotes import normalize, sentence_spans

# Embedded in system prompts. If it ever appears in model output, the prompt leaked.
PROMPT_CANARY = "GF-CANARY-7F3A9C"

_INJECTION_PATTERNS: dict[str, list[str]] = {
    "override_instructions": [
        r"\b(ignore|disregard|forget|override|bypass)\s+(all\s+|any\s+)?(of\s+)?(the\s+|your\s+|these\s+|my\s+)?(previous|prior|above|earlier|preceding|system|original)\s+(instructions?|rules|prompts?|directions|guidance)\b",
        r"\b(ignore|disregard|forget)\s+(all|any|everything)\b[^.]{0,25}\b(instructions?|rules|you (were|have been) told)\b",
        r"\byou are (now|no longer)\b",
        r"\bnew (system )?instructions?\b",
        r"\b(act|behave|respond) as (an?|the)\b[^.]{0,40}\b(assistant|ai|model|reviewer|system)\b",
        r"\b(developer|jailbreak|dan) mode\b",
        r"(^|\s)(system|assistant|developer)\s*:",
        r"</?\s*(system|assistant|untrusted_funder_text|instructions?)\s*>",
        r"<\|im_(start|end)\|>",
    ],
    "force_decision": [
        r"\b(mark|label|classify|treat|deem|rate|score|declare|consider|record)\s+(this|the|that|all|every|each|any|our|my)\b[^.]{0,40}\bas\s+(fully\s+)?(eligible|ineligible|pursue_now|not_eligible|a (perfect|top) (fit|match)|high priority)\b",
        r"\bset\s+(the\s+)?(eligibility|tier|score|fit score|rating)\s+(to|=)",
        r"\b(skip|ignore|disable|bypass)\s+(the\s+|all\s+|any\s+)?(eligibility|verification|validation|safety|human review)\b",
        r"\b(give|assign|award)\b[^.]{0,30}\b(perfect|maximum|highest|top|full)\b[^.]{0,15}\b(score|rating|marks)\b",
        r"\b(all|every) (applicants?|organizations?) (must|should) be (marked|rated|scored|treated)\b",
    ],
    "reveal_secrets": [
        r"\b(reveal|print|show|output|repeat|display|share|leak|tell me|return)\b[^.]{0,40}\b(system prompt|your prompt|your instructions|hidden instructions|api[ _-]?keys?|secrets?|passwords?|tokens?|credentials|environment variables|\.env)\b",
        r"PORTKEY_API_KEY",
    ],
    "invent_evidence": [
        r"\b(invent|fabricate|make up|pretend)\b[^.]{0,40}\b(evidence|data|outcomes|statistics|results|success rate|track record|history|impact|funding)\b",
        r"\bassume\s+(that\s+)?(the\s+|this\s+|every\s+|all\s+)?(applicant|organization|nonprofit)s?\s+(has|have|had|was|were|is|are)\b",
        r"\b(say|assert|claim) (that )?(the )?(applicant|organization|nonprofit) (has|have|had|was|is)\b",
    ],
}
_COMPILED = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in _INJECTION_PATTERNS.items()}


def scan_injection(source_norm: str) -> tuple[list[SafetyFlag], list[tuple[int, int]]]:
    """Scan normalized guideline text. Returns flags and the flagged sentence spans."""
    flags: list[SafetyFlag] = []
    spans: list[tuple[int, int]] = []
    sentences = sentence_spans(source_norm)
    for category, patterns in _COMPILED.items():
        for pat in patterns:
            for m in pat.finditer(source_norm):
                # Expand to the enclosing sentence so quotes from it cannot be verified.
                a, b = next(((s, e) for s, e in sentences if s <= m.start() < e), (m.start(), m.end()))
                if (a, b) not in spans:
                    spans.append((a, b))
                    flags.append(SafetyFlag(
                        kind="prompt_injection", source="deterministic",
                        detail=f"Possible prompt injection ({category}) in pasted guidelines; "
                               "treated as data and excluded from verification.",
                        excerpt=source_norm[a:b][:160],
                    ))
    return flags, spans


# ---------------------------------------------------------------------------
# Award-probability / guarantee claims in generated text
# ---------------------------------------------------------------------------

_WIN = r"(win|winning|won|be funded|get funded|receive (the |this |an? )?(grant|award)|be awarded (the |this |an? )?(grant|award|funding)|secure (the |this )?(grant|award|funding))"
_PROBABILITY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bguarantee(d|s)?\b",
        r"\b(certain|sure|bound|poised|destined|assured|positioned|set|expected|likely|unlikely|probably|very likely|highly likely)\b[^.]{0,20}\bto " + _WIN,
        # "will win" is a prediction; "would" only with a likelihood adverb ("would likely win").
        # Conditional/question forms ("whether ... will be funded") are excluded below.
        r"\b(will (almost )?((definitely|certainly|surely|probably|likely) )?|would (almost )?(definitely|certainly|surely|probably|likely) )" + _WIN + r"\b",
        r"\b\d{1,3}(\.\d+)?\s?%\s?(chance|probability|likelihood|odds|success rate|win rate)",
        r"\b(chance|probability|likelihood|odds)\b[^.]{0,25}\b(of|for)\b[^.]{0,15}\b(winning|success|funding|award|being funded|approval|selection)\b",
        r"\b(high|strong|good|excellent|low|slim|great) (chance|probability|likelihood|odds)\b",
        r"\b(shoo-in|sure thing|lock to win|slam dunk)\b",
    ]
]
REDACTION = "[removed by GrantFit validator: award-likelihood claim]"


_PREDICTION = _PROBABILITY_PATTERNS[2]
_CONDITIONAL = re.compile(r"\b(whether|if|which|what|how much|confirm|clarify|ask)\b", re.IGNORECASE)


def find_probability_claims(text: str) -> list[str]:
    text = text or ""
    hits = []
    for p in _PROBABILITY_PATTERNS:
        for m in p.finditer(text):
            if p is _PREDICTION and _CONDITIONAL.search(text[max(0, m.start() - 80):m.start()]):
                continue  # a question about funding, not a prediction
            hits.append(m.group(0))
    return hits


def sanitize_probability_claims(text: str) -> tuple[str, list[str]]:
    """Replace each sentence containing an award-likelihood claim with a redaction marker."""
    norm = normalize(text)
    hits: list[str] = []
    out = []
    for a, b in sentence_spans(norm):
        sentence = norm[a:b]
        found = find_probability_claims(sentence)
        if found:
            hits.extend(found)
            out.append(REDACTION)
        else:
            out.append(sentence)
    return (" ".join(out) if hits else text), hits


# ---------------------------------------------------------------------------
# Prompt / secret leakage in generated text
# ---------------------------------------------------------------------------


def find_leaks(text: str) -> list[str]:
    leaks = []
    if PROMPT_CANARY in text:
        leaks.append("system prompt canary")
    key = os.environ.get("PORTKEY_API_KEY", "").strip()
    if key and len(key) >= 8 and key in text:
        leaks.append("API key")
    return leaks


def redact_leaks(text: str) -> str:
    text = text.replace(PROMPT_CANARY, "[REDACTED]")
    key = os.environ.get("PORTKEY_API_KEY", "").strip()
    if key and len(key) >= 8:
        text = text.replace(key, "[REDACTED]")
    return text
