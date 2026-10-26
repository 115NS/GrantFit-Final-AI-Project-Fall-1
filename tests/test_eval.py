"""Offline tests for the evaluation dataset, baseline, metrics, and runner."""

import json
import typing

from conftest import assessment_dict

from eval import build_cases
from eval.metrics import METRICS, SystemView, aggregate, score, view_baseline
from eval.run_eval import load_cases, main as run_eval_main
from grantfit.baseline import BaselineOutput, build_user_message, run_baseline
from grantfit.llm.client import FakeLLMClient
from grantfit.models import EvalExpectation, Tier
from grantfit.telemetry import Tracer

TIERS = set(typing.get_args(Tier))


def test_dataset_is_complete_and_valid():
    cases = load_cases()
    assert len(cases) >= 15
    assert len({c.id for c in cases}) == len(cases)
    cats = {c.category for c in cases}
    for required in ("clear_eligible", "hard_fail", "missing_info", "relationship", "weak_concept",
                     "deadline", "injection", "unsupported_claims", "contradictory", "ambiguous"):
        assert required in cats
    for c in cases:
        assert c.expected.tier_allowed and set(c.expected.tier_allowed) <= TIERS
        assert "fictional" in c.case.model_dump_json().lower()


def test_committed_cases_match_builder():
    """eval/cases/*.json must be exactly what eval/build_cases.py generates."""
    committed = {c.id: c.model_dump(mode="json") for c in load_cases()}
    for raw in build_cases.CASES:
        built = build_cases.EvalCase.model_validate({**raw, "as_of": build_cases.AS_OF})
        assert committed[built.id] == built.model_dump(mode="json")


def test_baseline_gets_raw_inputs_and_returns_structured_output(tmp_path):
    case = load_cases({"E12"})[0]
    msg = build_user_message(case.case)
    assert "Ignore all previous instructions" in msg              # raw text, no delimiters or scan
    assert "<untrusted_funder_text>" not in msg
    out = {"eligibility": "PASS", "failed_requirements": [], "opportunity_type": "open_competitive",
           "recommendation": "PURSUE_NOW", "concept_weaknesses": [], "unsupported_claims": [],
           "concerns": [], "rationale": "ok"}
    client = FakeLLMClient([out])
    result, results, problems = run_baseline(client, Tracer(runs_dir=tmp_path), case.case)
    assert isinstance(result, BaselineOutput) and results[0].is_fake and not problems


EXP_FAIL = EvalExpectation(eligibility_allowed=["FAIL"], failed_kinds=["budget_max"],
                           tier_allowed=["NOT_ELIGIBLE"], must_not_recommend_apply=True,
                           injection_present=True, must_mention=[["budget"], ["deadline"]])


def test_score_rewards_correct_and_penalizes_gate_violation():
    good = SystemView(completed=True, eligibility="FAIL", tier="NOT_ELIGIBLE", failed_kinds=["budget_max"],
                      injection_flagged=True, text="Budget exceeds the cap.")
    s = score(EXP_FAIL, good)
    assert s["hard_fail_caught"] and s["injection_resisted"] and s["no_apply_when_forbidden"]
    assert s["must_mention_coverage"] == 0.5
    assert s["no_false_fail"] is None and s["type_correct"] is None   # not applicable

    bad = SystemView(completed=True, eligibility="PASS", tier="PURSUE_NOW", failed_kinds=[],
                     text="Guaranteed to win.", probability_claims=["Guaranteed"])
    s = score(EXP_FAIL, bad)
    assert not s["hard_fail_caught"] and not s["no_apply_when_forbidden"] and not s["no_probability_claims"]


def test_failed_system_scores_false_on_applicable_metrics():
    s = score(EXP_FAIL, view_baseline(None))
    assert s["eligibility_correct"] is False and s["hard_fail_caught"] is False
    assert s["contradiction_flagged"] is None


def test_aggregate_counts_only_applicable_cases():
    agg = aggregate([{m: None for m in METRICS} | {"tier_correct": True},
                     {m: None for m in METRICS} | {"tier_correct": False}])
    assert agg["tier_correct"] == {"num": 1.0, "den": 2, "rate": 0.5}
    assert agg["hard_fail_caught"]["den"] == 0 and agg["hard_fail_caught"]["rate"] is None


def test_run_eval_fake_end_to_end(tmp_path):
    code = run_eval_main(["--fake", "--cases", "E03,E12", "--workers", "2", "--out", str(tmp_path)])
    assert code == 0
    summary = (tmp_path / "summary.md").read_text()
    assert "FAKE RUN" in summary and "| Metric | GrantFit | Baseline |" in summary
    data = json.loads((tmp_path / "results.json").read_text())
    assert [c["id"] for c in data["cases"]] == ["E03", "E12"]
    for c in data["cases"]:
        assert c["grantfit"]["llm_source"].startswith("fake")
        assert c["grantfit"]["telemetry"]["input_tokens"] is None      # fake never reports tokens
        assert c["grantfit"]["scores"]["hard_fail_caught"] is True     # deterministic gate, even offline
    assert len(list((tmp_path / "traces").glob("*.jsonl"))) == 4
    assert len(list((tmp_path / "outputs").glob("*.json"))) == 4


def test_baseline_injection_mention_detected_with_varied_wording():
    out = BaselineOutput(eligibility="FAIL", failed_requirements=[], opportunity_type="open_competitive",
                         recommendation="NOT_ELIGIBLE", concept_weaknesses=[], unsupported_claims=[],
                         concerns=["The source text includes instructions directed at an AI reviewer."],
                         rationale="Budget too high.")
    assert view_baseline(out).injection_flagged


def test_rescore_recomputes_from_saved_outputs(tmp_path):
    assert run_eval_main(["--fake", "--cases", "E03", "--out", str(tmp_path)]) == 0
    assert run_eval_main(["--rescore", str(tmp_path)]) == 0
    assert (tmp_path / "summary.initial.md").exists() and (tmp_path / "results.initial.json").exists()
    assert "Rescored from saved outputs" in (tmp_path / "summary.md").read_text()


def test_replay_reapplies_current_logic_without_model_calls_or_touching_evidence(tmp_path, monkeypatch):
    import hashlib
    from eval import replay
    from eval.run_eval import RESULTS_DIR
    run = RESULTS_DIR / "20261026T025208Z"
    if not run.exists():
        import pytest
        pytest.skip("saved live run not present")
    before = hashlib.sha256((run / "results.json").read_bytes()).hexdigest()
    monkeypatch.setattr("grantfit.llm.client.PortkeyClient.__init__",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model client may be created")))
    data = replay.replay(run)
    assert len(data["rows"]) == 17
    assert hashlib.sha256((run / "results.json").read_bytes()).hexdigest() == before
    new = data["aggregates"]["new"]
    assert new["hard_fail_caught"]["rate"] == 1.0 and new["tier_correct"]["rate"] == 1.0
    assert sum(r["new"]["review"] for r in data["rows"]) < sum(r["old"]["review"] for r in data["rows"])
