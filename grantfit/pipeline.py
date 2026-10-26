"""Orchestrator: fixed, typed pipeline. Every stage writes a trace line.

[if pasted guidelines] guideline_safety_scan (tool) -> extract_guidelines (LLM)
    -> quote_verification (validator, merges verified requirements)
eligibility (tool) -> effort + deadline (tool) -> fit/concept (LLM)
-> output_safety_validation (validator) -> priority (tool) -> output_validation (validator)
-> Recommendation

Failed LLM stages are labeled degraded; deterministic findings are always kept and no fake or
guessed output is substituted.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from grantfit.assess import run_assessment
from grantfit.extract import run_extraction
from grantfit.llm.client import LLMClient, LLMResult
from grantfit.review import classify_review
from grantfit.models import (
    AssessmentCase, FitConceptAssessment, Recommendation, SafetyFlag, UnsupportedClaim,
)
from grantfit.telemetry import Tracer
from grantfit.tools.effort import check_deadline, estimate_effort
from grantfit.tools.eligibility import check_eligibility
from grantfit.tools.scoring import decide_priority
from grantfit.tools.validators import check_unsupported_claims, sanitize_assessment

DISCLAIMERS = [
    "GrantFit does not estimate or promise award probabilities.",
    "Eligibility is checked only against the criteria supplied or verified from the guidelines; "
    "confirm all requirements with the funder.",
    "Pasted guidelines are treated as untrusted data; requirements are used only when an exact "
    "quote was verified.",
]

_REF_PATTERN = re.compile(r"^(profile|opportunity|concept)(\.|$)")


def _validate_refs(a: FitConceptAssessment) -> list[str]:
    """Warn about evidence references that do not point at supplied input fields."""
    warnings = []
    for group_name, group in (("fit", a.fit), ("concept", a.concept)):
        for dim, score in group:
            bad = [r for r in score.evidence_refs if not _REF_PATTERN.match(r.strip())]
            if bad:
                warnings.append(f"{group_name}.{dim}: evidence_refs not pointing to inputs: {bad}")
    return warnings


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _llm_source(results: list[LLMResult], degraded: list[str]) -> str:
    if results and all(r.is_fake for r in results):
        return "fake (offline test client)"
    live = [r for r in results if not r.is_fake]
    if not live:
        return "unavailable"
    if degraded:
        return f"partial:live:{live[-1].model_requested} (degraded: {', '.join(degraded)})"
    return f"live:{live[-1].model_requested}"


def run_case(
    case: AssessmentCase,
    client: LLMClient,
    *,
    as_of: date | None = None,
    tracer: Tracer | None = None,
    runs_dir: Path | None = None,
) -> Recommendation:
    as_of = as_of or date.today()
    tracer = tracer or Tracer(runs_dir=runs_dir)
    p, c = case.profile, case.concept
    o = case.opportunity
    case_json = case.model_dump_json()

    flags: list[SafetyFlag] = []
    llm_results: list[LLMResult] = []
    problems: list[str] = []
    degraded: list[str] = []
    extraction_report = None
    guideline_grounding = ""

    if o.source_text and o.source_text.strip():
        outcome = run_extraction(client, tracer, o)
        o = outcome.opportunity
        extraction_report = outcome.report
        flags += outcome.flags
        llm_results += outcome.llm_results
        problems += outcome.problems
        guideline_grounding = outcome.grounding_text
        if outcome.report.status == "degraded":
            degraded.append("extract_guidelines")
        # The assessment sees only verified structured fields, never the raw pasted text.
        assess_opp = o.model_copy(update={"source_text": None})
    else:
        assess_opp = o

    with tracer.tool_span("eligibility_check", input_payload=case_json) as s:
        eligibility = check_eligibility(p, o, as_of)
        s["output_summary"] = f"overall={eligibility.overall} " + ", ".join(
            f"{ch.criterion_id}={ch.status}" for ch in eligibility.checks)

    with tracer.tool_span("effort_and_deadline", input_payload=case_json) as s:
        effort = estimate_effort(o)
        deadline = check_deadline(o, p, effort.hours, as_of)
        s["output_summary"] = f"effort={effort.hours}h deadline_feasible={deadline.feasible}"

    assessment, a_results, a_problems = run_assessment(client, tracer, p, assess_opp, c)
    llm_results += a_results
    problems += a_problems
    if assessment is None:
        degraded.append("assess_fit_concept")

    with tracer.tool_span("output_safety_validation", kind="validator") as s:
        unsupported = check_unsupported_claims(p, c)
        if assessment is not None:
            grounding = json.dumps([p.model_dump(mode="json"), assess_opp.model_dump(mode="json"),
                                    c.model_dump(mode="json")]) + " " + guideline_grounding
            assessment, out_flags = sanitize_assessment(assessment, grounding, p)
            flags += out_flags
            known = {_key(u.claim) for u in unsupported}
            for claim in assessment.unsupported_claims:
                if _key(claim) not in known and not any(_key(claim) in k or k in _key(claim) for k in known):
                    unsupported.append(UnsupportedClaim(
                        claim=claim, source="model",
                        reason="Model judged this claim not backed by the supplied evidence assets."))
        for u in unsupported:
            flags.append(SafetyFlag(kind="unsupported_claim", source=u.source, detail=u.reason,
                                    excerpt=u.claim[:160]))
        s["output_summary"] = (f"flags={len(flags)} unsupported_claims={len(unsupported)} "
                               f"kinds={sorted({f.kind for f in flags})}")

    with tracer.tool_span("priority_decision") as s:
        priority = decide_priority(eligibility=eligibility, assessment=assessment,
                                   opportunity=o, concept=c, effort=effort, deadline=deadline)
        s["output_summary"] = (f"tier={priority.tier} fit={priority.fit_score_0_100} "
                               f"concept={priority.concept_score_0_100}")

    with tracer.tool_span("output_validation", kind="validator") as s:
        # Defensive re-check of the core guarantees, independent of scoring.py.
        if eligibility.overall == "FAIL" and priority.tier != "NOT_ELIGIBLE":
            raise AssertionError("Hard eligibility failure was not enforced")
        if eligibility.overall == "UNKNOWN" and priority.tier != "VERIFY_FIRST":
            raise AssertionError("Unknown eligibility was not routed to VERIFY_FIRST")
        unverified_ids = {cr.id for cr in o.hard_criteria if not cr.verified}
        if any(ch.status == "FAIL" and ch.criterion_id in unverified_ids for ch in eligibility.checks):
            raise AssertionError("An unverified criterion produced a FAIL")
        if o.opportunity_type in ("invitation_only", "individual_philanthropy") and \
                priority.tier in ("PURSUE_NOW", "STRENGTHEN_THEN_PURSUE"):
            raise AssertionError("Relationship-based funding was routed to an open application")
        warnings = _validate_refs(assessment) if assessment else []
        warnings += problems
        s["output_summary"] = f"warnings={len(warnings)} degraded={degraded}"

    review_reasons, advisories = classify_review(
        eligibility=eligibility, opportunity=o, opportunity_type=o.opportunity_type, degraded=degraded,
        assessment=assessment, unsupported=unsupported, extraction=extraction_report, flags=flags,
        deadline=deadline, effort=effort, warnings=warnings, score_notes=priority.score_notes)

    return Recommendation(
        run_id=tracer.run_id,
        created_at=datetime.now(timezone.utc),
        as_of=as_of,
        llm_source=_llm_source(llm_results, degraded),
        degraded_stages=degraded,
        nonprofit_id=p.id,
        opportunity_id=o.id,
        opportunity_type=o.opportunity_type,
        effective_opportunity=o.model_copy(update={"source_text": None}),
        extraction=extraction_report,
        eligibility=eligibility,
        assessment=assessment,
        priority=priority,
        safety_flags=flags,
        unsupported_claims=unsupported,
        validation_warnings=warnings,
        needs_human_review=bool(review_reasons),
        review_reasons=review_reasons,
        advisories=advisories,
        disclaimers=DISCLAIMERS,
        telemetry=tracer.summary(),
    )
