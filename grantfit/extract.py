"""Guideline extraction stage (LLM) + deterministic verification and merge.

Flow:
1. scan_injection + scan_guidelines on the normalized source (deterministic).
2. LLM extraction into GuidelineExtraction (strict schema, one repair retry).
3. Every quote is verified against the source; the extracted value must also be visible in its
   own quote. Unverified items are reported but never affect eligibility.
4. Verified items are merged into a copy of the opportunity. User-supplied structured fields
   take precedence; deterministic scan findings are always added (they cannot be hidden by
   the model). Contradictions and ambiguities become explicit UNKNOWN hard checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from grantfit.assess import wrap_untrusted
from grantfit.llm.client import LLMClient, LLMResult
from grantfit.llm.structured import call_structured
from grantfit.models import (
    Criterion, ExtractedRequirement, ExtractionReport, FundingOpportunity, GuidelineExtraction,
    RequiredComponent, RequirementVerification, SafetyFlag,
)
from grantfit.telemetry import Tracer
from grantfit.tools.eligibility import _norm_place
from grantfit.tools.guideline_scan import scan_guidelines
from grantfit.tools.quotes import (
    amount_in_quote, date_in_quote, legal_status_in_quote, normalize, parse_small_numbers, verify_quote,
)
from grantfit.tools.safety import PROMPT_CANARY, scan_injection

PROMPT_VERSION = "extract_v1"
STAGE = "extract_guidelines"
_PROMPT_PATH = Path(__file__).parent / "llm" / "prompts" / f"{PROMPT_VERSION}.md"
HARD_CATEGORIES = {"eligibility", "geography", "organization_type"}
NUMERIC_KINDS = {"budget_max", "budget_min", "org_age_min"}


@dataclass
class ExtractionOutcome:
    opportunity: FundingOpportunity           # effective opportunity used downstream
    report: ExtractionReport
    flags: list[SafetyFlag] = field(default_factory=list)
    llm_results: list[LLMResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    grounding_text: str = ""                  # source text minus injection-flagged spans


def _summarize(x: GuidelineExtraction) -> str:
    return (f"type={x.opportunity_type} requirements={len(x.requirements)} "
            f"ambiguities={len(x.ambiguities)} contradictions={len(x.contradictions)} "
            f"suspected_injection={len(x.suspected_injection)}")


def value_supported(item: ExtractedRequirement) -> tuple[bool, str]:
    """The extracted value must be visible in the item's own quote."""
    q, k = item.exact_quote, item.criterion_kind
    if k in ("budget_max", "budget_min"):
        return (amount_in_quote(item.number, q), "threshold amount not found in its quote")
    if k == "org_age_min":
        return (item.number is not None and item.number in parse_small_numbers(q),
                "minimum age not found in its quote")
    if k == "legal_status":
        ok = bool(item.values) and all(legal_status_in_quote(v, q) for v in item.values)
        return ok, "legal status values not found in its quote"
    if k == "geography":
        nq = _norm_place(q)
        ok = bool(item.values) and all(_norm_place(v) in nq or v.lower() in q.lower() for v in item.values)
        return ok, "place names not found in its quote"
    if k == "program_area":
        ok = bool(item.values) and all(v.lower() in q.lower() for v in item.values)
        return ok, "program areas not found in its quote"
    if k == "audited_financials":
        return "audit" in q.lower(), "audit requirement not found in its quote"
    return True, ""


def _criterion_from(item: ExtractedRequirement, cid: str) -> Criterion | None:
    """Map a verified hard/unclear eligibility item to a deterministic criterion."""
    if item.category not in HARD_CATEGORIES or item.strength == "soft":
        return None
    if item.strength == "unclear":
        return Criterion(id=cid, kind="other", hard=True, source_quote=item.exact_quote,
                         description=f"Ambiguous requirement (confirm with funder): {item.summary}")
    kind = item.criterion_kind if item.criterion_kind != "none" else "other"
    if kind == "legal_status":
        value = [v for v in item.values if v in ("501c3", "fiscally_sponsored")]
        if not value:
            kind, value = "other", None
    elif kind in NUMERIC_KINDS:
        value = item.number
    elif kind == "audited_financials":
        value = True
    elif kind in ("geography", "program_area"):
        value = item.values
    else:
        value = None
    return Criterion(id=cid, kind=kind, value=value, hard=True, source_quote=item.exact_quote,
                     description=item.summary)


def run_extraction(
    client: LLMClient, tracer: Tracer, opportunity: FundingOpportunity
) -> ExtractionOutcome:
    source = normalize(opportunity.source_text or "")
    opp = opportunity.model_copy(deep=True)
    user_components = bool(opp.required_components)

    # 1. Deterministic scans (always run, even if the LLM later fails).
    with tracer.tool_span("guideline_safety_scan", input_payload=source) as s:
        flags, flagged = scan_injection(source)
        findings = scan_guidelines(source)
        s["output_summary"] = f"injection_flags={len(flags)} deterministic_findings={len(findings)}"
    grounding = "".join(ch for i, ch in enumerate(source)
                        if not any(a <= i < b for a, b in flagged))

    report = ExtractionReport(status="ok")
    criteria: list[Criterion] = []

    for n, f in enumerate(findings, 1):
        cid = f"scan{n}"
        overlaps = any(f.span[0] < b and a < f.span[1] for a, b in flagged)
        status = "UNVERIFIED" if overlaps else "VERIFIED"
        reason = ("Sentence overlaps text flagged as possible prompt injection." if overlaps
                  else "Found by deterministic scan; quote is a sentence of the source text.")
        if f.kind == "invitation_only":
            report.requirements.append(RequirementVerification(
                origin="deterministic_scan", status=status, reason=reason, category="other",
                strength="hard", summary="Invitation-only / no unsolicited proposals", exact_quote=f.quote))
            continue
        report.requirements.append(RequirementVerification(
            origin="deterministic_scan", status=status, reason=reason, category="eligibility",
            strength="hard", summary=f"{f.kind} = {f.value}", exact_quote=f.quote,
            criterion_id=cid if not overlaps else None))
        if not overlaps:
            criteria.append(Criterion(id=cid, kind=f.kind, value=f.value, hard=True,
                                      source_quote=f.quote, description=f"Deterministic scan: {f.kind}"))
    invitation_found = any(r.status == "VERIFIED" and r.summary.startswith("Invitation-only")
                           for r in report.requirements)

    # 2. LLM extraction.
    system = _PROMPT_PATH.read_text(encoding="utf-8").replace("{{CANARY}}", PROMPT_CANARY)
    # Flagged sentences are withheld from the model: quotes from them can never verify, and
    # sending them only adds injection exposure (and can trip provider content filters).
    user = ("Extract the funding requirements from these guidelines.\n\n"
            + wrap_untrusted(redact_spans(source, flagged)))
    extraction, results, problems = call_structured(
        client=client, tracer=tracer, stage=STAGE, prompt_version=PROMPT_VERSION, system=system,
        user=user, model_cls=GuidelineExtraction, schema_name="guideline_extraction",
        summarize=_summarize)

    # 3. Verify + merge.
    with tracer.tool_span("quote_verification") as s:
        if extraction is None:
            report.status = "degraded"
            if any("content_filter" in p or "content management policy" in p for p in problems):
                flags.append(SafetyFlag(
                    kind="provider_content_filter", source="provider",
                    detail="The model provider's content filter refused the guideline text; it may "
                           "contain manipulative instructions. Review the pasted text manually."))
            report.field_notes.append(
                "Guideline extraction unavailable; only deterministic scan findings were used. "
                "Requirements are not fully known.")
            criteria.append(Criterion(
                id="extraction_unavailable", kind="other", hard=True,
                description="Guideline extraction failed; confirm all eligibility requirements manually."))
        else:
            _merge_extraction(extraction, source, flagged, opp, report, criteria, flags, user_components)

        # Deterministic contradiction check across verified numeric criteria.
        for kind in NUMERIC_KINDS:
            vals = sorted({float(c.value) for c in criteria + opp.hard_criteria
                           if c.kind == kind and c.verified and c.value is not None})
            if len(vals) > 1:
                msg = f"Conflicting {kind} thresholds in guidelines: {vals}"
                report.contradictions.append(msg)
                criteria.append(Criterion(id=f"contradiction_{kind}", kind="other", hard=True,
                                          description=msg))
                flags.append(SafetyFlag(kind="contradictory_requirements", source="deterministic",
                                        detail=msg))

        # Opportunity type: user-supplied wins, except verified invitation-only language,
        # which always prevents an open-application recommendation.
        if invitation_found and opp.opportunity_type not in ("invitation_only", "individual_philanthropy"):
            if opp.opportunity_type is not None:
                flags.append(SafetyFlag(
                    kind="opportunity_type_conflict", source="deterministic",
                    detail=f"Declared type '{opp.opportunity_type}' conflicts with invitation-only "
                           "language in the guidelines; treated as invitation_only."))
            opp.opportunity_type = "invitation_only"

        opp.hard_criteria = opp.hard_criteria + _dedupe(criteria, opp.hard_criteria)
        for r in report.unverified:
            if r.category in HARD_CATEGORIES or r.strength == "hard":
                flags.append(SafetyFlag(kind="unverified_requirement", source="deterministic",
                                        detail=f"Unverified requirement ignored for eligibility: {r.reason}",
                                        excerpt=r.exact_quote[:160]))
        s["output_summary"] = (
            f"status={report.status} verified={len(report.requirements) - len(report.unverified)} "
            f"unverified={len(report.unverified)} contradictions={len(report.contradictions)} "
            f"type={opp.opportunity_type}")

    return ExtractionOutcome(opportunity=opp, report=report, flags=flags, llm_results=results,
                             problems=problems, grounding_text=grounding)


REDACTED_MARKER = "[REMOVED BY GRANTFIT: possible prompt injection]"


def redact_spans(source: str, spans: list[tuple[int, int]]) -> str:
    out, pos = [], 0
    for a, b in sorted(spans):
        if a >= pos:
            out += [source[pos:a], REDACTED_MARKER]
            pos = b
    out.append(source[pos:])
    return "".join(out)


def _dedupe(new: list[Criterion], existing: list[Criterion]) -> list[Criterion]:
    seen = {(c.kind, str(c.value)) for c in existing if c.kind != "other"}
    out = []
    for c in new:
        key = (c.kind, str(c.value))
        if c.kind != "other" and key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _merge_extraction(
    x: GuidelineExtraction, source: str, flagged, opp: FundingOpportunity,
    report: ExtractionReport, criteria: list[Criterion], flags: list[SafetyFlag],
    user_components: bool,
) -> None:
    for i, item in enumerate(x.requirements, 1):
        ok, why = verify_quote(item.exact_quote, source, flagged)
        if ok:
            ok_val, why_val = value_supported(item)
            if not ok_val:
                ok, why = False, f"Extracted value not supported: {why_val}."
        cid = f"x{i}"
        crit = _criterion_from(item, cid) if ok else None
        report.requirements.append(RequirementVerification(
            origin="model", status="VERIFIED" if ok else "UNVERIFIED", reason=why,
            category=item.category, strength=item.strength, summary=item.summary,
            exact_quote=item.exact_quote, criterion_id=crit.id if crit else None))
        if not ok:
            continue
        if crit:
            criteria.append(crit)
        if item.category == "funder_priority":
            opp.priorities.append(item.summary)
        elif item.category == "restriction":
            opp.restrictions.append(item.summary)
        elif item.category == "required_material" and item.component and not user_components:
            opp.required_components.append(
                RequiredComponent(name=item.component, quantity=max(1, item.quantity or 1)))

    # Opportunity-level fields: only fill what the user did not supply, only from verified quotes.
    if x.deadline and opp.deadline is None:
        ok, why = verify_quote(x.deadline_quote, source, flagged)
        if ok and x.deadline == "rolling" and "rolling" in (x.deadline_quote or "").lower():
            opp.deadline = "rolling"
        elif ok and x.deadline != "rolling":
            try:
                d = date.fromisoformat(x.deadline)
                if date_in_quote(d, x.deadline_quote or ""):
                    opp.deadline = d
                else:
                    report.field_notes.append(f"Deadline {x.deadline} not found in its quote; ignored.")
            except ValueError:
                report.field_notes.append(f"Deadline '{x.deadline}' is not a full date; ignored.")
        else:
            report.field_notes.append(f"Deadline quote unverified ({why}); deadline left unknown.")
    if (x.award_min_usd is not None or x.award_max_usd is not None) and \
            opp.award_min_usd is None and opp.award_max_usd is None:
        ok, why = verify_quote(x.award_quote, source, flagged)
        q = x.award_quote or ""
        if ok and all(v is None or amount_in_quote(v, q) for v in (x.award_min_usd, x.award_max_usd)):
            opp.award_min_usd, opp.award_max_usd = x.award_min_usd, x.award_max_usd
        else:
            report.field_notes.append("Award range not verified against its quote; left unknown.")
    if opp.opportunity_type is None:
        if x.opportunity_type != "unclear" and verify_quote(x.opportunity_type_quote, source, flagged)[0]:
            opp.opportunity_type = x.opportunity_type
        else:
            report.field_notes.append("Opportunity type unclear or unverified; left unknown.")

    # Ambiguities / contradictions / missing info: explicit, never guessed.
    for a in x.ambiguities:
        if verify_quote(a.exact_quote, source, flagged)[0]:
            report.ambiguities.append(f"{a.description} — \"{a.exact_quote}\"")
            flags.append(SafetyFlag(kind="ambiguous_requirement", source="model",
                                    detail=a.description, excerpt=a.exact_quote[:160]))
    for c in x.contradictions:
        if verify_quote(c.quote_a, source, flagged)[0] and verify_quote(c.quote_b, source, flagged)[0]:
            msg = f"{c.description} — \"{c.quote_a}\" vs. \"{c.quote_b}\""
            report.contradictions.append(msg)
            criteria.append(Criterion(id=f"contradiction{len(report.contradictions)}", kind="other",
                                      hard=True, description=f"Contradictory requirements: {c.description}"))
            flags.append(SafetyFlag(kind="contradictory_requirements", source="model",
                                    detail=c.description, excerpt=c.quote_a[:160]))
    report.missing_information.extend(x.missing_information)
    for s in x.suspected_injection:
        flags.append(SafetyFlag(kind="prompt_injection", source="model",
                                detail=f"Model reported possible injection: {s.description}",
                                excerpt=(s.exact_quote or "")[:160]))

