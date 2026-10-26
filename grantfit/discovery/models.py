"""Discovery data models.

Every displayed detail is an EvidenceField with an explicit status. A value is shown only when it
came from an official structured listing (Grants.gov API) or from a sentence quoted from the
retrieved page; otherwise the status is "unknown". Nothing is inferred or invented.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["grants_gov", "web_search", "user_url"]
EvidenceStatus = Literal["listing_field", "quoted_from_page", "unknown"]


class EvidenceField(BaseModel):
    status: EvidenceStatus = "unknown"
    value: str | None = None                 # human-readable value, only when supported
    quote: str | None = None                 # exact sentence from retrieved text (quoted_from_page)
    note: str | None = None                  # e.g. which listing field, or why unknown


class DiscoveryQuery(BaseModel):
    keywords: str = Field(description="Cause area / keywords (required)")
    mission: str = ""
    geography_served: str = ""
    funder_geography: str = ""
    legal_status: Literal["501c3", "fiscally_sponsored", "other", "unknown"] = "unknown"
    amount_min: float | None = None
    amount_max: float | None = None
    deadline_min_days: int | None = Field(default=None, description="Only deadlines at least N days away")
    include_unknown_deadlines: bool = True
    use_grants_gov: bool = True
    use_web_search: bool = False
    max_results: int = 8


class DiscoveredOpportunity(BaseModel):
    key: str                                  # stable id: hash of canonical URL
    source: SourceKind
    funder: str
    title: str
    url: str
    canonical_url: str
    also_found_via: list[str] = Field(default_factory=list)
    relevance: str
    relevance_basis: Literal["keyword_match", "ai_search_summary"]
    keyword_matches: list[str] = Field(default_factory=list, description="Query words found in retrieved text")
    match: Literal["full", "partial", "none"] = "none"
    ai_summary: str | None = Field(default=None, description="Web-search model text; unverified")
    date_checked: datetime
    source_updated: str | None = None
    opportunity_type: Literal["government", "unknown"] = "unknown"
    deadline: EvidenceField = Field(default_factory=EvidenceField)
    deadline_date: date | None = None
    funding: EvidenceField = Field(default_factory=EvidenceField)
    award_min: float | None = None
    award_max: float | None = None
    eligibility: EvidenceField = Field(default_factory=EvidenceField)
    geography: EvidenceField = Field(default_factory=EvidenceField)
    retrieval: Literal["ok", "failed", "not_attempted"] = "not_attempted"
    retrieval_note: str | None = None
    safety_warnings: list[str] = Field(default_factory=list)
    source_text: str | None = Field(default=None, description="Retrieved text for assessment (untrusted)")


class DiscoveryResult(BaseModel):
    query: DiscoveryQuery
    started_at: datetime
    results: list[DiscoveredOpportunity] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)       # filtering, dedupe, coverage notes
    errors: list[str] = Field(default_factory=list)      # sanitized source failures
    web_search_usage: dict | None = None                  # provider-reported tokens/latency, if used
