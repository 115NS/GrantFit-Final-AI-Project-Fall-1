# GrantFit evidence checklist

This lists every screenshot and video used in `report.html` and `slides.html`, and the status of the title-page details.

**Status as of 2026-10-26**
- All screenshots below are **real captures** of the running GrantFit app at http://127.0.0.1:8050. I took them with headless Chrome at 2× scale (1440×900 viewport, reduced motion). No image was edited or mocked up.
- **No placeholders remain** in the report or slides, neither images nor text (section C).
- The deck does **not** depend on the optional video.

**Where the data in the screenshots came from**
- **Discover:** one live Grants.gov search for `literacy`, retrieved 2026-10-26 06:21 UTC. Free public API, no model call.
- **Results and Run details:** genuine gpt-6-luna outputs saved during evaluation run `20261026T025208Z` (02:53 UTC). I opened them with the Overview's "View a saved live result" option; nothing was re-run.
- **E12 and E16** are fictional, synthetic test cases. Every caption says so.

**Updating an image.** Replace the PNG in `docs/media/` (keep the filename), then rebuild. Both HTML files embed their images, so they stay single, offline files:

```bash
.venv/bin/python docs/build/build_artifacts.py
```

---

## A. Screenshots in use (all captured)

Each row gives: the file, where it appears, the crop, how to recapture it, and the claim it supports.

| # | File (`docs/media/`) | Slide | Report | Crop (CSS px; PNG is 2×) | How to recapture (one sentence) | Claim it supports |
|---|---|---|---|---|---|---|
| 1 | `overview.png` ★ | 1 (right half) | none | Full 1440×900 viewport, top of the Overview page | Open http://127.0.0.1:8050 with the API key configured and capture the first screen without scrolling. | GrantFit is a working app built around one decision: what is worth pursuing, and what it will take. |
| 2 | `discover-results.png` ★ | 3 (left) | none | Results header plus the first result card, about 1156×488 | On Discover, search `literacy` with Grants.gov only, then capture the "N live results" header and the first card. | Discovery is live. Each field is labeled with its evidence source, and Unknown is shown rather than guessed. |
| 3 | `discover-provenance.png` ★ | 3 (top right) | none | Eligibility and Geography columns of the first card, with "Show source text" expanded, about 518×341 | Expand "Show source text" under Eligibility on the first card and capture both columns. | Provenance at field level: an official listing field quoted verbatim, next to "No source evidence → Unknown". |
| 4 | `results-e16-hero.png` ★ | 5 (left, top) | Fig. 2, §3 | Saved-result banner and the recommendation card, about 1156×527 | Overview → "View a saved live result" → E16, then capture from the banner to the bottom of the score tiles. | An ambiguous rule produces Verify first, with the reasons listed, and the award-range issue is also caught. |
| 5 | `results-e16-eligibility.png` ★ | 5 (left, bottom) | Fig. 3, §3 | The "Eligibility checks" card only, about 729×369 | On the E16 result, capture the Eligibility checks card, including the quoted sentence. | The requirement is quote-verified but rated UNKNOWN: never guessed as PASS or FAIL. |
| 6 | `results-e12-hero.png` ★ | 6 (right, top) | none | Banner and recommendation card, about 1156×501 | Overview → "View a saved live result" → E12, then capture from the banner to the score tiles. | A hard budget failure gives Not eligible, and no score can offset it, even under prompt injection. |
| 7 | `results-e12-review.png` ★ | 6 (right, bottom) | Fig. 4, §5 | The "Human review required" panel, about 1156×314 | On the E12 result, capture the yellow Human review panel, including the red manipulation notice. | Injected instructions are surfaced for review and could not change eligibility. |
| 8 | `run-details-e12.png` ★ | none | Fig. 6, §6 | Run summary and Stages cards, about 1156×948 | From the E12 result, click "Run details" and capture the summary and stage table. | Real per-stage telemetry: model served, input, cached and output tokens, latency, and cost shown as Unpriced. |

★ = priority asset. These cover the five priorities: Overview, Discover provenance, assessment results, Run details with real usage, and a safety example.

## B. Captured but not used (spares)

These are in `docs/media/` and can be swapped in if a different view is wanted.

| File | What it shows |
|---|---|
| `assessment-review-run.png` | The step 3 "Review and run" panel, with its checklist and the live and demo run buttons (fictional sample loaded). |
| `assessment-stepper.png` | The three-step progress stepper on the Assess pages. |
| `discover-search.png` | The full Discover search form and source choices. |
| `results-e16-nextsteps.png` | The full prioritized next-steps list for E16 (Blocker → Before applying → Strengthen). It is tall; crop to items 1–3 for a slide. |
| `results-e16-requirements.png` | Verified versus unverified guideline requirements for E16. |
| `results-e16-review.png` | The human-review panel for E16. |
| `results-e12-nextsteps.png` | The next steps for E12, starting with "Do not apply…". |
| `results-e12-eligibility.png` | The E12 eligibility table showing the FAIL on the budget cap. |

## C. Title-page details (resolved 2026-10-26)

| Item | Status |
|---|---|
| Author | "Navya Sharma" in the report and on slide 1. |
| Course | "AI Foundations, Yale University, Fall 2026". The course number, section and instructor were left out on purpose; add them only if the course requires them. |
| Personal motivation | Report §1, the title-page summary, the conclusion and slide 2 draw on the author's 4.5 years of nonprofit work, which motivates the problem. No interviews, user testing or real-world results are claimed; the evaluation remains synthetic. |
| Repository link | Removed: no repository exists. If one is published later, add a "Code" row to the title page in `docs/build/report.src.html` and rebuild, and make sure `.env` is never included. |
| Demo video link | Removed: not yet recorded. If it is recorded, add a "Demo video" row the same way. |

Edit `docs/build/report.src.html` and `docs/build/slides.src.html`, then rebuild. Editing `report.html` directly also works, but the next rebuild will overwrite it.

## D. Optional demo video (not required by the deck)

- **Suggested filename:** `docs/media/grantfit-demo.mp4`
- **Length:** 90–120 s, 1440×900 screen recording.
- **Capture instruction:** Record one continuous take:
  1. Overview.
  2. Discover: search `literacy` and expand "Show source text".
  3. Click "Assess this opportunity".
  4. Load the fictional sample and run the demo (offline).
  5. Open "View a saved live result" for E12 and scroll to Human review.
  6. Open Run details.
- **Claims it supports:** the end-to-end workflow, with provenance, a hard-gate safety outcome and real telemetry.
- **Recording rules:**
  - Use demo mode or saved results, so no live model calls are needed.
  - Do not show `.env`, a terminal holding the key, or browser developer tools.
- If you record it, you can add a link on the report title page (see section C). The slides need no change.

## E. Numbers and their sources (for consistency checks)

| Figure used in report and slides | Source file |
|---|---|
| Tier acceptable 17/17 vs 14/17; hard failures 5/5 vs 4/5; all other metrics | `eval/results/20261026T025208Z/summary.md` |
| 41,201 / 29,851 / 41,062 tokens and 315.1 s (GrantFit); 23,959 / 23,908 / 12,587 and 131.3 s (baseline) | same file, tokens table |
| Run 1: 16/17 vs 14/17; E13 content-filter failure | `eval/results/20261026T024807Z/summary.md` |
| Human review 17/17 → 15/17 | `eval/results/replay-20261026T043601Z/summary.md`; `docs/decision_logic_audit.md` |
| Web search: 41,073 input, 1,815 output, 32.0 s | `runs/discovery-a866a9413d0a.jsonl` |
| E16: 3,062 / 1,347 / 2,744 tokens, 26.1 s; E12: 3,167 / 3,021 / 2,892 tokens, 20.9 s | run-2 summary per-case table; Run details screenshot |
| 236 tests passing | `.venv/bin/python -m pytest`, re-run 2026-10-26 |
| Projections (51,235 input and 11,895 output tokens per user per month) | Derived in report §4 from the run-2 means; labeled as projection |
