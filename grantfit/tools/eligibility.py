"""Deterministic eligibility checks: PASS / FAIL / UNKNOWN per criterion.

Rules:
- Missing profile information -> UNKNOWN (never assumed).
- An unverified criterion can never produce FAIL (downgraded to UNKNOWN).
- Overall: any hard FAIL -> FAIL; else any hard UNKNOWN -> UNKNOWN; else PASS.
"""

from __future__ import annotations

import re
from datetime import date

from grantfit.models import (
    CheckStatus,
    Criterion,
    CriterionCheck,
    EligibilityResult,
    FundingOpportunity,
    NonprofitProfile,
)

RELATIONSHIP_TYPES = {"invitation_only", "individual_philanthropy"}

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire",
    "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee",
    "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}


def _norm_place(text: str) -> str:
    """Lowercase and expand a trailing US state abbreviation ('New Haven, CT' -> 'new haven, connecticut')."""
    text = text.strip()
    m = re.search(r",\s*([A-Za-z]{2})$", text)
    if m and m.group(1).upper() in US_STATES:
        text = text[: m.start(1)] + US_STATES[m.group(1).upper()]
    elif text.upper() in US_STATES:
        text = US_STATES[text.upper()]
    return text.lower()


_STATE_NAMES = set(US_STATES.values())
REGIONS = {
    "new england": {"connecticut", "maine", "massachusetts", "new hampshire", "rhode island", "vermont"},
    "mid-atlantic": {"new york", "new jersey", "pennsylvania", "delaware", "maryland", "district of columbia"},
    "pacific northwest": {"washington", "oregon", "idaho"},
    "united states": _STATE_NAMES,
    "nationwide": _STATE_NAMES,
}


def _states_of(place_norm: str) -> set[str] | None:
    """US states a normalized place refers to, or None if it cannot be resolved."""
    if place_norm in _STATE_NAMES:
        return {place_norm}
    if place_norm in REGIONS:
        return REGIONS[place_norm]
    tail = place_norm.rsplit(",", 1)[-1].strip() if "," in place_norm else None
    if tail in _STATE_NAMES:
        return {tail}
    return None


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _check(c: Criterion, p: NonprofitProfile, as_of: date) -> tuple[CheckStatus, str]:
    if c.kind == "legal_status":
        allowed = _as_list(c.value)
        if p.legal_status == "unknown":
            return "UNKNOWN", "Nonprofit legal status not provided."
        if p.legal_status in allowed:
            return "PASS", f"Legal status '{p.legal_status}' is allowed."
        if p.legal_status == "fiscally_sponsored" and "501c3" in allowed:
            # Fiscally sponsored projects often apply through their 501(c)(3) sponsor; the rule as
            # stated does not settle that, so this is insufficient evidence, not confirmed ineligibility.
            return "UNKNOWN", ("Funder requires 501(c)(3) status; a fiscally sponsored project may be able to "
                               "apply through its sponsor. Confirm with the funder.")
        return "FAIL", f"Legal status '{p.legal_status}' is not in allowed {allowed}."

    if c.kind in ("budget_max", "budget_min"):
        if c.value is None:
            return "UNKNOWN", "Criterion has no numeric limit."
        if p.annual_budget_usd is None:
            return "UNKNOWN", "Nonprofit annual budget not provided."
        limit, budget = float(c.value), p.annual_budget_usd
        ok = budget <= limit if c.kind == "budget_max" else budget >= limit
        word = "at most" if c.kind == "budget_max" else "at least"
        verdict = "meets" if ok else "does not meet"
        return ("PASS" if ok else "FAIL"), f"Budget ${budget:,.0f} {verdict} requirement of {word} ${limit:,.0f}."

    if c.kind == "geography":
        allowed = [_norm_place(a) for a in _as_list(c.value)]
        served = [_norm_place(s) for s in p.geography_served]
        if not served:
            return "UNKNOWN", "Nonprofit geography served not provided."
        if not allowed:
            return "UNKNOWN", "Criterion lists no regions."
        allowed_states = [_states_of(a) for a in allowed]
        served_states = [_states_of(s) for s in served]
        allowed_label = ", ".join(_as_list(c.value))
        if all(allowed_states) and all(served_states):
            # Both sides resolve to US states: compare state sets exactly. (Substring matching would
            # wrongly pass "Kansas" for "Arkansas" or "West Virginia" for "Virginia".)
            allowed_union = set().union(*allowed_states)
            state_hits = [orig for orig, st in zip(p.geography_served, served_states) if st & allowed_union]
            if state_hits:
                return "PASS", f"Serves {', '.join(state_hits)}, within the allowed area ({allowed_label})."
            return "FAIL", f"Serves {', '.join(p.geography_served)}, outside the allowed area ({allowed_label})."
        # Sub-state places (cities, counties): whole-phrase match only, never raw substrings.
        hits = [orig for orig, s in zip(p.geography_served, served)
                if any(re.search(rf"(?<![\w-]){re.escape(a)}(?![\w-])", s) for a in allowed)]
        if hits:
            return "PASS", f"Serves {', '.join(hits)}, within the allowed area ({allowed_label})."
        # e.g. county/city-level rules or unrecognized place names: not provably a mismatch.
        return "UNKNOWN", (f"Cannot deterministically compare {p.geography_served} with "
                           f"{_as_list(c.value)}; confirm geographic eligibility.")

    if c.kind == "program_area":
        allowed = [a.lower() for a in _as_list(c.value)]
        areas = [a.lower() for a in p.program_areas]
        if not areas:
            return "UNKNOWN", "Nonprofit program areas not provided."
        word = lambda needle, hay: re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", hay)  # noqa: E731
        hits = [a for a in areas if any(word(x, a) or word(a, x) for x in allowed)]
        if hits:
            return "PASS", f"Program areas {hits} match allowed {_as_list(c.value)}."
        # Program areas are semantic; a lexical miss is not proof of ineligibility.
        return "UNKNOWN", f"No exact match between {p.program_areas} and {_as_list(c.value)}; needs human check."

    if c.kind == "org_age_min":
        if c.value is None:
            return "UNKNOWN", "Criterion has no minimum age."
        if p.founded_year is None:
            return "UNKNOWN", "Nonprofit founding year not provided."
        age = as_of.year - p.founded_year
        ok = age >= float(c.value)
        return ("PASS" if ok else "FAIL"), f"Organization age ~{age} years vs. minimum {c.value}."

    if c.kind == "audited_financials":
        if p.audited_financials is None:
            return "UNKNOWN", "Whether audited financials exist was not provided."
        if p.audited_financials:
            return "PASS", "Audited financials available."
        return "FAIL", "Audited financials required but not available."

    return "UNKNOWN", f"Criterion requires human verification: {c.description or c.value}"


def check_eligibility(
    profile: NonprofitProfile, opportunity: FundingOpportunity, as_of: date
) -> EligibilityResult:
    checks: list[CriterionCheck] = []
    notes: list[str] = []
    for c in opportunity.hard_criteria:
        status, reason = _check(c, profile, as_of)
        if status == "FAIL" and not c.verified:
            status = "UNKNOWN"
            reason += " (Criterion unverified against funder text, so not treated as a failure.)"
        checks.append(
            CriterionCheck(
                criterion_id=c.id, kind=c.kind, hard=c.hard, status=status,
                reason=reason, source_quote=c.source_quote,
            )
        )

    hard = [ch for ch in checks if ch.hard]
    if any(ch.status == "FAIL" for ch in hard):
        overall = "FAIL"
    elif any(ch.status == "UNKNOWN" for ch in hard):
        overall = "UNKNOWN"
    elif hard:
        overall = "PASS"
    elif opportunity.opportunity_type in RELATIONSHIP_TYPES:
        overall = "NOT_APPLICABLE"
        notes.append("Relationship-based funding with no formal eligibility criteria supplied.")
    else:
        overall = "UNKNOWN"
        notes.append("No eligibility criteria supplied; confirm requirements with the funder.")

    return EligibilityResult(overall=overall, checks=checks, notes=notes)
