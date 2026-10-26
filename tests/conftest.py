from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from grantfit.models import AssessmentCase  # noqa: E402

AS_OF = date(2026, 10, 25)
FIT_DIMS = ("mission_alignment", "population_alignment", "geography_alignment",
            "award_size_fit", "funder_priority_match")
CONCEPT_DIMS = ("problem_clarity", "logic_model", "measurable_outcomes",
                "feasibility", "budget_realism", "evidence_strength")


def assessment_dict(fit: int = 5, concept: int = 5, confidence: str = "high") -> dict:
    def dim(s):
        return {"score": s, "rationale": "test", "evidence_refs": ["profile.mission"]}
    return {
        "fit": {k: dim(fit) for k in FIT_DIMS},
        "concept": {k: dim(concept) for k in CONCEPT_DIMS},
        "evidence_gaps": ["baseline reading data for the new school"],
        "unsupported_claims": ["proven to double reading growth"],
        "confidence": confidence,
        "summary": "test assessment",
    }


@pytest.fixture
def sample_case() -> AssessmentCase:
    data = json.loads((ROOT / "data" / "samples" / "sample_case.json").read_text())
    return AssessmentCase.model_validate(data)


@pytest.fixture
def runs_dir(tmp_path) -> Path:
    return tmp_path / "runs"
