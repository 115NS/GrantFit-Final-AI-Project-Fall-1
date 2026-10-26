"""Run one nonprofit x opportunity x concept case end to end.

    python -m grantfit.cli --case data/samples/sample_case.json            # live gpt-6-luna
    python -m grantfit.cli --case data/samples/sample_case.json --fresh    # live, bypass Portkey cache
    python -m grantfit.cli --case data/samples/sample_case.json --fake     # offline, labeled fake
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from grantfit.llm.client import FakeLLMClient, LLMError, PortkeyClient
from grantfit.llm.fake_responses import fake_response
from grantfit.models import AssessmentCase
from grantfit.pipeline import run_case


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GrantFit single-case assessment")
    ap.add_argument("--case", required=True, type=Path, help="JSON with profile, opportunity, concept")
    ap.add_argument("--fake", action="store_true", help="Use the offline fake LLM (output labeled fake)")
    ap.add_argument("--fresh", action="store_true",
                    help="Bypass the Portkey response cache so usage reflects a real model call")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None, help="Evaluation date (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    case = AssessmentCase.model_validate_json(args.case.read_text(encoding="utf-8"))
    try:
        client = FakeLLMClient(fake_response) if args.fake else PortkeyClient(fresh=args.fresh)
    except LLMError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rec = run_case(case, client, as_of=args.as_of)
    print(json.dumps(rec.model_dump(mode="json"), indent=2))
    t = rec.telemetry
    print(
        f"\n[GrantFit] tier={rec.priority.tier} llm_source={rec.llm_source} "
        f"safety_flags={len(rec.safety_flags)} review={rec.needs_human_review} "
        f"gateway_cache_hits={t.gateway_cache_hits} "
        f"tokens(in/cached/out)={t.input_tokens}/{t.cached_input_tokens}/{t.output_tokens} "
        f"cost={t.cost_usd} ({t.cost_source}) trace={t.trace_path}",
        file=sys.stderr,
    )
    # Non-zero exit when any LLM stage degraded, so failures are never silent.
    return 1 if rec.degraded_stages else 0


if __name__ == "__main__":
    raise SystemExit(main())
