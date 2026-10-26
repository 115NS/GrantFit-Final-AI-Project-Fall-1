"""Offline tests for the Dash UI integration (forms, jobs, rendering, roadmap, assets)."""

import time

import pytest
from conftest import AS_OF

from app import app as app_module
from app.forms import FIELD_IDS, case_to_form, form_to_case
from app.jobs import get_job, progress, start_job
from app.results import render_results, render_run_details
from app.samples import SAMPLES, load_sample, load_saved_result, saved_result_options
from grantfit.llm.client import FakeLLMClient
from grantfit.llm.fake_responses import fake_response
from grantfit.pipeline import run_case
from grantfit.roadmap import build_roadmap


def text_of(component) -> str:
    """All visible text in a Dash component tree."""
    if component is None:
        return ""
    if isinstance(component, (str, int, float)):
        return str(component)
    if isinstance(component, (list, tuple)):
        return " ".join(text_of(c) for c in component)
    return text_of(getattr(component, "children", None))


def demo(case, runs_dir):
    return run_case(case, FakeLLMClient(fake_response), as_of=AS_OF, runs_dir=runs_dir)


# --------------------------------------------------------------------------- forms

@pytest.mark.parametrize("key", [k for k, _, _ in SAMPLES])
def test_samples_round_trip_through_form(key):
    original = load_sample(key)
    values = case_to_form(original)
    assert values["_unmapped_criteria"] == []
    case, errors = form_to_case(values)
    assert errors == {}
    assert case.profile.model_dump(exclude={"id"}) == original.profile.model_dump(exclude={"id"})
    assert case.concept == original.concept
    o, oo = case.opportunity, original.opportunity
    assert (o.opportunity_type, o.deadline, o.award_min_usd, o.award_max_usd, o.priorities) == \
           (oo.opportunity_type, oo.deadline, oo.award_min_usd, oo.award_max_usd, oo.priorities)
    assert (o.source_text or "") == (oo.source_text or "").strip()   # pasted text is trimmed
    assert o.source_url == oo.source_url

    def norm(v):
        return float(v) if isinstance(v, (int, float)) else str(v)
    assert sorted((c.kind, norm(c.value), c.source_quote) for c in o.hard_criteria) == \
           sorted((c.kind, norm(c.value), c.source_quote) for c in oo.hard_criteria)
    assert sorted((c.name, c.quantity) for c in o.required_components) == \
           sorted((c.name, c.quantity) for c in oo.required_components)


def test_form_validation_reports_errors_without_building_a_case():
    values = case_to_form(load_sample("sample"))
    values.update({"p-name": "  ", "p-annual_budget_usd": "lots", "o-award_min_usd": 90000,
                   "o-award_max_usd": 10000, "o-deadline": "next Tuesday", "p-founded_year": 1500})
    case, errors = form_to_case(values)
    assert case is None
    assert set(errors) == {"p-name", "p-annual_budget_usd", "o-award_max_usd", "o-deadline", "p-founded_year"}


def test_blank_form_needs_only_three_fields():
    values = {fid: None for fid in FIELD_IDS}
    _, errors = form_to_case(values)
    assert set(errors) == {"p-name", "o-funder_name", "c-title"}
    values.update({"p-name": "Org", "o-funder_name": "Funder", "c-title": "Project"})
    case, errors = form_to_case(values)
    assert errors == {} and case.opportunity.opportunity_type is None and case.profile.legal_status == "unknown"


# --------------------------------------------------------------------------- jobs

def wait(job_id, timeout=10):
    t0 = time.time()
    while get_job(job_id).status == "running" and time.time() - t0 < timeout:
        time.sleep(0.02)
    return get_job(job_id)


def test_demo_job_runs_real_pipeline_and_reports_progress(tmp_path):
    job = wait(start_job(load_sample("guidelines"), "demo", runs_dir=tmp_path))
    assert job.status == "done" and job.result.llm_source.startswith("fake")
    states = progress(job)
    assert [s for s, _, _ in states][:3] == ["guideline_safety_scan", "extract_guidelines", "quote_verification"]
    assert all(state == "done" for _, _, state in states)


def test_live_job_without_key_fails_clearly_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("grantfit.llm.client.get_api_key", lambda: None)
    job = get_job(start_job(load_sample("sample"), "live", runs_dir=tmp_path))
    assert job.status == "error" and "PORTKEY_API_KEY not found" in job.error
    assert job.result is None


# --------------------------------------------------------------------------- rendering

def test_hard_failure_renders_not_eligible_with_review(tmp_path):
    case = load_sample("E03")
    rec = demo(case, tmp_path)
    page = text_of(render_results(rec, "demo"))
    assert "Not eligible" in page and "Human review required" in page
    assert "Demo result: scripted offline output, not an AI assessment" in page
    assert "Do not apply to this opportunity" in page


def test_injection_warning_is_prominent(tmp_path):
    rec = demo(load_sample("injection"), tmp_path)
    page = text_of(render_results(rec, "demo"))
    assert "Possible manipulation detected in the pasted guidelines" in page
    assert "Verified against an exact quote" in page
    assert "Possible prompt injection in pasted guidelines" in page


def test_saved_live_result_is_labeled_and_run_details_show_real_telemetry():
    options = saved_result_options()
    if not options:
        pytest.skip("no saved live evaluation run available")
    rec, run = load_saved_result(options[0]["value"])
    page = text_of(render_results(rec, "saved", run))
    assert "Saved live result" in page and "It was not re-run now" in page
    details = text_of(render_run_details(rec, "saved", run))
    assert "gpt-6-luna" in details and "Unpriced" in details
    assert "Fit and concept assessment" in details     # per-stage rows from the trace


def test_saved_result_paths_cannot_escape_results_dir():
    with pytest.raises((ValueError, FileNotFoundError, StopIteration)):
        load_saved_result("../../config/x")


def test_demo_run_details_never_claim_tokens_or_cost(tmp_path):
    rec = demo(load_sample("sample"), tmp_path)
    details = text_of(render_run_details(rec, "demo"))
    assert "Demo (scripted offline output)" in details
    assert "Not applicable (no live model calls)" in details
    assert "token counts are not reported" in details


# --------------------------------------------------------------------------- roadmap

def test_roadmap_orders_blockers_first_and_cites_sources(tmp_path):
    rec = demo(load_sample("E03"), tmp_path)
    steps = build_roadmap(rec)
    assert steps[0].priority == "Blocker" and "Do not apply" in steps[0].action
    assert all(s.source for s in steps)
    order = {"Blocker": 0, "Before applying": 1, "Strengthen": 2, "Plan": 3}
    assert [order[s.priority] for s in steps] == sorted(order[s.priority] for s in steps)


def test_roadmap_includes_unsupported_claims_and_effort(tmp_path):
    rec = demo(load_sample("E14"), tmp_path)
    actions = " ".join(s.action for s in build_roadmap(rec, max_steps=20))
    assert "Substantiate or soften the claim" in actions


# --------------------------------------------------------------------------- app + assets

def test_layout_builds_and_load_sample_callback_fills_fields():
    app_module.layout()
    out = app_module.load_sample_cb(1, "sample")
    assert len(out) == len(FIELD_IDS) + 2
    assert out[FIELD_IDS.index("p-name")] == "Elm City Reading Partners (fictional)"
    assert out[-1] == "/profile"


def test_bulldog_cameo_uses_supplied_asset_and_degrades_if_missing(tmp_path, monkeypatch):
    import hashlib
    supplied = app_module.ASSETS_DIR.parent.parent / "images-2.jpeg"
    asset = app_module.ASSETS_DIR / app_module.BULLDOG_FILE
    assert hashlib.sha256(asset.read_bytes()).digest() == hashlib.sha256(supplied.read_bytes()).digest()

    job_id = start_job(load_sample("sample"), "demo", runs_dir=tmp_path)
    get_job(job_id).status = "running"   # render the in-progress view
    assert "yale_bulldog.jpeg" in str(app_module._progress_view(job_id))
    monkeypatch.setattr(app_module, "ASSETS_DIR", tmp_path)   # asset missing
    view = app_module._progress_view(job_id)
    assert "yale_bulldog" not in str(view) and "Running demo assessment" in text_of(view)
    wait(job_id)


def test_render_callback_empty_and_result_states(tmp_path):
    empty_results, empty_run = app_module.render_cb(None)
    assert "No results yet" in text_of(empty_results)
    pending_results, pending_run = app_module.render_cb({"pending": True})
    assert pending_results is None and "current assessment" in text_of(pending_run)
    rec = demo(load_sample("sample"), tmp_path)
    res, run = app_module.render_cb({"origin": "demo", "rec": rec.model_dump_json()})
    assert "Recommendation" in text_of(res) and "Run summary" in text_of(run)


# --------------------------------------------------------------------------- real callback dispatch

def dispatch(trigger: str, values: dict, output_contains: str):
    """POST to Dash's real /_dash-update-component endpoint, exercising callback wiring and
    argument signatures exactly as the browser does."""
    key = next(k for k, v in app_module.app.callback_map.items() if output_contains in k
               and trigger in {f"{i['id']}.{i['property']}" for i in v["inputs"]})
    spec = app_module.app.callback_map[key]
    outs = []
    for part in key.strip(".").split("..."):
        cid, prop = part.split(".", 1)
        outs.append({"id": cid, "property": prop})
    body = {
        "output": key, "outputs": outs if len(outs) > 1 else outs[0],
        "inputs": [{**i, "value": values.get(f"{i['id']}.{i['property']}")} for i in spec["inputs"]],
        "state": [{**s, "value": values.get(f"{s['id']}.{s['property']}")} for s in spec["state"]],
        "changedPropIds": [trigger],
    }
    resp = app_module.app.server.test_client().post("/_dash-update-component", json=body)
    assert resp.status_code in (200, 204), resp.get_data(as_text=True)[:500]
    return resp.get_json() if resp.status_code == 200 else None


def form_state(key="E03"):
    values = case_to_form(load_sample(key))
    return {f"{fid}.value": values[fid] for fid in FIELD_IDS}


def test_run_callback_starts_demo_job_via_http(monkeypatch, tmp_path):
    monkeypatch.setattr("app.jobs.Tracer", lambda runs_dir=None: __import__("grantfit.telemetry").telemetry.Tracer(runs_dir=tmp_path))
    state = {**form_state(), "btn-run-demo.n_clicks": 1, "opt-fresh.value": []}
    out = dispatch("btn-run-demo.n_clicks", state, "form-errors.children")["response"]
    job_id = out["job-id"]["data"]
    assert out["url"]["pathname"] == "/results" and out["poll"]["disabled"] is False
    assert out["result-store"]["data"] == {"pending": True}       # no "No results yet" mid-run
    job = wait(job_id)
    assert job.status == "done" and job.result.priority.tier == "NOT_ELIGIBLE"
    polled = dispatch("poll.n_intervals", {"poll.n_intervals": 1, "job-id.data": job_id}, "progress-panel.children@")
    assert polled["response"]["result-store"]["data"]["origin"] == "demo"


def test_run_callback_reports_validation_errors_via_http():
    state = {**form_state(), "p-name.value": "", "btn-run-demo.n_clicks": 1, "opt-fresh.value": []}
    out = dispatch("btn-run-demo.n_clicks", state, "form-errors.children")["response"]
    assert "Organization name" in json_text(out["form-errors"]["children"])
    assert "is-invalid" in out["p-name"]["className"]
    assert "job-id" not in out    # no job started; form values untouched (not an output)


def json_text(obj) -> str:
    import json
    return json.dumps(obj)


def test_without_api_key_live_button_is_disabled_and_explained(monkeypatch):
    monkeypatch.setattr("app.app.get_api_key", lambda: None)
    panel = app_module.run_panel()

    def find(c, cid):
        if getattr(c, "id", None) == cid:
            return c
        kids = getattr(c, "children", None)
        for k in (kids if isinstance(kids, list) else [kids]):
            if k is not None and not isinstance(k, str):
                hit = find(k, cid)
                if hit is not None:
                    return hit
    assert find(panel, "btn-run-live").disabled is True
    assert getattr(find(panel, "btn-run-demo"), "disabled", False) is False
    assert "PORTKEY_API_KEY in .env" in text_of(panel)
    assert "No API key found" in text_of(app_module.home_page())


# --------------------------------------------------------------------------- design-refresh components

def test_stepper_and_readiness_render_from_real_field_values():
    values = case_to_form(load_sample("sample"))
    out = dispatch("url.pathname", {f"{fid}.value": values[fid] for fid in FIELD_IDS} | {"url.pathname": "/profile"},
                   "stepper-profile.children")["response"]
    stepper = json_text(out["stepper-profile"]["children"])
    assert "(current step)" in stepper and "Nonprofit" in stepper and "answered" in stepper
    readiness = json_text(out["run-readiness"]["children"])
    assert "Funder guidelines pasted or retrieved" in readiness
    blank = dispatch("url.pathname", {f"{fid}.value": None for fid in FIELD_IDS} | {"url.pathname": "/concept"},
                     "stepper-profile.children")["response"]
    assert "required field missing" in json_text(blank["run-readiness"]["children"])


def test_workspace_snapshot_reflects_real_state(tmp_path):
    empty = text_of(app_module.render_snapshot([], None, live=False))
    assert "Nothing saved yet" in empty and "None yet" in empty and "demo mode" in empty
    rec = demo(load_sample("E03"), tmp_path)
    full = text_of(app_module.render_snapshot([{"item": {}}, {"item": {}}], {"origin": "demo", "rec": rec.model_dump_json()},
                                              live=True))
    assert "2 saved opportunities" in full and "Not eligible (demo run)" in full


def test_results_layout_puts_next_steps_first(tmp_path):
    rec = demo(load_sample("E03"), tmp_path)
    page = render_results(rec, "demo")
    layout = next(c for c in page if getattr(c, "className", "") == "results-layout")
    main = layout.children[0].children
    assert "Prioritized next steps" in text_of(main[0])
    assert "(demo placeholder)" in text_of(page)            # demo scores still labeled in the new tiles


# --------------------------------------------------------------------------- run buttons: never fail silently

def test_run_buttons_have_browser_side_feedback_even_without_a_server():
    """Regression: with the Python server stopped (or never started, e.g. 'Address already in use'),
    clicking Run did nothing visible. A clientside callback now shows immediate feedback and, if the
    server never answers, a clear error."""
    cbs = [c for c in app_module.app._callback_list if "run-status.children" in str(c.get("output"))]
    client = [c for c in cbs if c.get("clientside_function")]
    assert len(client) == 1
    assert {i["id"] for i in client[0]["inputs"]} == {"btn-run-live", "btn-run-demo"}
    script = " ".join(str(s) for s in app_module.app._inline_scripts)
    assert "No response from the GrantFit server" in script and "setTimeout" in script and "6000" in script
    assert 'id="run-status"' in str(app_module.run_panel()).replace("id='run-status'", 'id="run-status"')


def test_server_clears_run_status_on_success_and_on_validation_errors(monkeypatch, tmp_path):
    monkeypatch.setattr("app.jobs.Tracer", lambda runs_dir=None: __import__("grantfit.telemetry").telemetry.Tracer(runs_dir=tmp_path))
    ok = dispatch("btn-run-demo.n_clicks", {**form_state(), "btn-run-demo.n_clicks": 1, "opt-fresh.value": []},
                  "form-errors.children")["response"]
    assert ok["run-status"]["children"] is None and ok["url"]["pathname"] == "/results"
    bad = dispatch("btn-run-demo.n_clicks", {**form_state(), "p-name.value": "", "btn-run-demo.n_clicks": 1,
                                             "opt-fresh.value": []}, "form-errors.children")["response"]
    assert bad["run-status"]["children"] is None and "Organization name" in json_text(bad["form-errors"]["children"])
    wait(ok["job-id"]["data"])


def test_live_button_dispatch_starts_a_live_job_path(monkeypatch, tmp_path):
    """The live button routes to mode='live' (client creation is stubbed; no network)."""
    seen = {}

    def fake_start(case, mode, fresh=False, **kw):
        seen.update(mode=mode, fresh=fresh)
        return "stub-job"
    monkeypatch.setattr("app.app.start_job", fake_start)
    monkeypatch.setattr("app.app._progress_view", lambda job_id: None)
    out = dispatch("btn-run-live.n_clicks", {**form_state(), "btn-run-live.n_clicks": 1, "opt-fresh.value": ["fresh"]},
                   "form-errors.children")["response"]
    assert seen == {"mode": "live", "fresh": True} and out["url"]["pathname"] == "/results"


def test_startup_refuses_busy_port_with_clear_message():
    import socket
    import pytest
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert app_module.port_available("127.0.0.1", port) is False
        with pytest.raises(SystemExit) as exc:
            app_module.main(["--port", str(port)])
    assert "already in use" in str(exc.value) and "did NOT start" in str(exc.value)


def test_results_explain_method_once_and_keep_unknown_meaning(tmp_path):
    rec = demo(load_sample("E03"), tmp_path)
    page = text_of(render_results(rec, "demo"))
    assert page.count("How this was assessed") == 2          # section summary + pointer from the scores card
    assert page.count("(r−1)/4×100") == 1                    # formula stated once
    assert "UNKNOWN means not enough evidence to decide" in page
    assert "does not estimate or promise award probabilities" in page   # first backend disclaimer stays visible
    from grantfit.config import load_yaml
    assert f"at least {load_yaml('scoring')['thresholds']['concept_min']}" in page
