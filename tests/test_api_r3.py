"""Round 3 API parity: coding reads, the coding-review queue,
coder resolution acts, dictionary versions and additions over
HTTP."""

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


def test_case_coding_reads(call, coded):
    api_module.override_services(coded)
    body = call("GET", "/cases/CASE-RPT-2026-0203/coding").json()
    by_kind = {d["subject_kind"]: d for d in body}
    assert by_kind["event"]["status"] == "uncoded"
    assert by_kind["event"]["uncoded_reason"] == "no_dictionary_entry"
    assert by_kind["event"]["verbatim"] == "Blistering on hands and feet"
    assert by_kind["product"]["preferred_term"] is None
    assert by_kind["product"]["product_code"] == "NBT-317"
    assert call("GET", "/cases/CASE-GHOST/coding").status_code == 404


def test_queue_and_dictionary_reads(call, coded):
    api_module.override_services(coded)
    queue = call("GET", "/coding/queue").json()
    assert len(queue) == 4
    ambiguous = next(t for t in queue if t["case_id"] == "CASE-RPT-2026-0204")
    assert ambiguous["candidates"] == ["Asthenia", "Fatigue"]
    dictionary = call("GET", "/coding/dictionary").json()
    assert dictionary["version"] == "2026.10.1"
    assert len(dictionary["event_entries"]) == 12
    versions = call("GET", "/coding/dictionary/versions").json()
    assert [v["version"] for v in versions] == ["2026.10.1"]
    assert call("GET", "/coding/dictionary/1999.01.1").status_code == 404


def test_resolve_over_http(call, coded):
    api_module.override_services(coded)
    task_id = "CT-CASE-RPT-2026-0204-E0"
    unnamed = call(
        "POST", f"/coding/tasks/{task_id}/resolve",
        json={"coder": "unknown", "preferred_term": "Fatigue"},
    )
    assert unnamed.status_code == 400
    off_menu = call(
        "POST", f"/coding/tasks/{task_id}/resolve",
        json={"coder": "J. Coder", "preferred_term": "Headache"},
    )
    assert off_menu.status_code == 400  # not one of the candidates
    body = call(
        "POST", f"/coding/tasks/{task_id}/resolve",
        json={"coder": "J. Coder", "preferred_term": "Fatigue"},
    )
    assert body.status_code == 200
    decision = body.json()
    assert decision["match_path"] == "manual"
    assert decision["preferred_term"] == "Fatigue"
    again = call(
        "POST", f"/coding/tasks/{task_id}/resolve",
        json={"coder": "J. Coder", "preferred_term": "Fatigue"},
    )
    assert again.status_code == 409
    assert (
        call(
            "POST", "/coding/tasks/CT-GHOST/resolve",
            json={"coder": "J. Coder", "preferred_term": "Fatigue"},
        ).status_code
        == 404
    )


def test_addition_and_recode_over_http(call, coded):
    api_module.override_services(coded)
    created = call(
        "POST", "/coding/dictionary/additions",
        json={
            "proposed_by": "J. Coder",
            "kind": "event",
            "entry": {
                "verbatim": "Blistering on hands and feet",
                "preferred_term": "Rash vesicular",
                "soc": "Skin and subcutaneous tissue disorders",
            },
        },
    )
    assert created.status_code == 200
    assert created.json()["version"] == "2026.10.2"
    versions = call("GET", "/coding/dictionary/versions").json()
    assert [v["version"] for v in versions] == ["2026.10.1", "2026.10.2"]
    v1 = call("GET", "/coding/dictionary/2026.10.1").json()
    assert len(v1["event_entries"]) == 12  # the old version stands
    recoded = call(
        "POST", "/cases/CASE-RPT-2026-0203/coding/recode", json={}
    ).json()
    assert any(d["status"] == "coded" for d in recoded["written"])
    trail = call("GET", "/cases/CASE-RPT-2026-0203/coding/decisions").json()
    event_trail = [d for d in trail if d["subject_kind"] == "event"]
    assert len(event_trail) == 2  # the uncoded original + its successor
    assert event_trail[0]["status"] == "uncoded"
