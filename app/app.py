"""GrantFit web UI (Dash).

    .venv/bin/python -m app.app            # http://127.0.0.1:8050

All assessments come from grantfit.pipeline.run_case; this module only collects input,
starts jobs, and displays results.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update

from app.discover import discover_page, register_callbacks as register_discover
from app.forms import (FIELD_BY_ID, FIELD_IDS, SECTION_LABELS, SECTION_PATHS, base_class, case_to_form, completion,
                       form_to_case, section_form)
from app.jobs import LLM_STAGES, get_job, progress, start_job
from app.results import card, render_results, render_run_details
from app.samples import load_sample, load_saved_result, sample_options, saved_result_options
from grantfit.config import get_api_key, load_yaml
from grantfit.models import Recommendation

ASSETS_DIR = Path(__file__).parent / "assets"
BULLDOG_FILE = "yale_bulldog.jpeg"
MODEL = load_yaml("settings")["llm"]["model"]

PAGES = [("home", "/", "Overview"), ("discover", "/discover", "Discover funding"), ("profile", "/profile", "1 · Nonprofit"),
         ("opportunity", "/opportunity", "2 · Opportunity"), ("concept", "/concept", "3 · Concept"),
         ("results", "/results", "Results"), ("run", "/run", "Run details")]

app = Dash(__name__, assets_folder=str(ASSETS_DIR), title="GrantFit",
           suppress_callback_exceptions=True, update_title=None)
server = app.server
app.index_string = app.index_string.replace("<html>", '<html lang="en">', 1)  # screen-reader language


def bulldog_available() -> bool:
    return (ASSETS_DIR / BULLDOG_FILE).is_file()


def live_available() -> bool:
    return get_api_key() is not None   # presence only; the key is never displayed or returned


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

STEP_KEYS = ["profile", "opportunity", "concept"]
STEP_NAMES = {"profile": "Nonprofit", "opportunity": "Opportunity", "concept": "Concept"}


def _step_nav(back: tuple[str, str] | None, nxt: tuple[str, str] | None):
    return html.Nav([
        dcc.Link(f"← {back[1]}", href=back[0], className="btn btn-ghost") if back else html.Span(),
        dcc.Link(f"{nxt[1]} →", href=nxt[0], className="btn btn-primary") if nxt else html.Span(),
    ], className="step-nav", **{"aria-label": "Form steps"})


def _form_page(section: str, intro: str, back, nxt, extra=None, before=None):
    n = STEP_KEYS.index(section) + 1
    return [
        html.Div(id=f"stepper-{section}", className="stepper-slot"),
        html.Header([html.P(f"Assess · step {n} of 3", className="eyebrow"),
                     html.H1(SECTION_LABELS[section]), html.P(intro, className="lead")], className="page-head"),
        *(before or []),
        html.Div(section_form(section), className="form-stack"),
        *(extra or []),
        _step_nav(back, nxt),
    ]


def render_stepper(current: str, comp: dict):
    items = []
    for i, key in enumerate(STEP_KEYS, 1):
        c = comp[key]
        state = "current" if key == current else ("done" if c["required_ok"] and c["answered"] else "todo")
        detail = f"{c['answered']} of {c['total']} answered" + ("" if c["required_ok"] else " · required field missing")
        items.append(html.Li(dcc.Link([
            html.Span("✓" if state == "done" else str(i), className="step-dot", **{"aria-hidden": "true"}),
            html.Span([html.Span(STEP_NAMES[key], className="step-name"), html.Span(detail, className="step-detail")],
                      className="step-text"),
            # dcc.Link does not accept aria-current; announce the current step as text instead.
            html.Span(" (current step)", className="sr-only") if key == current else None,
        ], href=SECTION_PATHS[key], className="step-link"),
            className=f"step step-{state}"))
    run_ready = all(comp[k]["required_ok"] for k in STEP_KEYS)
    items.append(html.Li(html.Div([
        html.Span("4", className="step-dot", **{"aria-hidden": "true"}),
        html.Span([html.Span("Run", className="step-name"),
                   html.Span("Ready" if run_ready else "Complete required fields", className="step-detail")],
                  className="step-text")], className="step-link step-static"),
        className="step " + ("step-ready" if run_ready else "step-todo")))
    return html.Nav(html.Ol(items, className="stepper"), **{"aria-label": "Assessment progress"})


def home_page():
    live = live_available()
    steps = [
        ("Discover", "Search live sources; every detail on a card shows where it came from."),
        ("Assess", "Check eligibility by rules and rate funder fit and concept quality."),
        ("Act", "Get a recommendation, the risks, and prioritized next steps."),
    ]
    principles = [
        ("Hard rules stay hard", "No score can outweigh a failed requirement."),
        ("Evidence or Unknown", "Unsupported details are marked Unknown."),
        ("Effort-adjusted", "Staff time and deadlines shape the advice."),
        ("No award promises", "No chance-of-winning estimates."),
    ]
    return [
        html.Section([
            html.Div([
                html.P("Funding intelligence for nonprofits", className="eyebrow"),
                html.H1("Decide which funding is worth pursuing, and what it will take.", className="hero-title"),
                html.P("Find live opportunities and get an evidence-based assessment before you spend scarce "
                       "writing time.", className="lead"),
                html.Div([dcc.Link(["Discover funding", html.Span(" →", **{"aria-hidden": "true"})], href="/discover",
                                   className="btn btn-primary btn-lg"),
                          dcc.Link("Assess an opportunity you have", href="/profile", className="btn btn-secondary btn-lg")],
                         className="actions"),
                html.P([html.Span(className="dot " + ("dot-on" if live else "dot-off"), **{"aria-hidden": "true"}),
                        f"Live model: {MODEL} via Portkey, API key configured." if live else
                        "No API key found: discovery, demo runs, and saved results still work (see README)."],
                       className="status-line"),
            ], className="hero-copy"),
            html.Aside([html.H2("Your workspace", className="panel-title"), html.Div(id="home-snapshot")],
                       className="workspace-panel", **{"aria-label": "Your workspace"}),
        ], className="hero"),
        html.Section([
            html.H2("How it works", className="section-title"),
            html.Ol([html.Li([html.Span(str(i), className="flow-num", **{"aria-hidden": "true"}),
                              html.H3(t, className="flow-title"), html.P(d, className="flow-text")], className="flow-step")
                     for i, (t, d) in enumerate(steps, 1)], className="flow"),
        ], className="home-section"),
        html.Section([
            html.H2("Built to be trusted", className="section-title"),
            html.Ul([html.Li([html.Span("✓", className="principle-mark", **{"aria-hidden": "true"}),
                              html.Div([html.Strong(t), html.Span(d)])], className="principle")
                     for t, d in principles], className="principles"),
        ], className="home-section"),
        html.Section([
            html.Div([html.H2("Explore safely", className="section-title", id="samples"),
                      html.P([html.Span("Fictional demo data", className="badge badge-warn"),
                              " Invented for testing; not real discoveries."],
                             className="help")], className="section-head"),
            html.Div([
                html.Div([
                    html.Fieldset([html.Legend("Load a sample into the form", className="label"),
                                   dcc.RadioItems(id="sample-select", options=sample_options(), value="sample",
                                                  className="choice-group choice-list", labelClassName="choice",
                                                  inputClassName="choice-input")]),
                    html.Button("Load sample", id="btn-load-sample", className="btn btn-primary", n_clicks=0),
                    html.P(id="sample-status", className="help", role="status"),
                ], className="card sample-card"),
                html.Div([
                    html.Fieldset([html.Legend("View a saved live result", className="label"),
                                   html.P("Real gpt-6-luna results from evaluation; no new model call.",
                                          className="help"),
                                   dcc.RadioItems(id="saved-select", options=saved_result_options(),
                                                  className="choice-group choice-list choice-scroll",
                                                  labelClassName="choice", inputClassName="choice-input")]),
                    html.Button("View saved result", id="btn-view-saved", className="btn btn-secondary", n_clicks=0),
                    html.P(id="saved-status", className="help", role="status"),
                ], className="card sample-card"),
            ], className="grid-2"),
        ], className="home-section samples"),
    ]


def render_snapshot(shortlist, result, live: bool):
    from app.results import TIERS
    n = len(shortlist or [])
    rows = [("Shortlist", f"{n} saved opportunit{'y' if n == 1 else 'ies'}" if n else "Nothing saved yet",
             "/discover", "Open Discover")]
    if result and result.get("rec"):
        rec = Recommendation.model_validate_json(result["rec"])
        label = TIERS.get(rec.priority.tier, (rec.priority.tier,))[0]
        origin = {"demo": "demo run", "saved": "saved live result"}.get(result.get("origin"), "live run")
        rows.append(("Current assessment", f"{label} ({origin})", "/results", "View results"))
    else:
        rows.append(("Current assessment", "None yet", "/profile", "Start one"))
    sources = "Grants.gov live" + (f" · web search and {MODEL} ready" if live else " · demo mode for AI stages")
    return html.Dl([
        *[html.Div([html.Dt(k), html.Dd([html.Span(v), dcc.Link(link_text, href=href, className="panel-link")])],
                   className="panel-row") for k, v, href, link_text in rows],
        html.Div([html.Dt("Sources"), html.Dd([html.Span(className="dot " + ("dot-on" if live else "dot-off"),
                                                        **{"aria-hidden": "true"}), sources])], className="panel-row"),
    ], className="panel-list")


def run_panel():
    live = live_available()
    return html.Section([
        html.Div([html.H2("Review and run", className="card-title"),
                  html.P("Check the list, then run.", className="card-subtitle")]),
        html.Div(id="run-readiness"),
        html.Div(id="form-errors", role="alert"),
        dcc.Checklist(id="opt-fresh", options=[{"label": "Force a fresh model call (bypass the Portkey cache)",
                                                "value": "fresh"}],
                      value=[], className="choice-group", labelClassName="choice", inputClassName="choice-input"),
        html.Div([
            html.Button(f"Run live assessment ({MODEL})", id="btn-run-live", className="btn btn-primary btn-lg",
                        n_clicks=0, disabled=not live),
            html.Button("Run demo (offline, scripted)", id="btn-run-demo", className="btn btn-secondary btn-lg", n_clicks=0),
        ], className="actions"),
        # Immediate click feedback; replaced by a clear error if the server never answers (see run_click_feedback).
        html.Div(id="run-status", role="status", **{"aria-live": "polite"}),
        html.P("Live: about 15–30 s via Portkey. Demo: offline, with real rules and scripted ratings." if live else
               "Live runs need PORTKEY_API_KEY in .env (see README). Demo runs work offline.", className="help"),
    ], className="card run-card")


NAV_GROUPS = [("Workspace", ["home", "discover"]), ("Assess", ["profile", "opportunity", "concept"]),
              ("Outcome", ["results", "run"])]
NAV_LABELS = {"home": "Overview", "discover": "Discover", "profile": "Nonprofit", "opportunity": "Opportunity",
              "concept": "Concept", "results": "Results", "run": "Run details"}


def _nav():
    path = {k: p for k, p, _ in PAGES}
    groups = []
    for title, keys in NAV_GROUPS:
        links = []
        for k in keys:
            num = STEP_KEYS.index(k) + 1 if k in STEP_KEYS else None
            links.append(html.Li(dcc.Link(([html.Span(str(num), className="nav-num", **{"aria-hidden": "true"})]
                                           if num else []) + [NAV_LABELS[k]], href=path[k], id=f"nav-{k}",
                                          className="nav-link")))
        groups.append(html.Li([html.Span(title, className="nav-group-label"), html.Ul(links, className="nav-items")],
                              className="nav-group"))
    return html.Nav(html.Ul(groups, className="nav-groups"), className="nav", **{"aria-label": "Main"})


def layout():
    live = live_available()
    pages = {
        "home": home_page(),
        "discover": discover_page(live, MODEL),
        "profile": _form_page("profile", "Only what you enter is used; nothing is assumed.", None,
                              ("/opportunity", "Next: Funding opportunity")),
        "opportunity": _form_page("opportunity", "Paste the funder's guidelines and add what you know.",
                                  ("/profile", "Back"), ("/concept", "Next: Proposal concept"),
                                  before=[html.Div(id="assess-origin-banner")]),
        "concept": _form_page("concept", "Describe the project you would propose.", ("/opportunity", "Back"), None,
                              extra=[run_panel()]),
        "results": [html.Div(id="progress-panel", **{"aria-live": "polite"}), html.Div(id="results-content")],
        "run": [html.Header([html.P("Outcome", className="eyebrow"), html.H1("Run details"),
                             html.P("Model, stages, latency, tokens, and cost for the result currently shown.",
                                    className="lead")], className="page-head"), html.Div(id="run-content")],
    }
    status = html.Span([html.Span(className="dot " + ("dot-on" if live else "dot-off"), **{"aria-hidden": "true"}),
                        f"{MODEL} live" if live else "Demo mode (no API key)"], className="header-status")
    return html.Div([
        dcc.Location(id="url", refresh="callback-nav"),  # navigate without reloading (keeps form state)
        dcc.Store(id="job-id"), dcc.Store(id="result-store"), dcc.Store(id="scroll-dummy"),
        dcc.Interval(id="poll", interval=700, disabled=True),
        dcc.Store(id="d-job"), dcc.Store(id="d-data"), dcc.Store(id="assess-origin"),
        dcc.Store(id="shortlist", storage_type="local"),     # shortlist persists in this browser only
        dcc.Interval(id="d-poll", interval=800, disabled=True),
        html.A("Skip to main content", href="#main", className="skip-link"),
        html.Header([
            html.Div([dcc.Link([html.Span("GrantFit", className="wordmark"),
                                html.Span("Funding strategy and proposal readiness", className="tagline")],
                               href="/", className="brand"), status], className="header-inner"),
            html.Div(_nav(), className="nav-bar"),
        ], className="site-header"),
        html.Main([html.Div(children, id=f"page-{key}", className="page") for key, children in pages.items()],
                  id="main", tabIndex="-1", className="main"),
        html.Footer(html.Div([html.Span("GrantFit", className="footer-mark"),
                              html.Span("AI Foundations final project · Never promises award outcomes; always confirm "
                                        "requirements with the funder.")], className="footer-inner"),
                    className="site-footer"),
    ], className="app")


app.layout = layout


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@app.callback([Output(f"page-{k}", "className") for k, _, _ in PAGES]
              + [Output(f"nav-{k}", "className") for k, _, _ in PAGES],
              Input("url", "pathname"))
def route(pathname):
    current = next((k for k, path, _ in PAGES if path == pathname), "home")
    return ([("page page-active" if k == current else "page") for k, _, _ in PAGES]
            + [("nav-link nav-active" if k == current else "nav-link") for k, _, _ in PAGES])


app.clientside_callback(
    "function(p){ var m = window.matchMedia('(prefers-reduced-motion: reduce)').matches;"
    " window.scrollTo({top: 0, behavior: m ? 'auto' : 'smooth'}); return null; }",
    Output("scroll-dummy", "data"), Input("url", "pathname"))


@app.callback([Output(fid, "value") for fid in FIELD_IDS]
              + [Output("sample-status", "children"), Output("url", "pathname", allow_duplicate=True)],
              Input("btn-load-sample", "n_clicks"), State("sample-select", "value"), prevent_initial_call=True)
def load_sample_cb(_n, key):
    if not key:
        return [no_update] * len(FIELD_IDS) + ["Choose a sample first.", no_update]
    values = case_to_form(load_sample(key))
    note = "Sample loaded. Review each step, then run the assessment."
    if values.get("_unmapped_criteria"):
        note += f" Some structured rules could not be shown in the form: {values['_unmapped_criteria']}."
    return [values[fid] for fid in FIELD_IDS] + [note, "/profile"]


@app.callback(Output("assess-origin", "data", allow_duplicate=True), Input("btn-load-sample", "n_clicks"),
              prevent_initial_call=True)
def clear_origin_on_sample(_n):
    return None


register_discover(app)


def _error_summary(errors: dict[str, str]):
    if not errors:
        return None
    items = []
    for fid, msg in errors.items():
        if fid == "_form":
            items.append(html.Li(msg))
            continue
        f = FIELD_BY_ID[fid]
        items.append(html.Li(dcc.Link(f"{SECTION_LABELS[f.section]}: {f.label}. {msg}", href=SECTION_PATHS[f.section])))
    return html.Div([html.P(html.Strong("Please fix these fields. Your other entries are kept."), className="m0"),
                     html.Ul(items)], className="error-summary")


@app.callback([Output("form-errors", "children"), Output("job-id", "data"),
               Output("poll", "disabled", allow_duplicate=True), Output("url", "pathname", allow_duplicate=True),
               Output("result-store", "data", allow_duplicate=True),
               Output("progress-panel", "children", allow_duplicate=True),
               Output("run-status", "children", allow_duplicate=True)]
              + [Output(fid, "className") for fid in FIELD_IDS],
              Input("btn-run-live", "n_clicks"), Input("btn-run-demo", "n_clicks"),
              State("opt-fresh", "value"), [State(fid, "value") for fid in FIELD_IDS], prevent_initial_call=True)
def run_cb(_live, _demo, fresh, *values):  # Dash passes each State positionally
    mode = "live" if ctx.triggered_id == "btn-run-live" else "demo"
    case, errors = form_to_case(dict(zip(FIELD_IDS, values)))
    classes = [base_class(FIELD_BY_ID[fid]) + (" is-invalid" if fid in errors else "") for fid in FIELD_IDS]
    if case is None:
        return [_error_summary(errors), no_update, True, no_update, no_update, no_update, None] + classes
    job_id = start_job(case, mode, fresh="fresh" in (fresh or []))
    return [None, job_id, False, "/results", {"pending": True}, _progress_view(job_id), None] + classes


# Runs in the browser, so it works even when the Python server is unreachable. Without it, a click
# on a page whose server has stopped (or never started, e.g. "Address already in use") does nothing
# visible at all. The server callback above clears this message as soon as it responds.
RUN_STATUS_STARTING = "Starting the assessment…"
RUN_STATUS_NO_SERVER = (
    "No response from the GrantFit server. It may have stopped or failed to start (for example "
    "\u201cAddress already in use\u201d). Check the terminal where you ran the app, restart it with "
    "\u201c.venv/bin/python -m app.app\u201d, then reload this page.")
app.clientside_callback(
    """
    function(nLive, nDemo) {
        var trig = (window.dash_clientside.callback_context || {}).triggered || [];
        if (!trig.length || !trig[0].value) { return window.dash_clientside.no_update; }
        var token = Date.now();
        window.__grantfitRunToken = token;
        window.dash_clientside.set_props('run-status', {className: 'run-status'});
        setTimeout(function () {
            var el = document.getElementById('run-status');
            if (window.__grantfitRunToken === token && el && el.textContent.indexOf('%(starting)s') === 0) {
                window.dash_clientside.set_props('run-status', {children: '%(noserver)s', className: 'run-status run-status-error'});
            }
        }, 6000);
        return '%(starting)s';
    }
    """ % {"starting": RUN_STATUS_STARTING, "noserver": RUN_STATUS_NO_SERVER},
    Output("run-status", "children", allow_duplicate=True),
    Input("btn-run-live", "n_clicks"), Input("btn-run-demo", "n_clicks"), prevent_initial_call=True)


def progress_card(title: str, subtitle: str, rows: list[tuple[str, str]], heading_tag=html.H1):
    """Shared loading card: real stage states, a bar from completed stages, and the bulldog cameo."""
    icon = {"done": "✓", "running": "", "pending": "", "failed": "!"}
    done = sum(1 for _, st in rows if st == "done")
    pct = round(100 * done / max(1, len(rows)))
    items = [html.Li([html.Span(icon[st], className=f"stage-icon stage-{st}", **{"aria-hidden": "true"}),
                      html.Span(label), html.Span(f" {st}", className="sr-only")], className=f"stage stage-row-{st}")
             for label, st in rows]
    cameo = html.Div(html.Img(src=app.get_asset_url(BULLDOG_FILE), alt="", width=84, className="bulldog"),
                     className="cameo", **{"aria-hidden": "true"}) if bulldog_available() else None
    return html.Section([
        html.Div([
            heading_tag(title, className="progress-title"),
            html.P(subtitle, className="help"),
            html.Div(html.Div(className="progress-fill", style={"width": f"{pct}%"}), className="progress-track",
                     role="progressbar", **{"aria-valuenow": str(pct), "aria-valuemin": "0", "aria-valuemax": "100",
                                            "aria-label": f"{done} of {len(rows)} steps complete"}),
            html.Ol(items, className="stages"),
        ], className="progress-main"),
        cameo,
    ], className="card progress-card", **{"aria-busy": "true"})


def error_card(title: str, message: str, help_text: str, action):
    return html.Section([
        html.Div([html.Span("!", className="state-icon state-icon-bad", **{"aria-hidden": "true"}),
                  html.H2(title, className="card-title")], className="state-head"),
        html.P(message, className="error-text"), html.P(help_text, className="help"), action,
    ], className="card card-error", role="alert")


def empty_state(title: str, text: str, actions: list, tag=html.H2):
    return html.Div([html.Span("○", className="state-icon", **{"aria-hidden": "true"}), tag(title, className="empty-title"),
                     html.P(text, className="lead"), html.Div(actions, className="actions")], className="empty-state")


def _progress_view(job_id):
    job = get_job(job_id)
    if job is None:
        return None
    if job.status == "error":
        return error_card("Assessment could not run", job.error,
                          "Your form entries are preserved. Check the API key and network (README, Troubleshooting), "
                          "or try a demo run.", dcc.Link("Back to the form", href="/concept", className="btn btn-secondary"))
    rows = []
    for stage, label, state in progress(job):
        if stage in LLM_STAGES:
            label += f" ({MODEL})" if job.mode == "live" else " (scripted demo)"
        rows.append((label, state))
    title = "Running live assessment" if job.mode == "live" else "Running demo assessment"
    return progress_card(title, "Each step below updates as the pipeline actually completes it.", rows)


@app.callback(Output("progress-panel", "children", allow_duplicate=True),
              Output("result-store", "data", allow_duplicate=True),
              Output("poll", "disabled", allow_duplicate=True),
              Input("poll", "n_intervals"), State("job-id", "data"), prevent_initial_call=True)
def poll_cb(_n, job_id):
    job = get_job(job_id)
    if job is None:
        return None, no_update, True
    if job.status == "running":
        return _progress_view(job_id), no_update, False
    if job.status == "error":
        return _progress_view(job_id), {"failed": True}, True
    return None, {"origin": job.mode, "rec": job.result.model_dump_json()}, True


@app.callback(Output("result-store", "data", allow_duplicate=True), Output("url", "pathname", allow_duplicate=True),
              Output("progress-panel", "children", allow_duplicate=True), Output("poll", "disabled", allow_duplicate=True),
              Output("saved-status", "children"),
              Input("btn-view-saved", "n_clicks"), State("saved-select", "value"), prevent_initial_call=True)
def view_saved_cb(_n, value):
    if not value:
        return no_update, no_update, no_update, no_update, "Choose a saved result first."
    rec, run = load_saved_result(value)
    return {"origin": "saved", "saved_run": run, "rec": rec.model_dump_json()}, "/results", None, True, None


@app.callback(Output("results-content", "children"), Output("run-content", "children"), Input("result-store", "data"))
def render_cb(data):
    if not data:
        none = empty_state("No results yet", "Discover an opportunity or complete the three assessment steps, then run "
                           "an assessment. You can also explore a fictional sample from the overview.",
                           [dcc.Link("Discover funding", href="/discover", className="btn btn-primary"),
                            dcc.Link("Start an assessment", href="/profile", className="btn btn-secondary")], tag=html.H1)
        return none, empty_state("No run yet", "Run details appear after an assessment.",
                                 [dcc.Link("Go to results", href="/results", className="btn btn-secondary")])
    if "rec" not in data:   # a run is in progress or just failed; the progress panel explains it
        return None, html.Div(html.P("Run details appear when the current assessment finishes.", className="lead"),
                              className="empty-state")
    rec = Recommendation.model_validate_json(data["rec"])
    origin, saved = data.get("origin", "live"), data.get("saved_run")
    return (html.Div(render_results(rec, origin, saved), className="results fade-in"),
            html.Div(render_run_details(rec, origin, saved), className="fade-in"))


@app.callback(Output("home-snapshot", "children"), Input("shortlist", "data"), Input("result-store", "data"))
def snapshot_cb(shortlist, result):
    return render_snapshot(shortlist, result, live_available())


@app.callback([Output(f"stepper-{k}", "children") for k in STEP_KEYS] + [Output("run-readiness", "children")],
              [Input(fid, "value") for fid in FIELD_IDS] + [Input("url", "pathname")])
def stepper_cb(*args):
    values = dict(zip(FIELD_IDS, args[:-1]))
    comp = completion(values)
    steppers = [render_stepper(k, comp) for k in STEP_KEYS]
    checks = []
    for k in STEP_KEYS:
        c = comp[k]
        ok = c["required_ok"]
        checks.append(html.Li([html.Span("✓" if ok else "!", className="check-mark " + ("check-ok" if ok else "check-miss"),
                                         **{"aria-hidden": "true"}),
                               dcc.Link(SECTION_LABELS[k], href=SECTION_PATHS[k]),
                               html.Span(f" {c['answered']} of {c['total']} answered"
                                         + ("" if ok else ", required field missing"), className="meta")],
                              className="check"))
    has_text = bool((values.get("o-source_text") or "").strip())
    checks.append(html.Li([html.Span("✓" if has_text else "–", className="check-mark " + ("check-ok" if has_text else "check-optional"),
                                     **{"aria-hidden": "true"}),
                           html.Span("Funder guidelines pasted or retrieved"),
                           html.Span(" (optional, improves verification)" if not has_text else "", className="meta")],
                          className="check"))
    return steppers + [html.Ul(checks, className="checklist", **{"aria-label": "Readiness"})]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="GrantFit web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8050)))
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    if not bulldog_available():
        print(f"[GrantFit] Note: {BULLDOG_FILE} not found in app/assets; the loading cameo is skipped.")
    if not port_available(args.host, args.port):
        raise SystemExit(
            f"[GrantFit] Port {args.port} is already in use, so GrantFit did NOT start.\n"
            f"  - If another GrantFit is already running, open http://{args.host}:{args.port} (and close duplicate tabs).\n"
            f"  - Otherwise start on another port:  .venv/bin/python -m app.app --port {args.port + 10}\n"
            "  Pages from a stopped server cannot run assessments; reload after starting the app.")
    print(f"[GrantFit] Open http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


def port_available(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    main()
