from conftest import AS_OF

from grantfit.models import Criterion, FundingOpportunity, NonprofitProfile
from grantfit.tools.eligibility import check_eligibility


def profile(**kw) -> NonprofitProfile:
    base = dict(id="p", name="P", legal_status="501c3", annual_budget_usd=500_000,
                founded_year=2010, geography_served=["New Haven, CT"],
                program_areas=["early literacy"], audited_financials=True)
    base.update(kw)
    return NonprofitProfile(**base)


def opp(*criteria: Criterion, type_="open_competitive") -> FundingOpportunity:
    return FundingOpportunity(id="o", funder_name="F", title="T", opportunity_type=type_,
                              hard_criteria=list(criteria))


def status(p, c):
    return check_eligibility(p, opp(c), AS_OF).checks[0].status


def test_legal_status():
    c = Criterion(id="c", kind="legal_status", value=["501c3"])
    assert status(profile(), c) == "PASS"
    assert status(profile(legal_status="other"), c) == "FAIL"
    assert status(profile(legal_status="unknown"), c) == "UNKNOWN"


def test_budget_bounds_inclusive():
    cmax = Criterion(id="c", kind="budget_max", value=500_000)
    assert status(profile(), cmax) == "PASS"
    assert status(profile(annual_budget_usd=500_001), cmax) == "FAIL"
    assert status(profile(annual_budget_usd=None), cmax) == "UNKNOWN"
    cmin = Criterion(id="c", kind="budget_min", value=1_000_000)
    assert status(profile(), cmin) == "FAIL"


def test_geography_expands_state_abbreviations():
    c = Criterion(id="c", kind="geography", value=["Connecticut"])
    assert status(profile(), c) == "PASS"
    assert status(profile(geography_served=["Boston, MA"]), c) == "FAIL"
    assert status(profile(geography_served=[]), c) == "UNKNOWN"


def test_geography_regions_and_unresolvable_places():
    assert status(profile(), Criterion(id="c", kind="geography", value=["New England"])) == "PASS"
    assert status(profile(geography_served=["Newark, NJ"]),
                  Criterion(id="c", kind="geography", value=["New England"])) == "FAIL"
    # County-level rule cannot be compared deterministically -> UNKNOWN, never a guessed FAIL.
    assert status(profile(), Criterion(id="c", kind="geography", value=["Fairfield County"])) == "UNKNOWN"
    assert status(profile(geography_served=["Greater Hartford area"]),
                  Criterion(id="c", kind="geography", value=["Connecticut"])) == "UNKNOWN"


def test_program_area_miss_is_unknown_not_fail():
    c = Criterion(id="c", kind="program_area", value=["arts"])
    assert status(profile(), c) == "UNKNOWN"
    assert status(profile(program_areas=["community arts"]), c) == "PASS"


def test_org_age_and_audit_and_other():
    assert status(profile(founded_year=2024), Criterion(id="c", kind="org_age_min", value=3)) == "FAIL"
    assert status(profile(founded_year=None), Criterion(id="c", kind="org_age_min", value=3)) == "UNKNOWN"
    assert status(profile(audited_financials=None), Criterion(id="c", kind="audited_financials", value=True)) == "UNKNOWN"
    assert status(profile(audited_financials=False), Criterion(id="c", kind="audited_financials", value=True)) == "FAIL"
    assert status(profile(), Criterion(id="c", kind="other", description="Must attend webinar")) == "UNKNOWN"


def test_unverified_criterion_cannot_fail():
    c = Criterion(id="c", kind="budget_max", value=100, verified=False)
    assert status(profile(), c) == "UNKNOWN"


def test_overall_rules():
    fail = Criterion(id="f", kind="budget_max", value=100)
    unknown = Criterion(id="u", kind="other", description="x")
    ok = Criterion(id="ok", kind="legal_status", value=["501c3"])
    soft_fail = Criterion(id="s", kind="budget_max", value=100, hard=False)
    assert check_eligibility(profile(), opp(ok, unknown, fail), AS_OF).overall == "FAIL"
    assert check_eligibility(profile(), opp(ok, unknown), AS_OF).overall == "UNKNOWN"
    assert check_eligibility(profile(), opp(ok, soft_fail), AS_OF).overall == "PASS"
    assert check_eligibility(profile(), opp(), AS_OF).overall == "UNKNOWN"
    assert check_eligibility(profile(), opp(type_="individual_philanthropy"), AS_OF).overall == "NOT_APPLICABLE"
