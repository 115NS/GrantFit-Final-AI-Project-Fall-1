from datetime import date

from conftest import AS_OF, assessment_dict

from grantfit.models import (
    EligibilityResult, FitConceptAssessment, FundingOpportunity, NonprofitProfile,
    ProposalConcept, RequiredComponent,
)
from grantfit.tools.effort import check_deadline, estimate_effort
from grantfit.tools.scoring import decide_priority, weighted_score
from grantfit.config import load_yaml


def opp(**kw) -> FundingOpportunity:
    base = dict(id="o", funder_name="F", title="T", opportunity_type="open_competitive",
                deadline=date(2026, 12, 15), award_min_usd=10_000, award_max_usd=50_000,
                required_components=[RequiredComponent(name="narrative_page", quantity=5),
                                     RequiredComponent(name="budget")])
    base.update(kw)
    return FundingOpportunity(**base)


PROFILE = NonprofitProfile(id="p", name="P", staff_capacity_hours_per_month=30)
CONCEPT = ProposalConcept(title="C", requested_amount_usd=40_000)


def decide(eligibility="PASS", fit=5, concept=5, opportunity=None, assessment=True):
    o = opportunity or opp()
    effort = estimate_effort(o)
    a = FitConceptAssessment.model_validate(assessment_dict(fit, concept)) if assessment else None
    return decide_priority(
        eligibility=EligibilityResult(overall=eligibility), assessment=a, opportunity=o,
        concept=CONCEPT, effort=effort, deadline=check_deadline(o, PROFILE, effort.hours, AS_OF),
    )


def test_weighted_score_bounds():
    w = load_yaml("scoring")["fit_weights"]
    assert weighted_score(FitConceptAssessment.model_validate(assessment_dict(5, 5)).fit, w) == 100.0
    assert weighted_score(FitConceptAssessment.model_validate(assessment_dict(1, 1)).fit, w) == 0.0
    assert weighted_score(FitConceptAssessment.model_validate(assessment_dict(3, 3)).fit, w) == 50.0


def test_effort_sum_and_defaulted_components():
    e = estimate_effort(opp(required_components=[RequiredComponent(name="narrative_page", quantity=5),
                                                 RequiredComponent(name="mystery_form")]))
    assert e.hours == 4 + 15 + 3
    assert e.defaulted_components == ["mystery_form"]
    assert estimate_effort(opp(opportunity_type="government")).hours == round((4 + 15 + 6) * 1.3, 1)


def test_deadline_feasibility():
    o = opp()
    assert check_deadline(o, PROFILE, 25, AS_OF).feasible is True          # 51 days * 1h/day = 51h
    assert check_deadline(o, PROFILE, 100, AS_OF).feasible is False
    assert check_deadline(opp(deadline=date(2026, 1, 1)), PROFILE, 1, AS_OF).feasible is False
    assert check_deadline(o, NonprofitProfile(id="p", name="P"), 25, AS_OF).feasible is None
    assert check_deadline(opp(deadline="rolling"), PROFILE, 999, AS_OF).feasible is True
    assert check_deadline(opp(deadline=None), PROFILE, 1, AS_OF).feasible is None


def test_hard_fail_overrides_perfect_scores():
    r = decide(eligibility="FAIL", fit=5, concept=5)
    assert r.tier == "NOT_ELIGIBLE"
    assert r.fit_score_0_100 == 100.0  # scores still shown, but cannot change the tier


def test_unknown_eligibility_is_verify_first():
    assert decide(eligibility="UNKNOWN", fit=5, concept=5).tier == "VERIFY_FIRST"


def test_tier_rules():
    assert decide().tier == "PURSUE_NOW"
    assert decide(concept=2).tier == "STRENGTHEN_THEN_PURSUE"
    assert decide(fit=2).tier == "DEPRIORITIZE"
    assert decide(assessment=False).tier == "INCOMPLETE_ASSESSMENT"
    assert decide(opportunity=opp(deadline=date(2026, 10, 30))).tier == "DEPRIORITIZE"
    assert decide(eligibility="NOT_APPLICABLE",
                  opportunity=opp(opportunity_type="invitation_only")).tier == "CULTIVATE_RELATIONSHIP"
    assert decide(eligibility="NOT_APPLICABLE", fit=1,
                  opportunity=opp(opportunity_type="individual_philanthropy")).tier == "DEPRIORITIZE"


def test_award_size_factor_and_value_per_hour():
    r = decide(opportunity=opp(award_max_usd=20_000))
    assert r.award_size_factor == 0.5
    assert any("outside the award range" in x for x in r.tier_reasons)
    assert r.value_per_effort_hour == round(100 * 0.5 / r.effort.hours, 2)
