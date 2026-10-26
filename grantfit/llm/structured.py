"""Shared structured-output call: Pydantic validation with at most one repair retry.

Returns None on failure. Never substitutes fabricated or fake output for a failed live call.
Traces record hashes and short summaries only, never prompts or raw user inputs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from grantfit.llm.client import LLMClient, LLMError, LLMResult
from grantfit.telemetry import Tracer

T = TypeVar("T", bound=BaseModel)


def parse_json_model(text: str, model_cls: type[T]) -> T:
    """Parse model text as JSON (tolerating code fences) and validate it."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    return model_cls.model_validate_json(cleaned)


def call_structured(
    *,
    client: LLMClient,
    tracer: Tracer,
    stage: str,
    prompt_version: str,
    system: str,
    user: str,
    model_cls: type[T],
    schema_name: str,
    summarize: Callable[[T], str],
) -> tuple[T | None, list[LLMResult], list[str]]:
    """Returns (validated output or None, raw LLM results, problems)."""
    schema = model_cls.model_json_schema()
    results: list[LLMResult] = []
    problems: list[str] = []

    for attempt in (1, 2):  # attempt 2 = the single repair retry
        started = datetime.now(timezone.utc)
        try:
            res = client.complete_json(system=system, user=user, schema_name=schema_name, schema=schema)
        except LLMError as exc:
            problems.append(f"{stage}: LLM call failed: {exc}")
            tracer.llm_span(stage=stage, started=started, attempt=attempt, status="error",
                            prompt_version=prompt_version, input_payload=system + user,
                            result=None, model_requested=client.model, error=str(exc))
            return None, results, problems  # transport errors are not retried here
        results.append(res)
        try:
            parsed = parse_json_model(res.text, model_cls)
        except (ValidationError, ValueError) as exc:
            err = str(exc)[:800]
            problems.append(f"{stage} attempt {attempt}: invalid structured output: {err[:200]}")
            tracer.llm_span(stage=stage, started=started, attempt=attempt,
                            status="retry" if attempt == 1 else "degraded",
                            prompt_version=prompt_version, input_payload=system + user,
                            result=res, model_requested=client.model,
                            error=f"validation failed: {err[:300]}")
            user = (user + "\n\nYour previous reply failed schema validation with this error:\n"
                    f"{err}\nReturn a corrected JSON object only.")
            continue
        tracer.llm_span(stage=stage, started=started, attempt=attempt, status="ok",
                        prompt_version=prompt_version, input_payload=system + user,
                        result=res, model_requested=client.model, output_summary=summarize(parsed))
        return parsed, results, problems

    return None, results, problems
