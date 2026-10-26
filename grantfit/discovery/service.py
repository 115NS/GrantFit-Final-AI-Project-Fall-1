"""Discovery orchestration: sources -> retrieval -> evidence scan -> de-duplication.

Sources are injectable so tests run offline. Retrieved pages are untrusted: they are scanned for
injection, evidence comes only from non-flagged sentences, and the text sent onward to the
assessment pipeline goes through its own quote-verified extraction.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from grantfit.discovery import grants_gov
from grantfit.discovery.evidence import keyword_relevance, match_level, scan_page
from grantfit.discovery.fetch import FetchError, FetchedPage, canonical_url, fetch_page
from grantfit.discovery.models import DiscoveredOpportunity, DiscoveryQuery, DiscoveryResult, EvidenceField
from grantfit.discovery.web_search import WebCandidate, run_web_search
from grantfit.models import FundingOpportunity
from grantfit.telemetry import Tracer

Fetcher = Callable[[str], FetchedPage]


def _key(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12]


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def page_to_opportunity(url: str, page: FetchedPage | None, error: str | None, query: DiscoveryQuery,
                        source: str, funder: str | None, title: str | None, ai_summary: str | None,
                        checked: datetime, today: date | None = None) -> DiscoveredOpportunity:
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    final_url = page.url if page else url
    if page is None:
        return DiscoveredOpportunity(
            key=_key(url), source=source, funder=funder or host, title=title or host, url=url,
            canonical_url=canonical_url(url),
            relevance=("AI search summary (unverified): " + ai_summary) if ai_summary else "Page not retrieved.",
            relevance_basis="ai_search_summary" if ai_summary else "keyword_match", ai_summary=ai_summary,
            date_checked=checked, retrieval="failed",
            retrieval_note=f"{error} Nothing on this card was verified; open the link to check it yourself.")
    ev = scan_page(page.text, today=today)
    first_line = html_lib.unescape(page.title or next(
        (ln.strip() for ln in page.text.splitlines() if len(ln.split()) >= 3), host))
    found, kw = keyword_relevance(query.keywords, page.text)
    relevance = (f"AI search summary (unverified): {ai_summary} " if ai_summary else "") + f"Page check: {kw}"
    header = (f"Funder web page retrieved by GrantFit on {checked:%Y-%m-%d} from {final_url}"
              + (" (text truncated)" if page.truncated else "") + "\n")
    return DiscoveredOpportunity(
        key=_key(final_url), source=source, funder=funder or host, title=title or first_line[:120],
        url=final_url, canonical_url=canonical_url(final_url), relevance=relevance.strip(),
        relevance_basis="ai_search_summary" if ai_summary else "keyword_match", ai_summary=ai_summary,
        keyword_matches=found, match=match_level(found, query.keywords),
        date_checked=checked, source_updated=page.last_modified, opportunity_type="unknown",
        deadline=ev["deadline"], deadline_date=ev["deadline_date"], funding=ev["funding"],
        award_min=ev["award_min"], award_max=ev["award_max"], eligibility=ev["eligibility"],
        geography=ev["geography"], retrieval="ok",
        retrieval_note="Page text was truncated to the first 20,000 characters." if page.truncated else None,
        safety_warnings=ev["safety_warnings"], source_text=header + page.text)


def fetch_url_opportunity(url: str, query: DiscoveryQuery, fetcher: Fetcher = fetch_page,
                          today: date | None = None) -> DiscoveredOpportunity:
    """'Add by URL': retrieve one official page the user supplies."""
    checked = datetime.now(timezone.utc)
    try:
        page, err = fetcher(url), None
    except FetchError as exc:
        page, err = None, str(exc)
    return page_to_opportunity(url, page, err, query, "user_url", None, None, None, checked, today)


def dedupe(items: list[DiscoveredOpportunity]) -> tuple[list[DiscoveredOpportunity], int]:
    by_url: dict[str, DiscoveredOpportunity] = {}
    by_title: dict[str, DiscoveredOpportunity] = {}
    out, merged = [], 0
    for it in items:
        tkey = _norm_title(f"{it.funder} {it.title}")
        prior = by_url.get(it.canonical_url) or by_title.get(tkey)
        if prior:
            if it.source != prior.source and it.source not in prior.also_found_via:
                prior.also_found_via.append(it.source)
            merged += 1
            continue
        by_url[it.canonical_url] = it
        by_title[tkey] = it
        out.append(it)
    return out, merged


def discover(query: DiscoveryQuery, *, grants_post=grants_gov.default_post, fetcher: Fetcher = fetch_page,
             web_search=run_web_search, tracer: Tracer | None = None, today: date | None = None,
             progress: Callable[[str], None] = lambda s: None) -> DiscoveryResult:
    result = DiscoveryResult(query=query, started_at=datetime.now(timezone.utc))
    items: list[DiscoveredOpportunity] = []

    if query.use_grants_gov:
        try:
            found, notes, errors = grants_gov.discover(query, grants_post, today=today, progress=progress)
            items += found
            result.notes += notes
            result.errors += errors
        except Exception as exc:  # noqa: BLE001 - network/API failures are reported, never hidden
            result.errors.append(f"Grants.gov search failed ({type(exc).__name__}). Try again later.")

    if query.use_web_search:
        progress("web_search")
        outcome = web_search(query, tracer=tracer)
        result.web_search_usage = outcome.usage
        if outcome.error:
            result.errors.append(outcome.error)
        elif not outcome.candidates:
            result.notes.append("Web search returned no cited funder pages.")
        progress("retrieve_pages")
        checked = datetime.now(timezone.utc)

        def retrieve(c: WebCandidate) -> DiscoveredOpportunity:
            try:
                page, err = fetcher(c.url), None
            except FetchError as exc:
                page, err = None, str(exc)
            except Exception as exc:  # noqa: BLE001
                page, err = None, f"The page could not be retrieved ({type(exc).__name__})."
            return page_to_opportunity(c.url, page, err, query, "web_search", c.funder,
                                       c.program or c.citation_title, c.summary or None, checked, today)

        with ThreadPoolExecutor(max_workers=5) as pool:
            web_items = list(pool.map(retrieve, outcome.candidates))
        failed = sum(1 for w in web_items if w.retrieval == "failed")
        if failed:
            result.notes.append(f"{failed} cited page(s) could not be retrieved; they are shown as unverified.")
        items += web_items

    progress("dedupe")
    result.results, merged = dedupe(items)
    if merged:
        result.notes.append(f"{merged} duplicate result(s) merged.")
    if not result.results and not result.errors:
        result.notes.append("No opportunities matched. Try broader keywords or relax the amount or deadline filters.")
    return result


def to_funding_opportunity(d: DiscoveredOpportunity) -> FundingOpportunity:
    """Hand a discovered opportunity to the assessment pipeline.

    Only official structured listing fields (Grants.gov) pre-fill deadline/amounts; for web pages,
    those come from the pipeline's own quote-verified extraction of the retrieved text.
    """
    listing = d.source == "grants_gov"
    return FundingOpportunity(
        id=f"disc-{d.key}", funder_name=d.funder, title=d.title,
        opportunity_type="government" if listing else None,
        deadline=d.deadline_date if listing and d.deadline.status == "listing_field" else None,
        award_min_usd=d.award_min if listing else None, award_max_usd=d.award_max if listing else None,
        source_text=d.source_text, source_url=d.url,
    )
