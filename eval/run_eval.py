"""Run the evaluation: GrantFit vs. the single-prompt baseline on eval/cases/*.json.

    .venv/bin/python -m eval.run_eval --fresh              # live gpt-6-luna, bypass Portkey cache
    .venv/bin/python -m eval.run_eval --fake               # offline harness check (labeled FAKE)
    .venv/bin/python -m eval.run_eval --fresh --cases E03,E12 --workers 2
    .venv/bin/python -m eval.run_eval --rescore eval/results/<run>   # re-score saved outputs

Writes eval/results/<UTC timestamp>[-FAKE]/:
    summary.md     headline metrics, per-case table, token/latency tables
    results.json   every per-case score, view, and telemetry summary
    outputs/       full GrantFit Recommendation and baseline output per case
    traces/        JSONL traces per case and system
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from eval.metrics import (
    METRICS, aggregate, score, telemetry_dict, view_baseline, view_dict, view_grantfit,
)
from grantfit.assess import PROMPT_VERSION as ASSESS_PROMPT
from grantfit.baseline import PROMPT_VERSION as BASELINE_PROMPT, BaselineOutput, run_baseline
from grantfit.extract import PROMPT_VERSION as EXTRACT_PROMPT
from grantfit.config import PROJECT_ROOT, load_yaml
from grantfit.llm.client import FakeLLMClient, LLMError, PortkeyClient
from grantfit.llm.fake_responses import fake_response
from grantfit.models import EvalCase, Recommendation
from grantfit.pipeline import run_case
from grantfit.telemetry import Tracer

CASES_DIR = PROJECT_ROOT / "eval" / "cases"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"


def load_cases(only: set[str] | None = None) -> list[EvalCase]:
    cases = [EvalCase.model_validate_json(p.read_text(encoding="utf-8"))
             for p in sorted(CASES_DIR.glob("E*.json"))]
    return [c for c in cases if not only or c.id in only]


def make_client(fake: bool, fresh: bool):
    return FakeLLMClient(fake_response) if fake else PortkeyClient(fresh=fresh)


def run_one(ec: EvalCase, out_dir: Path, fake: bool, fresh: bool) -> dict:
    traces = out_dir / "traces"
    record: dict = {"id": ec.id, "title": ec.title, "category": ec.category,
                    "expected": ec.expected.model_dump()}

    t0 = time.perf_counter()
    gf_tracer = Tracer(run_id=f"{ec.id}-grantfit", runs_dir=traces)
    rec = run_case(ec.case, make_client(fake, fresh), as_of=ec.as_of, tracer=gf_tracer)
    gf_view = view_grantfit(rec)
    record["grantfit"] = {
        "view": view_dict(gf_view), "scores": score(ec.expected, gf_view),
        "telemetry": telemetry_dict(rec.telemetry), "llm_source": rec.llm_source,
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    (out_dir / "outputs" / f"{ec.id}-grantfit.json").write_text(
        rec.model_dump_json(indent=2), encoding="utf-8")

    t0 = time.perf_counter()
    bl_tracer = Tracer(run_id=f"{ec.id}-baseline", runs_dir=traces)
    bl_out, bl_results, bl_problems = run_baseline(make_client(fake, fresh), bl_tracer, ec.case)
    bl_view = view_baseline(bl_out)
    record["baseline"] = {
        "view": view_dict(bl_view), "scores": score(ec.expected, bl_view),
        "telemetry": telemetry_dict(bl_tracer.summary()), "problems": bl_problems,
        "llm_source": ("fake (offline test client)" if bl_results and all(r.is_fake for r in bl_results)
                       else ("unavailable" if bl_out is None else f"live:{bl_results[-1].model_requested}")),
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    (out_dir / "outputs" / f"{ec.id}-baseline.json").write_text(
        json.dumps(bl_out.model_dump() if bl_out else None, indent=2), encoding="utf-8")
    return record


def _served_models(traces: Path) -> list[str]:
    served = set()
    for p in traces.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            s = json.loads(line)
            if s.get("kind") == "llm" and s.get("model_served"):
                served.add(s["model_served"])
    return sorted(served)


def rescore(out_dir: Path) -> int:
    """Recompute views/scores/summary from saved outputs (no model calls).

    The first rescore preserves the original files as results.initial.json / summary.initial.md.
    """
    data = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    for name in ("results.json", "summary.md"):
        stem, ext = name.split(".")
        initial = out_dir / f"{stem}.initial.{ext}"
        if not initial.exists():
            initial.write_text((out_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
    cases = {c.id: c for c in load_cases()}
    for r in data["cases"]:
        exp = cases[r["id"]].expected
        gf = Recommendation.model_validate_json((out_dir / "outputs" / f"{r['id']}-grantfit.json").read_text())
        bl_raw = json.loads((out_dir / "outputs" / f"{r['id']}-baseline.json").read_text())
        gv, bv = view_grantfit(gf), view_baseline(BaselineOutput.model_validate(bl_raw) if bl_raw else None)
        r["expected"] = exp.model_dump()
        r["grantfit"].update(view=view_dict(gv), scores=score(exp, gv))
        r["baseline"].update(view=view_dict(bv), scores=score(exp, bv))
    data["meta"]["rescored_utc"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data["aggregates"] = {s: aggregate([r[s]["scores"] for r in data["cases"]]) for s in ("grantfit", "baseline")}
    (out_dir / "results.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_summary(data["meta"], data["aggregates"], data["cases"]),
                                        encoding="utf-8")
    print(f"Rescored {len(data['cases'])} cases: {out_dir / 'summary.md'}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GrantFit vs. single-prompt baseline evaluation")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rescore", type=Path, metavar="RESULTS_DIR",
                      help="Recompute scores from a previous run's saved outputs (no model calls)")
    mode.add_argument("--fresh", action="store_true", help="Live calls, bypassing the Portkey cache")
    mode.add_argument("--live-cached", action="store_true", help="Live calls, Portkey cache allowed")
    mode.add_argument("--fake", action="store_true", help="Offline fake LLM (harness check only)")
    ap.add_argument("--cases", default="", help="Comma-separated case IDs (default: all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.rescore:
        return rescore(args.rescore)

    cases = load_cases({c.strip() for c in args.cases.split(",") if c.strip()} or None)
    if not cases:
        print("No cases found.", file=sys.stderr)
        return 2
    if not args.fake:
        try:
            PortkeyClient()  # fail fast on a missing key
        except LLMError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or RESULTS_DIR / (stamp + ("-FAKE" if args.fake else ""))
    (out_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (out_dir / "traces").mkdir(parents=True, exist_ok=True)

    print(f"Running {len(cases)} cases ({'FAKE' if args.fake else 'LIVE'}) -> {out_dir}", file=sys.stderr)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        records = list(pool.map(lambda c: run_one(c, out_dir, args.fake, args.fresh), cases))
    wall_s = round(time.perf_counter() - t0, 1)

    meta = {
        "timestamp_utc": stamp,
        "mode": "FAKE (offline harness check; not model output)" if args.fake
                else ("LIVE --fresh (Portkey cache bypassed)" if args.fresh else "LIVE (Portkey cache allowed)"),
        "model_requested": load_yaml("settings")["llm"]["model"],
        "models_served": _served_models(out_dir / "traces"),
        "prompt_versions": {"grantfit": [EXTRACT_PROMPT, ASSESS_PROMPT], "baseline": [BASELINE_PROMPT]},
        "n_cases": len(records),
        "workers": args.workers,
        "wall_clock_s": wall_s,
        "pricing_configured": _pricing_configured(),
    }
    aggregates = {sys_: aggregate([r[sys_]["scores"] for r in records]) for sys_ in ("grantfit", "baseline")}
    (out_dir / "results.json").write_text(
        json.dumps({"meta": meta, "aggregates": aggregates, "cases": records}, indent=2, default=str),
        encoding="utf-8")
    (out_dir / "summary.md").write_text(render_summary(meta, aggregates, records), encoding="utf-8")
    print(f"Done in {wall_s}s. Summary: {out_dir / 'summary.md'}", file=sys.stderr)
    return 0


def _pricing_configured() -> bool:
    rates = (load_yaml("pricing").get("models") or {}).get(load_yaml("settings")["llm"]["model"]) or {}
    return all(rates.get(k) is not None for k in ("input_per_1m", "output_per_1m"))


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def _fmt_rate(a: dict) -> str:
    if not a["den"]:
        return "n/a"
    num = a["num"] if a["num"] % 1 else int(a["num"])
    return f"{num}/{a['den']} ({a['rate'] * 100:.0f}%)"


def _tok(t: dict, k: str) -> str:
    v = t.get(k)
    return "—" if v is None else f"{v:,}"


def _sum(records, sys_, key) -> int | None:
    vals = [r[sys_]["telemetry"].get(key) for r in records]
    return None if any(v is None for v in vals) else sum(vals)


def render_summary(meta: dict, agg: dict, records: list[dict]) -> str:
    L: list[str] = []
    L.append(f"# GrantFit evaluation — {meta['timestamp_utc']}\n")
    if meta["mode"].startswith("FAKE"):
        L.append("> **FAKE RUN.** Scripted offline responses, not model output. Use only to check the harness.\n")
    L.append(f"- Mode: {meta['mode']}")
    if meta.get("rescored_utc"):
        L.append(f"- Rescored from saved outputs at {meta['rescored_utc']} (no new model calls; "
                 "original scoring kept in summary.initial.md)")
    L.append(f"- Model requested: `{meta['model_requested']}`; served: {', '.join(meta['models_served']) or 'n/a'}")
    L.append(f"- Prompts: GrantFit {meta['prompt_versions']['grantfit']}, baseline {meta['prompt_versions']['baseline']}")
    L.append(f"- Cases: {meta['n_cases']}; workers: {meta['workers']}; wall clock: {meta['wall_clock_s']} s")
    hits = sum(r[s]["telemetry"].get("gateway_cache_hits", 0) for r in records for s in ("grantfit", "baseline"))
    degraded = [f"{r['id']}:{','.join(r['grantfit']['view']['degraded_stages'])}"
                for r in records if r["grantfit"]["view"]["degraded_stages"]]
    bl_failed = [r["id"] for r in records if not r["baseline"]["view"]["completed"]]
    L.append(f"- Portkey cache hits (excluded from token totals): {hits}")
    L.append(f"- GrantFit degraded stages: {degraded or 'none'}; baseline failures: {bl_failed or 'none'}")
    L.append(f"- Cost: {'computed from config/pricing.yaml' if meta['pricing_configured'] else 'unpriced (no rates in config/pricing.yaml); token counts below are the provider-reported usage'}\n")

    L.append("## Headline metrics\n")
    L.append("Baseline = same model, same inputs, same output vocabulary, one prompt, no tools/verification/validators. "
             "n/a = metric not applicable to any case.\n")
    L.append("| Metric | GrantFit | Baseline |")
    L.append("|---|---|---|")
    for m, label in METRICS.items():
        L.append(f"| {label} | {_fmt_rate(agg['grantfit'][m])} | {_fmt_rate(agg['baseline'][m])} |")
    gf_review = sum(1 for r in records if r["grantfit"]["view"]["needs_human_review"])
    redactions = sum(r["grantfit"]["view"]["redactions"] for r in records)
    L.append(f"\nGrantFit flagged {gf_review}/{len(records)} cases for human review and redacted "
             f"{redactions} award-likelihood sentence(s) from model output before display.\n")

    L.append("## Per-case results\n")
    L.append("| Case | Category | Expected tier(s) | GrantFit tier | Baseline tier | Expected eligibility | GrantFit | Baseline |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in records:
        g, b = r["grantfit"], r["baseline"]
        mark = lambda ok: "✅" if ok else ("—" if ok is None else "❌")  # noqa: E731
        L.append(
            f"| {r['id']} {r['title']} | {r['category']} | {', '.join(r['expected']['tier_allowed'])} | "
            f"{mark(g['scores']['tier_correct'])} {g['view']['tier']} | {mark(b['scores']['tier_correct'])} {b['view']['tier']} | "
            f"{', '.join(r['expected']['eligibility_allowed'])} | {mark(g['scores']['eligibility_correct'])} {g['view']['eligibility']} | "
            f"{mark(b['scores']['eligibility_correct'])} {b['view']['eligibility']} |")

    L.append("\n## Tokens and latency (provider-reported)\n")
    L.append("Latency is the sum of stage latencies (LLM calls dominate). Cached = provider prompt-cache hits within input tokens.\n")
    L.append("| Case | GF calls | GF input | GF cached | GF output | GF latency (s) | BL input | BL cached | BL output | BL latency (s) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in records:
        g, b = r["grantfit"]["telemetry"], r["baseline"]["telemetry"]
        L.append(f"| {r['id']} | {g.get('llm_calls', '—')} | {_tok(g, 'input_tokens')} | {_tok(g, 'cached_input_tokens')} | "
                 f"{_tok(g, 'output_tokens')} | {g.get('total_latency_ms', 0) / 1000:.1f} | {_tok(b, 'input_tokens')} | "
                 f"{_tok(b, 'cached_input_tokens')} | {_tok(b, 'output_tokens')} | {b.get('total_latency_ms', 0) / 1000:.1f} |")
    n = len(records)
    tot = {s: {k: _sum(records, s, k) for k in ("input_tokens", "cached_input_tokens", "output_tokens")}
           for s in ("grantfit", "baseline")}
    lat = {s: sum(r[s]["telemetry"].get("total_latency_ms", 0) for r in records) / 1000 for s in ("grantfit", "baseline")}
    calls = sum(r["grantfit"]["telemetry"].get("llm_calls", 0) for r in records)
    fmt = lambda v: "—" if v is None else f"{v:,}"  # noqa: E731
    L.append(f"| **Total** | {calls} | {fmt(tot['grantfit']['input_tokens'])} | {fmt(tot['grantfit']['cached_input_tokens'])} | "
             f"{fmt(tot['grantfit']['output_tokens'])} | {lat['grantfit']:.1f} | {fmt(tot['baseline']['input_tokens'])} | "
             f"{fmt(tot['baseline']['cached_input_tokens'])} | {fmt(tot['baseline']['output_tokens'])} | {lat['baseline']:.1f} |")
    L.append(f"| **Mean / case** | {calls / n:.1f} | {_div(tot['grantfit']['input_tokens'], n)} | {_div(tot['grantfit']['cached_input_tokens'], n)} | "
             f"{_div(tot['grantfit']['output_tokens'], n)} | {lat['grantfit'] / n:.1f} | {_div(tot['baseline']['input_tokens'], n)} | "
             f"{_div(tot['baseline']['cached_input_tokens'], n)} | {_div(tot['baseline']['output_tokens'], n)} | {lat['baseline'] / n:.1f} |")
    gi, bi = tot["grantfit"]["input_tokens"], tot["baseline"]["input_tokens"]
    go, bo = tot["grantfit"]["output_tokens"], tot["baseline"]["output_tokens"]
    if all(v for v in (gi, bi, go, bo)):
        L.append(f"\nGrantFit used {(gi + go) / (bi + bo):.2f}× the baseline's total tokens and "
                 f"{lat['grantfit'] / lat['baseline']:.2f}× its summed latency.")
    if meta["pricing_configured"]:
        gc = [r["grantfit"]["telemetry"].get("cost_usd") for r in records]
        bc = [r["baseline"]["telemetry"].get("cost_usd") for r in records]
        if None not in gc and None not in bc:
            L.append(f"Cost: GrantFit ${sum(gc):.4f} total (${sum(gc) / n:.4f}/case); "
                     f"baseline ${sum(bc):.4f} total (${sum(bc) / n:.4f}/case).")

    L.append("\n## Misses\n")
    misses = []
    for r in records:
        for sys_ in ("grantfit", "baseline"):
            bad = [m for m, v in r[sys_]["scores"].items() if v is False or (isinstance(v, float) and v < 1)]
            if bad:
                v = r[sys_]["view"]
                misses.append(f"- **{r['id']} {sys_}**: {', '.join(bad)} (got tier={v['tier']}, "
                              f"eligibility={v['eligibility']}, type={v['opportunity_type']})")
    L.extend(misses or ["None."])
    L.append("")
    return "\n".join(L)


def _div(v, n) -> str:
    return "—" if v is None else f"{v / n:,.0f}"


if __name__ == "__main__":
    raise SystemExit(main())
