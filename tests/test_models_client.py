import pytest
from conftest import assessment_dict
from pydantic import ValidationError

from grantfit.assess import parse_assessment
from grantfit.config import load_yaml
from grantfit.llm.client import FakeLLMClient, MissingAPIKeyError, PortkeyClient, to_strict_schema
from grantfit.models import FitConceptAssessment
from grantfit.telemetry import compute_cost


def test_assessment_schema_rejects_out_of_range_and_missing():
    bad = assessment_dict()
    bad["fit"]["mission_alignment"]["score"] = 7
    with pytest.raises(ValidationError):
        FitConceptAssessment.model_validate(bad)
    missing = assessment_dict()
    del missing["concept"]["feasibility"]
    with pytest.raises(ValidationError):
        FitConceptAssessment.model_validate(missing)
    extra = assessment_dict()
    extra["award_probability"] = 0.8
    with pytest.raises(ValidationError):
        FitConceptAssessment.model_validate(extra)


def test_strict_schema_closes_every_object():
    schema = to_strict_schema(FitConceptAssessment.model_json_schema())

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    objs = list(objects(schema))
    assert objs
    for o in objs:
        assert o["additionalProperties"] is False
        assert set(o["required"]) == set(o["properties"])


def test_parse_tolerates_code_fences():
    import json
    text = "```json\n" + json.dumps(assessment_dict()) + "\n```"
    assert parse_assessment(text).confidence == "high"


def test_cost_is_unpriced_without_rates_and_computed_with_rates():
    assert compute_cost("gpt-6-luna", 1000, 0, 100, pricing={"models": {}}) == (None, "unpriced")
    assert compute_cost("gpt-6-luna", None, None, None,
                        pricing={"models": {"gpt-6-luna": {"input_per_1m": 1, "cached_input_per_1m": 0.1,
                                                           "output_per_1m": 4}}}) == (None, "unpriced")
    cost, src = compute_cost("gpt-6-luna", 1_000_000, 200_000, 500_000,
                             pricing={"models": {"gpt-6-luna": {"input_per_1m": 1.0,
                                                                "cached_input_per_1m": 0.1,
                                                                "output_per_1m": 4.0}}})
    assert src == "computed_from_config"
    assert cost == pytest.approx(0.8 + 0.02 + 2.0)


def test_required_model_configured():
    assert load_yaml("settings")["llm"]["model"] == "gpt-6-luna"


def test_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr("grantfit.llm.client.get_api_key", lambda: None)
    with pytest.raises(MissingAPIKeyError):
        PortkeyClient()


def test_fake_client_is_labeled_and_reports_no_usage():
    res = FakeLLMClient([assessment_dict()]).complete_json(system="s", user="u", schema_name="x", schema={})
    assert res.is_fake and res.output_mode == "fake"
    assert res.input_tokens is None and res.output_tokens is None


def test_gateway_cache_hit_is_not_counted_as_new_spend(tmp_path):
    from datetime import datetime, timezone
    from grantfit.llm.client import LLMResult
    from grantfit.telemetry import Tracer

    def live_like(cache_status):
        return LLMResult(text="{}", model_requested="gpt-6-luna", model_served="gpt-6-luna-global",
                         is_fake=False, output_mode="json_schema_strict", latency_ms=900.0,
                         input_tokens=100, cached_input_tokens=0, output_tokens=50,
                         gateway_cache_status=cache_status)

    tracer = Tracer(runs_dir=tmp_path)
    for status in ("HIT", "MISS"):
        tracer.llm_span(stage="s", started=datetime.now(timezone.utc), attempt=1, status="ok",
                        prompt_version="v", input_payload="x", result=live_like(status),
                        model_requested="gpt-6-luna")
    assert tracer.spans[0].cost_source == "gateway_cache_hit" and tracer.spans[0].cost_usd is None
    summary = tracer.summary()
    assert summary.gateway_cache_hits == 1
    assert summary.input_tokens == 100 and summary.output_tokens == 50  # only the genuine call
