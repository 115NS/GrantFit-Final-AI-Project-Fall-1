"""JSONL run traces. One line per stage / LLM attempt. Never records secrets or prompts."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from grantfit.config import RUNS_DIR, load_yaml
from grantfit.llm.client import LLMResult
from grantfit.models import TelemetrySummary, TraceSpan


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def hash_payload(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_cost(
    model: str, input_tokens: int | None, cached_input_tokens: int | None,
    output_tokens: int | None, pricing: dict | None = None,
) -> tuple[float | None, str]:
    """Cost from config/pricing.yaml. Returns (None, 'unpriced') if rates or usage are missing."""
    pricing = pricing if pricing is not None else load_yaml("pricing")
    rates = (pricing.get("models") or {}).get(model) or {}
    r_in, r_cached, r_out = (rates.get(k) for k in ("input_per_1m", "cached_input_per_1m", "output_per_1m"))
    if None in (r_in, r_out, input_tokens, output_tokens):
        return None, "unpriced"
    cached = cached_input_tokens or 0
    if cached and r_cached is None:
        return None, "unpriced"
    uncached = input_tokens - cached
    cost = (uncached * r_in + cached * (r_cached or 0) + output_tokens * r_out) / 1_000_000
    return round(cost, 8), "computed_from_config"


class Tracer:
    def __init__(self, run_id: str | None = None, runs_dir: Path | None = None):
        self.run_id = run_id or new_run_id()
        self.dir = Path(runs_dir or RUNS_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.run_id}.jsonl"
        self.spans: list[TraceSpan] = []

    def write(self, span: TraceSpan) -> None:
        self.spans.append(span)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(span.model_dump_json() + "\n")

    @contextmanager
    def tool_span(self, stage: str, kind: str = "tool", input_payload: str | None = None):
        """Times a deterministic stage. The body may set info['output_summary'] / info['status']."""
        info: dict = {"status": "ok", "output_summary": None}
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        try:
            yield info
        except Exception as exc:
            info["status"] = "error"
            info["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            raise
        finally:
            self.write(TraceSpan(
                run_id=self.run_id, stage=stage, kind=kind, started_at=started,
                latency_ms=round((time.perf_counter() - t0) * 1000, 3),
                status=info["status"], output_summary=info.get("output_summary"),
                input_hash=hash_payload(input_payload) if input_payload else None,
                error=info.get("error"),
            ))

    def llm_span(
        self, *, stage: str, started: datetime, attempt: int, status: str,
        prompt_version: str, input_payload: str, result: LLMResult | None,
        model_requested: str, output_summary: str | None = None, error: str | None = None,
    ) -> None:
        if result is not None and (result.gateway_cache_status or "").upper().startswith("HIT"):
            cost, cost_source = None, "gateway_cache_hit"
        elif result is not None and not result.is_fake:
            cost, cost_source = compute_cost(
                result.model_requested, result.input_tokens,
                result.cached_input_tokens, result.output_tokens,
            )
        else:
            cost, cost_source = None, "not_applicable" if result is not None else "unpriced"
        notes = "; ".join(result.notes) if result and result.notes else None
        self.write(TraceSpan(
            run_id=self.run_id, stage=stage, kind="llm", started_at=started,
            latency_ms=result.latency_ms if result else 0.0, status=status, attempt=attempt,
            model_requested=model_requested,
            model_served=result.model_served if result else None,
            provider=result.provider if result else None,
            gateway_trace_id=result.gateway_trace_id if result else None,
            gateway_cache_status=result.gateway_cache_status if result else None,
            is_fake_llm=result.is_fake if result else None,
            output_mode=result.output_mode if result else None,
            prompt_version=prompt_version,
            input_tokens=result.input_tokens if result else None,
            cached_input_tokens=result.cached_input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            reasoning_tokens=result.reasoning_tokens if result else None,
            cost_usd=cost, cost_source=cost_source,
            input_hash=hash_payload(input_payload),
            output_summary=output_summary,
            error="; ".join(x for x in (error, notes) if x) or None,
        ))

    def summary(self) -> TelemetrySummary:
        real = [s for s in self.spans if s.kind == "llm" and s.is_fake_llm is False]
        hits = [s for s in real if s.cost_source == "gateway_cache_hit"]
        llm = [s for s in real if s.cost_source != "gateway_cache_hit"]  # genuine model calls only

        def total(attr: str) -> int | None:
            vals = [getattr(s, attr) for s in llm]
            return None if not vals or any(v is None for v in vals) else sum(vals)

        costs = [s.cost_usd for s in llm]
        priced = bool(llm) and all(c is not None for c in costs)
        return TelemetrySummary(
            trace_path=str(self.path),
            llm_calls=len([s for s in self.spans if s.kind == "llm"]),
            gateway_cache_hits=len(hits),
            input_tokens=total("input_tokens"),
            cached_input_tokens=total("cached_input_tokens"),
            output_tokens=total("output_tokens"),
            total_latency_ms=round(sum(s.latency_ms for s in self.spans), 1),
            cost_usd=round(sum(costs), 8) if priced else None,
            cost_source="computed_from_config" if priced else (
                "unpriced" if llm else ("gateway_cache_hit" if hits else "not_applicable")),
        )


def dumps_compact(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)
