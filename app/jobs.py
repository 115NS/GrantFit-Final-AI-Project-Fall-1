"""Background assessment jobs.

Runs the existing pipeline (grantfit.pipeline.run_case) in a worker thread so the UI stays
responsive. Progress is read from the run's real trace spans as each stage completes; nothing
is simulated. Single-user local app: jobs live in memory.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

from grantfit.llm.client import FakeLLMClient, LLMError, PortkeyClient
from grantfit.llm.fake_responses import fake_response
from grantfit.models import AssessmentCase, Recommendation
from grantfit.pipeline import run_case
from grantfit.telemetry import Tracer

GUIDELINE_STAGES = ["guideline_safety_scan", "extract_guidelines", "quote_verification"]
CORE_STAGES = ["eligibility_check", "effort_and_deadline", "assess_fit_concept",
               "output_safety_validation", "priority_decision", "output_validation"]
STAGE_LABELS = {
    "guideline_safety_scan": "Scanning pasted guidelines for manipulation",
    "extract_guidelines": "Extracting requirements from the guidelines",
    "quote_verification": "Verifying each requirement against an exact quote",
    "eligibility_check": "Checking eligibility rules",
    "effort_and_deadline": "Estimating effort and deadline feasibility",
    "assess_fit_concept": "Assessing funder fit and concept quality",
    "output_safety_validation": "Checking claims and output safety",
    "priority_decision": "Deciding the recommendation",
    "output_validation": "Final consistency checks",
}
LLM_STAGES = {"extract_guidelines", "assess_fit_concept"}

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="grantfit-job")
_LOCK = threading.Lock()
JOBS: dict[str, Job] = {}


@dataclass
class Job:
    id: str
    mode: str                         # "live" | "demo"
    has_guidelines: bool
    tracer: Tracer | None = None
    status: str = "running"           # running | done | error
    result: Recommendation | None = None
    error: str | None = None
    stages: list[str] = field(default_factory=list)


def make_client(mode: str, fresh: bool = False):
    if mode == "demo":
        return FakeLLMClient(fake_response)
    return PortkeyClient(fresh=fresh)


def start_job(case: AssessmentCase, mode: str, fresh: bool = False, as_of: date | None = None,
              runs_dir=None) -> str:
    has_guidelines = bool(case.opportunity.source_text and case.opportunity.source_text.strip())
    job = Job(id=uuid.uuid4().hex[:12], mode=mode, has_guidelines=has_guidelines,
              stages=(GUIDELINE_STAGES if has_guidelines else []) + CORE_STAGES)
    with _LOCK:
        JOBS[job.id] = job
    try:
        client = make_client(mode, fresh)
    except LLMError as exc:          # e.g. missing API key; message is already sanitized
        job.status, job.error = "error", str(exc)
        return job.id
    job.tracer = Tracer(runs_dir=runs_dir)
    _EXECUTOR.submit(_run, job, case, client, as_of)
    return job.id


def _run(job: Job, case: AssessmentCase, client, as_of: date | None) -> None:
    try:
        job.result = run_case(case, client, as_of=as_of, tracer=job.tracer)
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, never swallowed
        job.error = f"{type(exc).__name__}: {str(exc)[:400]}"
        job.status = "error"


def get_job(job_id: str | None) -> Job | None:
    with _LOCK:
        return JOBS.get(job_id or "")


def progress(job: Job) -> list[tuple[str, str, str]]:
    """[(stage, label, state)] with state in done | running | pending | failed, from real spans."""
    spans = list(job.tracer.spans) if job.tracer else []
    finished = {}
    for s in spans:
        finished[s.stage] = s.status
    rows, running_marked = [], False
    for stage in job.stages:
        if stage in finished:
            state = "failed" if finished[stage] in ("error", "degraded") else "done"
        elif job.status == "running" and not running_marked:
            state, running_marked = "running", True
        else:
            state = "pending"
        rows.append((stage, STAGE_LABELS.get(stage, stage), state))
    return rows


# ---------------------------------------------------------------------------
# Discovery jobs (live search can take up to a minute with web search)
# ---------------------------------------------------------------------------

DISCOVERY_STEP_LABELS = {
    "grants_gov_search": "Searching Grants.gov (official federal listings)",
    "grants_gov_details": "Retrieving official listing details",
    "web_search": "Web search for foundation programs (gpt-6-luna, about 30–60 s)",
    "retrieve_pages": "Retrieving each cited funder page",
    "fetch_url": "Retrieving the page",
    "dedupe": "Checking evidence and removing duplicates",
}


@dataclass
class DiscoveryJob:
    id: str
    steps: list[str]
    done: list[str] = field(default_factory=list)
    status: str = "running"
    result: object = None
    error: str | None = None


DISCOVERY_JOBS: dict[str, DiscoveryJob] = {}


def start_discovery_job(query, url: str | None = None, discover_fn=None, url_fn=None) -> str:
    from grantfit.discovery.service import discover, fetch_url_opportunity

    if url:
        steps = ["fetch_url", "dedupe"]
    else:
        steps = (["grants_gov_search", "grants_gov_details"] if query.use_grants_gov else []) + \
                (["web_search", "retrieve_pages"] if query.use_web_search else []) + ["dedupe"]
    job = DiscoveryJob(id=uuid.uuid4().hex[:12], steps=steps)
    with _LOCK:
        DISCOVERY_JOBS[job.id] = job

    def progress(step: str):
        if step not in job.done:
            # A new step starting means the previous ones are complete.
            idx = job.steps.index(step) if step in job.steps else len(job.done)
            job.done = job.steps[:idx]

    def run():
        try:
            if url:
                progress("fetch_url")
                job.result = (url_fn or fetch_url_opportunity)(url, query)
            else:
                job.result = (discover_fn or discover)(query, tracer=Tracer(run_id="discovery-" + job.id),
                                                       progress=progress)
            job.done = list(job.steps)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            job.status = "error"

    _EXECUTOR.submit(run)
    return job.id


def get_discovery_job(job_id: str | None) -> DiscoveryJob | None:
    with _LOCK:
        return DISCOVERY_JOBS.get(job_id or "")


def discovery_progress(job: DiscoveryJob) -> list[tuple[str, str, str]]:
    rows, running = [], False
    for step in job.steps:
        if step in job.done:
            state = "done"
        elif job.status == "running" and not running:
            state, running = "running", True
        else:
            state = "failed" if job.status == "error" and not running else "pending"
        rows.append((step, DISCOVERY_STEP_LABELS.get(step, step), state))
    return rows
