"""The case API over HTTP (ASGI transport): health, intake, case
reads, follow-up queue, evidence, chain verification, assembly."""

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


def test_health_and_readiness(call):
    assert call("GET", "/health").json() == {"status": "ok"}
    assert call("GET", "/readiness").json() == {"status": "ready"}


def test_feed_then_reads(call, fed):
    api_module.override_services(fed)
    cases = call("GET", "/cases").json()
    assert len(cases) == 4
    follow_ups = call("GET", "/follow-ups").json()
    assert len(follow_ups) == 2
    case = call("GET", "/cases/CASE-RPT-2026-0006").json()
    assert case["suspected_duplicate_of"] == "CASE-RPT-2026-0005"


def test_case_not_found(call):
    assert call("GET", "/cases/CASE-GHOST").status_code == 404


def test_evidence_and_chain(call, fed):
    api_module.override_services(fed)
    items = call("GET", "/cases/CASE-RPT-2026-0001/evidence").json()
    assert len(items) == 1 and items[0]["kind"] == "intake_report"
    verdict = call("GET", "/cases/CASE-RPT-2026-0001/chain/verify").json()
    assert verdict == {"case_id": "CASE-RPT-2026-0001", "chain_valid": True}


def test_assemble_via_api(call, fed):
    api_module.override_services(fed)
    body = call("POST", "/cases/CASE-RPT-2026-0002/assemble").json()
    assert body["status"] == "assembled"
    summaries = call("GET", "/cases/CASE-RPT-2026-0002/summaries").json()
    assert len(summaries) == 1
    assert all(s["citations"] for s in summaries[0]["statements"])


def test_ingest_single_report(call):
    from pv_cases.seed import reference_feed

    report = reference_feed()[0]
    body = call("POST", "/intake/reports", json=report.model_dump(mode="json")).json()
    assert body == {
        "report_id": "RPT-2026-0001",
        "outcome": "case",
        "case_id": "CASE-RPT-2026-0001",
        "missing_criteria": [],
    }
