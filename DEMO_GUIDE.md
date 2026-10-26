# GrantFit demo guide (2–3 minute recording)

All organization, funder and project details below are **fictional**. The funding opportunity is **synthetic**: it does not exist, and the recording should say so.

**Dry run, 2026-10-26.** I ran these exact inputs once through the live pipeline (gpt-6-luna, fresh call, 28.7 s; trace `runs/20261026T071010Z-4bad78.jsonl`). The result:
- **Eligibility checks** (all requirements quote-verified):
  - 501(c)(3): **PASS**.
  - Budget at most $1.5M: **PASS**.
  - Connecticut: **PASS**.
  - Independent audit: **UNKNOWN**, because the profile says "Not sure".
- **Recommendation:** overall eligibility **UNKNOWN**, so the recommendation is **Verify first**.
- **Effort and deadline:** 37 hours; deadline feasible (95 days left, 79.2 h of capacity against 44.4 h needed).
- **Readiness gap:** the claim "proven to close the reading gap" was flagged as not supported by the listed evidence.

Model wording and scores vary from run to run, so your recording may differ in the details.

---

## DEMO INPUTS — COPY AND PASTE

Every field is typed or pasted by hand. The app cannot import a complete profile; "Load sample" loads only the built-in samples. Field labels below match the UI exactly. Fields marked **(required)** are the only three the app requires.

### Step 1 · Nonprofit (page "Nonprofit profile")

| Field label | Enter |
|---|---|
| **Organization name (required)** | `Riverside Family Literacy Alliance (fictional)` |
| Legal status | select **501(c)(3) public charity** |
| Annual operating budget (USD) | `640000` |
| Year founded | `2016` |
| Audited financial statements available? | select **Not sure** ← deliberate; this is what produces the UNKNOWN |
| Mission | `Help children in grades K-3 and their families in Hartford build early reading skills through trained volunteer tutors, family reading nights, and take-home book kits.` |
| Geography served (one per line) | `Hartford, CT`<br>`East Hartford, CT` |
| Program areas (one per line) | `early literacy`<br>`family engagement`<br>`out-of-school-time tutoring` |
| Populations served (one per line) | `children in grades K-3`<br>`low-income families`<br>`English language learners` |
| Staff time available for proposals (hours per month) | `25` |
| Prior funding experience | **leave blank** (avoids inventing funder history) |
| Available impact evidence (one per line) | `2025-26 internal program report: 186 students tutored; fall and spring DIBELS 8 scores for 152 students`<br>`Signed partnership agreements with two Hartford elementary schools`<br>`Family reading night attendance sheets for 9 events in 2025-26` |

### Step 2 · Opportunity (page "Funding opportunity")

| Field label | Enter |
|---|---|
| **Funder name (required)** | `Northfield Community Literacy Fund (fictional)` |
| Program or opportunity name | `Family Literacy Grants 2027` |
| Source web address | **leave blank** (synthetic, so there is no real page) |
| Type of funding | select **Open grant program** |
| Pasted funding guidelines | paste the whole block below |
| Deadline, Rolling deadline, Smallest award, Largest award, Funder priorities | **leave blank**: GrantFit reads them from the pasted guidelines |
| Known eligibility rules (all fields) | **leave blank or as they are**: the rules come from the guidelines and are quote-verified |
| Narrative page limit, Letters of support required, Other required materials | **leave blank**: extracted from the guidelines for the effort estimate |

**Pasted funding guidelines** (copy everything between the lines):

```
Northfield Community Literacy Fund (fictional) - Family Literacy Grants 2027
This is an open, competitive grant program. Any eligible organization may apply.

Eligibility
Applicants must be 501(c)(3) public charities.
Organizations with annual operating budgets of $1.5 million or less are eligible.
Programs must serve residents of Connecticut.
Applicants must have completed an independent financial audit for their most recent fiscal year.

Funding
Grants range from $20,000 to $50,000 for a 12-month project period.

Priorities
We especially welcome programs that involve parents and caregivers in children's reading.
Priority will be given to programs that report measurable reading outcomes for grades K-3.

Restrictions
The Fund does not support capital projects, endowments, or lobbying.

Application
Proposals are due January 29, 2027 at 5:00 p.m. Eastern.
Proposals must include a narrative of no more than 5 pages, a project budget, an evaluation plan, and two letters of support.
```

### Step 3 · Concept (page "Proposal concept")

| Field label | Enter |
|---|---|
| **Project title (required)** | `Reading Together at Home: Family Literacy Kits and Tutoring at a Third School` |
| Problem statement | `At our two partner schools, fewer than half of K-3 students met the spring reading benchmark last year (per school-reported data shared with us).` |
| Target population | `90 additional K-3 students at a third Hartford elementary school and their families` |
| Proposed activities (one per line) | `Recruit and train 30 volunteer tutors`<br>`Twice-weekly 30-minute tutoring sessions, October-May`<br>`Monthly family reading nights with take-home book kits` |
| Intended outcomes (one per line) | `65% of tutored students move up at least one DIBELS 8 benchmark level by May`<br>`60% of enrolled families attend at least three family reading nights` |
| Impact claims you plan to make (one per line) | `In 2025-26, most tutored students improved their DIBELS 8 scores between fall and spring`<br>`Our program is proven to close the reading gap` ← deliberately overstated, to show the readiness check |
| Requested amount (USD) | `45000` |
| Timeline (months) | `12` |
| Budget summary | `Program coordinator 0.4 FTE ($24,000), tutor training and background checks ($6,000), book kits and materials ($9,000), family reading nights ($4,000), assessment materials ($2,000).` |

In multi-line fields, press Return between items. Each line is one item. The budget items add up to the $45,000 request.

### Opportunity-selection workflow (why it is pasted)

- **Recommended: paste synthetic guidelines** on the Funding opportunity page. This is a supported method ("Pasted funding guidelines"). It is the only way to show quote-verified PASS / FAIL / UNKNOWN checks reliably and cheaply.
- **Not recommended for this demo:** handing off a real Grants.gov listing. Grants.gov returns federal programs (for example, a search for "literacy" returned a family-planning grant), which are a poor fit for a small literacy nonprofit. Federal listing fields also rarely contain the plain eligibility sentences GrantFit checks.
- **Avoid:** web search. It is opt-in and takes 30–60 s and tens of thousands of input tokens per query; one test used 41,073. Its results also vary.
- **Optional (free, 2 seconds):** show the **Discover** page with a Grants.gov search for `literacy`, only to show the provenance labels (Official listing field / No source evidence). Do not assess those results.

---

## Demo and recording checklist

1. **Launch the app.** Open Terminal and run:
   ```bash
   cd "/Users/navyasharma/Desktop/Yale Fall 1/AI Foundations/FInal Project/GrantFit"
   ```
   ```bash
   .venv/bin/python -m app.app
   ```
   - If you see **"Port 8050 is already in use, so GrantFit did NOT start"**, a GrantFit server is already running. Open http://127.0.0.1:8050. If the run buttons then say "No response from the GrantFit server", that old server has stopped: run `.venv/bin/python -m app.app --port 8060` and use http://127.0.0.1:8060.
   - Leave the Terminal window open, and do **not** record it.
2. **Open http://127.0.0.1:8050 in the browser** and check that the badge at the top right says **gpt-6-luna live**. Before you start recording, fill in all three steps from the tables above, then reload the page to clear them. That rehearsal catches typos. (A full browser reload clears the form; moving between pages does not.)
3. **Start recording** (step 7 explains how). Begin on the **Overview** page.
4. Click **Assess an opportunity you have**. Fill in **1 Nonprofit**, then click **Next: Funding opportunity**.
5. Fill in **2 Opportunity** and paste the guidelines. Click **Next: Proposal concept**.
6. Fill in **3 Concept**. In **Review and run**:
   - Tick **Force a fresh model call (bypass the Portkey cache)**, so the recording shows a genuinely new call rather than a cached replay of my dry run.
   - Click **Run live assessment (gpt-6-luna)**.
   - Wait about 25–35 s while the stage progress updates.

   *Tip: to keep the video short, you can pre-fill the forms before recording and just scroll through them on camera.*
7. **Results**: pause on each screen.
   - **The recommendation card.** Expected: *Verify first*, with eligibility *Unknown*, funder fit, concept quality, estimated effort (about 37 hours) and the deadline.
   - **Human review required.** It should list the eligibility UNKNOWN and the unsupported claim.
   - **Prioritized next steps.** The first item should be a *Blocker* to confirm the audit requirement before writing, followed by strengthening the claims.
   - **Eligibility checks.** Three PASS rows and one UNKNOWN (independent audit), each with the funder's exact sentence. A reason may show an internal requirement ID such as "(x4)": that is an ID, not a count.
   - **Guideline requirements** (verified quotes), the **scores** with each rationale, and **Worth checking** (advisories).
8. Click **Run details**. Show the model served, the input / cached / output tokens, the per-stage latency, and **Cost: Unpriced**.
9. **Optional (only if time allows), 15 s:** go back to **1 Nonprofit** and change *Audited financial statements available?* to **Yes**. Run again with a fresh call; this is about 30 s and one more paid call. The audit check becomes PASS and the tier changes. In my dry run the scores were above both thresholds, so this would most likely give *Pursue now*. Model scores vary, so say what actually appears.
10. **Optional, free:** open **Discover**, search `literacy` with **Grants.gov** only, and expand **Show source text** on a card to show field-level provenance.
11. **Optional, no model call:** on Overview, use **View a saved live result** → **E12** to show a hard FAIL (budget cap) that a prompt injection could not override. Say it is a saved result from the synthetic evaluation.

### Recording script (about 2½ minutes)

> **[Overview]** "This is GrantFit, an agent I built for small nonprofits. Search tools can find grants; the hard part is deciding which ones are worth a small team's limited writing time. GrantFit checks eligibility against the funder's own words, keeps uncertainty visible, and turns the result into a recommendation with next steps. It never gives a probability of winning."
>
> **[Forms]** "I'll use a fictional organization: a Hartford early-literacy nonprofit with a $640,000 budget. The funder is synthetic: I wrote these guidelines for the demo, and they are not a real grant. Note one deliberate gap: the organization isn't sure whether its latest audit is complete."
>
> **[Run]** "I'll run a live assessment. Two model calls happen here: one extracts requirements from the guidelines, and one rates fit and proposal quality. Everything else is fixed code: quote verification, the eligibility rules, effort and deadline arithmetic, and the recommendation rules."
>
> **[Eligibility checks]** "Each requirement counts only if its exact sentence appears in the guidelines. Three checks pass: 501(c)(3) status, the budget cap and Connecticut. The audit requirement is UNKNOWN, because I didn't provide that fact, and GrantFit doesn't guess. An unknown never becomes a pass."
>
> **[Recommendation and next steps]** "So the recommendation is Verify first, not Pursue. The first next step is a blocker: confirm the audit before writing. Fit and concept scores still appear, but no score can override an eligibility gate. It also flagged 'proven to close the reading gap' as unsupported by the evidence I listed. That's a readiness gap to fix before submitting. The effort estimate, about 37 hours against a 25-hour-a-month team, shows the deadline is feasible."
>
> **[Run details]** "Every run records the model served, tokens and latency per stage. Cost shows as Unpriced because I haven't configured verified per-token rates."
>
> **[Close]** "In my evaluation on 17 synthetic cases I wrote, GrantFit gave an acceptable recommendation in all 17, versus 14 for a single-prompt baseline using the same model, at about 2.25 times the tokens. Those are synthetic cases, so the next step is testing with real nonprofit staff and real guidelines."

### Claims to avoid

- Do not say GrantFit is **accurate in the real world**, **validated**, or **tested with nonprofits**. The evaluation is 17 synthetic cases I wrote, and the second run was not held out.
- Do not say it **saves time**, **raises win rates** or **helps organizations get funded**. None of this has been measured.
- Do not give a **dollar cost** per assessment. Tokens are measured, but cost is unpriced.
- Do not call the Northfield fund, the Riverside organization or the project **real**, and do not show any real beneficiary data.
- Do not present the scores as **probabilities** or as **exact**: they vary between runs (61.2 vs 57.5 on one repeated input).
- Do not say it **reads PDFs or tables**. It reads plain text only.
- Do not show `.env`, the Terminal with environment variables, or browser developer tools.

### Saving the recording for Canvas (optional)

1. On the Mac, press **Shift-Command-5**, choose **Record Selected Portion** around the browser window (or **Record Entire Screen**), and turn on the microphone under **Options**. Click **Record**.
2. Stop with the stop button in the menu bar. The file is saved to the Desktop as a `.mov`.
3. Optional, for a smaller file: open it in **QuickTime Player** → **File → Export As → 1080p**.
4. Rename it, for example `GrantFit_Demo_Navya_Sharma.mov`, and upload it to the Canvas assignment. Check the assignment's file-size limit first. If the file is too large, upload it to Yale Box or Panopto and submit the link instead.
