"""Human-review classification: critical items vs. informational advisories.

`needs_human_review` is true only when a person must resolve something before acting:
- hard safety risks (prompt injection, provider refusal, leaked internal text, invented funding
  history, an opportunity-type conflict);
- hard requirements that are unverified, contradictory, or could not be evaluated;
- an eligibility failure that rests on requirements read from guideline text;
- material unsupported claims found by deterministic checks (figures or strong wording absent
  from the nonprofit's own evidence);
- incomplete (degraded) pipeline stages.

Everything else is an advisory: shown to the user, never hidden, but not presented as a
blocking warning (e.g. unknown capacity, defaulted effort hours, funder restrictions to
confirm, model-only claim judgments, self-reported low confidence, redacted wording).
"""

from __future__ import annotations

from grantfit.models import (
    DeadlineCheck, EffortEstimate, EligibilityResult, ExtractionReport, FitConceptAssessment,
    FundingOpportunity, SafetyFlag, UnsupportedClaim,
)

CRITICAL_FLAGS = {"prompt_injection", "provider_content_filter", "prompt_or_secret_leak",
                  "opportunity_type_conflict", "funding_history_claim"}
GUIDELINE_DERIVED = ("x", "scan", "contradiction", "extraction_unavailable")


def classify_review(
    *,
    eligibility: EligibilityResult,
    opportunity: FundingOpportunity | None,
    opportunity_type: str | None,
    degraded: list[str],
    assessment: FitConceptAssessment | None,
    unsupported: list[UnsupportedClaim],
    extraction: ExtractionReport | None,
    flags: list[SafetyFlag],
    deadline: DeadlineCheck,
    effort: EffortEstimate,
    warnings: list[str],
    score_notes: list[str] = (),
) -> tuple[list[str], list[str]]:
    """Returns (required review items, advisories)."""
    required: list[str] = []
    advisory: list[str] = []

    # Eligibility
    if eligibility.overall == "UNKNOWN":
        n = sum(1 for c in eligibility.checks if c.hard and c.status == "UNKNOWN")
        required.append(f"Eligibility UNKNOWN: {n or 'some'} requirement(s) could not be confirmed; resolve them "
                        "with the funder before writing.")
    elif eligibility.overall == "FAIL":
        failing = [c for c in eligibility.checks if c.hard and c.status == "FAIL"]
        if any(c.criterion_id.startswith(GUIDELINE_DERIVED) for c in failing):
            required.append("Eligibility FAIL: confirm the excluding requirement at the source; it was read from "
                            "the guideline text.")
        else:
            advisory.append("Not eligible under the rules you entered; double-check them if you are unsure.")
    if opportunity_type is None:
        required.append("Opportunity type unknown: confirm how this funder accepts requests.")
    for stage in degraded:
        required.append(f"Stage degraded: {stage} (results incomplete).")

    # Requirements read from guidelines
    if opportunity is not None and any(not cr.verified for cr in opportunity.hard_criteria):
        required.append("Some hard criteria are unverified against funder text.")
    if extraction is not None:
        hard_unverified = [r for r in extraction.unverified
                           if r.strength == "hard" or r.category in ("eligibility", "geography", "organization_type")]
        if hard_unverified:
            required.append(f"{len(hard_unverified)} extracted hard requirement(s) could not be verified by exact "
                            "quote and were ignored; check them at the source.")
        soft_unverified = len(extraction.unverified) - len(hard_unverified)
        if soft_unverified:
            advisory.append(f"{soft_unverified} other extracted detail(s) could not be verified and were ignored.")
        if extraction.contradictions:
            required.append("Guidelines contain contradictory requirements.")
        if extraction.ambiguities:
            advisory.append("Guidelines contain ambiguous wording (ambiguous eligibility rules are already "
                            "treated as unconfirmed).")

    # Safety
    critical = sorted({f.kind for f in flags} & CRITICAL_FLAGS)
    if critical:
        required.append(f"Safety flags raised: {critical}.")
    if any(f.kind == "award_probability_claim" for f in flags):
        advisory.append("Award-likelihood wording was removed from the model's text.")
    if any(f.kind == "ungrounded_number" for f in flags):
        advisory.append("The model cited figures not found in your inputs; check them before reusing.")

    # Claims
    rule_claims = [u for u in unsupported if u.source == "deterministic"]
    model_claims = [u for u in unsupported if u.source == "model"]
    if rule_claims:
        required.append(f"{len(rule_claims)} concept claim(s) not backed by listed evidence (figures or strong "
                        "wording missing from your evidence).")
    if model_claims:
        advisory.append(f"The model judged {len(model_claims)} further statement(s) as not backed by listed "
                        "evidence; consider citing support.")

    # Routine caveats
    if assessment is not None and assessment.confidence == "low":
        advisory.append("The model's self-reported confidence is low; treat the ratings as tentative.")
    if deadline.feasible is None:
        advisory.append(deadline.reason)
    if effort.defaulted_components:
        advisory.append(f"Effort uses default hours for: {effort.defaulted_components}")
    if opportunity is not None and opportunity.restrictions:
        advisory.append(f"{len(opportunity.restrictions)} funder restriction(s) to confirm against the concept.")
    advisory.extend(score_notes)
    if warnings and not degraded:
        advisory.append("Minor validation warnings (e.g. evidence references) were recorded.")
    return required, advisory
