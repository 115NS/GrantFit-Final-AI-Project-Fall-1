"""Deterministic scores and recommendation tiers (weights in config/scoring.yaml).

Rule order matters and is the core safety guarantee:
1. Eligibility FAIL                 -> NOT_ELIGIBLE (no score can override)
2. Deadline already passed          -> DEPRIORITIZE (closed; nothing to verify this cycle)
3. Eligibility UNKNOWN              -> VERIFY_FIRST (also when the opportunity type is unknown)
4. AI assessment missing, or too
   little information to score      -> INCOMPLETE_ASSESSMENT
5. Relationship funding             -> CULTIVATE_RELATIONSHIP (or DEPRIORITIZE on low fit)
6. Deadline infeasible for capacity -> DEPRIORITIZE
7. Low fit                          -> DEPRIORITIZE
8. Weak concept                     -> STRENGTHEN_THEN_PURSUE
9. Request outside stated range     -> STRENGTHEN_THEN_PURSUE (adjust the request)
10. Otherwise                       -> PURSUE_NOW

Scores: each dimension is a model rating r in 1..5 mapped to (r-1)/4*100, combined as a weighted mean.
Dimensions the model marks insufficient_information are excluded and the remaining weights are
renormalized; if less than `min_coverage` of the weight is assessable, no score is produced.
"""

from __future__ import annotations

from pydantic import BaseModel

from grantfit.config import load_yaml
from grantfit.models import (
    DeadlineCheck,
    EffortEstimate,
    EligibilityResult,
    FitConceptAssessment,
    FundingOpportunity,
    PriorityResult,
    ProposalConcept,
)
from grantfit.tools.eligibility import RELATIONSHIP_TYPES


def score_with_coverage(dimensions: BaseModel, weights: dict[str, float],
                        min_coverage: float = 0.0) -> tuple[float | None, float, list[str]]:
    """(score 0-100 or None, assessable share of weight, excluded dimension names).

    Missing information is excluded rather than scored low: absence of information is not
    evidence of poor performance.
    """
    total_w = sum(weights.values())
    used = {n: w for n, w in weights.items() if not getattr(getattr(dimensions, n), "insufficient_information", False)}
    excluded = [n for n in weights if n not in used]
    coverage = round(sum(used.values()) / total_w, 3) if total_w else 0.0
    if not used or coverage < min_coverage:
        return None, coverage, excluded
    acc = sum(w * (getattr(dimensions, n).score - 1) / 4 * 100 for n, w in used.items())
    return round(acc / sum(used.values()), 1), coverage, excluded


def weighted_score(dimensions: BaseModel, weights: dict[str, float]) -> float | None:
    """Weighted mean of (rating - 1) / 4 * 100 over the assessable configured dimensions."""
    return score_with_coverage(dimensions, weights)[0]


def award_size_factor(
    opportunity: FundingOpportunity, concept: ProposalConcept, cfg: dict
) -> tuple[float, str | None]:
    f = cfg["award_size_factor"]
    req = concept.requested_amount_usd
    lo, hi = opportunity.award_min_usd, opportunity.award_max_usd
    if req is None or (lo is None and hi is None):
        return float(f["unknown"]), None
    if (lo is not None and req < lo) or (hi is not None and req > hi):
        return float(f["outside_range"]), (
            f"Requested ${req:,.0f} is outside the award range "
            f"{'$' + format(lo, ',.0f') if lo is not None else '?'}–"
            f"{'$' + format(hi, ',.0f') if hi is not None else '?'}."
        )
    return float(f["within_range"]), None


def decide_priority(
    *,
    eligibility: EligibilityResult,
    assessment: FitConceptAssessment | None,
    opportunity: FundingOpportunity,
    concept: ProposalConcept,
    effort: EffortEstimate,
    deadline: DeadlineCheck,
    cfg: dict | None = None,
) -> PriorityResult:
    cfg = cfg or load_yaml("scoring")
    th = cfg["thresholds"]

    fit = concept_score = fit_cov = concept_cov = None
    score_notes: list[str] = []
    min_cov = float(th.get("min_coverage", 0.6))
    if assessment is not None:
        fit, fit_cov, fit_ex = score_with_coverage(assessment.fit, cfg["fit_weights"], min_cov)
        concept_score, concept_cov, con_ex = score_with_coverage(assessment.concept, cfg["concept_weights"], min_cov)
        for label, ex, cov, val in (("Funder fit", fit_ex, fit_cov, fit), ("Concept quality", con_ex, concept_cov, concept_score)):
            if ex:
                names = ", ".join(e.replace("_", " ") for e in ex)
                score_notes.append(
                    f"{label} excludes {names} (insufficient information); based on {cov:.0%} of its weight."
                    if val is not None else
                    f"{label} not scored: only {cov:.0%} of its weight could be assessed (minimum {min_cov:.0%}).")

    size_factor, size_note = award_size_factor(opportunity, concept, cfg)
    value = None
    if fit is not None and concept_score is not None and effort.hours > 0:
        blend = cfg["value_blend"]["fit"] * fit + cfg["value_blend"]["concept"] * concept_score
        value = round(blend * size_factor / effort.hours, 2)

    reasons: list[str] = []
    if size_note:
        reasons.append(size_note)

    def result(tier: str) -> PriorityResult:
        return PriorityResult(
            tier=tier, tier_reasons=reasons, fit_score_0_100=fit,
            concept_score_0_100=concept_score, fit_coverage=fit_cov, concept_coverage=concept_cov,
            score_notes=score_notes, award_size_factor=size_factor,
            effort=effort, deadline=deadline, value_per_effort_hour=value,
        )

    if eligibility.overall == "FAIL":
        failed = [c for c in eligibility.checks if c.hard and c.status == "FAIL"]
        reasons[:0] = [f"Hard eligibility failure ({c.criterion_id}): {c.reason}" for c in failed]
        return result("NOT_ELIGIBLE")

    if deadline.days_left is not None and deadline.days_left < 0:
        reasons.insert(0, "The deadline has passed; look for the funder's next cycle before verifying eligibility.")
        return result("DEPRIORITIZE")

    if eligibility.overall == "UNKNOWN":
        unknown = [c for c in eligibility.checks if c.hard and c.status == "UNKNOWN"]
        reasons[:0] = [f"Eligibility unconfirmed ({c.criterion_id}): {c.reason}" for c in unknown]
        reasons[:0] = eligibility.notes
        return result("VERIFY_FIRST")

    if opportunity.opportunity_type is None:
        reasons.insert(0, "Opportunity type (open, invitation-only, individual philanthropy, ...) "
                          "is not stated or not verifiable; confirm how this funder accepts requests.")
        return result("VERIFY_FIRST")

    if assessment is None:
        reasons.insert(0, "AI fit/concept assessment unavailable; human review required.")
        return result("INCOMPLETE_ASSESSMENT")

    if fit is None or concept_score is None:
        reasons.insert(0, "Not enough information to rate fit and concept reliably; add the missing details "
                          "(see the score notes) and re-run.")
        return result("INCOMPLETE_ASSESSMENT")

    if opportunity.opportunity_type in RELATIONSHIP_TYPES:
        if fit < th["fit_min"]:
            reasons.insert(0, f"Fit score {fit} below {th['fit_min']}; relationship unlikely to be a good use of time.")
            return result("DEPRIORITIZE")
        reasons.insert(0, f"'{opportunity.opportunity_type}' funding is relationship-based: cultivate, do not cold-apply.")
        return result("CULTIVATE_RELATIONSHIP")

    if deadline.feasible is False:
        reasons.insert(0, f"Deadline not feasible: {deadline.reason}")
        return result("DEPRIORITIZE")

    if fit < th["fit_min"]:
        reasons.insert(0, f"Fit score {fit} is below threshold {th['fit_min']}.")
        return result("DEPRIORITIZE")

    if concept_score < th["concept_min"]:
        reasons.insert(0, f"Concept score {concept_score} is below threshold {th['concept_min']}; strengthen before applying.")
        return result("STRENGTHEN_THEN_PURSUE")

    if size_note:   # request outside a stated award range: fixable, but not ready to submit as is
        reasons.insert(0, "Adjust the requested amount to fit the funder's stated award range before applying.")
        return result("STRENGTHEN_THEN_PURSUE")

    reasons.insert(0, f"Eligible, fit {fit}, concept {concept_score}; deadline: {deadline.reason}")
    return result("PURSUE_NOW")
