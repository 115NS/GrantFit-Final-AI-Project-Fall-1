# GrantFit Architecture (Phase 1 Plan)

GrantFit is a funding strategy and proposal-readiness agent for small and midsize nonprofits. It helps them decide **which opportunities are worth pursuing**, **what is weak or missing**, and **where to spend scarce proposal-writing hours**.

GrantFit is not a grant search engine or an auto-writer. Its value comes from four things together: transparent eligibility checks, concept and feasibility assessment, effort-adjusted prioritization, and an honest readiness roadmap.

**Non-negotiables**
- No award probabilities.
- No fabricated requirements, donor histories, evidence, or outcomes.
- A hard eligibility failure can never be offset by a high score elsewhere.
- Open grant programs, invitation-only funding, and individual philanthropy are treated differently.

---

## 1. Current state (inspected 2026-10-25)

| Item | Finding |
|---|---|
| `GrantFit/` | Empty, so this is a new project. |
| `.env` | Exists in the **parent** folder `FInal Project/.env` and defines `PORTKEY_API_KEY`. The value was not read. It will not be moved, copied, or overwritten. |
| `.gitignore` | `FInal Project/.gitignore` contains `.env`. Left unchanged. A project-level `GrantFit/.gitignore` has been added. |
| Git | Not a git repository yet. |
| Runtime | Python 3.14.7, pip, Node 24, and git are available. No `uv`. No relevant packages are installed globally. |

**`.env` resolution:** the app calls `python-dotenv`'s `find_dotenv(usecwd=True)`, which searches upward from the working directory. The existing parent `.env` therefore works as-is. A fresh clone uses `cp .env.example .env` instead.

---

## 2. Architecture

The stack is deliberately lightweight: **Python + Pydantic + Dash**, with no database, queue, vector store, or server infrastructure.

```
Dash UI (app/)  ──▶  Orchestrator (grantfit/pipeline.py)  ──▶  JSON result + run trace (runs/*.jsonl)
                         │
          ┌──────────────┼───────────────────────────┐
          ▼              ▼                           ▼
  Deterministic tools   LLM stages (gpt-6-luna)     Validators / safety
  (grantfit/tools/)     via Portkey (grantfit/llm/) (grantfit/tools/safety.py,
  eligibility, effort,  extraction, fit+concept     validators.py)
  scoring, deadlines    review, roadmap synthesis
```

**Why this stack**

- **Python + Pydantic:** one schema language serves LLM structured outputs, validation, tests, and evaluation.
- **Dash:** a single-process local app with accessible HTML components and easy CSS theming for the Yale blue-and-white look later. There is no separate frontend build.
- **Model access:** the OpenAI Python client points at the Portkey gateway, or the `portkey-ai` SDK is used if preferred. Either is a thin wrapper in `grantfit/llm/client.py`, so the rest of the code never imports a provider SDK.
- **Storage:** local JSON/JSONL files. Results and traces are plain files, so they are inspectable and can serve as rubric evidence.
- **Setup:** a fresh clone needs `python -m venv .venv && pip install -r requirements.txt && cp .env.example .env && python -m app.app`, which is well under 10 minutes. The plan is to keep it to about 6 dependencies: `openai` or `portkey-ai`, `pydantic`, `python-dotenv`, `dash`, `pytest`, and `pyyaml`.

---

## 3. Agent design: one orchestrator with specialized stages

**Decision:** a single orchestrator runs a fixed, typed pipeline. Some stages are deterministic and some are narrow LLM calls. Multiple independent, free-roaming agents are not used.

**Why**
- **Correctness:** eligibility gating must be guaranteed by code ordering, not by agents negotiating with each other.
- **Traceability:** every stage has typed inputs and outputs, so each step is logged and testable.
- **Cost and latency:** there are about 3 LLM calls per assessment instead of an open-ended agent loop.
- **Safety:** the LLM has no tools that take actions, so injected text cannot trigger side effects.
- **Deadline fit:** this is far fewer moving parts to debug.

It still counts as an "agent": it plans across stages, calls tools, self-checks with validators, retries on schema failure, and escalates to human review.

### Pipeline (per nonprofit × opportunity × concept)

| # | Stage | Type | Output |
|---|---|---|---|
| 0 | **Intake & sanitize**: normalize inputs, wrap pasted guidelines as untrusted, run the injection scan | Deterministic | `SanitizedInput`, `SafetyFlags` |
| 1 | **Requirement extraction**: turn pasted guidelines into structured requirements, each with a **verbatim source quote** | LLM (structured) | `ExtractedRequirements` |
| 2 | **Quote verification**: drop or flag any requirement whose quote is not found in the source text | Deterministic | Verified requirements and an `unverified[]` list |
| 3 | **Eligibility check**: evaluate the profile against verified hard criteria | Deterministic | `EligibilityResult` (PASS / FAIL / UNKNOWN for each criterion) |
| 4 | **Fit & concept review**: rate funder fit and concept quality on a fixed rubric (1–5 per dimension), citing evidence from inputs | LLM (structured) | `FitAssessment`, `ConceptAssessment` |
| 5 | **Effort & priority**: estimate effort hours, check deadline feasibility, compute the weighted score and tier | Deterministic | `PriorityResult` |
| 6 | **Roadmap synthesis**: produce prioritized next actions, evidence gaps, and risks | LLM (structured) | `Roadmap` |
| 7 | **Output validation**: enforce the hard-fail gate, block probability language, check that citations exist, apply the schema | Deterministic | `Recommendation` + `ValidationReport` |
| 8 | **Human review**: the user accepts or edits the result and records the outcome later | UI | `ReviewRecord` (future institutional learning) |

If structured data is supplied directly (for example the evaluation dataset), stages 1–2 are skipped.

**Gating rule:** if any verified hard criterion is FAIL, the tier is forced to `NOT_ELIGIBLE` and the result says *why* with the quote. The LLM fit stage still runs, but only to suggest alternatives. If a criterion is UNKNOWN, the result is capped at `VERIFY_FIRST`.

**Opportunity types** change the recommended action:
- `open_competitive`: apply.
- `invitation_only`: cultivate the relationship. Never say "apply".
- `individual_philanthropy`: relationship and stewardship plan. No "eligibility" framing and no invented donor history.
- Also: `government`, `corporate`.

---

## 4. Data models (`grantfit/models.py`, Pydantic)

All fields are typed. Fields the user did not supply are explicitly `None` or `"unknown"` and are never guessed.

```text
NonprofitProfile
  id, name, legal_status (501c3|fiscally_sponsored|other|unknown), ein_provided: bool,
  annual_budget_usd, geography_served[], mission, program_areas[], populations_served[],
  staff_capacity_hours_per_month, prior_funders[] (user-supplied only), evidence_assets[]
  (e.g. "2024 outcome report"), audited_financials: bool|None

FundingOpportunity
  id, funder_name, title, opportunity_type (open_competitive|invitation_only|
  individual_philanthropy|government|corporate), deadline (date|None|rolling),
  award_min_usd, award_max_usd, hard_criteria: [Criterion], priorities[],
  required_components[] (LOI, narrative pages, budget, audit, letters…),
  source_text (untrusted, optional), source_url (optional)

Criterion
  id, kind (legal_status|budget_max|budget_min|geography|program_area|org_age|other),
  operator, value, source_quote, verified: bool, hard: bool

ProposalConcept
  title, problem_statement, target_population, activities[], outcomes[],
  evidence_claims[], requested_amount_usd, timeline_months, budget_summary

EligibilityResult
  overall (PASS|FAIL|UNKNOWN), checks: [{criterion_id, status, reason, source_quote}]

FitAssessment / ConceptAssessment          (from the LLM, then validated)
  dimensions: [{name, score 1-5, rationale, evidence_refs[]}],
  evidence_gaps[], unsupported_claims[], confidence (low|med|high)
  Fit dimensions: mission_alignment, population_alignment, geography_alignment,
    award_size_fit, funder_priority_match
  Concept dimensions: problem_clarity, logic_model, measurable_outcomes,
    feasibility, budget_realism, evidence_strength

PriorityResult                              (deterministic)
  fit_score_0_100, concept_score_0_100, effort_hours_est, effort_breakdown{},
  deadline_feasible: bool|None, value_per_effort, tier
  tier ∈ {NOT_ELIGIBLE, VERIFY_FIRST, PURSUE_NOW, STRENGTHEN_THEN_PURSUE,
          CULTIVATE_RELATIONSHIP, DEPRIORITIZE}

Roadmap
  actions: [{rank, action, why, effort_hours, owner_hint, closes_gap}],
  evidence_gaps[], risks[], questions_for_funder[]

Recommendation                              (top-level output)
  run_id, inputs_ref, eligibility, fit, concept, priority, roadmap,
  safety_flags[], validation_report, disclaimers[], needs_human_review: bool

EvalCase
  id, description, profile, opportunity, concept,
  expected: {eligibility, failed_criteria[], opportunity_type, tier_allowed[],
             must_mention[], must_not_contain[], injection_present: bool}

RunTrace (one JSONL line per stage)
  run_id, stage, kind (tool|llm|validator), started_at, latency_ms, model,
  prompt_version, input_tokens, cached_input_tokens, output_tokens,
  cost_usd|None, cost_source (computed|unpriced), status (ok|retry|degraded|error),
  error, input_hash, output_summary
```

---

## 5. Deterministic tools vs. AI responsibilities

| Deterministic (code, unit-tested) | AI (gpt-6-luna, schema-constrained) |
|---|---|
| Eligibility rules (legal status, budget bounds, geography, program area) | Extracting requirements from free-text guidelines, with quotes |
| Quote verification (normalized substring match) | Nuanced fit judgment (mission and priority alignment) |
| Effort estimate (component-based hours table, in `config/effort.yaml`) | Concept critique: logic model, outcomes, feasibility |
| Deadline feasibility (hours needed vs. capacity × weeks left) | Evidence-gap identification |
| Weighted scores, tiers, gate enforcement | Roadmap wording and prioritized actions |
| Token, cost, and latency accounting | Questions to ask the funder |
| Injection pattern scan; banned-phrase filter (e.g. "% chance", "likely to win", "guaranteed") | — |

**Scoring transparency:** each weight lives in `config/scoring.yaml` and is shown in the UI. `fit_score = Σ wᵢ·(scoreᵢ−1)/4·100`. Priority uses `value_per_effort = (0.6·fit + 0.4·concept) × award_size_factor / effort_hours`, with tier thresholds documented in the config file.

---

## 6. Safety design

1. **Prompt injection in pasted guidelines**
   - Untrusted text is wrapped in `<untrusted_guidelines>` delimiters.
   - The system prompt states that the text is data only.
   - A regex and heuristic scan (for "ignore previous", "you are now", "mark as eligible", "system:", and similar) raises a visible `SafetyFlag`.
   - The LLM has no tools, and eligibility is decided by code from **verified quotes**, so injected text cannot flip the gate on its own.
2. **Unsupported claims**
   - Every extracted requirement needs a verbatim quote, or it is shown as "unverified, check the guidelines".
   - Concept `evidence_claims` that do not appear in the profile's `evidence_assets` are flagged as unsupported.
   - Rationales must reference input fields.
3. **No probability promises:** an output validator blocks or rewrites banned phrases. A standing disclaimer is shown.
4. **No fabrication:** prompts require `"unknown"` when information is missing. Validators reject donor or funder history that is not present in the inputs.
5. **Secrets**
   - The key is loaded only via dotenv and never logged.
   - Traces store token counts, not prompts containing secrets.
   - `.env` is gitignored, and `.env.example` holds placeholders.
6. **Human in the loop:** `needs_human_review=true` whenever there are UNKNOWN criteria, safety flags, unverified requirements, or low confidence.

**Failure handling**

| Failure | Behavior |
|---|---|
| Missing API key | Clear startup message. Deterministic-only mode still works. |
| API timeout or error | One retry with backoff, then the stage is marked `degraded`. Deterministic results are still shown and AI sections say "unavailable". |
| Invalid structured output | One repair retry that includes the validation error. If it still fails, the stage is `degraded`. Nothing is guessed. |
| Injection detected | Processing continues, the result is flagged, and the fields involved are marked untrusted. |
| Missing fields | The result is UNKNOWN and `VERIFY_FIRST`, never assumed. |

---

## 7. Evaluation plan

**Dataset:** `eval/cases/*.json` with about 12–15 synthetic but realistic cases. These are not real confidential data. Planned coverage:

| Case type | # |
|---|---|
| Clearly eligible, strong fit | 2 |
| Hard fail: budget cap, geography, or legal status, combined with an otherwise excellent fit (tests that the gate holds) | 3 |
| Missing information → UNKNOWN / VERIFY_FIRST | 1 |
| Invitation-only funder | 1 |
| Individual philanthropy prospect | 1 |
| Weak concept (vague outcomes, unrealistic budget) | 2 |
| Deadline infeasible given capacity | 1 |
| Prompt injection inside pasted guidelines | 2 |
| Unsupported evidence claims in the concept | 1 |

**Baseline:** the same model (`gpt-6-luna`) and the same inputs get a single prompt: "Assess whether this nonprofit should apply and give recommendations." It has no tools, no gate, and no validators (`grantfit/baseline.py`). This isolates the value added by the architecture rather than by the model.

**Metrics** (`eval/run_eval.py` writes `eval/results/<timestamp>/summary.md` and `results.json`):
- Eligibility accuracy, and **hard-fail recall** (target: 100%).
- Opportunity-type handling: no "apply" advice for invitation-only or individual-philanthropy cases.
- Tier agreement with `tier_allowed`.
- Injection resistance: the gate is not flipped and the flag is raised.
- Unsupported-claim rate: unverified quotes, and probability language from `must_not_contain`.
- `must_mention` coverage, for key evidence gaps.
- Per case and in total: input, cached-input, and output tokens, latency, and cost, for **both** GrantFit and the baseline.

**Reproducibility**
- Fixed `prompt_version`, with temperature set as low as the model permits.
- Seeds are recorded where supported.
- Raw traces are saved. A committed sample of traces serves as evidence.
- `pytest` runs fully offline with a `FakeLLMClient` that returns recorded fixtures, so CI and graders need no key.

**Token and cost capture**
- Read from the OpenAI-compatible `response.usage`: `prompt_tokens`, `prompt_tokens_details.cached_tokens`, and `completion_tokens`.
- Latency is measured with wall-clock time around each call.
- Cost is computed from rates in `config/pricing.yaml`, which the user fills in from Portkey or the provider's pricing. If rates are absent, `cost_usd=null` and `cost_source="unpriced"`. Prices are never guessed. If Portkey's response headers or logs expose cost, that value is recorded too.

**Real-user test:** `docs/user_test_protocol.md`
- Participants: 1–3 people from or near nonprofit development roles, or proxies such as classmates with nonprofit experience.
- Tasks: assess 2 opportunities for a sample org with and without GrantFit.
- Measures: time to decision, whether the decision changed, perceived trust and clarity (5-point), whether the roadmap was useful, and errors noticed.
- Notes are anonymized, consent is obtained, and no confidential organizational data is used.

---

## 8. File structure

```
GrantFit/
├── README.md                 # setup in <10 min, usage, eval, safety notes
├── ARCHITECTURE.md
├── requirements.txt
├── .env.example              # PORTKEY_API_KEY=your-key-here
├── .gitignore
├── config/
│   ├── settings.yaml         # model id gpt-6-luna, timeouts, retries
│   ├── scoring.yaml          # weights, tier thresholds
│   ├── effort.yaml           # hours per component
│   └── pricing.yaml          # per-token rates (user-filled; null = unpriced)
├── grantfit/
│   ├── models.py             # all Pydantic schemas
│   ├── pipeline.py           # orchestrator
│   ├── telemetry.py          # RunTrace, JSONL writer, cost calc
│   ├── baseline.py           # single-prompt baseline
│   ├── cli.py                # python -m grantfit.cli --case …
│   ├── llm/
│   │   ├── client.py         # Portkey wrapper + FakeLLMClient
│   │   └── prompts/          # extract.md, assess.md, roadmap.md (versioned)
│   └── tools/
│       ├── eligibility.py
│       ├── effort.py
│       ├── scoring.py
│       ├── safety.py         # injection scan, banned phrases
│       └── validators.py     # quote verification, output checks
├── app/
│   ├── app.py                # Dash UI
│   └── assets/style.css      # Yale blue/white (later phase)
├── data/samples/             # demo profiles, opportunities, concepts
├── eval/
│   ├── cases/                # EvalCase JSON
│   ├── run_eval.py
│   ├── metrics.py
│   └── results/              # committed summaries + sample traces
├── tests/
│   ├── fixtures/             # recorded LLM responses
│   ├── test_eligibility.py
│   ├── test_scoring_effort.py
│   ├── test_safety.py
│   ├── test_validators.py
│   └── test_pipeline_offline.py
├── docs/user_test_protocol.md
└── runs/                     # local traces (gitignored)
```

---

## 9. Rubric evidence map

The course rubric has not been provided yet. Adjust this table once it is shared.

| Expected criterion | Evidence produced |
|---|---|
| Agent design and traces | Stage-by-stage `runs/*.jsonl`, a trace panel in the UI, and committed sample traces in `eval/results/` |
| Actual token costs | Per-stage and per-run input, cached-input, and output tokens, latency, and cost from real `usage` fields, plus GrantFit-vs-baseline cost comparison tables |
| Deterministic tools + AI | `grantfit/tools/` with unit tests; the transparent scoring config |
| Safety | `tests/test_safety.py`, injection eval cases, gate tests (high fit + hard fail → NOT_ELIGIBLE), banned-phrase tests |
| Evaluation and baseline | `eval/run_eval.py`, `summary.md` with metrics for both systems |
| Reproducibility | README, pinned requirements, offline tests via `FakeLLMClient` |
| User impact | User-test protocol plus results: time saved, decisions changed, trust ratings |
| Secrets hygiene | `.gitignore`, `.env.example`, no keys in traces |
| UI and accessibility | Dash app with semantic labels, contrast-checked Yale palette, keyboard navigation (later phase) |

---

## 10. Phased build order

1. **MVP vertical slice** (next step): `models.py`, `tools/eligibility.py`, `tools/scoring.py` + `effort.py`, `llm/client.py` (Portkey + Fake), and one combined LLM fit+concept stage. Also `telemetry.py` and `pipeline.py`, and `cli.py` run on **one structured sample case**. It prints a `Recommendation` JSON and writes a trace with real token counts. Includes tests for the gate and eligibility.
2. **Safety and validators:** injection scan, banned phrases, output validator, and the human-review flag, with tests.
3. **Extraction from pasted guidelines** with quote verification, plus the roadmap synthesis stage.
4. **Evaluation:** 12–15 cases, the baseline, `run_eval.py`, and the metrics summary with token and cost tables.
5. **Dash UI:** functional forms, results, and a trace panel. After that, the Yale blue-and-white styling and an accessibility pass.
6. **User test, README polish**, a fresh-clone timing check, then the report and slides.

**The smallest viable end-to-end workflow** is:

```
sample profile + structured opportunity + concept  →  deterministic eligibility gate
  →  one gpt-6-luna fit/concept call (via Portkey)  →  deterministic score/effort/tier
  →  Recommendation JSON  +  trace line with tokens/latency/cost
```

It proves the key, the gateway, structured output, telemetry, and the gate before anything else is built on top.

---

## 10a. Phase 2A implementation notes (as built)

These are deviations from and refinements to the plan above, confirmed against the live gateway on 2026-10-25.

**Portkey and the model**
- Calls use the OpenAI SDK `responses.create` against `https://api.portkey.ai/v1` with model `gpt-6-luna`.
- Portkey routes to `azure-openai`, and the served model is reported as `gpt-6-luna-global`.
- Strict `json_schema` output, including `minimum`/`maximum`, is accepted. The prompt-level JSON fallback exists but has not been needed.
- `usage` returns input, cached-input, output, and reasoning tokens.

**Portkey response cache**
- An identical repeat request can be a gateway cache **HIT** that replays the earlier `usage`. Example: 0.96 s vs. 14.3 s for the same request.
- Traces record `gateway_cache_status`. HITs get `cost_source="gateway_cache_hit"` and are excluded from token and spend totals.
- `--fresh` sends `x-portkey-cache-force-refresh` and should be used for evaluation runs.
- The first trace from a repeated run, made before this field existed (`runs/20261026T015049Z-859fcb.jsonl`), is a cache replay and must not be used as cost evidence.

**Model changes**
- `Criterion` uses a `kind` with fixed semantics instead of a generic `operator`.
- `program_area` lexical misses produce UNKNOWN, not FAIL, because the comparison is semantic.
- Unverified criteria can never produce FAIL.
- `EligibilityResult.overall` adds `NOT_APPLICABLE` for relationship-based funding with no criteria supplied.
- A new tier, `INCOMPLETE_ASSESSMENT`, is used when the LLM stage fails after one repair retry. Nothing is fabricated in that case.

**Layout and scope**
- The LLM stage lives in `grantfit/assess.py`. The prompt is `grantfit/llm/prompts/assess_v1.md`.
- The fake client (`FakeLLMClient`) is always labeled `is_fake_llm: true` and never reports token counts.
- Human review is flagged for unsupported concept claims, low confidence, unknown capacity or deadline, defaulted effort components, and unverified criteria.
- Dash is not installed yet. It will be added in the UI phase.

## 10b. Phase 2B implementation notes (as built)

**Pipeline with pasted guidelines**

`guideline_safety_scan` → `extract_guidelines` (LLM, `extract_v1`) → `quote_verification` → eligibility → effort → `assess_fit_concept` (LLM, `assess_v2`) → `output_safety_validation` → priority → `output_validation`.

**Quote verification** (`tools/quotes.py`)
- A quote must be an exact span of the source. Only typography and whitespace are normalized; case, wording, and ellipses are not.
- Quotes must be at least 12 characters and 3 words, and must not overlap injection-flagged sentences.
- The extracted *value* must also appear in its own quote: an amount, date, place, legal status, or years. A real quote paired with a wrong number is UNVERIFIED.
- UNVERIFIED items are reported but never become criteria.

**Deterministic backstop** (`tools/guideline_scan.py`)
- Budget caps and minimums, 501(c)(3) requirements, and invitation-only language are found directly in the text. A manipulated or confused model therefore cannot hide a hard failure by omitting it.
- Conflicting numeric thresholds produce a contradiction. That gives an UNKNOWN check, so FAIL still dominates.

**Explicit uncertainty**
- Ambiguous ("unclear") eligibility items, verified contradictions, and a failed extraction all become hard `other` checks with status UNKNOWN, which routes to VERIFY_FIRST. Known FAILs still win.
- An unknown opportunity type gives VERIFY_FIRST.
- Verified invitation-only language overrides a declared open type and raises an `opportunity_type_conflict` flag.
- Soft preferences become `priorities`. Restrictions are surfaced for human confirmation.

**Assessment input**
- The assessment receives only verified structured fields, never the raw pasted text.

**Output validators** (`tools/validators.py`, `tools/safety.py`)
- Award-likelihood sentences are redacted. Question forms such as "whether … will be funded" are allowed.
- A prompt canary or the API key appearing in output is redacted and flagged.
- Unsupported concept claims are detected deterministically (figures or strong wording not in `evidence_assets`) and merged with the model's own list.
- Ungrounded figures are flagged. Percentages derivable from two input amounts are allowed.
- Invented funding history is flagged.

**Human review**
- Review is required if any of these are present: a safety flag, unverified or contradictory requirements, ambiguous requirements, unknown eligibility or type, a degraded stage, unsupported claims, or restrictions.

**Injection scan**
- The injection scan is defense in depth. It is tuned against false positives on legitimate funder text, with tests covering both. The critical rules do not depend on it.

**Degraded runs**
- `degraded_stages` and `llm_source="partial:…"` record failed stages, and the CLI exits 1.
- Deterministic findings are retained. Fake output is never substituted.

**Observed live behavior** (2026-10-25, `--fresh`)
- On the normal sample, 10/10 model items were verified.
- The model reported the opportunity type as "unclear" because the text never states it, so the result is VERIFY_FIRST. This is intended: the type is not guessed.
- On the malicious sample, the model refused the injection and reported 3 suspected-injection passages. The verified $500k cap produced NOT_ELIGIBLE.

## 10c. Phase 2C evaluation (as built)

**Dataset:** `eval/cases/E01–E17`, fictional data, generated by `eval/build_cases.py`. A test checks that the committed files match the builder.
- 2 clear-eligible cases, 5 hard fails (budget, geography, legal status, injection + budget, extraction-only geography), missing info, invitation-only, individual donor, 2 weak concepts, an infeasible deadline, 2 injections (one forcing "eligible", one forcing "ineligible"), unsupported claims, a contradiction, and an ambiguity.
- Gold labels list every defensible tier or eligibility value, with reasons in the builder.

**Baseline:** `grantfit/baseline.py` uses the same model, inputs, output vocabulary, structured-output mode, and basic instructions. It uses one prompt with no tools, verification, gate, or validators.

**Runner:** `eval/run_eval.py` runs both systems in parallel per case. It writes the summary, per-case scores, full outputs, and JSONL traces. `--rescore DIR` recomputes scores from saved outputs with no new model calls, keeping the originals as `*.initial.*`.

**Run 1** (`eval/results/20261026T024807Z`, live `--fresh`)

| Metric | GrantFit | Baseline |
|---|---|---|
| Eligibility correct | 16/17 | 16/17 |
| Tier acceptable | 16/17 | 14/17 |
| Hard-fail recall | 5/5 | 4/5 |

- The baseline's E17 miss was a false PASS with "STRENGTHEN_THEN_PURSUE" for a Massachusetts-only org applying to a Connecticut-only funder.
- GrantFit's E13 miss: Azure's content filter refused the extraction prompt because the injected text was sent verbatim. GrantFit degraded safely to VERIFY_FIRST without faking anything.
- After the run, a scoring regex bug that undercounted the baseline's injection mentions was fixed and the run was rescored. Only the baseline's injection-surfaced figure changed (0/2 to 1/2).

**Fixes after run 1**
- Injection-flagged sentences are now withheld from the extractor (`redact_spans`). Quotes from them could never verify anyway.
- Content-filter refusals raise `ContentFilterError`, with no wasted schema-fallback call, and a `provider_content_filter` flag.
- Found while designing the cases (before run 1):
  - Geography uses region-to-state expansion, and unresolvable places give UNKNOWN instead of FAIL.
  - The deterministic scan now covers mandatory geography statements.

**Run 2** (`eval/results/20261026T025208Z`, live `--fresh`, all calls `REFRESH`)

| Metric | GrantFit | Baseline |
|---|---|---|
| Eligibility correct | 17/17 | 16/17 |
| Tier acceptable | 17/17 | 14/17 |
| Hard-fail recall | 5/5 | 4/5 |
| Injection surfaced | 2/2 | 1/2 |
| Contradiction surfaced | 1/1 | 0/1 |
| Mean tokens per case (in / out) | 2,424 / 2,415 | 1,409 / 740 |
| Mean latency per case | 18.5 s | 7.7 s |

GrantFit costs about 2.25× the baseline's tokens and 2.4× its latency. High cached-input counts in run 2 come from the provider's prompt-prefix cache, warmed by run 1, and are reported by the provider.

**Caveats**
- The E13 fix was made after seeing run 1 on the same dataset, so run 2 is not held-out evidence for that case.
- n = 17, so the differences are directional, not statistically significant.
- The baseline is nondeterministic. E17 was a false PASS in run 1 and UNKNOWN in run 2.
- GrantFit requested human review on 17/17 cases. Every case had at least one model- or code-detected unsupported claim. For example, E02's problem statement asserts a produce gap that the evidence does not document. This is safe but not selective, and it should be revisited with real users.

## 10d. Phase 3 web UI (as built)

**Package**
- `app/app.py`: layout, routing, and callbacks. Launch with `python -m app.app`.
- `app/forms.py`: one field spec drives the layout, sample loading, and conversion to `AssessmentCase`.
- `app/jobs.py`: background pipeline jobs, with progress read from real trace spans.
- `app/results.py`: display only.
- `app/samples.py`
- `app/assets/style.css`

**No duplicated logic:** the UI calls `grantfit.pipeline.run_case` and renders the `Recommendation`. Tiers, scores, eligibility, and flags all come from the backend.

**Backend additions**
- `Recommendation.effective_opportunity`: the opportunity as scored, with the pasted text omitted. This is an additive field.
- `grantfit/roadmap.py`: a deterministic, rule-based readiness roadmap built from the Recommendation's findings. It makes no LLM call. Each step cites its source finding.

**Honesty in the UI**
- Every result shows its source: Live, Saved live, Demo, Partial, or Unavailable.
- Demo scores are labeled as placeholders.
- Run details show only trace-recorded telemetry, and cost reads "Unpriced" when rates are absent.

**Bulldog cameo**
- `app/assets/yale_bulldog.jpeg` is a byte-identical copy of the supplied `images-2.jpeg`. The original is untouched.
- It appears only in the in-progress card, at 84 px wide with proportions preserved, decorative (`alt=""`, `aria-hidden`).
- It fades in, then bobs 2 px. Both are disabled under `prefers-reduced-motion`.
- It is omitted if the file is missing.

**Bugs found during browser verification and fixed**
- URL changes caused full reloads that wiped the form. Fixed with `refresh="callback-nav"`.
- A Dash State-list signature mismatch broke the Run buttons. An HTTP-level callback test now covers it.
- A CSS class collision.
- Dash 4 markup differences in inputs and radios.
- An empty-state flash during runs.

## 10e. Phase 4 live discovery (as built)

**Mechanisms**
These were chosen after testing what works from this environment:
1. **Grants.gov public API** (`search2`, `fetchOpportunity`). It needs no key, takes about 1 s, and returns official structured fields. This is the default source and covers federal listings only.
2. **gpt-6-luna hosted `web_search` tool** via Portkey. It is opt-in for foundation and corporate programs. One search took 32–50 s and 41k–64k input tokens. Only provider `url_citation` URLs are used; the model's text is kept only as a labeled, unverified summary.
3. **Add by URL**, for a page the user supplies.

Module: `grantfit/discovery/` (`models`, `fetch`, `evidence`, `grants_gov`, `web_search`, `service`).

**Evidence rule**
- Every card field is an `EvidenceField` with status `listing_field` (a named Grants.gov field), `quoted_from_page` (an exact sentence from the retrieved text), or `unknown`.
- Evidence sentences must be at least 6 words, which excludes menus and headings.
- Injection-flagged sentences are never evidence.
- "<Place> Native" population terms are not treated as geography.
- Relevance uses only the user's keywords, labeled full, partial, or weak match.

**Retrieval safety**
- Only public http(s) URLs are fetched. Private, loopback, and link-local hosts are refused, including after redirects.
- Requests have timeouts and a 2 MB cap. Content is limited to HTML or plain text, with scripts and styles dropped. PDFs are reported, not parsed.
- Duplicates are merged by canonical URL and by funder + title, with "also found via" recorded.

**Hand-off**
- `to_funding_opportunity()` passes `source_text` and `source_url`.
- Official deadline, award range, and type are pre-filled only for Grants.gov. Web pages rely on the existing quote-verified extraction.

**UI**
- The "Discover funding" page has background jobs with real step progress, opportunity cards, a shortlist in browser `localStorage`, and "Assess" buttons that fill step 2 and keep the profile and concept.
- A provenance banner appears on step 2, and a source link appears on results.
- The Overview was refreshed with Discover and Assess entry points and a "how it works" section. Samples are labeled "Fictional demo data".

**Live verification** (2026-10-26)
- One Grants.gov + web search through the UI.
- An NEH card was cross-checked field by field against the official API record and matched.
- The NewAlliance web result's Unknown deadline and amount were confirmed absent from the live page.
- That check exposed four quality issues, fixed with regression tests: loose relevance from mission words, nav text used as evidence, "Alaska Native" read as geography, and HTML entities in titles.
- One live assessment of a discovered NEH listing verified 5 of 5 extracted requirements against exact quotes.
- Browser checks found and fixed two more bugs: a shortlist render crash when a saved list exists, and the source URL being dropped in the hand-off.

## 10f. Decision-logic audit

See `docs/decision_logic_audit.md`. It covers the eligibility fixes (state-level geography matching, fiscal-sponsor → UNKNOWN, whole-word program areas), coverage-aware scoring with `assess_v3`, the corrected rule order (closed deadline; request outside the award range), critical-vs-advisory review classification in `grantfit/review.py`, and the offline replay in `eval/replay.py`.

---

## 11. Trade-offs and risks

- **Python 3.14 is very new.** If any dependency lacks wheels, fall back to Python 3.12 via `pyenv` or python.org. The README will state "3.11+" and the setup will be verified on a fresh venv.
- **`gpt-6-luna` via Portkey.** The exact model string, Portkey routing (virtual key or provider header), and structured-output support all need verifying in the MVP. If JSON-schema mode is not supported, fall back to JSON mode plus Pydantic validation and a repair retry.
- **Cached-token reporting** depends on the provider exposing `cached_tokens`. If it is absent, it is recorded as `null` (not 0).
- **Pricing for `gpt-6-luna`** is not assumed. The user supplies it in `pricing.yaml`.
- **Synthetic evaluation data** limits external validity. The real-user test partially compensates.
- **A fixed pipeline is less "agentic"** than a multi-agent system, but much more reliable and gradeable. That is the right trade-off for this deadline.
