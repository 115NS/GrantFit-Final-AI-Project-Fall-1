"""Form specification, layout, and conversion to/from the backend's AssessmentCase.

One FIELDS spec drives the layout, sample loading, and validation, so the UI never keeps its
own copy of backend rules. Validation here only checks input shape (required fields, numbers);
eligibility and scoring stay in the backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from dash import dcc, html
from pydantic import ValidationError

from grantfit.models import AssessmentCase, Criterion, FundingOpportunity, NonprofitProfile, ProposalConcept

LEGAL_OPTIONS = [
    {"label": "501(c)(3) public charity", "value": "501c3"},
    {"label": "Fiscally sponsored project", "value": "fiscally_sponsored"},
    {"label": "Other (e.g. LLC, government unit)", "value": "other"},
    {"label": "Not sure", "value": "unknown"},
]
YES_NO_UNKNOWN = [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"},
                  {"label": "Not sure", "value": "unknown"}]
TYPE_OPTIONS = [
    {"label": "Open grant program", "value": "open_competitive"},
    {"label": "Invitation-only foundation", "value": "invitation_only"},
    {"label": "Individual donor", "value": "individual_philanthropy"},
    {"label": "Government grant", "value": "government"},
    {"label": "Corporate giving", "value": "corporate"},
    {"label": "Not sure (let GrantFit check the guidelines)", "value": "unknown"},
]
REQ_LEGAL_OPTIONS = [
    {"label": "No legal-status rule entered", "value": "none"},
    {"label": "501(c)(3) only", "value": "501c3"},
    {"label": "501(c)(3) or fiscally sponsored", "value": "501c3_or_fs"},
]
COMPONENT_OPTIONS = [
    {"label": "Letter of inquiry", "value": "letter_of_inquiry"},
    {"label": "Project budget", "value": "budget"},
    {"label": "Budget narrative", "value": "budget_narrative"},
    {"label": "Logic model", "value": "logic_model"},
    {"label": "Evaluation plan", "value": "evaluation_plan"},
    {"label": "Audited financials", "value": "audited_financials"},
    {"label": "Board list", "value": "board_list"},
    {"label": "Portal registration", "value": "registration"},
]
COUNTED_COMPONENTS = {"narrative_page": "o-comp_narrative_pages", "letter_of_support": "o-comp_letters"}


@dataclass
class Field:
    id: str
    label: str
    kind: str                    # text | textarea | lines | number | radio | checklist | date
    section: str                 # profile | opportunity | concept
    group: str = ""
    help: str = ""
    required: bool = False
    options: list = field(default_factory=list)
    default: object = None
    placeholder: str = ""
    rows: int = 3


FIELDS: list[Field] = [
    # ---- Nonprofit profile
    Field("p-name", "Organization name", "text", "profile", "About the organization", required=True),
    Field("p-legal_status", "Legal status", "radio", "profile", "About the organization",
          options=LEGAL_OPTIONS, default="unknown"),
    Field("p-annual_budget_usd", "Annual operating budget (USD)", "number", "profile", "About the organization"),
    Field("p-founded_year", "Year founded", "number", "profile", "About the organization"),
    Field("p-audited_financials", "Audited financial statements available?", "radio", "profile",
          "About the organization", options=YES_NO_UNKNOWN, default="unknown"),
    Field("p-mission", "Mission", "textarea", "profile", "Mission and programs", rows=3),
    Field("p-geography_served", "Geography served", "lines", "profile", "Mission and programs",
          help="One place per line, e.g. “New Haven, CT”.", rows=2),
    Field("p-program_areas", "Program areas", "lines", "profile", "Mission and programs",
          help="One per line.", rows=3),
    Field("p-populations_served", "Populations served", "lines", "profile", "Mission and programs",
          help="One per line.", rows=3),
    Field("p-staff_capacity_hours_per_month", "Staff time available for proposals (hours per month)",
          "number", "profile", "Capacity and track record"),
    Field("p-prior_funders", "Prior funding experience", "lines", "profile", "Capacity and track record",
          help="Funders that have granted to you, one per line. Leave blank if none.", rows=2),
    Field("p-evidence_assets", "Available impact evidence", "lines", "profile", "Capacity and track record",
          help="Reports, data, or agreements you can cite, one per line.", rows=3),
    # ---- Funding opportunity
    Field("o-funder_name", "Funder name", "text", "opportunity", "The funder", required=True),
    Field("o-title", "Program or opportunity name", "text", "opportunity", "The funder"),
    Field("o-source_url", "Source web address", "text", "opportunity", "The funder",
          placeholder="Optional: link to the official guidelines"),
    Field("o-opportunity_type", "Type of funding", "radio", "opportunity", "The funder",
          options=TYPE_OPTIONS, default="unknown"),
    Field("o-source_text", "Pasted funding guidelines", "textarea", "opportunity", "Guidelines",
          help="Paste the funder's eligibility and application text.", rows=10),
    Field("o-deadline", "Deadline", "date", "opportunity", "Timing and funding range"),
    Field("o-rolling", "Rolling deadline", "checklist", "opportunity", "Timing and funding range",
          options=[{"label": "Applications accepted on a rolling basis", "value": "rolling"}], default=[]),
    Field("o-award_min_usd", "Smallest award (USD)", "number", "opportunity", "Timing and funding range"),
    Field("o-award_max_usd", "Largest award (USD)", "number", "opportunity", "Timing and funding range"),
    Field("o-priorities", "Funder priorities", "lines", "opportunity", "Timing and funding range",
          help="Optional, one per line.", rows=3),
    Field("o-req_legal", "Required legal status", "radio", "opportunity", "Known eligibility rules (optional)",
          options=REQ_LEGAL_OPTIONS, default="none"),
    Field("o-req_legal_quote", "Source wording for legal-status rule", "text", "opportunity",
          "Known eligibility rules (optional)", placeholder="Optional: paste the funder's sentence"),
    Field("o-req_budget_max", "Maximum organization budget (USD)", "number", "opportunity",
          "Known eligibility rules (optional)"),
    Field("o-req_budget_max_quote", "Source wording for maximum budget", "text", "opportunity",
          "Known eligibility rules (optional)", placeholder="Optional"),
    Field("o-req_budget_min", "Minimum organization budget (USD)", "number", "opportunity",
          "Known eligibility rules (optional)"),
    Field("o-req_budget_min_quote", "Source wording for minimum budget", "text", "opportunity",
          "Known eligibility rules (optional)", placeholder="Optional"),
    Field("o-req_geography", "Eligible geography", "lines", "opportunity", "Known eligibility rules (optional)",
          help="States or regions, one per line.", rows=2),
    Field("o-req_geography_quote", "Source wording for geography rule", "text", "opportunity",
          "Known eligibility rules (optional)", placeholder="Optional"),
    Field("o-comp_narrative_pages", "Narrative page limit", "number", "opportunity", "Application materials"),
    Field("o-comp_letters", "Letters of support required", "number", "opportunity", "Application materials"),
    Field("o-comp_other", "Other required materials", "checklist", "opportunity", "Application materials",
          options=COMPONENT_OPTIONS, default=[]),
    # ---- Proposal concept
    Field("c-title", "Project title", "text", "concept", "The project", required=True),
    Field("c-problem_statement", "Problem statement", "textarea", "concept", "The project", rows=3),
    Field("c-target_population", "Target population", "text", "concept", "The project"),
    Field("c-activities", "Proposed activities", "lines", "concept", "The project", help="One per line.", rows=3),
    Field("c-outcomes", "Intended outcomes", "lines", "concept", "Results and evidence",
          help="One per line, ideally measurable.", rows=3),
    Field("c-evidence_claims", "Impact claims you plan to make", "lines", "concept", "Results and evidence",
          help="One per line; checked against your listed evidence.", rows=3),
    Field("c-requested_amount_usd", "Requested amount (USD)", "number", "concept", "Budget and timeline"),
    Field("c-timeline_months", "Timeline (months)", "number", "concept", "Budget and timeline"),
    Field("c-budget_summary", "Budget summary", "textarea", "concept", "Budget and timeline", rows=3),
]
FIELD_BY_ID = {f.id: f for f in FIELDS}
FIELD_IDS = [f.id for f in FIELDS]
SECTION_LABELS = {"profile": "Nonprofit profile", "opportunity": "Funding opportunity", "concept": "Proposal concept"}
SECTION_PATHS = {"profile": "/profile", "opportunity": "/opportunity", "concept": "/concept"}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def base_class(f: Field) -> str:
    return {"textarea": "textarea", "lines": "textarea", "radio": "choice-group",
            "checklist": "choice-group"}.get(f.kind, "input")


def _control(f: Field):
    # dcc components do not accept aria-describedby; help text lives inside the <label>/<legend>
    # instead, so it is part of each control's accessible description.
    aria: dict = {}
    if f.kind in ("text", "number", "date"):
        return dcc.Input(id=f.id, type=f.kind, value=f.default, className=base_class(f), debounce=False,
                         placeholder=f.placeholder or None, required=f.required,
                         min=0 if f.kind == "number" else None, **aria)
    if f.kind in ("textarea", "lines"):
        return dcc.Textarea(id=f.id, value=f.default or "", className=base_class(f), rows=f.rows,
                            placeholder=f.placeholder or None, required=f.required, **aria)
    if f.kind == "radio":
        return dcc.RadioItems(id=f.id, options=f.options, value=f.default, className=base_class(f),
                              labelClassName="choice", inputClassName="choice-input")
    if f.kind == "checklist":
        return dcc.Checklist(id=f.id, options=f.options, value=f.default or [], className=base_class(f),
                             labelClassName="choice", inputClassName="choice-input")
    raise ValueError(f.kind)


def field_block(f: Field):
    label_text = [f.label] + ([html.Span(" (required)", className="required-mark")] if f.required else [])
    if f.help:
        label_text.append(html.Span(f.help, className="help help-in-label"))
    if f.kind in ("radio", "checklist"):
        label = html.Legend(label_text, className="label")
        wrapper = html.Fieldset
    else:
        label = html.Label(label_text, htmlFor=f.id, className="label")
        wrapper = html.Div
    children = [label, _control(f)]
    wide = f.kind in ("textarea", "lines", "radio", "checklist") or f.id == "o-source_text"
    return wrapper(children, className="field" + (" field-wide" if wide else ""))


GROUP_DESCRIPTIONS = {
    "About the organization": "Drives the hard eligibility rules.",
    "Mission and programs": "Used to judge funder fit.",
    "Capacity and track record": "Sets deadline feasibility; your claims are checked against this evidence.",
    "The funder": "Who is funding and how they accept requests.",
    "Guidelines": "Requirements count only if an exact quote is found here.",
    "Timing and funding range": "Leave blank if unknown.",
    "Known eligibility rules (optional)": "Each rule is checked by fixed code.",
    "Application materials": "Used for the effort estimate.",
    "The project": "What you would propose and for whom.",
    "Results and evidence": "Outcomes and the claims you plan to make.",
    "Budget and timeline": "Compared with the funder's award range.",
}


def section_form(section: str):
    """Each field group is its own card: title and purpose on the left, fields on the right."""
    groups: dict[str, list] = {}
    for f in FIELDS:
        if f.section == section:
            groups.setdefault(f.group, []).append(field_block(f))
    return [html.Section([
        html.Div([html.H2(g, className="group-title"), html.P(GROUP_DESCRIPTIONS.get(g, ""), className="group-desc")],
                 className="group-head"),
        html.Div(blocks, className="field-grid"),
    ], className="form-group card") for g, blocks in groups.items()]


def completion(values: dict) -> dict[str, dict]:
    """Per-section progress for the stepper: required fields present and how many fields are answered."""
    out = {}
    for section in ("profile", "opportunity", "concept"):
        fields = [f for f in FIELDS if f.section == section]

        def answered(f):
            v = values.get(f.id)
            if f.kind in ("radio",):
                return v not in (None, "", "unknown", "none")
            if f.kind == "checklist":
                return bool(v)
            return v not in (None, "") and str(v).strip() != ""
        out[section] = {"answered": sum(answered(f) for f in fields), "total": len(fields),
                        "required_ok": all(answered(f) for f in fields if f.required)}
    return out


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _lines(v) -> list[str]:
    return [ln.strip() for ln in (v or "").splitlines() if ln.strip()]


def _text(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def _slug(text: str, prefix: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return f"{prefix}-{s or 'unnamed'}"


def _number(values: dict, fid: str, errors: dict, integer: bool = False):
    v = values.get(fid)
    if v in (None, ""):
        return None
    try:
        num = float(v)
    except (TypeError, ValueError):
        errors[fid] = "Enter a number."
        return None
    if num < 0:
        errors[fid] = "Must be zero or more."
        return None
    if integer:
        if num != int(num):
            errors[fid] = "Enter a whole number."
            return None
        return int(num)
    return num


def form_to_case(values: dict) -> tuple[AssessmentCase | None, dict[str, str]]:
    """Build an AssessmentCase from form values. Returns (case, field errors)."""
    errors: dict[str, str] = {}
    for f in FIELDS:
        if f.required and not _text(values.get(f.id)):
            errors[f.id] = "This field is required."

    num = lambda fid, integer=False: _number(values, fid, errors, integer)  # noqa: E731
    budget = num("p-annual_budget_usd")
    founded = num("p-founded_year", integer=True)
    if founded is not None and not (1800 <= founded <= date.today().year):
        errors["p-founded_year"] = f"Enter a year between 1800 and {date.today().year}."
    award_min, award_max = num("o-award_min_usd"), num("o-award_max_usd")
    if award_min is not None and award_max is not None and award_min > award_max:
        errors["o-award_max_usd"] = "Largest award must be at least the smallest award."
    req_max, req_min = num("o-req_budget_max"), num("o-req_budget_min")
    pages, letters = num("o-comp_narrative_pages", integer=True), num("o-comp_letters", integer=True)
    requested, months = num("c-requested_amount_usd"), num("c-timeline_months", integer=True)
    capacity = num("p-staff_capacity_hours_per_month")

    deadline = None
    if "rolling" in (values.get("o-rolling") or []):
        deadline = "rolling"
    elif _text(values.get("o-deadline")):
        try:
            deadline = date.fromisoformat(_text(values.get("o-deadline")))
        except ValueError:
            errors["o-deadline"] = "Use the date picker or the format YYYY-MM-DD."

    if errors:
        return None, errors

    criteria: list[Criterion] = []
    quote = lambda fid: _text(values.get(fid)) or None  # noqa: E731
    legal = values.get("o-req_legal") or "none"
    if legal != "none":
        criteria.append(Criterion(id="user-legal", kind="legal_status",
                                  value=["501c3"] if legal == "501c3" else ["501c3", "fiscally_sponsored"],
                                  description="Entered by user", source_quote=quote("o-req_legal_quote")))
    if req_max is not None:
        criteria.append(Criterion(id="user-budget-max", kind="budget_max", value=req_max,
                                  description="Entered by user", source_quote=quote("o-req_budget_max_quote")))
    if req_min is not None:
        criteria.append(Criterion(id="user-budget-min", kind="budget_min", value=req_min,
                                  description="Entered by user", source_quote=quote("o-req_budget_min_quote")))
    geo = _lines(values.get("o-req_geography"))
    if geo:
        criteria.append(Criterion(id="user-geography", kind="geography", value=geo,
                                  description="Entered by user", source_quote=quote("o-req_geography_quote")))

    components = [{"name": n} for n in (values.get("o-comp_other") or [])]
    if pages:
        components.append({"name": "narrative_page", "quantity": pages})
    if letters:
        components.append({"name": "letter_of_support", "quantity": letters})

    audited = {"yes": True, "no": False}.get(values.get("p-audited_financials"))
    otype = values.get("o-opportunity_type")
    name, funder, title = _text(values.get("p-name")), _text(values.get("o-funder_name")), _text(values.get("c-title"))
    try:
        case = AssessmentCase(
            profile=NonprofitProfile(
                id=_slug(name, "np"), name=name, legal_status=values.get("p-legal_status") or "unknown",
                annual_budget_usd=budget, founded_year=founded,
                geography_served=_lines(values.get("p-geography_served")),
                mission=_text(values.get("p-mission")), program_areas=_lines(values.get("p-program_areas")),
                populations_served=_lines(values.get("p-populations_served")),
                staff_capacity_hours_per_month=capacity, prior_funders=_lines(values.get("p-prior_funders")),
                evidence_assets=_lines(values.get("p-evidence_assets")), audited_financials=audited),
            opportunity=FundingOpportunity(
                id=_slug(funder + " " + _text(values.get("o-title")), "opp"), funder_name=funder,
                title=_text(values.get("o-title")) or "(untitled opportunity)",
                opportunity_type=None if otype in (None, "unknown") else otype,
                deadline=deadline, award_min_usd=award_min, award_max_usd=award_max,
                hard_criteria=criteria, priorities=_lines(values.get("o-priorities")),
                required_components=components,
                source_text=_text(values.get("o-source_text")) or None,
                source_url=_text(values.get("o-source_url")) or None),
            concept=ProposalConcept(
                title=title, problem_statement=_text(values.get("c-problem_statement")),
                target_population=_text(values.get("c-target_population")),
                activities=_lines(values.get("c-activities")), outcomes=_lines(values.get("c-outcomes")),
                evidence_claims=_lines(values.get("c-evidence_claims")),
                requested_amount_usd=requested, timeline_months=months,
                budget_summary=_text(values.get("c-budget_summary"))),
        )
    except ValidationError as exc:  # shape problems the checks above did not anticipate
        return None, {"_form": "; ".join(e["msg"] for e in exc.errors())[:300]}
    return case, {}


OPPORTUNITY_FIELD_IDS = [f.id for f in FIELDS if f.section == "opportunity"]


def _num(v):
    return None if v is None else (int(v) if float(v).is_integer() else v)


def opportunity_to_form(o: FundingOpportunity) -> dict:
    """Step-2 form values for an opportunity (used for samples and discovered opportunities)."""
    v = {fid: FIELD_BY_ID[fid].default for fid in OPPORTUNITY_FIELD_IDS}
    v.update({
        "o-funder_name": o.funder_name, "o-title": o.title, "o-opportunity_type": o.opportunity_type or "unknown",
        "o-source_text": o.source_text or "", "o-source_url": o.source_url,
        "o-deadline": o.deadline.isoformat() if isinstance(o.deadline, date) else None,
        "o-rolling": ["rolling"] if o.deadline == "rolling" else [],
        "o-award_min_usd": _num(o.award_min_usd), "o-award_max_usd": _num(o.award_max_usd),
        "o-priorities": "\n".join(o.priorities),
    })
    unmapped = []
    for cr in o.hard_criteria:
        if cr.kind == "legal_status":
            v["o-req_legal"] = "501c3" if list(cr.value) == ["501c3"] else "501c3_or_fs"
            v["o-req_legal_quote"] = cr.source_quote
        elif cr.kind == "budget_max":
            v["o-req_budget_max"], v["o-req_budget_max_quote"] = _num(cr.value), cr.source_quote
        elif cr.kind == "budget_min":
            v["o-req_budget_min"], v["o-req_budget_min_quote"] = _num(cr.value), cr.source_quote
        elif cr.kind == "geography":
            v["o-req_geography"], v["o-req_geography_quote"] = "\n".join(cr.value), cr.source_quote
        else:
            unmapped.append(cr.id)
    other = []
    for comp in o.required_components:
        if comp.name in COUNTED_COMPONENTS:
            v[COUNTED_COMPONENTS[comp.name]] = comp.quantity
        elif comp.name in {opt["value"] for opt in COMPONENT_OPTIONS}:
            other.append(comp.name)
    v["o-comp_other"] = other
    v["_unmapped_criteria"] = unmapped
    return v


def case_to_form(case: AssessmentCase) -> dict:
    """Form values for a case (used to load samples). Unmappable criteria are reported."""
    p, c = case.profile, case.concept
    v = {fid: FIELD_BY_ID[fid].default for fid in FIELD_IDS}
    v.update({
        "p-name": p.name, "p-legal_status": p.legal_status, "p-annual_budget_usd": _num(p.annual_budget_usd),
        "p-founded_year": p.founded_year,
        "p-audited_financials": {True: "yes", False: "no"}.get(p.audited_financials, "unknown"),
        "p-mission": p.mission, "p-geography_served": "\n".join(p.geography_served),
        "p-program_areas": "\n".join(p.program_areas), "p-populations_served": "\n".join(p.populations_served),
        "p-staff_capacity_hours_per_month": _num(p.staff_capacity_hours_per_month),
        "p-prior_funders": "\n".join(p.prior_funders), "p-evidence_assets": "\n".join(p.evidence_assets),
        "c-title": c.title, "c-problem_statement": c.problem_statement, "c-target_population": c.target_population,
        "c-activities": "\n".join(c.activities), "c-outcomes": "\n".join(c.outcomes),
        "c-evidence_claims": "\n".join(c.evidence_claims),
        "c-requested_amount_usd": _num(c.requested_amount_usd), "c-timeline_months": c.timeline_months,
        "c-budget_summary": c.budget_summary,
    })
    v.update(opportunity_to_form(case.opportunity))
    return v
