"""Rule-based readiness roadmap derived from a Recommendation.

Deterministic and transparent: every step is generated from a specific backend finding
(eligibility checks, verification results, safety flags, unsupported claims, the model's
evidence gaps, dimension scores, effort/deadline). No new facts are introduced, and no LLM
call is made. Steps are ordered by blocking severity.
"""

from __future__ import annotations

from dataclasses import dataclass

from grantfit.models import Recommendation

DIMENSION_LABELS = {
    "mission_alignment": "mission alignment", "population_alignment": "population alignment",
    "geography_alignment": "geographic alignment", "award_size_fit": "award size fit",
    "funder_priority_match": "funder priority match", "problem_clarity": "problem clarity",
    "logic_model": "logic model", "measurable_outcomes": "measurable outcomes",
    "feasibility": "feasibility", "budget_realism": "budget realism",
    "evidence_strength": "evidence strength",
}


@dataclass
class RoadmapStep:
    priority: str          # "Blocker" | "Before applying" | "Strengthen" | "Plan"
    action: str
    why: str
    source: str            # which backend finding produced this step


def build_roadmap(rec: Recommendation, max_steps: int = 10) -> list[RoadmapStep]:
    steps: list[RoadmapStep] = []
    tier = rec.priority.tier

    # 1. Blockers
    for c in rec.eligibility.checks:
        if c.hard and c.status == "FAIL":
            steps.append(RoadmapStep(
                "Blocker", "Do not apply to this opportunity as currently defined.",
                f"Hard eligibility requirement not met: {c.reason}", f"eligibility rule: {c.kind.replace('_', ' ')}"))
    if tier == "NOT_ELIGIBLE":
        steps.append(RoadmapStep(
            "Blocker", "Redirect effort to funders whose eligibility rules you meet, or confirm "
                       "with this funder whether an exception or fiscal-sponsor route exists.",
            "A hard eligibility failure cannot be offset by fit or concept strength.", "priority tier"))
    for c in rec.eligibility.checks:
        if c.hard and c.status == "UNKNOWN":
            steps.append(RoadmapStep(
                "Blocker", "Confirm this eligibility requirement before investing writing time.",
                c.reason, f"eligibility rule: {c.kind.replace('_', ' ')}"))
    if rec.opportunity_type is None:
        steps.append(RoadmapStep(
            "Blocker", "Find out how this funder accepts requests (open call, invitation, or relationship).",
            "The opportunity type is not stated or could not be verified.", "opportunity type"))
    if any(f.kind in ("prompt_injection", "provider_content_filter") for f in rec.safety_flags):
        steps.append(RoadmapStep(
            "Blocker", "Re-read the pasted guidelines at the source; parts were flagged as possible "
                       "manipulation and were excluded.",
            "Text in the guidelines looked like instructions to an AI rather than funder requirements.",
            "safety flags"))
    for stage in rec.degraded_stages:
        steps.append(RoadmapStep(
            "Blocker", "Re-run the assessment or review the requirements manually.",
            f"The '{stage}' stage did not complete, so results are incomplete.", "degraded stage"))

    # 2. Relationship-based funding
    if tier == "CULTIVATE_RELATIONSHIP":
        steps.append(RoadmapStep(
            "Before applying", "Do not submit an unsolicited proposal; plan relationship cultivation "
                               "(e.g. an introduction through a board member or partner).",
            f"This is '{rec.opportunity_type}' funding, which is relationship-based.", "opportunity type"))

    # 3. Verification gaps
    if rec.extraction is not None:
        unverified = rec.extraction.unverified
        if unverified:
            steps.append(RoadmapStep(
                "Before applying", f"Check {len(unverified)} requirement(s) that could not be matched to "
                                   "an exact quote in the guidelines.",
                "Unverified requirements were ignored for eligibility; confirm none of them apply.",
                "quote verification"))
        for c in rec.extraction.contradictions:
            steps.append(RoadmapStep("Before applying", "Ask the funder which requirement applies.",
                                     f"Contradiction: {c}", "guideline extraction"))
        for a in rec.extraction.ambiguities:
            steps.append(RoadmapStep("Before applying", "Clarify this ambiguous requirement with the funder.",
                                     a, "guideline extraction"))
    restrictions = rec.effective_opportunity.restrictions if rec.effective_opportunity else []
    for r in restrictions:
        steps.append(RoadmapStep("Before applying", "Confirm the concept does not conflict with this restriction.",
                                 r, "verified funder restriction"))

    # 4. Strengthen the proposal
    for u in rec.unsupported_claims:
        steps.append(RoadmapStep(
            "Strengthen", f"Substantiate or soften the claim: “{u.claim}”",
            u.reason, f"unsupported-claim check ({u.source})"))
    if rec.assessment is not None:
        for gap in rec.assessment.evidence_gaps:
            steps.append(RoadmapStep("Strengthen", gap, "Identified as missing evidence.", "AI assessment"))
        weak = sorted(((d.score, name, d.rationale) for name, d in rec.assessment.concept if d.score <= 2))
        for score, name, rationale in weak:
            steps.append(RoadmapStep(
                "Strengthen", f"Improve the concept's {DIMENSION_LABELS.get(name, name)} (rated {score}/5).",
                rationale, "AI assessment score"))

    # 5. Plan the work
    p = rec.priority
    if tier in ("PURSUE_NOW", "STRENGTHEN_THEN_PURSUE"):
        d = p.deadline
        when = f" over the {d.days_left} days before the deadline" if d.days_left is not None else ""
        steps.append(RoadmapStep(
            "Plan", f"Reserve about {p.effort.hours:g} staff hours{when}.",
            d.reason, "effort estimate"))
    elif tier == "DEPRIORITIZE" and p.deadline.feasible is False:
        steps.append(RoadmapStep(
            "Plan", "Skip this cycle or look for the funder's next deadline.", p.deadline.reason,
            "deadline feasibility"))

    # De-duplicate while preserving order.
    seen, out = set(), []
    for s in steps:
        key = (s.action, s.why)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out[:max_steps]
