# GrantFit decision-logic audit (2026-10-26)

This audit follows the code path, not the documentation. That path is `grantfit/pipeline.py` → `tools/eligibility.py` → `tools/effort.py` → `assess.py` (prompt `assess_v3`) → `tools/validators.py` → `tools/scoring.py` → `review.py` → `roadmap.py` → `app/results.py`.

Every claim below is backed by a test in `tests/test_decision_logic.py` unless marked otherwise.

---

## 1. Eligibility (deterministic, `tools/eligibility.py`)

Each hard criterion is evaluated by fixed code to `PASS`, `FAIL`, or `UNKNOWN`. Overall eligibility then follows:

- `FAIL` if any **verified** hard check is `FAIL`.
- Otherwise `UNKNOWN` if any hard check is `UNKNOWN`.
- Otherwise `PASS`, but only if at least one hard check exists.
- With no criteria: `NOT_APPLICABLE` for invitation-only or individual giving, and `UNKNOWN` for everything else.

| Rule | Behavior |
|---|---|
| Missing profile data (budget, legal status, geography, founding year, audit) | `UNKNOWN`, never assumed |
| Unverified criterion (not quote-verified) | can never produce `FAIL`; downgraded to `UNKNOWN` |
| Soft criteria (preferences) | never affect eligibility |
| `other` / ambiguous / contradictory / extraction-failed requirements | `UNKNOWN` |
| Budget limits | inclusive (`<=` max, `>=` min) |
| Geography | if both sides resolve to US states or known regions, the state sets are compared exactly; sub-state places use whole-phrase matching; anything else is `UNKNOWN` |
| Program area | whole-word match gives `PASS`; a lexical miss gives `UNKNOWN` (semantic, not provable) |
| Deadline | not an eligibility rule. A passed deadline gives `DEPRIORITIZE` (see §3). |

**Confirmed ineligibility vs. insufficient evidence**
- `FAIL` (tier `NOT_ELIGIBLE`) requires a verified rule and a known fact that violates it.
- `UNKNOWN` (tier `VERIFY_FIRST`) means there is not enough evidence to decide. The UI says so explicitly.
- `UNKNOWN` never becomes eligible (`test_unknown_never_becomes_eligible`).

**Defects found and fixed**

| Defect | Before | After |
|---|---|---|
| "Kansas" org vs. Arkansas-only rule (substring match) | false **PASS** | `FAIL` |
| West Virginia org vs. Virginia-only rule | false **PASS** | `FAIL` |
| Fiscally sponsored project vs. "501(c)(3) only" | confirmed `FAIL` | `UNKNOWN` (may apply through sponsor; confirm) |
| Program area "art" vs. org "heart health" | false `PASS` | `UNKNOWN` |

---

## 2. Scores (`tools/scoring.py`, `config/scoring.yaml`)

**Who decides what**
- **Model-judged:** all 11 dimension ratings (1–5) and the `insufficient_information` marker, from gpt-6-luna with prompt `assess_v3`.
- **Deterministic:** the mapping, weights, exclusions, coverage rule, thresholds, award-range check, effort, and deadline feasibility.

**Formula**

For each dimension *i* with rating *rᵢ* ∈ {1..5}:

```
sᵢ = (rᵢ − 1) / 4 × 100                     # 1→0, 3→50, 5→100
A  = dimensions NOT marked insufficient_information
score    = Σ_{i∈A} wᵢ·sᵢ / Σ_{i∈A} wᵢ           (rounded to 0.1)
coverage = Σ_{i∈A} wᵢ / Σ_all wᵢ
score = None (not scored) if coverage < min_coverage (0.6)
```

| Funder fit | weight | Concept quality | weight |
|---|---|---|---|
| mission_alignment | .30 | problem_clarity | .15 |
| population_alignment | .20 | logic_model | .20 |
| geography_alignment | .15 | measurable_outcomes | .20 |
| award_size_fit | .15 | feasibility | .20 |
| funder_priority_match | .20 | budget_realism | .15 |
| | | evidence_strength | .10 |

**Missing data (fixed)**
- The previous prompt told the model to "score conservatively" when information was missing. That turned absence of information into a low score.
- `assess_v3` instead marks such dimensions `insufficient_information`. They are excluded and the remaining weights are renormalized. The UI shows coverage ("Based on 65% of dimensions' weight") and labels the dimension "Not assessable".
- Deliberate exception: concept dimensions rate what the proposal itself says. No measurable outcomes or no supporting evidence is a real weakness of the concept, not missing information.
- One live check of `assess_v3` (2026-10-26) with a funder stating no priorities or geography: the model marked exactly `geography_alignment` and `funder_priority_match` as insufficient. Strict schema mode was accepted.

**Double counting**
- Geography and legal status are hard gates.
- `geography_alignment` (prompt v3) now rates only the funder's stated geographic focus, and is marked insufficient if none is stated.
- A geography `FAIL` already forces `NOT_ELIGIBLE`, so the model rating cannot mislead the tier.
- Legal status is not a scored dimension.
- `award_size_fit` (model) and the deterministic award-range rule overlap intentionally. The rule acts only when a request falls outside a *stated* range (§3).

**Comparability (limitation)**
- Scores are within-rubric model judgments, not measurements.
- The same input produced concept scores of 61.2 and 57.5 on two live runs.
- A score at 65% coverage is not directly comparable to one at 100%, so coverage is displayed.
- Scores are **not** probabilities of winning, and the UI says so.

**Displayed scores** come only from `PriorityResult` (pipeline output). The UI computes nothing (`app/results.py` displays `fit_score_0_100`, `concept_score_0_100`, coverage, and score notes).

**Arbitrary choices that need expert validation:** the weights, `fit_min = 50` (every fit dimension "adequate"), `concept_min = 60` (average rating 3.4/5), `min_coverage = 0.6`, and the effort hours per component and `deadline_capacity_buffer = 1.2`.
- None are calibrated against award outcomes.
- The smallest defensible correction is to keep them, label them as design choices (done in `config/scoring.yaml`), and calibrate with real nonprofit decisions before relying on exact cut-offs.
- `value_per_effort_hour` (blend 0.6/0.4 × award factor ÷ hours) is computed but **not used by any rule or shown**. Treat it as unvalidated.

---

## 3. Recommendation mapping (first matching rule wins)

| # | Condition | Tier | Meaning |
|---|---|---|---|
| 1 | Eligibility `FAIL` | NOT_ELIGIBLE | Confirmed ineligible; no score can override |
| 2 | Deadline already passed | DEPRIORITIZE | Closed; look for next cycle *(new: previously asked to verify eligibility for a closed call)* |
| 3 | Eligibility `UNKNOWN` or opportunity type unknown | VERIFY_FIRST | **Cannot yet be assessed:** decide eligibility first |
| 4 | Model assessment failed, or score coverage < 60% | INCOMPLETE_ASSESSMENT | **Cannot yet be assessed:** not enough information to rate *(coverage rule new)* |
| 5 | Invitation-only or individual giving | CULTIVATE_RELATIONSHIP (or DEPRIORITIZE if fit < 50) | Relationship-based funding |
| 6 | Deadline infeasible for staff capacity | DEPRIORITIZE | **Weak opportunity** for you now |
| 7 | Fit < 50 | DEPRIORITIZE | **Weak opportunity** (poor fit) |
| 8 | Concept < 60 | STRENGTHEN_THEN_PURSUE | **Eligible, underdeveloped concept** |
| 9 | Request outside the funder's stated award range | STRENGTHEN_THEN_PURSUE | Adjust the request *(new: previously could be PURSUE_NOW)* |
| 10 | Otherwise | PURSUE_NOW | |

**What affects prioritization**
- Eligibility (gate), deadline (closed or infeasible), fit, concept, and award range all change the tier.
- **Staff effort affects the tier only through deadline feasibility.** Hours are shown, but there is no rule like "too much effort for too little money". That would need a justified threshold, so it is noted as a limitation.

---

## 4. Human review (`grantfit/review.py`)

**Why 17/17 synthetic cases were flagged**
- `needs_human_review` was `bool(review_reasons) or bool(flags)`, so any caveat or any flag counted.
- Every case had at least one "concept claim not backed by evidence" item.
- 13 cases reuse a concept that deliberately claims "proven to double reading growth". E14 claims 40% gains with no evidence.
- Routine caveats (unknown deadline, funder restrictions, low model confidence, eligibility failures under the user's own rules) raised the same warning.

**New classification.** `needs_human_review` is true only for *required* items:
- Eligibility `UNKNOWN`.
- An eligibility `FAIL` based on requirements read from guideline text.
- Opportunity type unknown.
- A degraded stage.
- An unverified hard criterion, or an unverified extracted hard requirement.
- Contradictory requirements.
- Safety flags: prompt injection, provider content-filter refusal, leaked internal text, invented funding history, opportunity-type conflict.
- Unsupported claims found **by deterministic checks** (figures or strong wording absent from the nonprofit's evidence).

**Advisories** are shown in a separate "Worth checking" panel and never hidden:
- model-only claim judgments;
- a `FAIL` under rules the user entered;
- self-reported low confidence;
- unknown capacity or deadline, and defaulted effort components;
- funder restrictions to confirm;
- ambiguous non-eligibility wording, soft unverified details, redacted award-likelihood wording, figures not found in the inputs, and score-coverage notes.

**Tests**
- A fully supported, straightforward case needs **no** review.
- Routine caveats produce advisories only.
- Each genuine risk still requires review (`test_genuine_risks_still_require_review`, `test_failure_from_guideline_text_requires_review_but_from_own_rules_is_advisory`).

---

## 5. Explainability

Each results section carries an evidence-type badge:
- **Rule-checked:** eligibility checks.
- **Quote-verified:** guideline requirements, with exact quotes.
- **Model judgment:** fit and concept ratings, with each rationale and the input fields cited.
- **Rules + model judgment:** next steps, missing evidence (each claim tagged *Rule check* or *Model judgment*), risks.
- **Rule-based estimate:** effort.
- **Your entries + verified sources:** opportunity as assessed.

Discover cards mark each field as *Official listing field*, *Quoted from the page*, or *No source evidence*.

Missing or unverified information is shown as Unknown, Not assessable, or Not scored. Human-review actions are listed separately from advisories.

Confidence is labeled "model's self-reported confidence". No award probabilities, funder history, or evidence are generated (enforced by validators and tests from Phase 2B).

---

## 6. Evaluation (synthetic, offline)

`eval/replay.py` re-applies the current deterministic logic to the saved outputs of live run `20261026T025208Z`.
- It makes no model calls and leaves the original run folder untouched.
- Report: `eval/results/replay-*/summary.md`.
- All 13 synthetic metrics are unchanged (17/17 acceptable tiers, 5/5 hard failures caught).
- Human review required drops from **17/17 to 15/17** (E02, E09 now advisories only). The remaining 15 reflect genuine issues seeded in the dataset.
- Expected answers were **not** changed.

**Replay limits**
- Pasted-guideline cases keep their saved eligibility, because raw extraction output was not saved.
- Saved ratings predate `insufficient_information`.
- These are synthetic cases written by the developer. Results say nothing about real-world accuracy.

**Ambiguities documented, not "fixed" in the answer key**
- E15 (contradictory caps that the org satisfies under both readings) accepts PASS or UNKNOWN. GrantFit's conservative UNKNOWN is a policy choice.
- E16 (ambiguous "should generally be based in New England") accepts UNKNOWN or PASS.
