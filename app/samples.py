"""Sample inputs (fictional) and saved live results available in the UI."""

from __future__ import annotations

import json
from pathlib import Path

from grantfit.config import PROJECT_ROOT
from grantfit.models import AssessmentCase, EvalCase, Recommendation

SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"
EVAL_CASES_DIR = PROJECT_ROOT / "eval" / "cases"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"

SAMPLES = [
    ("sample", "Eligible literacy nonprofit, structured requirements", SAMPLES_DIR / "sample_case.json"),
    ("guidelines", "Same nonprofit, pasted funder guidelines", SAMPLES_DIR / "guideline_case.json"),
    ("injection", "Guidelines with a prompt-injection attempt and a budget cap", SAMPLES_DIR / "malicious_guideline_case.json"),
    ("E02", "Food network with an evidence-backed concept", EVAL_CASES_DIR / "E02_eligible_strong_fit_evidence-backed_concept.json"),
    ("E03", "Strong fit, but budget over the funder's cap", EVAL_CASES_DIR / "E03_high_fit_budget_over_cap.json"),
    ("E07", "Invitation-only foundation", EVAL_CASES_DIR / "E07_invitation-only_foundation.json"),
    ("E14", "Bold impact claims with no evidence listed", EVAL_CASES_DIR / "E14_unsupported_impact_claims_no_evidence.json"),
]


def sample_options() -> list[dict]:
    return [{"label": label, "value": key} for key, label, path in SAMPLES if path.exists()]


def load_sample(key: str) -> AssessmentCase:
    path = next(p for k, _, p in SAMPLES if k == key)
    data = json.loads(path.read_text(encoding="utf-8"))
    if "expected" in data:                  # an evaluation case wraps the AssessmentCase
        return EvalCase.model_validate(data).case
    return AssessmentCase.model_validate(data)


def latest_live_run() -> Path | None:
    runs = sorted(p for p in RESULTS_DIR.glob("*") if p.is_dir() and not p.name.endswith("-FAKE")
                  and (p / "results.json").exists())
    return runs[-1] if runs else None


def saved_result_options() -> list[dict]:
    run = latest_live_run()
    if run is None:
        return []
    data = json.loads((run / "results.json").read_text(encoding="utf-8"))
    if not data.get("meta", {}).get("mode", "").startswith("LIVE"):
        return []
    return [{"label": f"{c['id']}: {c['title']}", "value": f"{run.name}/{c['id']}"}
            for c in data["cases"] if (run / "outputs" / f"{c['id']}-grantfit.json").exists()]


def load_saved_result(value: str) -> tuple[Recommendation, str]:
    run_name, case_id = value.split("/", 1)
    path = RESULTS_DIR / run_name / "outputs" / f"{case_id}-grantfit.json"
    if RESULTS_DIR.resolve() not in path.resolve().parents:
        raise ValueError("Invalid saved-result path")
    return Recommendation.model_validate_json(path.read_text(encoding="utf-8")), run_name
