# GrantFit

A funding strategy and proposal-readiness agent for small and midsize nonprofits. It checks eligibility with transparent rules, assesses funder fit and proposal quality, estimates effort, surfaces missing evidence and risks, and lays out next steps. It never promises award probabilities.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design. **Status: Phase 4. Live funding discovery (Grants.gov API plus opt-in web search) connected to the assessment workflow.**

## Requirements

- **Python 3.11 or newer.** Verified on Python 3.14.7. Check with `python3 --version`.
- A Portkey API key with access to `gpt-6-luna`, for live assessments only. Demo mode, saved results, and all tests work without one.

## Setup (about 3 minutes)

From the `GrantFit` folder:

```bash
python3 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Then edit `.env` and set `PORTKEY_API_KEY=...`. Never commit `.env`; it is gitignored.

GrantFit looks for `.env` in the project folder first, then in parent folders. The key is read from the environment only. It is never displayed, logged, or written to traces.

## Launch the web app

```bash
.venv/bin/python -m app.app
```

Open **http://127.0.0.1:8050**. Use `--port 8060` (or set `PORT`) to change the port. The app binds to localhost only.

### Using it

1. **Overview**: what GrantFit does, whether a live model is configured, and the sample data.
2. **Assess → 1 Nonprofit, 2 Opportunity, 3 Concept**: the three input forms. A progress stepper at the top of each step shows what is answered and whether required fields are missing. Only three fields are required: organization name, funder name, and project title. Leave unknown fields blank; GrantFit never assumes missing facts. Paste the funder's guidelines on step 2. They are treated as untrusted text: requirements are used only when an exact quote can be verified.
3. **Run** from step 3:
   - In the **Review and run** panel on step 3, a readiness checklist confirms each step. **Run live assessment (gpt-6-luna)** uses the real pipeline and model. It usually takes 15–30 s, with real progress per stage.
   - **Run demo (offline, scripted)** runs the real eligibility rules, quote verification, scoring rules, and safety checks. The AI ratings are scripted placeholders, labeled "demo" everywhere, and no model call is made.
   - **Force a fresh model call** bypasses Portkey's response cache, so tokens and latency reflect a new call.
4. **Results**:
   - The recommendation, plus a human-review callout when needed.
   - Eligibility checks with the funder's wording, and verified versus unverified guideline quotes.
   - Fit and concept scores, the effort estimate and deadline feasibility, missing evidence, risks, and **Prioritized next steps** (a rule-based readiness roadmap), shown first on the results page.
5. **Run details**:
   - Model requested and served, and each stage's status and latency.
   - Input, cached-input, and output tokens, and cost. Cost shows **Unpriced** until rates are set in `config/pricing.yaml`.

### Discover funding (live)

Open **Discover** in the top navigation (or **Discover funding** on the Overview).

1. **Search.**
   - Enter short cause-area keywords, such as `literacy` or `food security`.
   - **Fill from my nonprofit profile** copies your mission, geography, and legal status from step 1.
   - Optionally set an award range and a deadline preference.
2. **Choose sources.**
   - **Grants.gov** (default): the official public Grants.gov API, with no key and no model cost, usually 1–2 s. It covers federal listings only.
   - **Web search** (opt-in, needs the API key): `gpt-6-luna`'s hosted web-search tool via Portkey finds foundation and corporate programs. One search takes about 30–60 s and tens of thousands of input tokens (one test search: 41k input, 1.8k output). Only pages the search actually cites are used.
3. **Read the cards.** Each card shows:
   - the funder and opportunity, why it may fit (your keywords only), and a link to the **original source**
   - the date checked and the source's last-updated date
   - deadline, funding, eligibility, and geography, each with an evidence status:
     - **Official listing field** (Grants.gov API)
     - **Quoted from the page** (expand to see the exact sentence)
     - **No source evidence** (shown as Unknown; never guessed)

   AI-written web-search summaries are labeled *unverified*. Text on a page that looks like instructions to an AI is flagged and never used as evidence.
4. **Add a funder page by URL** if you already have the official guidelines page. Inaccessible pages show a clear warning, and every field stays Unknown.
5. **Save to shortlist.** The shortlist is stored in this browser's local storage and survives reloads.
6. **Assess this opportunity.** This fills step 2 with the retrieved source text, the source URL, and, for Grants.gov only, the official deadline, award range, and type. Your profile and concept are kept. Complete step 3 and run. Retrieved text goes through the same injection scan and quote-verified extraction as pasted text.

Labels throughout:
- **Live · Grants.gov**, **Live · Web search**, or **Live · Added by URL**: fresh retrievals.
- **Saved**: shortlist items, as retrieved when saved.
- **Fictional demo data**: the samples on Overview, which are never shown as discoveries.

Form entries persist while you move between pages, but a full browser reload clears them. The shortlist persists.

### Demo mode and sample data

On the Overview page (the **Your workspace** panel also shows your shortlist count and current result):
- **Load a sample into the form** fills all three steps with fictional data. Then run it in demo or live mode.
- Samples include an eligible organization, pasted guidelines, a prompt-injection attempt with a hard budget failure, an invitation-only foundation, and unsupported impact claims.
- **View a saved live result** opens a genuine gpt-6-luna result recorded during the latest evaluation run, with its real trace. It is labeled as saved and is not re-run.

Every result carries a banner stating where it came from:
- **Live**: a fresh model call.
- **Saved live**: recorded during evaluation and not re-run.
- **Demo**: scripted placeholders.
- **Partial** or **Unavailable**: when model stages fail.

## Command line

```bash
.venv/bin/python -m grantfit.cli --case data/samples/sample_case.json --fresh
```

Other options:
- `data/samples/guideline_case.json` for pasted guidelines.
- `data/samples/malicious_guideline_case.json` for a prompt injection.
- `--fake` for offline mode.
- `--as-of YYYY-MM-DD` for a fixed evaluation date.

The recommendation JSON goes to stdout and the trace path to stderr. The CLI exits with code 1 if any model stage degraded.

## Evaluate (GrantFit vs. single-prompt baseline)

```bash
.venv/bin/python -m eval.run_eval --fresh
```

Live: 17 cases × (GrantFit + baseline), about 50 model calls, around 2–5 minutes.

```bash
.venv/bin/python -m eval.run_eval --fake
```

This is an offline harness check. Its output is labeled FAKE and is not evidence.

```bash
.venv/bin/python -m eval.run_eval --rescore eval/results/20261026T025208Z
```

This recomputes scores from saved outputs with no model calls.

```bash
.venv/bin/python -m eval.replay eval/results/20261026T025208Z
```

This re-applies the current eligibility, tier, and review logic to a saved live run's model outputs, with no model calls. The report goes to `eval/results/replay-<timestamp>/`. See `docs/decision_logic_audit.md` for the scoring formulas, rule order, and review classification.

Results go to `eval/results/<UTC timestamp>/`: `summary.md`, `results.json`, `outputs/`, and `traces/`. The cases are in `eval/cases/` (fictional) and are generated by `eval/build_cases.py`.

## Tests (offline, no API key needed)

```bash
.venv/bin/python -m pytest
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No API key found" on Overview; live button disabled | Create `.env` from `.env.example` and set `PORTKEY_API_KEY`. Restart the app. |
| Live run shows "Assessment could not run" | The message is a sanitized gateway error. Check the key, network access to `api.portkey.ai`, and that your Portkey workspace can use `gpt-6-luna`. Form entries are preserved, and demo mode still works. |
| "Partial live result" or "Provider refused the guideline text" | A model stage failed or a content filter refused the text. Deterministic findings are kept and nothing is guessed. Review the flagged guideline text and re-run. |
| "Port 8050 is already in use, so GrantFit did NOT start" | Another process (often another GrantFit) holds the port. Open that one at http://127.0.0.1:8050, or run `.venv/bin/python -m app.app --port 8060` and use the new address. |
| Run buttons show "No response from the GrantFit server" | The page's server has stopped or never started. Check the terminal, restart `.venv/bin/python -m app.app`, then reload the page. Pages from a stopped server cannot run assessments. |
| `ModuleNotFoundError: dash` or `openai` | Use the virtual environment's Python (`.venv/bin/python`) and re-run the `pip install` step. |
| Install fails on a very new Python | Create the venv with Python 3.12 (for example `python3.12 -m venv .venv`) and reinstall. |
| Cost shows "Unpriced" | Expected until per-token rates for `gpt-6-luna` are entered in `config/pricing.yaml`. GrantFit never guesses prices. |
| "Search could not run" or "Grants.gov search failed" | Live discovery needs internet access to `api.grants.gov` (and the funder sites for web or URL results). Try again; your inputs are kept. You can still paste guidelines on step 2. |
| Discovery shows many "Weak match" results | Grants.gov keyword search is broad. Use fewer, more specific keywords. Weak matches are listed last and labeled. |
| A funder page shows every field as Unknown | GrantFit reads plain sentences only. Tables, PDFs, and linked pages are not interpreted. Open the source, or paste the relevant text into step 2. |
| Web search option is greyed out | It needs `PORTKEY_API_KEY` in `.env`. Grants.gov search works without it. |
| No bulldog during loading | The loading cameo uses `app/assets/yale_bulldog.jpeg`, a copy of the supplied `images-2.jpeg`. If the file is missing, the app works normally without it. |

## Cost and telemetry

- Token counts come from the provider's `usage` fields.
- Dollar cost is computed only if rates are set in `config/pricing.yaml`. Otherwise it is recorded as `null` / "unpriced".
- Responses replayed from Portkey's cache are marked `gateway_cache_hit` and are not counted as new spend.
- Each run writes a JSONL trace to `runs/` (gitignored). Traces contain stage names, status, latency, token counts, and hashes, never prompts or keys.
