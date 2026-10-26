"""Re-apply the CURRENT deterministic decision logic to a previous live run's saved model outputs.

    .venv/bin/python -m eval.replay eval/results/20261026T025208Z

No model calls. The saved gpt-6-luna outputs (extraction results, fit/concept ratings, flags) are
reused as-is; eligibility rules, tier rules, and review classification are recomputed with the
current code. Limits, stated in the report:
- Structured-requirement cases: eligibility is recomputed exactly from the case inputs.
- Pasted-guideline cases: the run did not save raw extraction output, so the saved eligibility
  result is kept (extraction-derived criteria cannot be rebuilt without a model call).
- Saved ratings predate the insufficient-information option (assess_v2), so every dimension
  counts as assessed; coverage effects need a new live run to observe.
The original run folder is never modified; output goes to eval/results/replay-<timestamp>/.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval.metrics import METRICS, aggregate, score, view_grantfit
from eval.run_eval import RESULTS_DIR, _fmt_rate, load_cases
from grantfit.models import FundingOpportunity, Recommendation
from grantfit.review import classify_review
from grantfit.tools.eligibility import check_eligibility
from grantfit.tools.scoring import decide_priority

_RANGE = re.compile(r"outside the award range \$([\d,]+)–\$([\d,]+)")


def _opportunity(ec, rec: Recommendation) -> tuple[FundingOpportunity, str]:
    if rec.effective_opportunity is not None:
        return rec.effective_opportunity, "saved effective opportunity"
    o = ec.case.opportunity
    if not o.source_text:
        return o, "case inputs"
    # Pasted guidelines: rebuild only what the saved output records (type; award range from the saved note).
    lo = hi = None
    for r in rec.priority.tier_reasons:
        m = _RANGE.search(r)
        if m:
            lo, hi = (float(x.replace(",", "")) for x in m.groups())
    return o.model_copy(update={"opportunity_type": rec.opportunity_type, "award_min_usd": lo, "award_max_usd": hi,
                                "hard_criteria": []}), "saved type and award note (extraction not re-run)"


def replay(run_dir: Path) -> dict:
    cases = {c.id: c for c in load_cases()}
    rows = []
    for out in sorted((run_dir / "outputs").glob("E*-grantfit.json")):
        cid = out.name.split("-")[0]
        ec = cases[cid]
        old = Recommendation.model_validate_json(out.read_text(encoding="utf-8"))
        opp, opp_source = _opportunity(ec, old)
        structured = not ec.case.opportunity.source_text
        elig = check_eligibility(ec.case.profile, ec.case.opportunity, ec.as_of) if structured else old.eligibility
        prio = decide_priority(eligibility=elig, assessment=old.assessment, opportunity=opp, concept=ec.case.concept,
                               effort=old.priority.effort, deadline=old.priority.deadline)
        required, advisories = classify_review(
            eligibility=elig, opportunity=opp, opportunity_type=old.opportunity_type, degraded=old.degraded_stages,
            assessment=old.assessment, unsupported=old.unsupported_claims, extraction=old.extraction,
            flags=old.safety_flags, deadline=old.priority.deadline, effort=old.priority.effort,
            warnings=old.validation_warnings, score_notes=prio.score_notes)
        new = old.model_copy(update={"eligibility": elig, "priority": prio, "review_reasons": required,
                                     "advisories": advisories, "needs_human_review": bool(required)})
        old_view, new_view = view_grantfit(old), view_grantfit(new)
        rows.append({
            "id": cid, "title": ec.title, "expected_tiers": ec.expected.tier_allowed,
            "eligibility_source": "recomputed" if structured else "saved (extraction not re-run)",
            "opportunity_source": opp_source,
            "old": {"tier": old.priority.tier, "eligibility": old.eligibility.overall,
                    "review": old.needs_human_review, "review_reasons": old.review_reasons,
                    "scores": score(ec.expected, old_view)},
            "new": {"tier": prio.tier, "eligibility": elig.overall, "review": bool(required),
                    "review_reasons": required, "advisories": advisories, "scores": score(ec.expected, new_view)},
        })
    return {"run": run_dir.name, "rows": rows,
            "aggregates": {k: aggregate([r[k]["scores"] for r in rows]) for k in ("old", "new")}}


def render(data: dict, stamp: str) -> str:
    rows = data["rows"]
    L = [f"# Decision-logic replay of live run {data['run']}, {stamp}\n",
         "Saved gpt-6-luna outputs reused; **no model calls**. Current eligibility rules, tier rules, and review "
         "classification re-applied. Pasted-guideline cases keep their saved eligibility (raw extraction was not "
         "saved). Saved ratings predate the insufficient-information option. **Synthetic test cases only; this "
         "is not a measure of real-world accuracy.**\n",
         "## Metrics (GrantFit: before vs. after the audit fixes)\n",
         "| Metric | Before | After |", "|---|---|---|"]
    for m, label in METRICS.items():
        L.append(f"| {label} | {_fmt_rate(data['aggregates']['old'][m])} | {_fmt_rate(data['aggregates']['new'][m])} |")
    before = sum(r["old"]["review"] for r in rows)
    after = sum(r["new"]["review"] for r in rows)
    L += [f"\nHuman review required: **{before}/{len(rows)} before → {after}/{len(rows)} after**.\n",
          "## Per case\n", "| Case | Expected tier(s) | Before | After | Review before → after | Required review items (after) |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        ok = lambda s: "✅" if s["scores"]["tier_correct"] else "❌"  # noqa: E731
        L.append(f"| {r['id']} {r['title']} | {', '.join(r['expected_tiers'])} | {ok(r['old'])} {r['old']['tier']} | "
                 f"{ok(r['new'])} {r['new']['tier']} | {'yes' if r['old']['review'] else 'no'} → "
                 f"{'yes' if r['new']['review'] else 'no'} | {'; '.join(r['new']['review_reasons']) or '—'} |")
    L.append("\n## Advisories after reclassification (informational, shown to the user)\n")
    for r in rows:
        if r["new"]["advisories"]:
            L.append(f"- **{r['id']}**: " + " / ".join(r["new"]["advisories"]))
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    run_dir = Path(argv[0]) if argv else sorted(p for p in RESULTS_DIR.glob("2*") if not p.name.endswith("-FAKE"))[-1]
    data = replay(run_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"replay-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "replay.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    (out / "summary.md").write_text(render(data, stamp), encoding="utf-8")
    print(f"Replayed {len(data['rows'])} cases from {run_dir.name} -> {out / 'summary.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
