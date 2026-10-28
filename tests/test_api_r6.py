"""Round 6 API parity: the review pack, the dual-control act
intake, the per-case review position and trail, and the
PHI-minimised review queue over HTTP."""

import asyncio
import json

import httpx
import pytest

from pv_cases import api as api_module

COMPLETE = "CASE-RPT-2026-0304"
NO_OUTCOME = "CASE-RPT-2026-0305"


@pytest.fixture()
def call(services):
    api_module.override_services(services)
    transport = httpx.ASGITransport(app=api_module.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def _post_review(call, case_id, reviewer, role, decision, rationale=""):
    return call(
        "POST",
        f"/cases/{case_id}/reviews",
        json={
            "reviewer": reviewer,
            "role": role,
            "decision": decision,
            "rationale": rationale,
        },
    )


def test_pack_read(call, assessed):
    api_module.override_services(assessed)
    pack = call("GET", "/review/pack").json()
    assert pack["version"] == "2026.10.1"
    assert pack["roles"] == ["medical_reviewer", "safety_reviewer"]
    assert "reject_with_reason" in pack["decisions"]


def test_dual_control_over_http(call, assessed):
    api_module.override_services(assessed)
    resp = _post_review(call, COMPLETE, "Dr A. Rao", "medical_reviewer", "accept")
    assert resp.status_code == 200
    act = resp.json()
    assert act["act_id"] == f"REV-{COMPLETE}-01"
    assert {b["artefact_kind"] for b in act["bindings"]} >= {
        "assessment",
        "coding_decision",
    }

    view = call("GET", f"/cases/{COMPLETE}/review").json()
    assert view["state"] == "awaiting_second_review"
    assert view["missing_roles"] == ["safety_reviewer"]

    resp = _post_review(
        call, COMPLETE, "Dr B. Lindqvist", "safety_reviewer",
        "accept_with_comment", "Clock verified.",
    )
    assert resp.status_code == 200
    view = call("GET", f"/cases/{COMPLETE}/review").json()
    assert view["state"] == "medically_reviewed"
    assert view["accepted_roles"] == {
        "medical_reviewer": "Dr A. Rao",
        "safety_reviewer": "Dr B. Lindqvist",
    }
    trail = call("GET", f"/cases/{COMPLETE}/reviews").json()
    assert len(trail) == 2
    assert all(not t["stale"] for t in trail)


def test_same_person_second_role_is_a_conflict(call, assessed):
    api_module.override_services(assessed)
    assert _post_review(
        call, COMPLETE, "Dr A. Rao", "medical_reviewer", "accept"
    ).status_code == 200
    resp = _post_review(call, COMPLETE, "Dr A. Rao", "safety_reviewer", "accept")
    assert resp.status_code == 409
    assert "dual control" in resp.json()["detail"]


def test_recording_validation(call, assessed):
    api_module.override_services(assessed)
    resp = _post_review(call, COMPLETE, "unknown", "medical_reviewer", "accept")
    assert resp.status_code == 400
    resp = _post_review(call, COMPLETE, "Dr A. Rao", "chief", "accept")
    assert resp.status_code == 400
    resp = _post_review(call, COMPLETE, "Dr A. Rao", "medical_reviewer", "shrug")
    assert resp.status_code == 400
    resp = _post_review(
        call, COMPLETE, "Dr A. Rao", "medical_reviewer", "reject_with_reason"
    )
    assert resp.status_code == 409  # the reason the decision requires is absent
    resp = _post_review(call, "CASE-NOPE", "Dr A. Rao", "medical_reviewer", "accept")
    assert resp.status_code == 404
    resp = call("GET", "/cases/CASE-NOPE/review")
    assert resp.status_code == 404


def test_hold_and_queue_over_http(call, followed):
    api_module.override_services(followed)
    resp = _post_review(
        call, NO_OUTCOME, "Dr B. Lindqvist", "safety_reviewer",
        "reject_with_reason",
        "Seriousness cannot be confirmed until the outcome is on record.",
    )
    assert resp.status_code == 200
    view = call("GET", f"/cases/{NO_OUTCOME}/review").json()
    assert view["state"] == "review_hold"
    assert "outcome is on record" in view["hold_reason"]

    queue = call("GET", "/review/queue", params={"as_of": "2026-10-12"}).json()
    assert queue[0]["case_id"] == NO_OUTCOME
    assert queue[0]["bucket"] == "review_hold"
    blob = json.dumps(queue)
    for phi in ("H.B.", "1988-03-14", "dk@example.net", "R. Patel", "+1-555-0199"):
        assert phi not in blob
