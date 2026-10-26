"""Neutral scripted responses for --fake runs (CLI and eval harness). NOT model output.

Every text field says FAKE OFFLINE OUTPUT, and FakeLLMClient reports no token usage.
"""

from __future__ import annotations

FAKE = "FAKE OFFLINE OUTPUT"

_FIT = ("mission_alignment", "population_alignment", "geography_alignment", "award_size_fit",
        "funder_priority_match")
_CONCEPT = ("problem_clarity", "logic_model", "measurable_outcomes", "feasibility",
            "budget_realism", "evidence_strength")


def fake_response(system: str, _user: str) -> dict:
    if "funding-guideline extractor" in system:
        # Extracts nothing; only GrantFit's deterministic guideline scan contributes.
        return {
            "funder_name": None, "program_title": None, "opportunity_type": "unclear",
            "opportunity_type_quote": None, "deadline": None, "deadline_quote": None,
            "award_min_usd": None, "award_max_usd": None, "award_quote": None,
            "requirements": [], "ambiguities": [], "contradictions": [],
            "missing_information": [f"{FAKE} - no model extraction was performed."],
            "suspected_injection": [],
        }
    if "single-prompt baseline" in system:
        return {
            "eligibility": "UNKNOWN", "failed_requirements": [], "opportunity_type": "unclear",
            "recommendation": "VERIFY_FIRST", "concept_weaknesses": [FAKE],
            "unsupported_claims": [], "concerns": [FAKE], "rationale": FAKE,
        }
    dim = {"score": 3, "rationale": f"{FAKE} - not a real model assessment.", "evidence_refs": []}
    return {
        "fit": {k: dim for k in _FIT},
        "concept": {k: dim for k in _CONCEPT},
        "evidence_gaps": [FAKE],
        "unsupported_claims": [],
        "confidence": "low",
        "summary": f"{FAKE} - placeholder text, not a real gpt-6-luna assessment.",
    }
