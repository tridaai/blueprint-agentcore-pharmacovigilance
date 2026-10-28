"""Round 2 API parity: dedup candidates, the human queue, link
decisions, and history views over HTTP."""

import asyncio

import httpx
import pytest

from pv_cases import api as api_module

DEFINITE_LINK = "LINK-CASE-RPT-2026-0005-CASE-RPT-2026-0006"
PROBABLE_LINK = "LINK-CASE-RPT-2026-0101-CASE-RPT-2026-0102"


@pytest.fixture()
def call(services):
    api_module.override_services(services)
    transport = httpx.ASGITransport(app=api_module.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def test_queue_and_assessments(call, full):
    api_module.override_services(full)
    queue = call("GET", "/dedup/queue").json()
    assert {l["link_id"] for l in queue} == {DEFINITE_LINK, PROBABLE_LINK}
    bands = {l["link_id"]: l["band"] for l in queue}
    assert bands[DEFINITE_LINK] == "definite_duplicate"
    assert bands[PROBABLE_LINK] == "probable_duplicate"
    assessments = call("GET", "/dedup/assessments").json()
    assert len(assessments) == 45
    definite = next(a for a in assessments if a["assessment_id"].endswith("0005-CASE-RPT-2026-0006"))
    assert definite["score"] == 95
    assert any(s["name"] == "patient_exact_key" for s in definite["signals"])


def test_confirm_and_reject_over_http(call, full):
    api_module.override_services(full)
    body = call(
        "POST",
        f"/dedup/links/{DEFINITE_LINK}/confirm",
        json={"decided_by": "Dr A. Reviewer", "note": "same fax, twice"},
    ).json()
    assert body["status"] == "confirmed"
    case = call("GET", "/cases/CASE-RPT-2026-0006").json()
    assert case["status"] == "closed_as_duplicate"
    body = call(
        "POST",
        f"/dedup/links/{PROBABLE_LINK}/reject",
        json={"decided_by": "Dr A. Reviewer"},
    ).json()
    assert body["status"] == "rejected"
    assert call("GET", "/dedup/queue").json() == []


def test_decision_errors_over_http(call, full):
    api_module.override_services(full)
    unnamed = call(
        "POST", f"/dedup/links/{DEFINITE_LINK}/confirm", json={"decided_by": "unknown"}
    )
    assert unnamed.status_code == 400
    missing = call(
        "POST", "/dedup/links/LINK-GHOST/confirm", json={"decided_by": "Dr A. Reviewer"}
    )
    assert missing.status_code == 404
    call("POST", f"/dedup/links/{DEFINITE_LINK}/confirm", json={"decided_by": "Dr A. Reviewer"})
    again = call(
        "POST", f"/dedup/links/{DEFINITE_LINK}/reject", json={"decided_by": "Dr B. Other"}
    )
    assert again.status_code == 409


def test_history_views_over_http(call, full):
    api_module.override_services(full)
    view = call("GET", "/cases/CASE-RPT-2026-0005/history").json()
    assert view["patient_key"] == "dob:1988-03-14|f"
    assert view["case_count"] == 5
    assert view["identity_conflicts"] == {"age": ["38", "52"]}
    product = call("GET", "/history/products/NBT-317").json()
    assert product["case_count"] == 6
    assert product["follow_up_task_ids"] == ["FU-RPT-2026-0004"]
    by_key = call("GET", "/history/patient", params={"patient_key": "init:j t|m"}).json()
    assert by_key["follow_up_task_ids"] == ["FU-RPT-2026-0003"]
    assert call("GET", "/cases/CASE-GHOST/history").status_code == 404


def test_scan_endpoint_is_idempotent(call, full):
    api_module.override_services(full)
    body = call("POST", "/dedup/scan").json()
    assert body == {"links_created": []}
