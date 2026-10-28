"""The AgentCore Runtime contract, exercised over ASGI transport
against the real ``BedrockAgentCoreApp``: ``GET /ping`` is Healthy,
``POST /invocations`` runs the agent step, and an invalid report
comes back as a follow-up — never a case."""

import asyncio

import httpx
import pytest

import pv_cases.runtime_app as runtime_app
from pv_cases.seed import reference_feed

SESSION = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "s" * 40}


@pytest.fixture()
def client(fed):
    runtime_app._services = fed
    transport = httpx.ASGITransport(app=runtime_app.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def test_ping_is_healthy(client):
    response = client("GET", "/ping")
    assert response.status_code == 200
    assert response.json()["status"] == "Healthy"


def test_invocations_assembles_summary(client):
    response = client(
        "POST",
        "/invocations",
        json={"action": "assemble_summary", "case_id": "CASE-RPT-2026-0001"},
        headers=SESSION,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "assembled"
    assert body["summary"]["case_id"] == "CASE-RPT-2026-0001"
    assert body["summary"]["statements"]


def test_invocations_invalid_report_becomes_follow_up(services):
    runtime_app._services = services
    transport = httpx.ASGITransport(app=runtime_app.app)
    report = reference_feed()[2]  # RPT-2026-0003, no identifiable reporter

    async def _call():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(
                "/invocations",
                json={
                    "action": "ingest_and_assemble",
                    "report": report.model_dump(mode="json"),
                },
                headers=SESSION,
            )

    body = asyncio.run(_call()).json()
    assert body["status"] == "follow_up"
    assert body["missing_criteria"] == ["identifiable_reporter"]
    assert services.store.list_cases() == []


def test_invocations_unknown_action(client):
    response = client("POST", "/invocations", json={"action": "teleport"}, headers=SESSION)
    assert response.json()["status"] == "unknown_action"
