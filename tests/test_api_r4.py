"""Round 4 API parity: assessment reads, the re-assessment act,
the clock board and the reference safety information over HTTP."""

import asyncio

import httpx
import pytest

from pv_cases import api as api_module


@pytest.fixture()
def call(services):
    api_module.override_services(services)
    transport = httpx.ASGITransport(app=api_module.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def test_assessment_reads(call, assessed):
    api_module.override_services(assessed)
    body = call("GET", "/cases/CASE-RPT-2026-0302/assessment").json()
    assert body["seriousness"] == "serious"
    assert body["expectedness"] == "unexpected"
    assert body["clock"]["obligation_class"] == "expedited_15_day"
    assert body["clock"]["deadline"] == "2026-10-31"
    assert body["clock"]["day_zero"] == "2026-10-16"
    fired = {
        f["criterion"] for f in body["criteria"] if f["status"] == "fired"
    }
    assert fired == {"hospitalisation", "other_medically_important"}
    (entry,) = body["event_expectedness"]
    assert entry["basis"] == "more_specific_than_labelled"
    trail = call("GET", "/cases/CASE-RPT-2026-0302/assessments").json()
    assert [a["assessment_id"] for a in trail] == [body["assessment_id"]]
    assert call("GET", "/cases/CASE-GHOST/assessment").status_code == 404
    assert call("GET", "/cases/CASE-GHOST/assessments").status_code == 404


def test_clock_board_read(call, assessed):
    api_module.override_services(assessed)
    board = call("GET", "/clocks", params={"as_of": "2026-10-22"}).json()
    by_case = {e["case_id"]: e for e in board}
    fatal = by_case["CASE-RPT-2026-0301"]
    assert fatal["state"] == "breached"
    assert fatal["days_remaining"] == -1
    assert fatal["deadline"] == "2026-10-21"
    assert by_case["CASE-RPT-2026-0305"]["state"] == "awaiting_assessment"
    assert by_case["CASE-RPT-2026-0305"]["days_remaining"] is None
    assert board[0]["case_id"] == "CASE-RPT-2026-0301"


def test_rsi_reads(call, assessed):
    api_module.override_services(assessed)
    rsi = call("GET", "/assessment/rsi").json()
    assert rsi["version"] == "2026.10.1"
    assert "stand-in" in rsi["note"]
    dermaclear = {e["preferred_term"]: e for e in rsi["products"]["NBT-317"]}
    assert dermaclear["Rash"]["more_specific_terms"] == ["Rash vesicular"]
    assert call("GET", "/assessment/rsi/1999.01.1").status_code == 404


def test_reassessment_act_supersedes(call, coded):
    api_module.override_services(coded)
    case_id = "CASE-RPT-2026-0204"
    before = call("GET", f"/cases/{case_id}/assessment").json()
    assert before["clock"]["obligation_class"] == "awaiting_assessment"
    task_id = "CT-CASE-RPT-2026-0204-E0"
    resolved = call(
        "POST", f"/coding/tasks/{task_id}/resolve",
        json={"coder": "J. Coder", "preferred_term": "Fatigue"},
    )
    assert resolved.status_code == 200
    after = call("POST", f"/cases/{case_id}/assess").json()
    assert after["supersedes_id"] == before["assessment_id"]
    assert after["seriousness"] == "non_serious"
    assert after["change"]["expectedness"] == ["cannot_assess", "expected"]
    assert after["clock"]["day_zero"] == before["clock"]["day_zero"]
    trail = call("GET", f"/cases/{case_id}/assessments").json()
    assert len(trail) == 2
    # Re-assessing over unchanged inputs writes nothing new.
    again = call("POST", f"/cases/{case_id}/assess").json()
    assert again["assessment_id"] == after["assessment_id"]
    assert call("POST", "/cases/CASE-GHOST/assess").status_code == 404
