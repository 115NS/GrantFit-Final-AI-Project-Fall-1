"""Discover Funding page: live search, opportunity cards, shortlist, hand-off to assessment.

Display only. Search, retrieval, evidence, and de-duplication live in grantfit.discovery; this
module renders their output and never adds details of its own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from app.forms import FIELD_BY_ID, OPPORTUNITY_FIELD_IDS, base_class, opportunity_to_form
from app.jobs import discovery_progress, get_discovery_job, start_discovery_job
from grantfit.discovery.evidence import UNREAD_NOTE
from grantfit.discovery.models import DiscoveredOpportunity, DiscoveryQuery, DiscoveryResult
from grantfit.discovery.service import to_funding_opportunity

SHORTLIST_LIMIT = 30
SOURCE_LABELS = {"grants_gov": "Live · Grants.gov", "web_search": "Live · Web search", "user_url": "Live · Added by URL"}
STATUS_LABELS = {"listing_field": "Official listing field", "quoted_from_page": "Quoted from the page",
                 "unknown": "No source evidence"}
DEADLINE_OPTIONS = [{"label": "Any open deadline", "value": "any"}, {"label": "At least 30 days away", "value": "30"},
                    {"label": "At least 60 days away", "value": "60"}, {"label": "At least 90 days away", "value": "90"}]


def _choices(cid, options, value, cls="choice-group"):
    return dcc.RadioItems(id=cid, options=options, value=value, className=cls, labelClassName="choice",
                          inputClassName="choice-input")


def _field(label, control, help_text=None, wide=False, fieldset=False):
    lab = [label] + ([html.Span(help_text, className="help help-in-label")] if help_text else [])
    head = html.Legend(lab, className="label") if fieldset else html.Label(lab, htmlFor=getattr(control, "id", None),
                                                                            className="label")
    return (html.Fieldset if fieldset else html.Div)([head, control], className="field" + (" field-wide" if wide else ""))


def discover_page(live: bool, model: str):
    sources = [{"label": "Grants.gov: official federal listings (fast, free)", "value": "grants_gov"},
               {"label": f"Web search: foundation and corporate programs ({model}, ~30–60 s, uses many tokens)"
                         + ("" if live else ". Needs an API key"),
                "value": "web_search", "disabled": not live}]
    refine = html.Details([
        html.Summary([html.Span("Refine your search", className="summary-title"),
                      html.Span("Legal status, award size, deadline, geography, mission", className="summary-hint")]),
        html.Div([
            _field("Your legal status", _choices("d-legal", [
                {"label": "501(c)(3)", "value": "501c3"}, {"label": "Fiscally sponsored", "value": "fiscally_sponsored"},
                {"label": "Other", "value": "other"}, {"label": "Not sure", "value": "unknown"}], "501c3"),
                fieldset=True, wide=True),
            _field("Smallest useful award (USD)", dcc.Input(id="d-amount-min", type="number", min=0, className="input")),
            _field("Largest award you'd seek (USD)", dcc.Input(id="d-amount-max", type="number", min=0, className="input")),
            _field("Deadline", _choices("d-deadline", DEADLINE_OPTIONS, "any"), fieldset=True, wide=True),
            _field("", dcc.Checklist(id="d-unknown-deadline", options=[{"label": "Include opportunities without a stated "
                                                                        "deadline", "value": "yes"}], value=["yes"],
                                     className="choice-group", labelClassName="choice", inputClassName="choice-input"),
                   wide=True),
            _field("Geography you serve", dcc.Input(id="d-geo", type="text", className="input",
                                                    placeholder="e.g. New Haven, CT")),
            _field("Funder location or focus (web search)", dcc.Input(id="d-funder-geo", type="text", className="input",
                                                                      placeholder="e.g. Connecticut")),
            _field("Mission (optional, used for web search)", dcc.Textarea(id="d-mission", className="textarea", rows=2),
                   wide=True),
        ], className="field-grid"),
    ], className="refine", open=True)

    search_card = html.Section([
        html.Div([html.H2("Search live sources", className="card-title"),
                  html.P("Anything a source doesn't support is marked Unknown.",
                         className="card-subtitle")], className="card-head"),
        html.Label("Cause area or keywords (required)", htmlFor="d-keywords", className="label"),
        html.Div([dcc.Input(id="d-keywords", type="text", className="input input-lg",
                            placeholder="e.g. early literacy (short keywords work best)"),
                  html.Button("Search live sources", id="d-search", className="btn btn-primary btn-lg", n_clicks=0)],
                 className="search-bar"),
        html.Div([html.Button("Fill from my nonprofit profile", id="d-use-profile", className="btn btn-ghost btn-sm",
                              n_clicks=0)], className="search-sub"),
        html.Div(id="d-errors", role="alert"),
        html.Fieldset([html.Legend("Sources", className="label"),
                       dcc.Checklist(id="d-sources", options=sources, value=["grants_gov"],
                                     className="choice-group source-options", labelClassName="choice",
                                     inputClassName="choice-input")], className="field sources-field"),
        refine,
    ], className="card search-card")

    url_card = html.Section([
        html.H2("Have a specific funder page?", className="card-title"),
        html.P("Paste an official guidelines page.", className="card-subtitle"),
        html.Label("Funder page web address", htmlFor="d-url", className="label"),
        dcc.Input(id="d-url", type="url", className="input", placeholder="https://www.examplefoundation.org/grants"),
        html.Button("Retrieve page", id="d-add-url", className="btn btn-secondary", n_clicks=0),
        html.Details([
            html.Summary("Limitations"),
            html.Ul([html.Li("Grants.gov covers federal listings only; its keyword search is broad, so weak "
                             "matches are listed last."),
                     html.Li("Web search uses only pages the search cites; GrantFit reads each page itself."),
                     html.Li("Pages are read sentence by sentence; tables, PDFs, and linked pages are not "
                             "interpreted."),
                     html.Li("Only public http(s) pages are retrieved; text aimed at AI is flagged and ignored."),
                     html.Li("Details are as retrieved; confirm them at the source.")], className="mini-list"),
        ], className="limitations"),
    ], className="card url-card")

    return [
        html.Header([html.P("Workspace · Discover", className="eyebrow"), html.H1("Discover funding"),
                     html.P("Find live opportunities, shortlist the promising ones, and send any to an "
                            "assessment.", className="lead")],
                    className="page-head"),
        html.Div([search_card, url_card], className="discover-forms"),
        html.Div(id="d-progress", **{"aria-live": "polite"}),
        html.Div(id="d-results"),
        html.Section([html.Div([html.H2("Your shortlist", className="section-title"),
                                html.P("Saved in this browser; re-check sources before relying on saved details.",
                                       className="help")],
                               className="section-head"),
                       html.Div(id="d-shortlist")], className="shortlist", id="shortlist-section"),
    ]


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _eligibility_mark(ev: dict) -> tuple[str, str, str]:
    """(glyph, tone, screen-reader text) from the backend's eligibility evidence; display only."""
    status, value = ev.get("status", "unknown"), (ev.get("value") or "")
    if status == "listing_field":
        return ("✕", "bad", "Not listed as eligible") if "not listed" in value else ("✓", "good", "Listed as eligible")
    if status == "quoted_from_page":
        return ("i", "info", "Stated on the page")
    return ("?", "muted", "Unknown")


def _evidence_row(label: str, ev: dict):
    status = ev.get("status", "unknown")
    tone = {"listing_field": "good", "quoted_from_page": "info", "unknown": "muted"}[status]
    value = ev.get("value") if status != "unknown" else "Unknown"
    head = [html.Dt(label)]
    mark = None
    if label == "Eligibility":
        glyph, mtone, sr = _eligibility_mark(ev)
        mark = html.Span([html.Span(glyph, **{"aria-hidden": "true"}), html.Span(sr, className="sr-only")],
                         className=f"elig-mark elig-{mtone}")
    return html.Div(head + [html.Dd([
        html.Div([mark, html.Span(value, className="fact-value" + (" fact-unknown" if status == "unknown" else ""))],
                 className="fact-line"),
        html.Span(STATUS_LABELS[status], className=f"badge badge-{tone} fact-badge"),
        # Listing fields: the badge already states provenance. Unknown / quoted notes explain why, so keep them;
        # the tables/PDFs caveat is stated once in Limitations rather than on every fact.
        html.P(ev["note"].replace(UNREAD_NOTE, "").strip(), className="fact-note")
        if ev.get("note") and status != "listing_field" else None,
        html.Details([html.Summary("Show source text"), html.Blockquote(ev["quote"], className="quote")],
                     className="fact-quote") if ev.get("quote") else None,
    ])], className=f"fact fact-{status}")


def render_card(d: dict, *, saved: bool = False, saved_at: str | None = None, in_shortlist: bool = False):
    key = d["key"]
    source_badge = html.Span("Saved" if in_shortlist else SOURCE_LABELS.get(d["source"], d["source"]),
                             className="badge " + ("badge-muted" if in_shortlist else "badge-live"))
    level = d.get("match", "none")
    match = html.Span({"full": "Keyword match", "partial": "Partial match", "none": "Weak match"}[level],
                      className="badge " + {"full": "badge-info", "partial": "badge-muted", "none": "badge-warn"}[level])
    checked = datetime.fromisoformat(d["date_checked"]) if isinstance(d["date_checked"], str) else d["date_checked"]
    warnings = list(d.get("safety_warnings") or [])
    if d.get("retrieval") == "failed":
        warnings.insert(0, d.get("retrieval_note") or "The source page could not be retrieved.")
    meta = [f"Checked {checked:%Y-%m-%d %H:%M} UTC"]
    if d.get("source_updated"):
        meta.append(f"source updated {d['source_updated']}")
    if saved_at:
        meta.append(f"saved {saved_at}")
    if d.get("also_found_via"):
        meta.append("also found via " + ", ".join(SOURCE_LABELS.get(s, s) for s in d["also_found_via"]))
    prefix = "short" if in_shortlist else "disc"
    actions = [html.Button("Assess this opportunity", id={"type": f"{prefix}-assess", "key": key},
                           className="btn btn-primary", n_clicks=0,
                           disabled=not d.get("source_text"),
                           title=None if d.get("source_text") else "No retrieved text to assess")]
    if in_shortlist:
        actions.append(html.Button("Remove", id={"type": "short-remove", "key": key}, className="btn btn-ghost",
                                   n_clicks=0))
    else:
        actions.append(html.Button("Saved to shortlist ✓" if saved else "Save to shortlist",
                                   id={"type": "disc-save", "key": key}, className="btn btn-secondary",
                                   n_clicks=0, disabled=saved))
    return html.Article([
        html.Div([
            html.Div([source_badge, match], className="opp-badges"),
            html.H3(d["title"], className="opp-title"),
            html.P(d["funder"], className="opp-funder"),
            html.P([html.Span("Why it may fit", className="opp-label"), d["relevance"]], className="opp-relevance"),
            html.Ul([html.Li(w) for w in warnings], className="opp-warnings") if warnings else None,
            html.Div([html.A(["Original source ", html.Span("(opens in new tab)", className="sr-only")],
                             href=d["url"], target="_blank", rel="noopener noreferrer", className="source-link"),
                      html.Span(" · ".join(meta), className="meta")], className="opp-provenance"),
        ], className="opp-main"),
        html.Dl([_evidence_row("Deadline", d["deadline"]), _evidence_row("Funding", d["funding"]),
                 _evidence_row("Eligibility", d["eligibility"]), _evidence_row("Geography", d["geography"])],
                className="facts"),
        html.Div(actions, className="opp-actions"),
    ], className="opp-card" + (" opp-card-warn" if warnings else ""), **{"aria-label": d["title"]})


def render_results(data: dict | None, shortlist: list[dict]):
    if not data:
        return None
    if data.get("error"):
        return html.Section([
            html.Div([html.Span("!", className="state-icon state-icon-bad", **{"aria-hidden": "true"}),
                      html.H2("Search could not run", className="card-title")], className="state-head"),
            html.P(data["error"], className="error-text"),
            html.P("Live search needs internet access. Your inputs are kept; you can still paste guidelines on "
                   "the Assess steps.", className="help")],
            className="card card-error", role="alert")
    saved_keys = {s["item"]["key"] for s in shortlist or []}
    items = data.get("results") or []
    strong = sum(1 for d in items if d.get("match") == "full")
    weak = sum(1 for d in items if d.get("match", "none") == "none")
    sources = sorted({SOURCE_LABELS.get(d["source"], d["source"]).replace("Live · ", "") for d in items})
    chips = [html.Span(f"{strong} keyword match{'es' if strong != 1 else ''}", className="chip chip-info")]
    if weak:
        chips.append(html.Span(f"{weak} weak", className="chip chip-warn"))
    if sources:
        chips.append(html.Span("Sources: " + ", ".join(sources), className="chip"))
    chips.append(html.Span(f"Retrieved {data.get('checked', '')}", className="chip"))
    notes = [html.Li(n) for n in data.get("notes") or []]
    errors = [html.Li(e) for e in data.get("errors") or []]
    usage = data.get("web_search_usage")
    if usage:
        notes.append(html.Li(f"Web search used {usage.get('searches')} searches, "
                             f"{usage.get('input_tokens') or 0:,} input / {usage.get('output_tokens') or 0:,} output "
                             f"tokens, {usage.get('latency_s')} s on {usage.get('model_served') or usage.get('model')} "
                             "(provider-reported; cost unpriced)."))
    body = [html.Div([html.Div([html.H2(f"{len(items)} live result{'s' if len(items) != 1 else ''}",
                                        className="section-title")]),
                      html.Div(chips, className="chips")], className="results-bar")]
    if errors:
        body.append(html.Div([html.Strong("Some sources had problems:"), html.Ul(errors)], className="note note-warn"))
    if notes:
        body.append(html.Ul(notes, className="result-notes"))
    if items:
        body.append(html.Div([render_card(d, saved=d["key"] in saved_keys) for d in items], className="opp-list"))
    else:
        body.append(html.Div([html.Span("○", className="state-icon", **{"aria-hidden": "true"}),
                              html.H3("No opportunities found", className="empty-title"),
                              html.P("Nothing matched these filters. Try broader keywords, include unknown deadlines, "
                                     "or add web search.")], className="empty-state"))
    return html.Section(body, className="discover-results fade-in")


def render_shortlist(shortlist: list[dict]):
    if not shortlist:
        return html.Div([html.Span("☆", className="state-icon", **{"aria-hidden": "true"}),
                         html.P("No saved opportunities yet. Use “Save to shortlist” on any result.",
                                className="empty-note")], className="empty-state compact")
    return html.Div([render_card(s["item"], saved_at=s.get("saved_at"), in_shortlist=True) for s in shortlist],
                    className="opp-list")


def _progress(job):
    from app.app import progress_card  # late import avoids a cycle
    rows = [(label, state) for _, label, state in discovery_progress(job)]
    return progress_card("Searching live sources", "Each step updates as the search actually completes it.", rows,
                         heading_tag=html.H2)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def _query(keywords, mission, geo, fgeo, legal, amin, amax, deadline, unknown, sources) -> DiscoveryQuery:
    return DiscoveryQuery(
        keywords=(keywords or "").strip(), mission=(mission or "").strip(), geography_served=(geo or "").strip(),
        funder_geography=(fgeo or "").strip(), legal_status=legal or "unknown",
        amount_min=amin or None, amount_max=amax or None,
        deadline_min_days=None if deadline in (None, "any") else int(deadline),
        include_unknown_deadlines="yes" in (unknown or []),
        use_grants_gov="grants_gov" in (sources or []), use_web_search="web_search" in (sources or []))


def _result_payload(job) -> dict:
    r = job.result
    if isinstance(r, DiscoveryResult):
        return {"results": [o.model_dump(mode="json") for o in r.results], "notes": r.notes, "errors": r.errors,
                "web_search_usage": r.web_search_usage, "checked": f"{r.started_at:%Y-%m-%d %H:%M} UTC"}
    if isinstance(r, DiscoveredOpportunity):   # add-by-URL
        return {"results": [r.model_dump(mode="json")], "notes": ["Added by URL."], "errors": [],
                "checked": f"{r.date_checked:%Y-%m-%d %H:%M} UTC"}
    return {"error": job.error or "Search failed."}


def register_callbacks(app):
    search_states = [State(i, "value") for i in ("d-keywords", "d-mission", "d-geo", "d-funder-geo", "d-legal",
                                                 "d-amount-min", "d-amount-max", "d-deadline", "d-unknown-deadline",
                                                 "d-sources")]

    @app.callback(Output("d-keywords", "value"), Output("d-mission", "value"), Output("d-geo", "value"),
                  Output("d-legal", "value"), Input("d-use-profile", "n_clicks"),
                  State("p-program_areas", "value"), State("p-mission", "value"),
                  State("p-geography_served", "value"), State("p-legal_status", "value"), prevent_initial_call=True)
    def use_profile(_n, programs, mission, geo, legal):
        first_area = next((ln.strip() for ln in (programs or "").splitlines() if ln.strip()), "")
        geo_lines = [ln.strip() for ln in (geo or "").splitlines() if ln.strip()]
        return (first_area or no_update, mission or no_update, ", ".join(geo_lines) or no_update,
                legal if legal in ("501c3", "fiscally_sponsored", "other") else "unknown")

    @app.callback(Output("d-errors", "children"), Output("d-job", "data"), Output("d-poll", "disabled"),
                  Output("d-progress", "children", allow_duplicate=True),
                  Input("d-search", "n_clicks"), Input("d-add-url", "n_clicks"), State("d-url", "value"),
                  *search_states, prevent_initial_call=True)
    def start_search(_s, _u, url, *values):
        q = _query(*values)
        if ctx.triggered_id == "d-add-url":
            if not (url or "").strip().lower().startswith(("http://", "https://")):
                return html.P("Enter a full web address starting with https://", className="error-summary"), \
                    no_update, True, no_update
            job_id = start_discovery_job(q, url=url.strip())
        else:
            if not q.keywords:
                return html.P("Enter a cause area or keywords to search.", className="error-summary"), \
                    no_update, True, no_update
            if not (q.use_grants_gov or q.use_web_search):
                return html.P("Choose at least one source.", className="error-summary"), no_update, True, no_update
            job_id = start_discovery_job(q)
        return None, job_id, False, _progress(get_discovery_job(job_id))

    @app.callback(Output("d-progress", "children", allow_duplicate=True), Output("d-data", "data"),
                  Output("d-poll", "disabled", allow_duplicate=True),
                  Input("d-poll", "n_intervals"), State("d-job", "data"), State("d-data", "data"),
                  prevent_initial_call=True)
    def poll(_n, job_id, current):
        job = get_discovery_job(job_id)
        if job is None:
            return None, no_update, True
        if job.status == "running":
            return _progress(job), no_update, False
        payload = _result_payload(job)
        if job.status == "done" and isinstance(job.result, DiscoveredOpportunity) and current and current.get("results"):
            # Add-by-URL appends to the current result list (de-duplicated by key).
            keys = {r["key"] for r in current["results"]}
            new = [r for r in payload["results"] if r["key"] not in keys]
            payload = {**current, "results": new + current["results"],
                       "notes": (current.get("notes") or []) + (["Added by URL."] if new else
                                                                  ["That page is already in the results."])}
        return None, payload, True

    @app.callback(Output("d-results", "children"), Output("d-shortlist", "children"),
                  Input("d-data", "data"), Input("shortlist", "data"))
    def render(data, shortlist):
        return render_results(data, shortlist or []), render_shortlist(shortlist or [])

    @app.callback(Output("shortlist", "data"),
                  Input({"type": "disc-save", "key": ALL}, "n_clicks"),
                  Input({"type": "short-remove", "key": ALL}, "n_clicks"),
                  State("d-data", "data"), State("shortlist", "data"), prevent_initial_call=True)
    def shortlist_update(_save, _remove, data, shortlist):
        trig = ctx.triggered_id
        if not trig or not (ctx.triggered and ctx.triggered[0].get("value")):
            return no_update
        shortlist = list(shortlist or [])
        if trig["type"] == "short-remove":
            return [s for s in shortlist if s["item"]["key"] != trig["key"]]
        if any(s["item"]["key"] == trig["key"] for s in shortlist):
            return no_update
        item = next((r for r in (data or {}).get("results", []) if r["key"] == trig["key"]), None)
        if item is None:
            return no_update
        entry = {"item": item, "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        return ([entry] + shortlist)[:SHORTLIST_LIMIT]

    @app.callback([Output(fid, "value", allow_duplicate=True) for fid in OPPORTUNITY_FIELD_IDS]
                  + [Output(fid, "className", allow_duplicate=True) for fid in OPPORTUNITY_FIELD_IDS]
                  + [Output("assess-origin", "data"), Output("url", "pathname", allow_duplicate=True)],
                  Input({"type": "disc-assess", "key": ALL}, "n_clicks"),
                  Input({"type": "short-assess", "key": ALL}, "n_clicks"),
                  State("d-data", "data"), State("shortlist", "data"), prevent_initial_call=True)
    def assess(_a, _b, data, shortlist):
        trig = ctx.triggered_id
        if not trig or not (ctx.triggered and ctx.triggered[0].get("value")):
            return [no_update] * (2 * len(OPPORTUNITY_FIELD_IDS) + 2)
        from_shortlist = trig["type"] == "short-assess"
        pool = [s["item"] for s in shortlist or []] if from_shortlist else (data or {}).get("results", [])
        item = next((r for r in pool if r["key"] == trig["key"]), None)
        if item is None:
            return [no_update] * (2 * len(OPPORTUNITY_FIELD_IDS) + 2)
        opp = to_funding_opportunity(DiscoveredOpportunity.model_validate(item))
        values = opportunity_to_form(opp)
        origin = {"title": item["title"], "funder": item["funder"], "url": item["url"], "source": item["source"],
                  "saved": from_shortlist, "checked": item["date_checked"]}
        return ([values[fid] for fid in OPPORTUNITY_FIELD_IDS]
                + [base_class(FIELD_BY_ID[fid]) for fid in OPPORTUNITY_FIELD_IDS]
                + [origin, "/opportunity"])

    @app.callback(Output("assess-origin-banner", "children"), Input("assess-origin", "data"))
    def origin_banner(origin):
        if not origin:
            return None
        label = "saved shortlist item" if origin.get("saved") else SOURCE_LABELS.get(origin["source"], "live result")
        return html.Div([
            html.Strong("Loaded from Discover funding: "),
            f"{origin['title']} ({origin['funder']}), {label}. ",
            "The retrieved source text is in “Pasted funding guidelines” and will be quote-verified like any "
            "pasted text. Review the fields, then complete the concept and run. ",
            html.A("Original source", href=origin["url"], target="_blank", rel="noopener noreferrer"),
        ], className="banner banner-info", role="status")
