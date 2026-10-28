"""The AgentCore Runtime entrypoint.

The agent ships as an AgentCore Runtime app: the
``bedrock-agentcore`` SDK's ``BedrockAgentCoreApp`` serves the
Runtime HTTP contract — ``POST /invocations`` and ``GET /ping`` on
port 8080, in an ARM64 container (see the Dockerfile). One invoke
payload names one piece of case work:

- ``{"action": "assemble_summary", "case_id": "CASE-..."}`` — run
  the agent step for a stored case.
- ``{"action": "ingest_and_assemble", "report": {...}}`` — pass a
  report through the validity gate and, if it becomes a case,
  assemble its summary in the same session.

The Runtime session id (``context.session_id``) maps to one
case-working session and is carried into the logs. All domain work
delegates to the same service graph the API and demo use.
"""

from __future__ import annotations

import logging
from datetime import date

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from pv_cases.access import (
    ACT_ASSEMBLE_SUMMARY,
    ACT_INTAKE_REPORT,
    SURFACE_CASE,
    SURFACE_SUMMARY,
    AccessDeniedError,
)
from pv_cases.agent import CitationRefusal, ModelNotConfigured
from pv_cases.models import IntakeReport
from pv_cases.wiring import Services, build_services

log = logging.getLogger("pv_cases.runtime")

app = BedrockAgentCoreApp()
_services: Services | None = None


def services() -> Services:
    global _services
    if _services is None:
        _services = build_services()
    return _services


def handle_invocation(payload: dict, session_id: str | None = None) -> dict:
    """The invocation logic, callable in-process (the local harness
    and the tests use it without HTTP) and by the Runtime entrypoint
    below."""
    svc = services()
    action = payload.get("action", "assemble_summary")
    today = date.today()
    extra = {"session_id": session_id} if session_id else {}
    # Round 10: a payload may carry a principal ({"id", "role"}).
    # A carried principal is authenticated against the access
    # pack and judged for the action's act before any work runs;
    # the denial is recorded like every refusal at this layer.
    # No principal = the trusted local path, as before.
    raw_principal = payload.get("principal")
    if raw_principal is not None:
        act = (
            ACT_INTAKE_REPORT
            if action == "ingest_and_assemble"
            else ACT_ASSEMBLE_SUMMARY
        )
        surface = SURFACE_CASE if action == "ingest_and_assemble" else SURFACE_SUMMARY
        try:
            principal = svc.access.authenticate(
                raw_principal, surface=surface, on=today
            )
            svc.access.require_act(
                principal, act, surface=surface, on=today
            )
        except AccessDeniedError as exc:
            log.warning("access denied for %s: %s", action, exc, extra=extra)
            return {
                "status": "access_denied",
                "action": action,
                "reason": exc.reason,
                "detail": str(exc),
            }
    if action == "assemble_summary":
        case_id = payload["case_id"]
        try:
            summary = svc.assembler.assemble(case_id, today)
        except CitationRefusal as exc:
            log.warning("summary refused for %s: %s", case_id, exc, extra=extra)
            return {"status": "refused", "case_id": case_id, "problems": exc.problems}
        except ModelNotConfigured as exc:
            return {"status": "model_not_configured", "detail": str(exc)}
        return {
            "status": "assembled",
            "case_id": case_id,
            "summary": summary.model_dump(mode="json"),
        }
    if action == "ingest_and_assemble":
        report = IntakeReport.model_validate(payload["report"])
        outcome = svc.intake.ingest(report, today)
        if outcome.follow_up is not None:
            return {
                "status": "follow_up",
                "report_id": report.report_id,
                "missing_criteria": outcome.follow_up.missing_criteria,
            }
        summary = svc.assembler.assemble(outcome.case.case_id, today)
        return {
            "status": "assembled",
            "case_id": outcome.case.case_id,
            "summary": summary.model_dump(mode="json"),
        }
    return {"status": "unknown_action", "action": action}


@app.entrypoint
def invoke(payload: dict, context) -> dict:
    session_id = getattr(context, "session_id", None)
    return handle_invocation(payload or {}, session_id=session_id)
