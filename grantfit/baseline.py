"""Single-prompt baseline for evaluation.

Same model (gpt-6-luna via Portkey), same inputs, same output vocabulary and structured-output
mode as GrantFit, and the same basic instructions (no probability claims, no invention).
What it lacks is the architecture: no deterministic eligibility tools, no quote verification,
no hard-failure gate, no injection scan, no output validators. The comparison therefore
isolates what the architecture adds rather than what the model can do.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from grantfit.llm.client import LLMClient, LLMResult
from grantfit.llm.structured import call_structured
from grantfit.models import AssessmentCase
from grantfit.telemetry import Tracer

PROMPT_VERSION = "baseline_v1"
STAGE = "baseline_single_prompt"
_PROMPT_PATH = Path(__file__).parent / "llm" / "prompts" / f"{PROMPT_VERSION}.md"


class BaselineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligibility: Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
    failed_requirements: list[str]
    opportunity_type: Literal[
        "open_competitive", "invitation_only", "individual_philanthropy", "government",
        "corporate", "unclear",
    ]
    recommendation: Literal[
        "NOT_ELIGIBLE", "VERIFY_FIRST", "PURSUE_NOW", "STRENGTHEN_THEN_PURSUE",
        "CULTIVATE_RELATIONSHIP", "DEPRIORITIZE",
    ]
    concept_weaknesses: list[str]
    unsupported_claims: list[str]
    concerns: list[str]
    rationale: str


def build_user_message(case: AssessmentCase) -> str:
    # The pasted guideline text is included as an ordinary field: no delimiters, no scan.
    return "INPUT JSON:\n" + json.dumps(case.model_dump(mode="json"), indent=2)


def run_baseline(
    client: LLMClient, tracer: Tracer, case: AssessmentCase
) -> tuple[BaselineOutput | None, list[LLMResult], list[str]]:
    return call_structured(
        client=client, tracer=tracer, stage=STAGE, prompt_version=PROMPT_VERSION,
        system=_PROMPT_PATH.read_text(encoding="utf-8"), user=build_user_message(case),
        model_cls=BaselineOutput, schema_name="baseline_assessment",
        summarize=lambda b: f"eligibility={b.eligibility} recommendation={b.recommendation} "
                            f"type={b.opportunity_type}",
    )
