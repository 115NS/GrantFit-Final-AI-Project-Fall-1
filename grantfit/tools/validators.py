"""Deterministic output validators: unsupported claims, ungrounded numbers, funding-history
claims, award-probability language, and prompt/secret leaks in model output."""

from __future__ import annotations

import re

from grantfit.models import (
    FitConceptAssessment, NonprofitProfile, ProposalConcept, SafetyFlag, UnsupportedClaim,
)
from grantfit.tools.quotes import parse_amounts
from grantfit.tools.safety import find_leaks, redact_leaks, sanitize_probability_claims

_STRONG = re.compile(
    r"\b(proven|proves?|guarantee[sd]?|always|never fails|eliminat\w*|doubles?|doubled|tripled?|"
    r"evidence[- ]based|research[- ](shows|proves)|scientifically|best[- ]in[- ]class|only program|"
    r"transform\w*|100%)\b", re.IGNORECASE)
_PERCENT = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%")
_COUNT = re.compile(r"\b\d[\d,]*\b")


def _numbers(text: str) -> set[float]:
    vals = {float(p) for p in _PERCENT.findall(text)}
    vals |= {float(n.replace(",", "")) for n in _COUNT.findall(text) if n.replace(",", "").isdigit()}
    vals |= set(parse_amounts(text))
    return vals


def check_unsupported_claims(
    profile: NonprofitProfile, concept: ProposalConcept
) -> list[UnsupportedClaim]:
    """Concept evidence claims not backed by the nonprofit's supplied evidence assets."""
    evidence = " ".join(profile.evidence_assets)
    ev_numbers = _numbers(evidence)
    out = []
    for claim in concept.evidence_claims:
        if not profile.evidence_assets:
            out.append(UnsupportedClaim(claim=claim, source="deterministic",
                                        reason="No evidence assets were supplied for this nonprofit."))
            continue
        missing = sorted(n for n in _numbers(claim) if n not in ev_numbers)
        strong = [m.group(0) for m in _STRONG.finditer(claim)
                  if m.group(0).lower() not in evidence.lower()]
        reasons = []
        if missing:
            reasons.append(f"figures {[int(n) if n.is_integer() else n for n in missing]} not found in evidence assets")
        if strong:
            reasons.append(f"strong claim wording {strong} not substantiated by evidence assets")
        if reasons:
            out.append(UnsupportedClaim(claim=claim, source="deterministic", reason="; ".join(reasons)))
    return out


def _assessment_texts(a: FitConceptAssessment) -> list[str]:
    texts = [a.summary, *a.evidence_gaps]
    for group in (a.fit, a.concept):
        texts += [d.rationale for _, d in group]
    return texts


def sanitize_assessment(
    a: FitConceptAssessment, grounding_text: str, profile: NonprofitProfile
) -> tuple[FitConceptAssessment, list[SafetyFlag]]:
    """Redact award-likelihood claims and leaks; flag ungrounded numbers and invented funding history.

    `grounding_text` = everything the model was legitimately given (structured inputs plus
    guideline text outside injection-flagged spans).
    """
    flags: list[SafetyFlag] = []
    data = a.model_dump()

    def clean(text: str) -> str:
        new, hits = sanitize_probability_claims(text)
        for h in hits:
            flags.append(SafetyFlag(kind="award_probability_claim", source="deterministic",
                                    detail="Award-likelihood language removed from model output.",
                                    excerpt=h[:160]))
        for leak in find_leaks(new):
            flags.append(SafetyFlag(kind="prompt_or_secret_leak", source="deterministic",
                                    detail=f"Model output contained {leak}; redacted."))
        return redact_leaks(new)

    data["summary"] = clean(data["summary"])
    data["evidence_gaps"] = [clean(t) for t in data["evidence_gaps"]]
    for group in ("fit", "concept"):
        for dim in data[group].values():
            dim["rationale"] = clean(dim["rationale"])
    cleaned = FitConceptAssessment.model_validate(data)

    grounded = _numbers(grounding_text)
    amounts = sorted({a for a in parse_amounts(grounding_text) if a > 0})

    def derived_pct(p: float) -> bool:
        # e.g. "7% of the annual budget" = 60,000 / 850,000; a ratio of two input amounts.
        return any(abs(100 * a / b - p) <= 0.5 for i, a in enumerate(amounts) for b in amounts[i + 1:])

    for text in _assessment_texts(cleaned):
        pct = [float(p) for p in _PERCENT.findall(text)
               if float(p) not in grounded and not derived_pct(float(p))]
        amt = [v for v in parse_amounts(text) if v not in grounded]
        if pct or amt:
            figures = [f"{p:g}%" for p in pct] + [f"${v:,.0f}" for v in amt]
            flags.append(SafetyFlag(kind="ungrounded_number", source="deterministic",
                                    detail=f"Model output cites figures not present in or derivable "
                                           f"from the inputs: {figures}",
                                    excerpt=text[:160]))

    if not profile.prior_funders:
        history = re.compile(r"\b(previously|already|has been|have been|was) (funded|supported) by\b|"
                             r"\b(past|prior|existing|current) (grantee|funder relationship|relationship with)\b",
                             re.IGNORECASE)
        for text in _assessment_texts(cleaned):
            if history.search(text):
                flags.append(SafetyFlag(kind="funding_history_claim", source="deterministic",
                                        detail="Model asserted a funding history that was not supplied.",
                                        excerpt=text[:160]))
    return cleaned, flags
