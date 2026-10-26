"""LLM stage: structured fit + concept assessment, validated with Pydantic.

At most one repair retry on invalid output. On failure returns None (the pipeline degrades);
it never substitutes fabricated or fake results.
"""

from __future__ import annotations

import json
from pathlib import Path

from grantfit.llm.client import LLMClient, LLMResult
from grantfit.llm.structured import call_structured, parse_json_model
from grantfit.models import FitConceptAssessment, FundingOpportunity, NonprofitProfile, ProposalConcept
from grantfit.telemetry import Tracer
from grantfit.tools.safety import PROMPT_CANARY

PROMPT_VERSION = "assess_v3"
STAGE = "assess_fit_concept"
_PROMPT_PATH = Path(__file__).parent / "llm" / "prompts" / f"{PROMPT_VERSION}.md"


def wrap_untrusted(text: str) -> str:
    # Neutralize any attempt to close the delimiter from inside untrusted text.
    safe = text.replace("</untrusted_funder_text>", "[/untrusted_funder_text]")
    return f"<untrusted_funder_text>\n{safe}\n</untrusted_funder_text>"


def build_user_message(
    profile: NonprofitProfile, opportunity: FundingOpportunity, concept: ProposalConcept
) -> str:
    opp = opportunity.model_dump(mode="json", exclude={"source_text"})
    payload = {"profile": profile.model_dump(mode="json"), "opportunity": opp,
               "concept": concept.model_dump(mode="json")}
    msg = "INPUT JSON:\n" + json.dumps(payload, indent=2)
    if opportunity.source_text:
        msg += "\n\n" + wrap_untrusted(opportunity.source_text)
    return msg


def parse_assessment(text: str) -> FitConceptAssessment:
    return parse_json_model(text, FitConceptAssessment)


def _summarize(a: FitConceptAssessment) -> str:
    fit = {k: v.score for k, v in a.fit}
    con = {k: v.score for k, v in a.concept}
    return f"fit={fit} concept={con} confidence={a.confidence}"


def system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").replace("{{CANARY}}", PROMPT_CANARY)


def run_assessment(
    client: LLMClient,
    tracer: Tracer,
    profile: NonprofitProfile,
    opportunity: FundingOpportunity,
    concept: ProposalConcept,
) -> tuple[FitConceptAssessment | None, list[LLMResult], list[str]]:
    """Returns (assessment or None, raw LLM results, problems)."""
    return call_structured(
        client=client, tracer=tracer, stage=STAGE, prompt_version=PROMPT_VERSION,
        system=system_prompt(), user=build_user_message(profile, opportunity, concept),
        model_cls=FitConceptAssessment, schema_name="fit_concept_assessment", summarize=_summarize,
    )
