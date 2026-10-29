"""The case API: the safety team's surface over the same service
graph the Runtime entrypoint uses.

This FastAPI app is the local/operations API (intake, case reads,
follow-up queue, evidence and chain verification, summary assembly).
The agent itself is invoked through the AgentCore Runtime app
(``pv_cases.runtime_app``); this API exists so reviewers and tests
can see and drive the case work without an AWS account.
"""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from pv_cases import __version__
from pv_cases.access import (
    ACT_ASSEMBLE_SUMMARY,
    ACT_CODE_CASE,
    ACT_DEDUP_SCAN,
    ACT_INTAKE_REPORT,
    SURFACE_ASSESSMENT,
    SURFACE_CASE,
    SURFACE_CODING,
    SURFACE_DEDUP,
    SURFACE_DENIAL_LOG,
    SURFACE_DICTIONARY,
    SURFACE_EVIDENCE,
    SURFACE_FOLLOW_UP,
    SURFACE_GATEWAY,
    SURFACE_HISTORY,
    SURFACE_MEMORY,
    SURFACE_MODEL_LEDGER,
    SURFACE_OVERSIGHT,
    SURFACE_QUARANTINE,
    SURFACE_REVIEW,
    SURFACE_SEALING,
    SURFACE_SUMMARY,
    AccessDeniedError,
    Principal,
)
from pv_cases.agent import CitationRefusal
from pv_cases.assessment import AssessmentError
from pv_cases.coding import CodingError, CodingTaskClosed
from pv_cases.dedup import DedupDecisionError
from pv_cases.followup import FOLLOW_UP_PACK, FollowUpError
from pv_cases.models import (
    REVIEW_DECISIONS,
    REVIEW_ROLES,
    SUBJECT_EVENT,
    SUBJECT_PRODUCT,
    DictionaryEntry,
    IntakeReport,
    ProductDictionaryEntry,
    is_named_human,
)
from pv_cases.gateway import POLICY_PACK
from pv_cases.memory import MEMORY_PACK, MemoryError
from pv_cases.oversight import OversightError, load_oversight_pack
from pv_cases.quarantine import QUARANTINE_PACK, QuarantineError
from pv_cases.review import REVIEW_PACK, ReviewError
from pv_cases.runtime_app import handle_invocation
from pv_cases.sealing import SEALING_PACK, SealError
from pv_cases.verify import verify_package
from pv_cases.wiring import Services, build_services

app = FastAPI(title="Pharmacovigilance Case Agent — Case API", version=__version__)
_svc: Services | None = None


def svc() -> Services:
    global _svc
    if _svc is None:
        _svc = build_services()
    return _svc


def override_services(services: Services) -> None:
    """Tests and the demo inject their own graph."""
    global _svc
    _svc = services
    import pv_cases.runtime_app as runtime_app

    runtime_app._services = services


# ---------------------------------------------------------------------
# Round 10: access control. A call may carry a principal in the
# X-Principal-Id / X-Principal-Role headers. No headers = the
# trusted local path every earlier round used; headers present =
# the principal is authenticated against the access pack and
# judged before the surface answers (enforcement governs calls
# that carry a principal — see pv_cases.access). A case-scoped
# read by a principal whose role has no case scope is answered
# exactly as a non-existent case is answered: existence hiding.
# ---------------------------------------------------------------------


def _carried_principal(
    request: Request, surface: str, target: str = ""
) -> Principal | None:
    pid = request.headers.get("x-principal-id")
    role = request.headers.get("x-principal-role")
    if pid is None and role is None:
        return None
    try:
        return svc().access.authenticate(
            {"id": pid, "role": role},
            surface=surface,
            target=target,
            on=date.today(),
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))


def _case_read(request: Request, case_id: str, surface: str) -> None:
    """Judge a case-scoped read. A hidden refusal raises this
    API's own no-such-case 404 — the same status and body a
    missing case earns, so the two are indistinguishable."""
    principal = _carried_principal(request, surface, case_id)
    if principal is None:
        return
    try:
        svc().access.require_case_read(
            principal, surface, case_id=case_id, on=date.today()
        )
    except AccessDeniedError:
        raise HTTPException(404, f"no such case: {case_id}")


def _case_hidden(request: Request, case_id: str, surface: str) -> bool:
    """As `_case_read`, for the reads whose missing-case answer
    is an empty list rather than a 404."""
    principal = _carried_principal(request, surface, case_id)
    if principal is None:
        return False
    try:
        svc().access.require_case_read(
            principal, surface, case_id=case_id, on=date.today()
        )
        return False
    except AccessDeniedError:
        return True


def _surface_visible(request: Request, surface: str) -> bool:
    """Judge a list read: visible surfaces answer in full,
    others answer empty — a filtered list never leaks a count.
    The refusal is recorded either way."""
    principal = _carried_principal(request, surface)
    if principal is None:
        return True
    try:
        svc().access.require_read(principal, surface, on=date.today())
        return True
    except AccessDeniedError:
        return False


def _require_surface(request: Request, surface: str) -> None:
    """Judge a singleton (non-case) read: a refusal is named —
    nothing about a case's existence rides on these surfaces."""
    principal = _carried_principal(request, surface)
    if principal is None:
        return
    try:
        svc().access.require_read(principal, surface, on=date.today())
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))


def _require_act(
    request: Request, act: str, surface: str, target: str = ""
) -> Principal | None:
    """Authenticate and judge an act the API gates itself (the
    acts whose services take no principal). Returns the
    principal for endpoints that pass it down."""
    principal = _carried_principal(request, surface, target)
    if principal is None:
        return None
    try:
        svc().access.require_act(
            principal, act, surface=surface, target=target, on=date.today()
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    return principal


def _principal_for(
    request: Request, surface: str, target: str = ""
) -> Principal | None:
    """Authenticate a carried principal for an act whose service
    runs the judge (the retrofitted act methods)."""
    return _carried_principal(request, surface, target)


class IngestResponse(BaseModel):
    report_id: str
    outcome: str  # case | follow_up
    case_id: str | None = None
    missing_criteria: list[str] = []


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> dict:
    svc().store.list_cases()
    return {"status": "ready"}


@app.post("/intake/reports", response_model=IngestResponse)
def ingest_report(report: IntakeReport, request: Request) -> IngestResponse:
    _require_act(request, ACT_INTAKE_REPORT, SURFACE_CASE)
    outcome = svc().intake.ingest(report, date.today())
    if outcome.case is not None:
        return IngestResponse(
            report_id=report.report_id, outcome="case", case_id=outcome.case.case_id
        )
    return IngestResponse(
        report_id=report.report_id,
        outcome="follow_up",
        missing_criteria=outcome.follow_up.missing_criteria,
    )


@app.post("/intake/feed")
def ingest_feed(request: Request) -> dict:
    _require_act(request, ACT_INTAKE_REPORT, SURFACE_CASE)
    outcomes = svc().intake.ingest_feed(svc().feed.fetch_reports(), date.today())
    return {
        "ingested": len(outcomes),
        "cases": [o.case.case_id for o in outcomes if o.case],
        "follow_ups": [o.follow_up.task_id for o in outcomes if o.follow_up],
    }


@app.get("/cases")
def list_cases(request: Request) -> list[dict]:
    if not _surface_visible(request, SURFACE_CASE):
        return []
    return [c.model_dump(mode="json") for c in svc().store.list_cases()]


@app.get("/cases/{case_id}")
def get_case(case_id: str, request: Request) -> dict:
    _case_read(request, case_id, SURFACE_CASE)
    case = svc().store.get_case(case_id)
    if case is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return case.model_dump(mode="json")


@app.get("/follow-ups")
def list_follow_ups(request: Request) -> list[dict]:
    if not _surface_visible(request, SURFACE_FOLLOW_UP):
        return []
    return [t.model_dump(mode="json") for t in svc().store.list_follow_ups()]


@app.get("/cases/{case_id}/evidence")
def list_evidence(case_id: str, request: Request) -> list[dict]:
    _case_read(request, case_id, SURFACE_EVIDENCE)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [i.model_dump(mode="json") for i in svc().store.list_evidence(case_id)]


@app.get("/cases/{case_id}/chain/verify")
def verify_chain(case_id: str, request: Request) -> dict:
    _case_read(request, case_id, SURFACE_EVIDENCE)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return {"case_id": case_id, "chain_valid": svc().chain.verify_chain(case_id)}


@app.post("/cases/{case_id}/assemble")
def assemble(case_id: str, request: Request) -> dict:
    _require_act(request, ACT_ASSEMBLE_SUMMARY, SURFACE_SUMMARY, case_id)
    try:
        return handle_invocation({"action": "assemble_summary", "case_id": case_id})
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")


@app.get("/cases/{case_id}/summaries")
def list_summaries(case_id: str, request: Request) -> list[dict]:
    if _case_hidden(request, case_id, SURFACE_SUMMARY):
        return []
    return [s.model_dump(mode="json") for s in svc().store.list_summaries(case_id)]


# ---------------------------------------------------------------------
# Round 2: de-duplication + history views.
# ---------------------------------------------------------------------


class DecisionRequest(BaseModel):
    decided_by: str
    note: str | None = None


@app.post("/dedup/scan")
def dedup_scan(request: Request) -> dict:
    _require_act(request, ACT_DEDUP_SCAN, SURFACE_DEDUP)
    created = svc().dedup.scan_all(date.today())
    return {"links_created": [l.link_id for l in created]}


@app.get("/dedup/assessments")
def list_dedup_assessments(request: Request) -> list[dict]:
    if not _surface_visible(request, SURFACE_DEDUP):
        return []
    return [a.model_dump(mode="json") for a in svc().store.list_dedup_assessments()]


@app.get("/dedup/links")
def list_dedup_links(request: Request, status: str | None = None) -> list[dict]:
    if not _surface_visible(request, SURFACE_DEDUP):
        return []
    return [l.model_dump(mode="json") for l in svc().store.list_dedup_links(status=status)]


@app.get("/dedup/queue")
def dedup_queue(request: Request) -> list[dict]:
    """The human dedup queue: links awaiting a named decision."""
    if not _surface_visible(request, SURFACE_DEDUP):
        return []
    return [l.model_dump(mode="json") for l in svc().dedup.pending_queue()]


def _decide(
    link_id: str, req: DecisionRequest, confirm: bool, request: Request
) -> dict:
    if not is_named_human(req.decided_by):
        raise HTTPException(400, "a dedup decision needs a named human decider")
    principal = _principal_for(request, SURFACE_DEDUP, link_id)
    try:
        if confirm:
            link = svc().dedup.confirm_link(
                link_id, req.decided_by, date.today(), note=req.note,
                principal=principal,
            )
        else:
            link = svc().dedup.reject_link(
                link_id, req.decided_by, date.today(), note=req.note,
                principal=principal,
            )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such dedup link: {link_id}")
    except DedupDecisionError as exc:
        raise HTTPException(409, str(exc))
    return link.model_dump(mode="json")


@app.post("/dedup/links/{link_id}/confirm")
def confirm_dedup_link(link_id: str, req: DecisionRequest, request: Request) -> dict:
    return _decide(link_id, req, confirm=True, request=request)


@app.post("/dedup/links/{link_id}/reject")
def reject_dedup_link(link_id: str, req: DecisionRequest, request: Request) -> dict:
    return _decide(link_id, req, confirm=False, request=request)


@app.get("/cases/{case_id}/history")
def case_patient_history(case_id: str, request: Request) -> dict:
    _case_read(request, case_id, SURFACE_HISTORY)
    try:
        view = svc().history.patient_history_for_case(case_id, date.today())
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")
    return view.model_dump(mode="json")


@app.get("/history/patient")
def patient_history(patient_key: str, request: Request) -> dict:
    _require_surface(request, SURFACE_HISTORY)
    view = svc().history.build_patient_history(patient_key, date.today())
    return view.model_dump(mode="json")


@app.get("/history/products/{product_code}")
def product_history(product_code: str, request: Request) -> dict:
    _require_surface(request, SURFACE_HISTORY)
    view = svc().history.build_product_history(product_code, date.today())
    return view.model_dump(mode="json")


# ---------------------------------------------------------------------
# Round 3: dictionary coding.
# ---------------------------------------------------------------------


@app.get("/cases/{case_id}/coding")
def case_coding(case_id: str, request: Request) -> list[dict]:
    """The case's current coding decisions (one per subject)."""
    _case_read(request, case_id, SURFACE_CODING)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [d.model_dump(mode="json") for d in svc().coding.current_decisions(case_id)]


@app.get("/cases/{case_id}/coding/decisions")
def case_coding_decisions(case_id: str, request: Request) -> list[dict]:
    """Every coding decision ever taken for the case, including
    superseded ones — the full provenance trail."""
    _case_read(request, case_id, SURFACE_CODING)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [
        d.model_dump(mode="json") for d in svc().store.list_coding_decisions(case_id)
    ]


class RecodeRequest(BaseModel):
    version: str | None = None


@app.post("/cases/{case_id}/coding/recode")
def recode_case(case_id: str, req: RecodeRequest, request: Request) -> dict:
    """(Re)code the case under a dictionary version (default: the
    current one). Returns the decisions this run wrote."""
    _require_act(request, ACT_CODE_CASE, SURFACE_CODING, case_id)
    try:
        written = svc().coding.code_case(case_id, date.today(), version=req.version)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return {"written": [d.model_dump(mode="json") for d in written]}


@app.get("/coding/queue")
def coding_queue(request: Request) -> list[dict]:
    """The coding-review queue: uncoded terms awaiting a coder."""
    if not _surface_visible(request, SURFACE_CODING):
        return []
    return [t.model_dump(mode="json") for t in svc().coding.uncoded_queue()]


class ResolveRequest(BaseModel):
    coder: str
    preferred_term: str | None = None
    product_code: str | None = None


@app.post("/coding/tasks/{task_id}/resolve")
def resolve_coding_task(task_id: str, req: ResolveRequest, request: Request) -> dict:
    if not is_named_human(req.coder):
        raise HTTPException(400, "a coding resolution needs a named human coder")
    principal = _principal_for(request, SURFACE_CODING, task_id)
    try:
        decision = svc().coding.resolve(
            task_id,
            req.coder,
            date.today(),
            preferred_term=req.preferred_term,
            product_code=req.product_code,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such coding task: {task_id}")
    except CodingTaskClosed as exc:
        raise HTTPException(409, str(exc))
    except CodingError as exc:
        raise HTTPException(400, str(exc))
    return decision.model_dump(mode="json")


@app.get("/coding/dictionary")
def current_dictionary(request: Request) -> dict:
    _require_surface(request, SURFACE_DICTIONARY)
    return svc().coding.dictionary(today=date.today()).model_dump(mode="json")


@app.get("/coding/dictionary/versions")
def dictionary_versions(request: Request) -> list[dict]:
    _require_surface(request, SURFACE_DICTIONARY)
    return [
        {
            "version": v.version,
            "created_on": v.created_on.isoformat(),
            "created_by": v.created_by,
            "note": v.note,
            "supersedes_version": v.supersedes_version,
            "event_entries": len(v.event_entries),
            "product_entries": len(v.product_entries),
        }
        for v in svc().coding.list_versions(today=date.today())
    ]


@app.get("/coding/dictionary/{version}")
def dictionary_version(version: str, request: Request) -> dict:
    _require_surface(request, SURFACE_DICTIONARY)
    try:
        return svc().coding.dictionary(version).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, f"no such dictionary version: {version}")


# ---------------------------------------------------------------------
# Round 4: seriousness, expectedness, regulatory clocks.
# ---------------------------------------------------------------------


@app.get("/cases/{case_id}/assessment")
def case_assessment(case_id: str, request: Request) -> dict:
    """The case's current assessment artefact (seriousness,
    expectedness, clock) — the one nothing supersedes."""
    _case_read(request, case_id, SURFACE_ASSESSMENT)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    current = svc().assessment.current_assessment(case_id)
    if current is None:
        raise HTTPException(404, f"case {case_id} has no assessment yet")
    return current.model_dump(mode="json")


@app.get("/cases/{case_id}/assessments")
def case_assessments(case_id: str, request: Request) -> list[dict]:
    """Every assessment artefact for the case, including
    superseded ones — the full verdict trail."""
    _case_read(request, case_id, SURFACE_ASSESSMENT)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [a.model_dump(mode="json") for a in svc().assessment.list_assessments(case_id)]


@app.post("/cases/{case_id}/assess")
def assess_case(case_id: str, request: Request) -> dict:
    """(Re)assess the case from its current coded facts. Over
    unchanged inputs this returns the stored artefact; over
    changed inputs it writes a superseding one. (Re)assessment
    is engine work, not a named human act — a carried principal
    must still be entitled to read the case it re-judges."""
    _case_read(request, case_id, SURFACE_ASSESSMENT)
    try:
        return svc().assessment.assess_case(case_id, date.today()).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except AssessmentError as exc:
        raise HTTPException(409, str(exc))


@app.get("/clocks")
def clock_board(request: Request, as_of: date | None = None) -> list[dict]:
    """The clock board: every assessed case's obligation read at
    ``as_of`` (default today), deadline-bearing clocks first."""
    if not _surface_visible(request, SURFACE_ASSESSMENT):
        return []
    return [
        e.model_dump(mode="json")
        for e in svc().assessment.clock_board(as_of or date.today())
    ]


@app.get("/assessment/rsi")
def current_rsi(request: Request) -> dict:
    _require_surface(request, SURFACE_DICTIONARY)
    return svc().assessment.rsi(today=date.today()).model_dump(mode="json")


@app.get("/assessment/rsi/{version}")
def rsi_version(version: str, request: Request) -> dict:
    _require_surface(request, SURFACE_DICTIONARY)
    try:
        return svc().assessment.rsi(version).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, f"no such RSI version: {version}")


# ---------------------------------------------------------------------
# Round 5: the follow-up workflow.
# ---------------------------------------------------------------------


@app.get("/follow-ups/pack")
def follow_up_pack() -> dict:
    """The versioned follow-up pack: days per missing class and
    the cycle cap every request is raised under."""
    return FOLLOW_UP_PACK


@app.get("/follow-up-tasks/board")
def follow_up_board(request: Request, as_of: date | None = None) -> list[dict]:
    """The follow-up board at ``as_of`` (default today): escalated
    first, then overdue by days, then open by due date — ageing
    derived at read time."""
    if not _surface_visible(request, SURFACE_FOLLOW_UP):
        return []
    return [
        e.model_dump(mode="json")
        for e in svc().followup.board(as_of or date.today())
    ]


@app.get("/follow-up-tasks")
def list_follow_up_tasks(request: Request, as_of: date | None = None) -> list[dict]:
    """Every follow-up request with its derived state at ``as_of``."""
    if not _surface_visible(request, SURFACE_FOLLOW_UP):
        return []
    return [
        v.model_dump(mode="json")
        for v in svc().followup.list_views(as_of or date.today())
    ]


@app.get("/follow-up-tasks/{task_id}")
def get_follow_up_task(
    task_id: str, request: Request, as_of: date | None = None
) -> dict:
    """One request in full: the artefact, its derived state, and
    its acts — the resolution and escalation read. A principal
    without the follow-up surface gets the missing-request 404:
    tasks are work items over case files, and they hide on the
    same terms."""
    principal = _carried_principal(request, SURFACE_FOLLOW_UP, task_id)
    if principal is not None:
        try:
            svc().access.require_read(
                principal, SURFACE_FOLLOW_UP, target=task_id, on=date.today()
            )
        except AccessDeniedError:
            raise HTTPException(404, f"no such follow-up request: {task_id}")
    try:
        return svc().followup.view(task_id, as_of or date.today()).model_dump(
            mode="json"
        )
    except KeyError:
        raise HTTPException(404, f"no such follow-up request: {task_id}")


@app.get("/cases/{case_id}/follow-up-tasks")
def case_follow_up_tasks(
    case_id: str, request: Request, as_of: date | None = None
) -> list[dict]:
    """The follow-up work belonging to one case, including a
    validity request whose follow-up founded it."""
    _case_read(request, case_id, SURFACE_FOLLOW_UP)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [
        v.model_dump(mode="json")
        for v in svc().followup.tasks_for_case(case_id, as_of or date.today())
    ]


@app.post("/follow-up-tasks/{task_id}/reports")
def submit_follow_up_report(
    task_id: str, report: IntakeReport, request: Request
) -> dict:
    """The follow-up intake: answer one request with one report.
    The response records what the report did — resolved the
    request, rolled it a cycle, escalated it, or founded the
    request's case."""
    principal = _principal_for(request, SURFACE_FOLLOW_UP, task_id)
    try:
        return svc().followup.submit_follow_up(
            task_id, report, date.today(), principal=principal
        ).model_dump(mode="json")
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such follow-up request: {task_id}")
    except FollowUpError as exc:
        raise HTTPException(409, str(exc))


# ---------------------------------------------------------------------
# Round 6: medical review — dual control.
# ---------------------------------------------------------------------


@app.get("/review/pack")
def review_pack() -> dict:
    """The versioned review pack: the roles, the decisions, and
    the completion / disagreement / staleness rules every act is
    recorded under."""
    return REVIEW_PACK


@app.get("/review/queue")
def review_queue(request: Request, as_of: date | None = None) -> list[dict]:
    """The medical-review queue at ``as_of`` (default today):
    holds, then cases awaiting a second review, then stale
    reviews, then first reviews. Entries carry case id,
    seriousness, clock position and review state only — no
    patient- or reporter-identifying field."""
    if not _surface_visible(request, SURFACE_REVIEW):
        return []
    return [
        e.model_dump(mode="json")
        for e in svc().review.queue(as_of or date.today())
    ]


@app.get("/cases/{case_id}/review")
def case_review(case_id: str, request: Request) -> dict:
    """The case's review position, derived from its acts: the
    state, the roles holding a current accept, the hold and its
    reason where one stands, and the full act trail with each
    act's staleness labelled."""
    _case_read(request, case_id, SURFACE_REVIEW)
    try:
        return svc().review.review_for_case(case_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")


@app.get("/cases/{case_id}/reviews")
def case_reviews(case_id: str, request: Request) -> list[dict]:
    """Every review act recorded for the case, stale acts
    included and labelled — the trail is never rewritten."""
    _case_read(request, case_id, SURFACE_REVIEW)
    try:
        views = svc().review.list_act_views(case_id)
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")
    return [v.model_dump(mode="json") for v in views]


class ReviewRequest(BaseModel):
    reviewer: str
    role: str
    decision: str
    rationale: str = ""


@app.post("/cases/{case_id}/reviews")
def record_review(case_id: str, req: ReviewRequest, request: Request) -> dict:
    """Record one reviewer's decision on the case's current
    record. Dual control is enforced here in code: the same
    person cannot hold a current act in both roles. A carried
    principal is judged by the access pack first — and must
    hold this act's own role."""
    if not is_named_human(req.reviewer):
        raise HTTPException(400, "a review act needs a named human reviewer")
    if req.role not in REVIEW_ROLES:
        raise HTTPException(400, f"unknown reviewer role: {req.role!r}")
    if req.decision not in REVIEW_DECISIONS:
        raise HTTPException(400, f"unknown review decision: {req.decision!r}")
    principal = _principal_for(request, SURFACE_REVIEW, case_id)
    try:
        act = svc().review.record_review(
            case_id,
            req.reviewer,
            req.role,
            req.decision,
            req.rationale,
            date.today(),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")
    except ReviewError as exc:
        raise HTTPException(409, str(exc))
    return act.model_dump(mode="json")


# ---------------------------------------------------------------------
# Round 7: sealing, export, offline verification.
# ---------------------------------------------------------------------


@app.get("/sealing/pack")
def sealing_pack() -> dict:
    """The versioned sealing pack: the package format, the gate,
    immutability and export rules every seal and export is taken
    under."""
    return SEALING_PACK


class SealRequest(BaseModel):
    sealed_by: str


@app.post("/cases/{case_id}/seal")
def seal_case(case_id: str, req: SealRequest, request: Request) -> dict:
    """Seal one case's file. The case must stand medically
    reviewed over its current record; the refusal names the
    blocker (the missing role, the standing hold, the stale
    acts). A seal is taken once. A carried principal is judged
    by the access pack first — sealing is the medical
    reviewer's act."""
    if not is_named_human(req.sealed_by):
        raise HTTPException(400, "a seal needs a named human sealer")
    principal = _principal_for(request, SURFACE_SEALING, case_id)
    try:
        seal = svc().sealing.seal_case(
            case_id, req.sealed_by, date.today(), principal=principal
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")
    except SealError as exc:
        raise HTTPException(409, str(exc))
    return seal.model_dump(mode="json")


@app.get("/cases/{case_id}/seal")
def get_seal(case_id: str, request: Request) -> dict:
    """The case's seal, where one stands: the bound inventory,
    the chain head, the review bindings and the pack versions."""
    _case_read(request, case_id, SURFACE_SEALING)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    seal = svc().sealing.get_seal(case_id)
    if seal is None:
        raise HTTPException(404, f"case {case_id} is not sealed")
    return seal.model_dump(mode="json")


class ExportRequest(BaseModel):
    exported_by: str


@app.post("/cases/{case_id}/export")
def export_case(case_id: str, req: ExportRequest, request: Request) -> dict:
    """Serve one export package of a sealed case and record the
    serving. An unsealed case has no export — the refusal names
    the seal requirement. A carried principal is judged by the
    access pack first — export is the medical reviewer's act."""
    if not is_named_human(req.exported_by):
        raise HTTPException(400, "an export needs a named human exporter")
    principal = _principal_for(request, SURFACE_SEALING, case_id)
    try:
        record, package = svc().sealing.export_case(
            case_id, req.exported_by, date.today(), principal=principal
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")
    except SealError as exc:
        raise HTTPException(409, str(exc))
    return {"export": record.model_dump(mode="json"), "package": package}


@app.get("/cases/{case_id}/exports")
def list_exports(case_id: str, request: Request) -> list[dict]:
    """Every recorded export serving of the case: who, when, and
    the hash of the exact bytes served."""
    _case_read(request, case_id, SURFACE_SEALING)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [
        e.model_dump(mode="json") for e in svc().sealing.list_exports(case_id)
    ]


@app.get("/cases/{case_id}/export.txt")
def export_text(case_id: str, request: Request):
    """The plain-text rendering of the case's export package —
    deterministic, like the package itself."""
    from fastapi.responses import PlainTextResponse

    _case_read(request, case_id, SURFACE_SEALING)
    seal = svc().sealing.get_seal(case_id)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    if seal is None:
        raise HTTPException(409, f"case {case_id} is not sealed")
    package = svc().sealing.build_package(seal)
    return PlainTextResponse(svc().sealing.render_text(package))


class VerifyRequest(BaseModel):
    package: dict


@app.post("/exports/verify")
def verify_export(req: VerifyRequest) -> dict:
    """The offline verifier over a posted package: every named
    check re-performed from the package alone, no store read."""
    return verify_package(req.package).model_dump(mode="json")


class AdditionRequest(BaseModel):
    proposed_by: str
    kind: str  # event | product
    entry: dict
    note: str = ""


@app.post("/coding/dictionary/additions")
def propose_dictionary_addition(req: AdditionRequest, request: Request) -> dict:
    """A dictionary addition. It lands as a NEW dictionary
    version — the response is that version; nothing already stored
    changes. A carried principal is judged by the access pack
    first — dictionary administration is the case admin's act."""
    if not is_named_human(req.proposed_by):
        raise HTTPException(400, "a dictionary addition needs a named human proposer")
    principal = _principal_for(request, SURFACE_DICTIONARY)
    try:
        if req.kind == SUBJECT_EVENT:
            entry = DictionaryEntry.model_validate(req.entry)
        elif req.kind == SUBJECT_PRODUCT:
            entry = ProductDictionaryEntry.model_validate(req.entry)
        else:
            raise HTTPException(400, f"unknown addition kind: {req.kind!r}")
        version = svc().coding.propose_addition(
            req.proposed_by, date.today(), req.kind, entry, note=req.note,
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except CodingError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:  # entry payload failed validation
        raise HTTPException(400, str(exc))
    return version.model_dump(mode="json")


# ---------------------------------------------------------------------
# Round 8: injection quarantine, Memory port, Gateway + Policy.
# ---------------------------------------------------------------------


@app.get("/quarantine/pack")
def quarantine_pack() -> dict:
    """The versioned screening pack: the fields screened, the
    rules that hold a field, and the release rule."""
    return QUARANTINE_PACK


@app.get("/cases/{case_id}/quarantine")
def case_quarantine(case_id: str, request: Request) -> list[dict]:
    """The case's screened fields: every record with its
    findings, and whether a named release stands over it."""
    _case_read(request, case_id, SURFACE_QUARANTINE)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [
        v.model_dump(mode="json") for v in svc().quarantine.views_for_case(case_id)
    ]


class ReleaseRequest(BaseModel):
    released_by: str
    rationale: str = ""


@app.post("/quarantine/{record_id}/release")
def release_quarantine(record_id: str, req: ReleaseRequest, request: Request) -> dict:
    """A named reviewer's release of one held field. The refusal
    names its blocker: an unnamed reviewer, a missing rationale,
    an already-released record, or a sealed case. A carried
    principal is judged by the access pack first (Round 10,
    RT-07) — the release belongs to the safety scientist and
    the review roles, never to the text's own reporter."""
    if not is_named_human(req.released_by):
        raise HTTPException(400, "a quarantine release needs a named reviewer")
    principal = _principal_for(request, SURFACE_QUARANTINE, record_id)
    try:
        act = svc().quarantine.release(
            record_id, req.released_by, req.rationale, date.today(),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such quarantine record: {record_id}")
    except QuarantineError as exc:
        raise HTTPException(409, str(exc))
    return act.model_dump(mode="json")


@app.get("/memory/pack")
def memory_pack() -> dict:
    """The versioned memory pack: the recall strategies, the
    retention rule and the minimisation rule."""
    return MEMORY_PACK


class RecallRequest(BaseModel):
    strategy: str
    requested_by: str


@app.post("/cases/{case_id}/memory/recall")
def recall_for_case(case_id: str, req: RecallRequest, request: Request) -> dict:
    """One cross-case recall for a case, recorded through the
    Memory port: the matches by reference, the pack version and
    the retention date. A sealed case refuses — its file is
    closed, recall record included. A carried principal is
    judged by the access pack first."""
    if not is_named_human(req.requested_by):
        raise HTTPException(400, "a memory recall needs a named requester")
    principal = _principal_for(request, SURFACE_MEMORY, case_id)
    try:
        retrieval = svc().memory.recall_for_case(
            case_id, req.strategy, req.requested_by, date.today(),
            principal=principal,
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")
    except MemoryError as exc:
        raise HTTPException(409, str(exc))
    return retrieval.model_dump(mode="json")


@app.get("/cases/{case_id}/memory/retrievals")
def case_retrievals(case_id: str, request: Request) -> list[dict]:
    """The case's retrievals still served today: expired ones
    stand as tombstones and are not listed here (the retention
    view counts them)."""
    _case_read(request, case_id, SURFACE_MEMORY)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [
        r.model_dump(mode="json")
        for r in svc().memory.active_retrievals(date.today(), case_id)
    ]


@app.get("/memory/retention")
def memory_retention(request: Request) -> dict:
    """The retention position: retrievals active, and retrievals
    expired as tombstones — kept, counted, never served."""
    _require_surface(request, SURFACE_MEMORY)
    return svc().memory.retention_view(date.today())


class PrecedentRequest(BaseModel):
    verbatim: str
    requested_by: str


@app.post("/memory/coding-precedents")
def coding_precedents(req: PrecedentRequest, request: Request) -> dict:
    """A desk recall: every stored coding decision on one
    verbatim term across the book, by reference. A carried
    principal is judged by the access pack first."""
    if not is_named_human(req.requested_by):
        raise HTTPException(400, "a memory recall needs a named requester")
    principal = _principal_for(request, SURFACE_MEMORY)
    try:
        retrieval = svc().memory.recall_coding_precedents(
            req.verbatim, req.requested_by, date.today(), principal=principal
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except MemoryError as exc:
        raise HTTPException(409, str(exc))
    return retrieval.model_dump(mode="json")


@app.get("/gateway/pack")
def gateway_pack() -> dict:
    """The versioned policy pack: roles, the tool catalogue with
    its PHI families, the permit lists and the rule order."""
    return POLICY_PACK


@app.get("/gateway/tools")
def gateway_tools() -> list[str]:
    """The tools the gateway fronts — exactly the pack's
    catalogue, registered at wiring time."""
    return svc().gateway.tools()


class GatewayInvokeRequest(BaseModel):
    principal: str
    role: str
    tool: str
    case_id: str | None = None
    args: dict = {}


@app.post("/gateway/invoke")
def gateway_invoke(req: GatewayInvokeRequest) -> dict:
    """One tool call through the gateway. The answer is the
    JSON-RPC envelope either way: a result on an allow, and on a
    denial the platform's shape — HTTP 200 in spirit, error code
    -32002 in the body. A denial is never raised as an HTTP
    error here, because that is not what the platform does."""
    args = dict(req.args)
    case_id = args.pop("case_id", req.case_id)
    return svc().gateway.invoke(
        req.principal, req.role, req.tool, date.today(),
        case_id=case_id, **args,
    )


@app.get("/gateway/calls")
def gateway_calls(request: Request) -> list[dict]:
    """Every recorded gateway call, allow and deny alike: the
    record is how a denial (a bland 200 on the wire) is
    surfaced."""
    if not _surface_visible(request, SURFACE_GATEWAY):
        return []
    return [c.model_dump(mode="json") for c in svc().gateway.calls()]


# ---------------------------------------------------------------------
# Round 10: access control — the pack and the denial log.
# ---------------------------------------------------------------------


@app.get("/access/pack")
def access_pack() -> dict:
    """The versioned access pack: the roles, the surfaces each
    may read, the acts each may perform, and the roles every
    act requires."""
    from pv_cases.access import ACCESS_PACK_V1, access_pack_fingerprint

    pack = ACCESS_PACK_V1.model_dump(mode="json")
    pack["fingerprint"] = access_pack_fingerprint()
    return pack


@app.get("/access/denials")
def access_denials(request: Request) -> list[dict]:
    """The denial log: every recorded refusal — principal, role,
    surface, target, reason, pack version. The log is a surface:
    the quality auditor and the case admin read it; a carried
    principal without it is refused by name (no case's existence
    rides on this surface)."""
    _require_surface(request, SURFACE_DENIAL_LOG)
    return [d.model_dump(mode="json") for d in svc().access.denials()]


# ---------------------------------------------------------------------
# Round 11: safety oversight reporting — the pack, generation
# as a named act, the report reads, and re-performance.
# ---------------------------------------------------------------------


class OversightGenerateRequest(BaseModel):
    window_start: date
    window_end: date
    as_of: date
    generated_by: str


@app.get("/oversight/pack")
def oversight_pack(request: Request) -> dict:
    """The oversight pack: the section definitions a figure's
    meaning is versioned by, and the role rule for the
    surface."""
    _require_surface(request, SURFACE_OVERSIGHT)
    return load_oversight_pack().model_dump(mode="json")


@app.post("/oversight/reports")
def oversight_generate(body: OversightGenerateRequest, request: Request) -> dict:
    """Generate a report as a named act. The service runs the
    access judge when the call carries a principal — the
    safety scientist generates, bound to the named generator;
    the refusal is recorded in the denial log and answered
    403 by name (no case's existence rides on this surface).
    A window whose end precedes its start is refused 422."""
    principal = _principal_for(request, SURFACE_OVERSIGHT)
    try:
        report = svc().oversight.generate(
            body.window_start,
            body.window_end,
            body.as_of,
            body.generated_by,
            principal=principal,
            today=date.today(),
        )
    except AccessDeniedError as exc:
        raise HTTPException(403, str(exc))
    except OversightError as exc:
        raise HTTPException(422, str(exc))
    return report.model_dump(mode="json")


@app.get("/oversight/reports")
def oversight_reports(request: Request) -> list[dict]:
    """The reports of record, insert-only. A carried principal
    whose role may not read the oversight surface gets the
    empty list — a filtered list never leaks a count."""
    if not _surface_visible(request, SURFACE_OVERSIGHT):
        return []
    return [r.model_dump(mode="json") for r in svc().oversight.reports()]


@app.get("/oversight/reports/{report_id}")
def oversight_report(report_id: str, request: Request) -> dict:
    _require_surface(request, SURFACE_OVERSIGHT)
    report = svc().oversight.get(report_id)
    if report is None:
        raise HTTPException(404, f"no such report: {report_id}")
    return report.model_dump(mode="json")


@app.get("/oversight/reports/{report_id}/verify")
def oversight_report_verify(report_id: str, request: Request) -> dict:
    """Re-perform a stored report against the record as it
    stands: every figure recomputed by the second path. A
    report over a since-moved record names the figures that
    no longer re-perform — the standing answer is a new
    report superseding it, never an edit."""
    _require_surface(request, SURFACE_OVERSIGHT)
    report = svc().oversight.get(report_id)
    if report is None:
        raise HTTPException(404, f"no such report: {report_id}")
    problems = svc().oversight.verify(report)
    return {
        "report_id": report_id,
        "verified": not problems,
        "problems": problems,
    }


# ---------------------------------------------------------------------
# Round 12: the governed model gateway — the model pack and the
# per-case cost ledger, a case-scoped read under the access pack.
# ---------------------------------------------------------------------


@app.get("/model/pack")
def model_pack(request: Request) -> dict:
    """The versioned model pack: the token measurement rule,
    the reference rates (stated pack rates, never provider
    bills), and the per-case budgets that refuse on the record."""
    _require_surface(request, SURFACE_MODEL_LEDGER)
    from pv_cases.model_gateway import MODEL_PACK, model_pack_fingerprint

    pack = dict(MODEL_PACK)
    pack["fingerprint"] = model_pack_fingerprint()
    return pack


@app.get("/cases/{case_id}/model-ledger")
def case_model_ledger(case_id: str, request: Request) -> dict:
    """One case's model ledger, folded from its insert-only
    call records. A principal whose role has no case scope is
    answered exactly as for a non-existent case (existence
    hiding), and the refusal is on the denial log."""
    _case_read(request, case_id, SURFACE_MODEL_LEDGER)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return svc().model_gateway.ledger_view(case_id)


@app.get("/cases/{case_id}/model-ledger/verify")
def case_model_ledger_verify(case_id: str, request: Request) -> dict:
    """Re-perform one case's ledger: every record's tokens and
    cost re-derived from its stored sizes under the pack, and
    the fold re-accumulated a second way. Problems name the
    record that does not recompute."""
    _case_read(request, case_id, SURFACE_MODEL_LEDGER)
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    from pv_cases.model_gateway import verify_ledger

    problems = [
        p for p in verify_ledger(svc().store) if case_id in p
    ]
    return {
        "case_id": case_id,
        "verified": not problems,
        "problems": problems,
    }


# ---------------------------------------------------------------------
# Round 12: the model gateway's cost ledger — the pack, and a
# case's ledger under the access pack's case-read discipline.
# ---------------------------------------------------------------------


@app.get("/model-ledger/pack")
def model_ledger_pack(request: Request) -> dict:
    """The model pack: the token rule, the reference rates, the
    per-case budgets and the minimisation door's identifier
    lists — the rules every ledger record was judged under,
    with the pack's content fingerprint."""
    _require_surface(request, SURFACE_MODEL_LEDGER)
    from pv_cases.model_gateway import MODEL_PACK, model_pack_fingerprint

    pack = dict(MODEL_PACK)
    pack["fingerprint"] = model_pack_fingerprint()
    return pack


@app.get("/cases/{case_id}/model-ledger")
def case_model_ledger(case_id: str, request: Request) -> dict:
    """One case's model ledger, folded from its records, with
    the verifier's re-performance of those records. A
    case-scoped read: a carried principal whose role has no
    case scope — intake, case admin — is answered exactly as
    a non-existent case is answered, and the denial is
    recorded before the answer. A case that does not exist
    earns the same 404 whether or not it carries a ledger."""
    principal = _carried_principal(request, SURFACE_MODEL_LEDGER, case_id)
    view = None
    if principal is not None:
        try:
            view = svc().model_gateway.ledger_view(
                case_id, principal=principal, on=date.today()
            )
        except AccessDeniedError:
            raise HTTPException(404, f"no such case: {case_id}")
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    if view is None:
        view = svc().model_gateway.ledger_view(case_id)
    from pv_cases.model_gateway import verify_ledger

    problems = [
        p
        for p in verify_ledger(svc().store)
        if p.startswith(f"case {case_id}:")
        or any(
            r.call_id in p
            for r in svc().store.list_model_calls(case_id)
        )
    ]
    return {"case_id": case_id, **view, "verification_problems": problems}
