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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pv_cases import __version__
from pv_cases.agent import CitationRefusal
from pv_cases.dedup import DedupDecisionError
from pv_cases.models import IntakeReport, is_named_human
from pv_cases.runtime_app import handle_invocation
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
def ingest_report(report: IntakeReport) -> IngestResponse:
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
def ingest_feed() -> dict:
    outcomes = svc().intake.ingest_feed(svc().feed.fetch_reports(), date.today())
    return {
        "ingested": len(outcomes),
        "cases": [o.case.case_id for o in outcomes if o.case],
        "follow_ups": [o.follow_up.task_id for o in outcomes if o.follow_up],
    }


@app.get("/cases")
def list_cases() -> list[dict]:
    return [c.model_dump(mode="json") for c in svc().store.list_cases()]


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> dict:
    case = svc().store.get_case(case_id)
    if case is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return case.model_dump(mode="json")


@app.get("/follow-ups")
def list_follow_ups() -> list[dict]:
    return [t.model_dump(mode="json") for t in svc().store.list_follow_ups()]


@app.get("/cases/{case_id}/evidence")
def list_evidence(case_id: str) -> list[dict]:
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return [i.model_dump(mode="json") for i in svc().store.list_evidence(case_id)]


@app.get("/cases/{case_id}/chain/verify")
def verify_chain(case_id: str) -> dict:
    if svc().store.get_case(case_id) is None:
        raise HTTPException(404, f"no such case: {case_id}")
    return {"case_id": case_id, "chain_valid": svc().chain.verify_chain(case_id)}


@app.post("/cases/{case_id}/assemble")
def assemble(case_id: str) -> dict:
    try:
        return handle_invocation({"action": "assemble_summary", "case_id": case_id})
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")


@app.get("/cases/{case_id}/summaries")
def list_summaries(case_id: str) -> list[dict]:
    return [s.model_dump(mode="json") for s in svc().store.list_summaries(case_id)]


# ---------------------------------------------------------------------
# Round 2: de-duplication + history views.
# ---------------------------------------------------------------------


class DecisionRequest(BaseModel):
    decided_by: str
    note: str | None = None


@app.post("/dedup/scan")
def dedup_scan() -> dict:
    created = svc().dedup.scan_all(date.today())
    return {"links_created": [l.link_id for l in created]}


@app.get("/dedup/assessments")
def list_dedup_assessments() -> list[dict]:
    return [a.model_dump(mode="json") for a in svc().store.list_dedup_assessments()]


@app.get("/dedup/links")
def list_dedup_links(status: str | None = None) -> list[dict]:
    return [l.model_dump(mode="json") for l in svc().store.list_dedup_links(status=status)]


@app.get("/dedup/queue")
def dedup_queue() -> list[dict]:
    """The human dedup queue: links awaiting a named decision."""
    return [l.model_dump(mode="json") for l in svc().dedup.pending_queue()]


def _decide(link_id: str, req: DecisionRequest, confirm: bool) -> dict:
    if not is_named_human(req.decided_by):
        raise HTTPException(400, "a dedup decision needs a named human decider")
    try:
        if confirm:
            link = svc().dedup.confirm_link(
                link_id, req.decided_by, date.today(), note=req.note
            )
        else:
            link = svc().dedup.reject_link(
                link_id, req.decided_by, date.today(), note=req.note
            )
    except KeyError:
        raise HTTPException(404, f"no such dedup link: {link_id}")
    except DedupDecisionError as exc:
        raise HTTPException(409, str(exc))
    return link.model_dump(mode="json")


@app.post("/dedup/links/{link_id}/confirm")
def confirm_dedup_link(link_id: str, req: DecisionRequest) -> dict:
    return _decide(link_id, req, confirm=True)


@app.post("/dedup/links/{link_id}/reject")
def reject_dedup_link(link_id: str, req: DecisionRequest) -> dict:
    return _decide(link_id, req, confirm=False)


@app.get("/cases/{case_id}/history")
def case_patient_history(case_id: str) -> dict:
    try:
        view = svc().history.patient_history_for_case(case_id, date.today())
    except KeyError:
        raise HTTPException(404, f"no such case: {case_id}")
    return view.model_dump(mode="json")


@app.get("/history/patient")
def patient_history(patient_key: str) -> dict:
    view = svc().history.build_patient_history(patient_key, date.today())
    return view.model_dump(mode="json")


@app.get("/history/products/{product_code}")
def product_history(product_code: str) -> dict:
    view = svc().history.build_product_history(product_code, date.today())
    return view.model_dump(mode="json")
