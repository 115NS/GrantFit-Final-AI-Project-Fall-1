"""Grants.gov public API (no key): search2 + fetchOpportunity.

Official, structured federal listings. Displayed fields map one-to-one to named listing fields;
absent or zero fields are reported as unknown, never filled in.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import requests

from grantfit.discovery.evidence import keyword_relevance, match_level, scan_page
from grantfit.discovery.fetch import USER_AGENT, canonical_url, html_to_text
from grantfit.discovery.models import DiscoveredOpportunity, DiscoveryQuery, EvidenceField

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
DETAIL_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
LISTING_URL = "https://www.grants.gov/search-results-detail/{id}"
TIMEOUT = (5, 15)
ELIGIBILITY_CODES = {"501c3": "12", "fiscally_sponsored": "12"}   # fiscal sponsors apply as 501(c)(3)s

Poster = Callable[[str, dict], dict]


def default_post(url: str, payload: dict) -> dict:
    r = requests.post(url, json=payload, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    data = r.json()
    if data.get("errorcode") not in (0, None):
        raise RuntimeError(f"Grants.gov error: {data.get('msg')}")
    return data.get("data") or {}


def _mdY(s: str | None) -> date | None:
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _money(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def search(query: DiscoveryQuery, post: Poster = default_post, rows: int = 25) -> tuple[list[dict], int]:
    payload = {"keyword": query.keywords.strip(), "oppStatuses": "forecasted|posted", "rows": rows}
    code = ELIGIBILITY_CODES.get(query.legal_status)
    if code:
        payload["eligibilities"] = code
    data = post(SEARCH_URL, payload)
    return list(data.get("oppHits") or []), int(data.get("hitCount") or 0)


def fetch_details(opp_id: str, post: Poster = default_post) -> dict:
    data = post(DETAIL_URL, {"opportunityId": int(opp_id)})
    return data


def to_discovered(hit: dict, details: dict | None, query: DiscoveryQuery, checked: datetime) -> DiscoveredOpportunity:
    opp_id = str(hit.get("id"))
    url = LISTING_URL.format(id=opp_id)
    syn = (details or {}).get("synopsis") or (details or {}).get("forecast") or {}
    title = html_lib.unescape(hit.get("title") or (details or {}).get("opportunityTitle") or "(untitled listing)")
    agency = html_lib.unescape(hit.get("agency") or syn.get("agencyName") or "(agency not stated)")
    number = hit.get("number") or (details or {}).get("opportunityNumber") or ""
    status = hit.get("oppStatus") or ""

    close = _mdY(hit.get("closeDate"))
    if close:
        deadline = EvidenceField(status="listing_field", value=close.isoformat(),
                                 note="Grants.gov close date (closeDate)")
    elif status == "forecasted":
        deadline = EvidenceField(note="Forecasted opportunity: no close date posted yet.")
    else:
        deadline = EvidenceField(note="No close date in the Grants.gov listing.")

    floor, ceiling = _money(syn.get("awardFloor")), _money(syn.get("awardCeiling"))
    if details is None:
        funding = EvidenceField(note="Listing details could not be retrieved.")
    elif floor or ceiling:
        value = (f"${floor:,.0f} to ${ceiling:,.0f}" if floor and ceiling
                 else (f"Up to ${ceiling:,.0f}" if ceiling else f"From ${floor:,.0f}"))
        funding = EvidenceField(status="listing_field", value=value, note="Grants.gov awardFloor / awardCeiling")
    else:
        funding = EvidenceField(note="No award floor or ceiling in the Grants.gov listing.")

    types = [t.get("description", "") for t in (syn.get("applicantTypes") or [])]
    elig_desc = html_to_text(syn.get("applicantEligibilityDesc") or "")[1]
    if details is None:
        eligibility = EvidenceField(note="Listing details could not be retrieved.")
    elif types:
        has_501 = any(t.get("id") == "12" for t in syn.get("applicantTypes") or [])
        eligibility = EvidenceField(
            status="listing_field",
            value=("501(c)(3) nonprofits are an eligible applicant type" if has_501
                   else "501(c)(3) nonprofits are not listed as eligible"),
            quote=elig_desc[:300] or None, note=f"Grants.gov applicantTypes ({len(types)} types listed)")
    else:
        eligibility = EvidenceField(note="No eligible applicant types in the Grants.gov listing.")

    desc = html_to_text(syn.get("synopsisDesc") or "")[1]
    scanned = scan_page(f"{elig_desc}\n{desc}") if details else {}
    geography = scanned.get("geography") or EvidenceField(note="Not stated in the listing fields retrieved.")
    if geography.status == "quoted_from_page":
        geography.note = "Quoted from the Grants.gov listing text"
    elif details is not None:
        geography = EvidenceField(note="No geographic limit stated in the listing fields retrieved.")

    found, rel = keyword_relevance(query.keywords, title, desc)
    rel_parts = [rel]
    if eligibility.status == "listing_field":
        rel_parts.append(eligibility.value + ".")

    source_text = None
    if details is not None:
        lines = [f"Grants.gov listing retrieved via the official Grants.gov API on {checked:%Y-%m-%d}. Source: {url}",
                 f"Opportunity: {title} ({number})", f"Agency: {agency}", f"Status: {status}"]
        if syn.get("responseDateStr") or hit.get("closeDate"):
            lines.append(f"Close date: {syn.get('responseDateStr') or hit.get('closeDate')}")
        if syn.get("responseDateDesc"):
            lines.append(f"Close date explanation: {html_to_text(syn['responseDateDesc'])[1]}")
        if ceiling:
            lines.append(f"Award ceiling: ${ceiling:,.0f}")
        if floor:
            lines.append(f"Award floor: ${floor:,.0f}")
        if types:
            lines.append("Eligible applicant types listed on Grants.gov:")
            lines += [f"- {t}" for t in types]
        if elig_desc:
            lines.append(f"Additional information on eligibility: {elig_desc}")
        if desc:
            lines.append(f"Description: {desc[:8000]}")
        source_text = "\n".join(lines)

    return DiscoveredOpportunity(
        key=hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12], source="grants_gov",
        funder=agency, title=f"{title}" + (f" ({number})" if number else ""), url=url,
        canonical_url=canonical_url(url), relevance=" ".join(rel_parts), relevance_basis="keyword_match",
        keyword_matches=found, match=match_level(found, query.keywords),
        date_checked=checked, source_updated=syn.get("lastUpdatedDate"), opportunity_type="government",
        deadline=deadline, deadline_date=close, funding=funding, award_min=floor, award_max=ceiling,
        eligibility=eligibility, geography=geography,
        retrieval="ok" if details is not None else "failed",
        retrieval_note=None if details is not None else "Listing details could not be retrieved from Grants.gov.",
        safety_warnings=scanned.get("safety_warnings", []), source_text=source_text,
    )


def discover(query: DiscoveryQuery, post: Poster = default_post, today: date | None = None,
             progress: Callable[[str], None] = lambda s: None) -> tuple[list[DiscoveredOpportunity], list[str], list[str]]:
    """Returns (opportunities, notes, errors)."""
    today = today or date.today()
    checked = datetime.now(timezone.utc)
    notes: list[str] = []
    progress("grants_gov_search")
    hits, total = search(query, post)
    notes.append(f"Grants.gov: {total} forecasted or posted listing(s) match '{query.keywords}'"
                 + (" for 501(c)(3)-eligible applicants" if ELIGIBILITY_CODES.get(query.legal_status) else "") + ".")
    if query.legal_status in ("other", "unknown"):
        notes.append("Legal status not set to 501(c)(3): Grants.gov results are not filtered by applicant type.")

    kept, dropped_deadline = [], 0
    for h in hits:
        close = _mdY(h.get("closeDate"))
        if close and close < today:
            dropped_deadline += 1
            continue
        if query.deadline_min_days is not None:
            if close is None and not query.include_unknown_deadlines:
                dropped_deadline += 1
                continue
            if close is not None and (close - today).days < query.deadline_min_days:
                dropped_deadline += 1
                continue
        kept.append(h)
    if dropped_deadline:
        notes.append(f"{dropped_deadline} listing(s) hidden by your deadline preference or because they have closed.")

    candidates = kept[: max(query.max_results * 2, 6)]
    progress("grants_gov_details")
    errors: list[str] = []

    def detail(h):
        try:
            return fetch_details(str(h["id"]), post)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Grants.gov details for {h.get('number') or h.get('id')}: {type(exc).__name__}")
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        details = list(pool.map(detail, candidates))
    out, dropped_amount = [], 0
    for h, d in zip(candidates, details):
        opp = to_discovered(h, d, query, checked)
        if query.amount_min and opp.award_max and opp.award_max < query.amount_min:
            dropped_amount += 1
            continue
        if query.amount_max and opp.award_min and opp.award_min > query.amount_max:
            dropped_amount += 1
            continue
        out.append(opp)
    if dropped_amount:
        notes.append(f"{dropped_amount} listing(s) hidden because their stated award range is outside your amount.")
    # Listings whose retrieved text actually contains the keywords first (stable: keeps Grants.gov order).
    out.sort(key=lambda o: {"full": 0, "partial": 1, "none": 2}[o.match])   # stable within each level
    weak = sum(1 for o in out[: query.max_results] if o.match == "none")
    if weak:
        notes.append(f"{weak} result(s) are weak matches: Grants.gov returned them, but your keywords do not "
                     "appear in the listing text GrantFit retrieved.")
    return out[: query.max_results], notes, errors


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html_to_text(s)[1]).strip()
