"""Round 5 API parity: the follow-up board, per-case requests,
the request detail with its acts, and the follow-up intake over
HTTP."""

import asyncio

import httpx
import pytest

from pv_cases import api as api_module
from pv_cases.seed import followup_reports


@pytest.fixture()
def call(services):
    api_module.override_services(services)
    transport = httpx.ASGITransport(app=api_module.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def test_pack_and_board_reads(call, followed):
    api_module.override_services(followed)
    pack = call("GET", "/follow-ups/pack").json()
    assert pack["version"] == "2026.10.1"
    assert pack["max_cycles"] == 3
    assert pack["days_by_class"]["identifiable_reporter"] == 14

    board = call("GET", "/follow-up-tasks/board", params={"as_of": "2026-11-20"}).json()
    by_id = {e["task_id"]: e for e in board}
    overdue = by_id["FUP-RPT-2026-0003"]
    assert overdue["state"] == "overdue"
    assert overdue["days_overdue"] == 25
    assert overdue["missing_fields"] == ["reporter"]
    # Unresolved work leads the board.
    assert board[0]["state"] in ("escalated", "overdue")


def test_case_tasks_and_detail_reads(call, followed):
    api_module.override_services(followed)
    tasks = call(
        "GET",
        "/cases/CASE-RPT-2026-0401/follow-up-tasks",
        params={"as_of": "2026-10-12"},
    ).json()
    assert [t["task"]["task_id"] for t in tasks] == ["FUP-CASE-RPT-2026-0401-A"]
    (item,) = tasks[0]["task"]["items"]
    assert item["field"] == "events.0.outcome"
    assert item["blocks"] == ["death", "hospitalisation", "disability"]
    assert tasks[0]["state"] == "open"

    detail = call("GET", "/follow-up-tasks/FUP-CASE-RPT-2026-0401-A").json()
    assert detail["task"]["source"] == "assessment"
    assert detail["acts"] == []
    assert call("GET", "/follow-up-tasks/FUP-GHOST").status_code == 404
    assert call("GET", "/cases/CASE-GHOST/follow-up-tasks").status_code == 404
    complete = call("GET", "/cases/CASE-RPT-2026-0001/follow-up-tasks").json()
    assert complete == []


def test_follow_up_intake_over_http(call, followed):
    api_module.override_services(followed)
    answers = followup_reports()
    body = answers["RPT-2026-0402"].model_dump(mode="json")
    resp = call(
        "POST", "/follow-up-tasks/FUP-CASE-RPT-2026-0401-A/reports", json=body
    )
    assert resp.status_code == 200
    submission = resp.json()
    assert submission["outcome"] == "resolution"
    assert submission["state"] == "resolved"
    assert submission["follow_up_report_id"] == "RPT-2026-0402"

    detail = call("GET", "/follow-up-tasks/FUP-CASE-RPT-2026-0401-A").json()
    assert detail["state"] == "resolved"
    (act,) = detail["acts"]
    assert act["kind"] == "resolution"
    assert act["follow_up_report_id"] == "RPT-2026-0402"

    # A decided request refuses further reports; a ghost is a 404.
    again = call(
        "POST", "/follow-up-tasks/FUP-CASE-RPT-2026-0401-A/reports", json=body
    )
    assert again.status_code == 409
    ghost = call("POST", "/follow-up-tasks/FUP-GHOST/reports", json=body)
    assert ghost.status_code == 404
