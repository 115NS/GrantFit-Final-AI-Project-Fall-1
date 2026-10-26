"""Deterministic effort estimate and deadline feasibility (rates in config/effort.yaml)."""

from __future__ import annotations

from datetime import date

from grantfit.config import load_yaml
from grantfit.models import DeadlineCheck, EffortEstimate, FundingOpportunity, NonprofitProfile


def estimate_effort(opportunity: FundingOpportunity, cfg: dict | None = None) -> EffortEstimate:
    cfg = cfg or load_yaml("effort")
    rates: dict = cfg["components"]
    breakdown: dict[str, float] = {"base_overhead": float(cfg["base_overhead_hours"])}
    defaulted: list[str] = []
    for comp in opportunity.required_components:
        key = comp.name.strip().lower().replace(" ", "_")
        if key in rates:
            per_unit = float(rates[key])
        else:
            per_unit = float(cfg["default_component_hours"])
            defaulted.append(comp.name)
        breakdown[key] = breakdown.get(key, 0.0) + per_unit * comp.quantity

    multiplier = float(cfg["type_multipliers"].get(opportunity.opportunity_type, 1.0))
    hours = round(sum(breakdown.values()) * multiplier, 1)
    if multiplier != 1.0:
        breakdown["type_multiplier"] = multiplier
    return EffortEstimate(hours=hours, breakdown=breakdown, defaulted_components=defaulted)


def check_deadline(
    opportunity: FundingOpportunity,
    profile: NonprofitProfile,
    effort_hours: float,
    as_of: date,
    cfg: dict | None = None,
) -> DeadlineCheck:
    cfg = cfg or load_yaml("effort")
    deadline = opportunity.deadline
    if deadline is None:
        return DeadlineCheck(feasible=None, reason="No deadline provided; confirm with the funder.")
    if deadline == "rolling":
        return DeadlineCheck(feasible=True, reason="Rolling deadline; schedule work within available capacity.")

    days_left = (deadline - as_of).days
    if days_left < 0:
        return DeadlineCheck(feasible=False, days_left=days_left, reason="Deadline has passed.")

    needed = round(effort_hours * float(cfg["deadline_capacity_buffer"]), 1)
    if profile.staff_capacity_hours_per_month is None:
        return DeadlineCheck(
            feasible=None, days_left=days_left, hours_needed_with_buffer=needed,
            reason="Staff capacity not provided; cannot judge deadline feasibility.",
        )
    capacity = round(profile.staff_capacity_hours_per_month * days_left / 30, 1)
    feasible = capacity >= needed
    reason = (
        f"{capacity}h of proposal capacity in {days_left} days vs. {needed}h needed (incl. buffer)."
    )
    return DeadlineCheck(
        feasible=feasible, days_left=days_left, capacity_hours_available=capacity,
        hours_needed_with_buffer=needed, reason=reason,
    )
