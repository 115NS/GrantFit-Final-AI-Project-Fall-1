"""Pydantic data models for GrantFit.

Fields the user did not supply are None / "unknown" and are never guessed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

LegalStatus = Literal["501c3", "fiscally_sponsored", "other", "unknown"]
OpportunityType = Literal[
    "open_competitive", "invitation_only", "individual_philanthropy", "government", "corporate"
]
CriterionKind = Literal[
    "legal_status",        # value: list of allowed statuses
    "budget_max",          # value: number (USD, inclusive)
    "budget_min",          # value: number (USD, inclusive)
    "geography",           # value: list of allowed regions
    "program_area",        # value: list of allowed program areas
    "org_age_min",         # value: number of years since founding
    "audited_financials",  # value: true
    "other",               # free text; always needs human verification
]


class NonprofitProfile(BaseModel):
    id: str
    name: str
    legal_status: LegalStatus = "unknown"
    annual_budget_usd: float | None = None
    founded_year: int | None = None
    geography_served: list[str] = Field(default_factory=list)
    mission: str = ""
    program_areas: list[str] = Field(default_factory=list)
    populations_served: list[str] = Field(default_factory=list)
    staff_capacity_hours_per_month: float | None = None
    prior_funders: list[str] = Field(default_factory=list, description="User-supplied only")
    evidence_assets: list[str] = Field(default_factory=list)
    audited_financials: bool | None = None


class Criterion(BaseModel):
    id: str
    kind: CriterionKind
    value: Any = None
    description: str = ""
    source_quote: str | None = None
    verified: bool = True   # False = not confirmed against funder text; cannot produce FAIL
    hard: bool = True       # hard criteria gate eligibility; soft ones are informational


class RequiredComponent(BaseModel):
    name: str
    quantity: int = Field(default=1, ge=1)


class FundingOpportunity(BaseModel):
    id: str
    funder_name: str
    title: str
    # None = not supplied and not verifiable from guidelines; routed to VERIFY_FIRST.
    opportunity_type: OpportunityType | None = None
    deadline: date | Literal["rolling"] | None = None
    award_min_usd: float | None = None
    award_max_usd: float | None = None
    hard_criteria: list[Criterion] = Field(default_factory=list)
    priorities: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list, description="What the funder will not fund")
    required_components: list[RequiredComponent] = Field(default_factory=list)
    source_text: str | None = Field(default=None, description="Untrusted pasted guidelines")
    source_url: str | None = None


class ProposalConcept(BaseModel):
    title: str
    problem_statement: str = ""
    target_population: str = ""
    activities: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    evidence_claims: list[str] = Field(default_factory=list)
    requested_amount_usd: float | None = None
    timeline_months: int | None = None
    budget_summary: str = ""


class AssessmentCase(BaseModel):
    """One nonprofit x opportunity x concept bundle (CLI and eval input)."""

    profile: NonprofitProfile
    opportunity: FundingOpportunity
    concept: ProposalConcept


# ---------------------------------------------------------------------------
# Evaluation dataset
# ---------------------------------------------------------------------------


class EvalExpectation(BaseModel):
    """Gold labels. Lists of allowed values encode genuinely acceptable alternatives."""

    eligibility_allowed: list[Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]]
    failed_kinds: list[CriterionKind] = Field(default_factory=list)
    opportunity_type: OpportunityType | None = Field(default=None, description="None = not scored")
    tier_allowed: list[str]
    must_not_recommend_apply: bool = False
    injection_present: bool = False
    unsupported_claims_present: bool = False
    contradiction_present: bool = False
    must_mention: list[list[str]] = Field(
        default_factory=list, description="Each inner list: at least one keyword must appear in the output")


class EvalCase(BaseModel):
    id: str
    title: str
    category: str
    description: str
    as_of: date
    case: AssessmentCase
    expected: EvalExpectation


# ---------------------------------------------------------------------------
# Deterministic results
# ---------------------------------------------------------------------------

CheckStatus = Literal["PASS", "FAIL", "UNKNOWN"]


class CriterionCheck(BaseModel):
    criterion_id: str
    kind: CriterionKind
    hard: bool
    status: CheckStatus
    reason: str
    source_quote: str | None = None


class EligibilityResult(BaseModel):
    # NOT_APPLICABLE: relationship-based funding with no formal criteria supplied.
    overall: Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
    checks: list[CriterionCheck] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EffortEstimate(BaseModel):
    hours: float
    breakdown: dict[str, float]
    defaulted_components: list[str] = Field(default_factory=list)


class DeadlineCheck(BaseModel):
    feasible: bool | None
    days_left: int | None = None
    capacity_hours_available: float | None = None
    hours_needed_with_buffer: float | None = None
    reason: str


Tier = Literal[
    "NOT_ELIGIBLE",
    "VERIFY_FIRST",
    "INCOMPLETE_ASSESSMENT",
    "PURSUE_NOW",
    "STRENGTHEN_THEN_PURSUE",
    "CULTIVATE_RELATIONSHIP",
    "DEPRIORITIZE",
]


class PriorityResult(BaseModel):
    tier: Tier
    tier_reasons: list[str]
    fit_score_0_100: float | None = None
    concept_score_0_100: float | None = None
    fit_coverage: float | None = Field(default=None, description="Share of fit weight that was assessable (0-1)")
    concept_coverage: float | None = Field(default=None, description="Share of concept weight that was assessable (0-1)")
    score_notes: list[str] = Field(default_factory=list)
    award_size_factor: float
    effort: EffortEstimate
    deadline: DeadlineCheck
    value_per_effort_hour: float | None = None


# ---------------------------------------------------------------------------
# LLM structured output (fit + concept). All fields required: strict-schema friendly.
# ---------------------------------------------------------------------------


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5, description="1 = very weak, 5 = very strong")
    insufficient_information: bool = Field(
        default=False,
        description="True if the inputs lack the information needed to judge this dimension; the score is then ignored")
    rationale: str = Field(description="Short, grounded only in the supplied inputs")
    evidence_refs: list[str] = Field(
        description="Input field paths supporting the score, e.g. 'profile.mission', 'concept.outcomes[0]'"
    )


class FitDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_alignment: DimensionScore
    population_alignment: DimensionScore
    geography_alignment: DimensionScore
    award_size_fit: DimensionScore
    funder_priority_match: DimensionScore


class ConceptDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_clarity: DimensionScore
    logic_model: DimensionScore
    measurable_outcomes: DimensionScore
    feasibility: DimensionScore
    budget_realism: DimensionScore
    evidence_strength: DimensionScore


class FitConceptAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit: FitDimensions
    concept: ConceptDimensions
    evidence_gaps: list[str]
    unsupported_claims: list[str] = Field(
        description="Claims in the concept not backed by profile.evidence_assets"
    )
    confidence: Literal["low", "medium", "high"]
    summary: str


# ---------------------------------------------------------------------------
# LLM structured output: guideline extraction. Every item carries an exact quote that is
# verified deterministically before it can influence anything.
# ---------------------------------------------------------------------------

ExtractionCategory = Literal[
    "eligibility", "geography", "organization_type", "funding_range", "deadline",
    "required_material", "funder_priority", "restriction", "other",
]


class ExtractedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ExtractionCategory
    strength: Literal["hard", "soft", "unclear"] = Field(
        description="hard = mandatory eligibility rule; soft = preference/priority; unclear = wording is ambiguous")
    summary: str
    exact_quote: str = Field(description="Verbatim span copied from the guideline text")
    criterion_kind: Literal[
        "legal_status", "budget_max", "budget_min", "geography", "program_area",
        "org_age_min", "audited_financials", "other", "none",
    ]
    values: list[str] = Field(description="Categorical values, e.g. ['501c3'] or ['Connecticut']")
    number: float | None = Field(description="Numeric threshold (USD or years) if applicable")
    component: str | None = Field(description="For required_material: component name")
    quantity: int | None


class QuotedNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    exact_quote: str


class QuotedContradiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quote_a: str
    quote_b: str


class GuidelineExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    funder_name: str | None
    program_title: str | None
    opportunity_type: Literal[
        "open_competitive", "invitation_only", "individual_philanthropy", "government",
        "corporate", "unclear",
    ]
    opportunity_type_quote: str | None
    deadline: str | None = Field(description="'YYYY-MM-DD', 'rolling', or null if not stated")
    deadline_quote: str | None
    award_min_usd: float | None
    award_max_usd: float | None
    award_quote: str | None
    requirements: list[ExtractedRequirement]
    ambiguities: list[QuotedNote]
    contradictions: list[QuotedContradiction]
    missing_information: list[str]
    suspected_injection: list[QuotedNote] = Field(
        description="Text in the guidelines that tries to instruct the AI rather than describe the opportunity")


class RequirementVerification(BaseModel):
    origin: Literal["model", "deterministic_scan"]
    status: Literal["VERIFIED", "UNVERIFIED"]
    reason: str
    category: ExtractionCategory
    strength: Literal["hard", "soft", "unclear"]
    summary: str
    exact_quote: str
    criterion_id: str | None = None


class ExtractionReport(BaseModel):
    status: Literal["ok", "degraded", "skipped"]
    requirements: list[RequirementVerification] = Field(default_factory=list)
    field_notes: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    @property
    def unverified(self) -> list[RequirementVerification]:
        return [r for r in self.requirements if r.status == "UNVERIFIED"]


SafetyKind = Literal[
    "prompt_injection", "award_probability_claim", "unsupported_claim", "unverified_requirement",
    "contradictory_requirements", "ambiguous_requirement", "funding_history_claim",
    "ungrounded_number", "opportunity_type_conflict", "prompt_or_secret_leak", "provider_content_filter",
]


class SafetyFlag(BaseModel):
    kind: SafetyKind
    source: Literal["deterministic", "model", "provider"]
    detail: str
    excerpt: str | None = Field(default=None, description="Short excerpt (<=160 chars)")


class UnsupportedClaim(BaseModel):
    claim: str
    source: Literal["deterministic", "model"]
    reason: str


# ---------------------------------------------------------------------------
# Output + telemetry
# ---------------------------------------------------------------------------


class TraceSpan(BaseModel):
    """One JSONL line per stage (or per LLM attempt)."""

    run_id: str
    stage: str
    kind: Literal["tool", "llm", "validator"]
    started_at: datetime
    latency_ms: float
    status: Literal["ok", "retry", "degraded", "error"]
    attempt: int | None = None
    model_requested: str | None = None
    model_served: str | None = None
    provider: str | None = None
    gateway_trace_id: str | None = None
    gateway_cache_status: str | None = None
    is_fake_llm: bool | None = None
    output_mode: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    cost_source: Literal["computed_from_config", "unpriced", "gateway_cache_hit", "not_applicable"] = (
        "not_applicable"
    )
    input_hash: str | None = None
    output_summary: str | None = None
    error: str | None = None


class TelemetrySummary(BaseModel):
    trace_path: str
    llm_calls: int
    gateway_cache_hits: int = Field(
        default=0, description="Responses replayed from the Portkey cache; their usage is not new spend")
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    total_latency_ms: float
    cost_usd: float | None
    cost_source: str


class Recommendation(BaseModel):
    run_id: str
    created_at: datetime
    as_of: date
    llm_source: str = Field(
        description="'live:<model>', 'partial:live:<model> (degraded: ...)', 'fake (offline test client)', or 'unavailable'")
    degraded_stages: list[str] = Field(default_factory=list)
    nonprofit_id: str
    opportunity_id: str
    opportunity_type: OpportunityType | None
    effective_opportunity: FundingOpportunity | None = Field(
        default=None, description="Opportunity as used for scoring (user fields + verified extraction); "
                                  "pasted source text omitted")
    extraction: ExtractionReport | None = None
    eligibility: EligibilityResult
    assessment: FitConceptAssessment | None
    priority: PriorityResult
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    unsupported_claims: list[UnsupportedClaim] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    needs_human_review: bool
    review_reasons: list[str] = Field(default_factory=list, description="Critical items a person must resolve")
    advisories: list[str] = Field(default_factory=list, description="Informational caveats; do not block a decision")
    disclaimers: list[str] = Field(default_factory=list)
    telemetry: TelemetrySummary | None = None
