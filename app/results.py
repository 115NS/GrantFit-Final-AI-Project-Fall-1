"""Render a backend Recommendation (and its trace) as Dash components.

Display only: every value shown comes from the Recommendation or its JSONL trace. No scores,
tiers, or eligibility decisions are computed here.
"""

from __future__ import annotations

import json
from pathlib import Path

from dash import dcc, html

from grantfit.config import PROJECT_ROOT, load_yaml
from grantfit.models import Recommendation
from grantfit.roadmap import DIMENSION_LABELS, build_roadmap

TIERS = {
    "PURSUE_NOW": ("Pursue now", "Eligible, a strong fit, and feasible before the deadline.", "good"),
    "STRENGTHEN_THEN_PURSUE": ("Strengthen, then pursue", "Eligible and a good fit, but the concept needs work before you apply.", "info"),
    "VERIFY_FIRST": ("Verify first", "Eligibility or how this funder accepts requests is not yet confirmed. Resolve that before writing.", "warn"),
    "NOT_ELIGIBLE": ("Not eligible", "A hard eligibility requirement is not met. No score can offset this.", "bad"),
    "CULTIVATE_RELATIONSHIP": ("Cultivate the relationship", "Relationship-based funding: build a connection rather than submitting an application.", "info"),
    "DEPRIORITIZE": ("Deprioritize", "A weak fit or not feasible with your time and capacity right now.", "muted"),
    "INCOMPLETE_ASSESSMENT": ("Assessment incomplete", "The AI assessment did not complete. Only the rule-based findings below are available.", "warn"),
}
TYPE_LABELS = {
    "open_competitive": "Open grant program", "invitation_only": "Invitation-only",
    "individual_philanthropy": "Individual donor", "government": "Government grant",
    "corporate": "Corporate giving", None: "Not confirmed",
}
STATUS_TONE = {"PASS": "good", "FAIL": "bad", "UNKNOWN": "warn", "NOT_APPLICABLE": "muted",
               "VERIFIED": "good", "UNVERIFIED": "warn"}
FLAG_TITLES = {
    "prompt_injection": "Possible prompt injection in pasted guidelines",
    "provider_content_filter": "Model provider refused the guideline text",
    "award_probability_claim": "Award-likelihood language removed",
    "prompt_or_secret_leak": "Leaked internal text removed",
    "unverified_requirement": "Requirement could not be verified",
    "contradictory_requirements": "Contradictory requirements",
    "ambiguous_requirement": "Ambiguous requirement",
    "funding_history_claim": "Unsupported funding-history statement",
    "ungrounded_number": "Figure not found in your inputs",
    "opportunity_type_conflict": "Opportunity type conflict",
}
STAGE_NAMES = {
    "guideline_safety_scan": "Guideline safety scan", "extract_guidelines": "Guideline extraction",
    "quote_verification": "Quote verification", "eligibility_check": "Eligibility rules",
    "effort_and_deadline": "Effort and deadline", "assess_fit_concept": "Fit and concept assessment",
    "output_safety_validation": "Output safety validation", "priority_decision": "Recommendation rules",
    "output_validation": "Consistency checks",
}


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------

def badge(text: str, tone: str = "muted"):
    return html.Span(text, className=f"badge badge-{tone}")


EVIDENCE_KINDS = {
    "rule": ("Rule-checked", "good"), "quote": ("Quote-verified", "info"), "model": ("Model judgment", "warn"),
    "estimate": ("Rule-based estimate", "muted"), "mixed": ("Rules + model judgment", "muted"),
    "entered": ("Your entries + verified sources", "muted"),
}


def card(title: str, children, extra_class: str = "", subtitle: str | None = None, evidence: str | None = None):
    head = [html.Div([html.H2(title, className="card-title"),
                      badge(*EVIDENCE_KINDS[evidence]) if evidence else None], className="card-title-row")]
    if subtitle:
        head.append(html.P(subtitle, className="card-subtitle"))
    return html.Section([html.Div(head, className="card-head"), html.Div(children, className="card-body")],
                        className=f"card {extra_class}".strip())


def empty(text: str):
    return html.P(text, className="empty-note")


def money(v) -> str:
    return "—" if v is None else f"${v:,.0f}"


def source_kind(rec: Recommendation) -> str:
    s = rec.llm_source
    if s.startswith("fake"):
        return "demo"
    if s.startswith("partial"):
        return "partial"
    if s == "unavailable":
        return "unavailable"
    return "live"


def source_banner(rec: Recommendation, origin: str, saved_run: str | None = None):
    kind = source_kind(rec)
    if kind == "demo":
        return html.Div([
            html.Strong("Demo result: scripted offline output, not an AI assessment. "),
            "Rules and safety checks ran for real; the ratings and their text are placeholders.",
        ], className="banner banner-demo", role="note")
    if kind == "unavailable":
        return html.Div([html.Strong("AI stages unavailable. "),
                         "The model could not be reached, so only rule-based findings are shown."],
                        className="banner banner-bad", role="alert")
    if kind == "partial":
        return html.Div([html.Strong("Partial live result. "),
                         f"These stages did not complete: {', '.join(rec.degraded_stages)}. "
                         "Results are incomplete; nothing was filled in by guesswork."],
                        className="banner banner-warn", role="alert")
    model = rec.llm_source.split("live:", 1)[-1]
    if origin == "saved":
        return html.Div([html.Strong("Saved live result. "),
                         f"Produced by {model} during evaluation run {saved_run} on "
                         f"{rec.created_at:%Y-%m-%d %H:%M} UTC. It was not re-run now."],
                        className="banner banner-info", role="note")
    return html.Div([html.Strong("Live result. "), f"AI analysis by {model} via Portkey."],
                    className="banner banner-live", role="note")


# ---------------------------------------------------------------------------
# Results page
# ---------------------------------------------------------------------------

TIER_GLYPH = {"good": "✓", "bad": "✕", "warn": "!", "info": "→", "muted": "–"}


def _score_tile(label: str, value: float | None, demo: bool, coverage: float | None = None, assessed: bool = True):
    if value is None:
        text = "Not enough information" if assessed and coverage is not None else "—"
        return html.Div([html.Dt(label), html.Dd(html.Span(text, className="tile-value tile-value-sm"))], className="tile")
    partial = coverage is not None and coverage < 0.999
    return html.Div([html.Dt(label), html.Dd([
        html.Span(f"{value:g} / 100", className="tile-value"),
        html.Span("(demo placeholder)", className="tile-sub tile-demo") if demo else None,
        html.Span(f"Based on {coverage:.0%} of dimensions' weight", className="tile-sub") if partial else None,
        html.Div(html.Div(className="bar-fill" + (" bar-demo" if demo else ""), style={"width": f"{max(0, min(100, value))}%"}),
                 className="bar", role="img", **{"aria-label": f"{value:g} out of 100"}),
    ])], className="tile")


def render_results(rec: Recommendation, origin: str = "live", saved_run: str | None = None) -> list:
    p = rec.priority
    label, meaning, tone = TIERS.get(p.tier, (p.tier, "", "muted"))
    elig = rec.eligibility.overall
    demo = source_kind(rec) == "demo"
    d = p.deadline
    deadline_text = {True: "Feasible", False: "Not feasible", None: "Unknown"}[d.feasible]
    tiles = [
        html.Div([html.Dt("Eligibility"), html.Dd(badge(elig.replace("_", " ").title(), STATUS_TONE.get(elig, "muted")))],
                 className="tile"),
        _score_tile("Funder fit", p.fit_score_0_100, demo, p.fit_coverage, rec.assessment is not None),
        _score_tile("Concept quality", p.concept_score_0_100, demo, p.concept_coverage, rec.assessment is not None),
        html.Div([html.Dt("Estimated effort"), html.Dd([html.Span(f"{p.effort.hours:g} hours", className="tile-value"),
                                                        html.Span(f"Deadline: {deadline_text}", className="tile-sub")])],
                 className="tile"),
        html.Div([html.Dt("Funding type"), html.Dd(html.Span(TYPE_LABELS.get(rec.opportunity_type, rec.opportunity_type),
                                                             className="tile-value tile-value-sm"))], className="tile"),
    ]
    hero = html.Section([
        html.Div([
            html.Span(TIER_GLYPH.get(tone, "–"), className=f"tier-glyph tier-glyph-{tone}", **{"aria-hidden": "true"}),
            html.Div([
                html.P("Recommendation", className="eyebrow"),
                html.H1(label, className=f"tier tier-{tone}", id="result-heading", tabIndex="-1"),
                html.P(meaning, className="tier-meaning"),
            ]),
        ], className="hero-head"),
        html.Ul([html.Li(r) for r in p.tier_reasons], className="reasons") if p.tier_reasons else None,
        html.Dl(tiles, className="tiles"),
    ], className=f"card result-hero hero-{tone}", **{"aria-labelledby": "result-heading"})

    toolbar = html.Div([
        html.P(f"Assessed {rec.created_at:%Y-%m-%d %H:%M} UTC · run {rec.run_id}", className="meta"),
        html.Div([dcc.Link("Run details", href="/run", className="btn btn-ghost btn-sm"),
                  dcc.Link("Discover more funding", href="/discover", className="btn btn-ghost btn-sm")],
                 className="toolbar-actions"),
    ], className="results-toolbar")

    out = [source_banner(rec, origin, saved_run), toolbar, hero]
    if rec.needs_human_review:
        out.append(review_callout(rec))
    out.append(advisories_panel(rec))
    main = [roadmap_card(rec), eligibility_card(rec), verification_card(rec), scores_card(rec)]
    aside = [opportunity_card(rec), risks_card(rec), evidence_card(rec), effort_card(rec)]
    out += [
        html.Div([html.Div([c for c in main if c is not None], className="results-main"),
                  html.Aside(aside, className="results-aside", **{"aria-label": "Opportunity context, risks, and effort"})],
                 className="results-layout"),
        how_assessed(rec),
        html.Footer([html.P(x) for x in rec.disclaimers[:1]], className="disclaimers"),
    ]
    return [c for c in out if c is not None]


def how_assessed(rec: Recommendation):
    """The single place the methodology is explained (thresholds read from config, not hard-coded)."""
    th = load_yaml("scoring")["thresholds"]
    items = [
        "Eligibility: fixed rules check each hard requirement. FAIL means not eligible regardless of scores; "
        "UNKNOWN means there is not enough evidence to decide.",
        "Requirements from pasted or retrieved guidelines count only when an exact quote is found in the source.",
        "Scores: gpt-6-luna rates each dimension 1–5; a rating r becomes (r−1)/4×100 and the total is a weighted "
        "mean (weights in config/scoring.yaml). Dimensions without enough information are excluded; below "
        f"{th.get('min_coverage', 0.6):.0%} coverage no score is given.",
        f"Recommendation: fixed rules in order: eligibility, deadline, fit (at least {th['fit_min']}), concept "
        f"(at least {th['concept_min']}), award range. Thresholds are design choices, not calibrated predictions.",
        "Next steps are derived by rules from these findings, with no extra model call.",
        *rec.disclaimers[1:],
    ]
    return html.Details([html.Summary("How this was assessed"), html.Ul([html.Li(i) for i in items])],
                        className="method")


def review_callout(rec: Recommendation):
    injection = [f for f in rec.safety_flags if f.kind in ("prompt_injection", "provider_content_filter")]
    children = [html.H2("Human review required", className="callout-title"),
                html.P("Resolve these before acting:"),
                html.Ul([html.Li(r) for r in rec.review_reasons])]
    if injection:
        children.insert(1, html.P([
            html.Strong("Possible manipulation detected in the pasted guidelines. "),
            "Flagged text was treated as data and could not change eligibility; check the funder's own site."],
            className="callout-alert"))
    return html.Section(children, className="callout callout-review", role="alert" if injection else "status")


def advisories_panel(rec: Recommendation):
    items = getattr(rec, "advisories", None) or []
    if not items:
        return None
    return html.Section([
        html.H2("Worth checking", className="advisory-title"),
        html.P("Informational; these don't block a decision.", className="help"),
        html.Ul([html.Li(a) for a in items]),
    ], className="advisory", role="note")


def rule_origin(criterion_id: str) -> str:
    if criterion_id.startswith("user-"):
        return "Your entry"
    if criterion_id.startswith("scan"):
        return "Guideline scan"
    if criterion_id.startswith("x"):
        return "Guideline extraction"
    if criterion_id.startswith("contradiction"):
        return "Contradiction check"
    if criterion_id == "extraction_unavailable":
        return "Extraction failed"
    return "Supplied rule"


def eligibility_card(rec: Recommendation):
    e = rec.eligibility
    rows = [html.Tr([
        html.Td([html.Span(c.kind.replace("_", " ").capitalize(), className="rule-name"),
                 html.Span(rule_origin(c.criterion_id), className="meta block")]),
        html.Td(badge(c.status, STATUS_TONE[c.status])),
        html.Td([html.Span(c.reason), html.Blockquote(c.source_quote, className="quote") if c.source_quote else None]),
    ]) for c in e.checks if c.hard]
    body = [html.P(n, className="note") for n in e.notes]
    if rows:
        body.append(html.Table([
            html.Thead(html.Tr([html.Th("Requirement", scope="col"), html.Th("Status", scope="col"),
                                html.Th("Why", scope="col")])),
            html.Tbody(rows)], className="table"))
    else:
        body.append(empty("No eligibility rules were supplied or verified."))
    return card("Eligibility checks", body, evidence="rule",
                subtitle="FAIL means not eligible; UNKNOWN means not enough evidence to decide.")


def opportunity_card(rec: Recommendation):
    o = rec.effective_opportunity
    if o is None:
        return card("Opportunity as assessed", empty("Not recorded for this run."))
    d = o.deadline
    deadline = "Rolling" if d == "rolling" else (d.isoformat() if d else "Not stated")
    rng = "Not stated" if o.award_min_usd is None and o.award_max_usd is None else \
        f"{money(o.award_min_usd)} to {money(o.award_max_usd)}"
    items = [("Funder", o.funder_name), ("Type", TYPE_LABELS.get(o.opportunity_type, o.opportunity_type)),
             ("Deadline", deadline), ("Award range", rng)]
    if o.source_url:
        items.append(("Source", html.A(o.source_url, href=o.source_url, target="_blank", rel="noopener noreferrer",
                                       className="source-link")))
    body = [html.Dl([html.Div([html.Dt(k), html.Dd(v)], className="kv") for k, v in items], className="kv-list")]
    if o.priorities:
        body += [html.H3("Funder priorities", className="mini-title"), html.Ul([html.Li(x) for x in o.priorities])]
    if o.restrictions:
        body += [html.H3("Restrictions to confirm", className="mini-title"),
                 html.Ul([html.Li(x) for x in o.restrictions])]
    return card("Opportunity as assessed", body, evidence="entered",
                subtitle="Your entries and verified sources.")


def verification_card(rec: Recommendation):
    x = rec.extraction
    if x is None:
        return None
    strength = {"hard": "Hard rule", "soft": "Preference", "unclear": "Unclear"}
    verified = [r for r in x.requirements if r.status == "VERIFIED"]
    unverified = [r for r in x.requirements if r.status == "UNVERIFIED"]

    def item(r):
        origin = "deterministic scan" if r.origin == "deterministic_scan" else "AI extraction"
        return html.Li([
            html.Div([badge(r.status, STATUS_TONE[r.status]), badge(strength[r.strength], "muted"),
                      html.Span(r.category.replace("_", " "), className="meta"),
                      html.Span(origin, className="meta")], className="req-meta"),
            html.P(r.summary, className="req-summary"),
            html.Blockquote(r.exact_quote, className="quote"),
            html.P(r.reason, className="req-reason") if r.status == "UNVERIFIED" else None,
        ], className="req")

    body = []
    if x.status == "degraded":
        body.append(html.P("Guideline extraction did not complete. Only the deterministic scan's findings "
                           "were used, and eligibility is treated as unconfirmed.", className="note note-warn"))
    body.append(html.H3(f"Verified against an exact quote ({len(verified)})", className="mini-title"))
    body.append(html.Ul([item(r) for r in verified], className="req-list") if verified
                else empty("No requirements could be verified."))
    if unverified:
        body.append(html.H3(f"Not verified, ignored for eligibility ({len(unverified)})", className="mini-title"))
        body.append(html.Ul([item(r) for r in unverified], className="req-list"))
    for title, entries in (("Contradictions", x.contradictions), ("Ambiguities", x.ambiguities),
                           ("Missing information", x.missing_information), ("Notes", x.field_notes)):
        if entries:
            body += [html.H3(title, className="mini-title"), html.Ul([html.Li(e) for e in entries])]
    return card("Guideline requirements and quotes", body, evidence="quote",
                subtitle="Used only when the quote appears word for word in the source.")


def _meter(score: int):
    return html.Div([html.Span(className="seg" + (" on" if i < score else "")) for i in range(5)],
                    className="meter", role="img", **{"aria-label": f"{score} out of 5"})


def scores_card(rec: Recommendation):
    a, p = rec.assessment, rec.priority
    if a is None:
        return card("Fit and concept scores", empty("The AI assessment did not complete, so no scores are available."))

    def rating(d):
        if getattr(d, "insufficient_information", False):
            return html.Td(badge("Not assessable", "muted"), className="score-cell")
        return html.Td([_meter(d.score), html.Span(f"{d.score}/5", className="score-num")], className="score-cell")

    def table(title, group, total):
        rows = [html.Tr([
            html.Th(DIMENSION_LABELS.get(name, name).capitalize(), scope="row"),
            rating(d),
            html.Td([html.Span(d.rationale),
                     html.Span(" · ".join(d.evidence_refs), className="refs") if d.evidence_refs else None]),
        ]) for name, d in group]
        total_text = "Not scored" if total is None else f"{total:g} / 100"
        return html.Div([
            html.H3([title, html.Span(total_text, className="score-total")], className="mini-title"),
            html.Table([html.Thead(html.Tr([html.Th("Dimension", scope="col"), html.Th("Rating", scope="col"),
                                            html.Th("Rationale", scope="col")])), html.Tbody(rows)],
                       className="table table-scores")])

    demo_note = html.P("Demo run: these ratings are scripted placeholders (all 3/5), not an AI judgment of "
                       "your proposal.", className="note note-warn") if source_kind(rec) == "demo" else None
    return card("Fit and concept scores", [
        demo_note,
        html.P(a.summary, className="lead"),
        table("Funder fit", a.fit, p.fit_score_0_100),
        table("Concept quality", a.concept, p.concept_score_0_100),
        html.Ul([html.Li(n) for n in p.score_notes], className="note") if p.score_notes else None,
        html.P(f"Model's self-reported confidence: {a.confidence}. Scores are not award predictions.", className="note"),
    ], subtitle="Model ratings, 1–5 per dimension. See How this was assessed.", evidence="model")


def effort_card(rec: Recommendation):
    e, d = rec.priority.effort, rec.priority.deadline
    rows = [html.Tr([html.Td(k.replace("_", " ")), html.Td(f"{v:g}" + ("×" if k == "type_multiplier" else " h"))])
            for k, v in e.breakdown.items()]
    tone = {True: "good", False: "bad", None: "warn"}[d.feasible]
    text = {True: "Feasible", False: "Not feasible", None: "Unknown"}[d.feasible]
    body = [html.P([html.Span(f"{e.hours:g}", className="big-number"), " staff hours estimated"]),
            html.Table(html.Tbody(rows), className="table table-compact"),
            html.P([badge(f"Deadline: {text}", tone), " ", d.reason], className="deadline")]
    if e.defaulted_components:
        body.append(html.P(f"Default hours were used for: {', '.join(e.defaulted_components)}.", className="note"))
    return card("Effort estimate", body, evidence="estimate", subtitle="Rule-based hours per component.")


def evidence_card(rec: Recommendation):
    body = []
    if rec.unsupported_claims:
        body += [html.H3("Claims not backed by your listed evidence", className="mini-title"),
                 html.Ul([html.Li([badge("Rule check" if u.source == "deterministic" else "Model judgment",
                                         "good" if u.source == "deterministic" else "warn"), " ",
                                   html.Q(u.claim), html.Span(f" ({u.reason})", className="meta")])
                          for u in rec.unsupported_claims])]
    if rec.assessment and rec.assessment.evidence_gaps:
        body += [html.H3("Missing evidence (model judgment)", className="mini-title"),
                 html.Ul([html.Li(g) for g in rec.assessment.evidence_gaps])]
    return card("Missing evidence", body or empty("No evidence gaps were identified."), evidence="mixed")


def risks_card(rec: Recommendation):
    items = []
    for f in rec.safety_flags:
        if f.kind == "unsupported_claim":
            continue  # shown under Missing evidence
        items.append(html.Li([html.Strong(FLAG_TITLES.get(f.kind, f.kind)), html.Span(f" ({f.source}) "),
                              html.Span(f.detail),
                              html.Blockquote(f.excerpt, className="quote") if f.excerpt else None]))
    for w in rec.validation_warnings:
        items.append(html.Li([html.Strong("Validation warning: "), w]))
    if rec.priority.deadline.feasible is False:
        items.append(html.Li([html.Strong("Deadline risk: "), rec.priority.deadline.reason]))
    return card("Risks and warnings", html.Ul(items, className="risk-list") if items
                else empty("No safety flags or warnings were raised."), evidence="mixed")


def roadmap_card(rec: Recommendation):
    steps = build_roadmap(rec)
    if not steps:
        return card("Prioritized next steps", empty("No further steps identified."), extra_class="card-roadmap")
    tone = {"Blocker": "bad", "Before applying": "warn", "Strengthen": "info", "Plan": "good"}
    return card("Prioritized next steps", html.Ol([
        html.Li([html.Span(str(i), className=f"road-num road-{tone[s.priority]}", **{"aria-hidden": "true"}),
                 html.Div([html.Div([badge(s.priority, tone[s.priority]), html.Span(s.source, className="meta")],
                                    className="req-meta"),
                           html.P(s.action, className="step-action"), html.P(s.why, className="step-why")])],
                className="road-step") for i, s in enumerate(steps, 1)], className="roadmap"),
        extra_class="card-roadmap", evidence="mixed",
        subtitle="Ordered by rules from this assessment's findings.")


# ---------------------------------------------------------------------------
# Run details
# ---------------------------------------------------------------------------

def read_trace(rec: Recommendation) -> list[dict]:
    if not rec.telemetry:
        return []
    path = Path(rec.telemetry.trace_path)
    try:
        if PROJECT_ROOT.resolve() not in path.resolve().parents or not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def _cost_label(source: str | None, cost) -> str:
    return {"computed_from_config": f"${cost:.6f}" if cost is not None else "Unpriced",
            "unpriced": "Unpriced", "gateway_cache_hit": "Cache replay (no new spend)",
            "not_applicable": "Not applicable"}.get(source or "", "Unpriced")


def render_run_details(rec: Recommendation, origin: str = "live", saved_run: str | None = None) -> list:
    t = rec.telemetry
    spans = read_trace(rec)
    llm = [s for s in spans if s.get("kind") == "llm"]
    requested = sorted({s["model_requested"] for s in llm if s.get("model_requested")}) or ["—"]
    served = sorted({s["model_served"] for s in llm if s.get("model_served")}) or ["—"]
    fmt = lambda v: "—" if v is None else f"{v:,}"  # noqa: E731
    if t is None:
        cost = "Unpriced"
    elif t.cost_source == "not_applicable":
        cost = "Not applicable (no live model calls)" if source_kind(rec) == "demo" else "Not applicable"
    else:
        cost = _cost_label(t.cost_source, t.cost_usd)
    summary = [
        ("Result source", {"demo": "Demo (scripted offline output)", "live": "Live model",
                           "partial": "Live model, partially degraded",
                           "unavailable": "Model unavailable"}[source_kind(rec)]
         + (f", saved from run {saved_run}" if origin == "saved" else "")),
        ("Model requested", ", ".join(requested)), ("Model served", ", ".join(served)),
        ("Run ID", rec.run_id), ("Created", f"{rec.created_at:%Y-%m-%d %H:%M:%S} UTC"),
        ("LLM calls", str(t.llm_calls) if t else "—"),
        ("Input tokens", fmt(t.input_tokens) if t else "—"),
        ("Cached input tokens", fmt(t.cached_input_tokens) if t else "—"),
        ("Output tokens", fmt(t.output_tokens) if t else "—"),
        ("Total stage latency", f"{t.total_latency_ms / 1000:.1f} s" if t else "—"),
        ("Gateway cache replays", str(t.gateway_cache_hits) if t else "—"),
        ("Cost", cost),
    ]
    rows = [html.Tr([
        html.Td(STAGE_NAMES.get(s["stage"], s["stage"]) + (f" (attempt {s['attempt']})" if s.get("attempt") and s["attempt"] > 1 else "")),
        html.Td(s["kind"]), html.Td(badge(s["status"], {"ok": "good", "retry": "warn", "degraded": "bad", "error": "bad"}.get(s["status"], "muted"))),
        html.Td(f"{s['latency_ms'] / 1000:.2f} s" if s["latency_ms"] >= 100 else f"{s['latency_ms']:.1f} ms"),
        html.Td(s.get("model_served") or ("scripted demo" if s.get("is_fake_llm") else "—")),
        html.Td(fmt(s.get("input_tokens"))), html.Td(fmt(s.get("cached_input_tokens"))), html.Td(fmt(s.get("output_tokens"))),
        html.Td(s.get("gateway_cache_status") or "—"),
        html.Td(_cost_label(s.get("cost_source"), s.get("cost_usd")) if s["kind"] == "llm" else "—"),
    ]) for s in spans]
    body = [html.Dl([html.Div([html.Dt(k), html.Dd(v)], className="kv") for k, v in summary], className="kv-list kv-cols")]
    if t is not None and t.cost_source == "unpriced":
        body.append(html.P("Unpriced: no per-token rates in config/pricing.yaml; token counts are provider-reported.",
                           className="note"))
    if source_kind(rec) == "demo":
        body.append(html.P("Demo runs make no model calls, so token counts are not reported.", className="note"))
    stage_table = html.Div(html.Table([
        html.Thead(html.Tr([html.Th(h, scope="col") for h in ("Stage", "Kind", "Status", "Latency", "Model served",
                                                              "Input", "Cached", "Output", "Gateway cache", "Cost")])),
        html.Tbody(rows)], className="table table-compact table-run"), className="table-scroll", tabIndex="0",
        role="region", **{"aria-label": "Stage telemetry"}) if rows else empty("The trace file for this run is not available.")
    return [card("Run summary", body), card("Stages", stage_table,
                 subtitle="From this run's trace; tokens are provider-reported.")]
