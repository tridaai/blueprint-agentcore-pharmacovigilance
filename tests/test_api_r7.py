"""Round 7 API parity: the sealing pack, the seal act, the seal
read, the recorded export serving, the text rendering, and the
offline verifier over HTTP."""

import asyncio

import httpx
import pytest

from pv_cases import api as api_module

COMPLETE = "CASE-RPT-2026-0304"
MEDICAL = "Dr A. Rao"
SAFETY = "Dr B. Lindqvist"
SEALER = "PV Operations Lead"


@pytest.fixture()
def call(services):
    api_module.override_services(services)
    transport = httpx.ASGITransport(app=api_module.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def _review(call, reviewer, role):
    return call(
        "POST",
        f"/cases/{COMPLETE}/reviews",
        json={
            "reviewer": reviewer,
            "role": role,
            "decision": "accept",
            "rationale": "",
        },
    )


def _dual_review(call):
    # Ingest already coded and assessed the case; assemble the
    # summary, then the two accepts.
    assert call("POST", f"/cases/{COMPLETE}/assemble").status_code == 200
    assert _review(call, MEDICAL, "medical_reviewer").status_code == 200
    assert _review(call, SAFETY, "safety_reviewer").status_code == 200
    view = call("GET", f"/cases/{COMPLETE}/review").json()
    assert view["state"] == "medically_reviewed"


def test_sealing_pack_read(call, assessed):
    api_module.override_services(assessed)
    pack = call("GET", "/sealing/pack").json()
    assert pack["version"] == "2026.10.1"
    assert pack["package_format"] == "pv-case-export/1"
    assert "medically_reviewed" in pack["gate_rule"]
    assert "no unseal" in pack["immutability_rule"]


def test_seal_refused_before_dual_review(call, assessed):
    api_module.override_services(assessed)
    resp = call("POST", f"/cases/{COMPLETE}/seal", json={"sealed_by": SEALER})
    assert resp.status_code == 409
    assert "medical_reviewer" in resp.json()["detail"]


def test_seal_needs_a_named_sealer(call, assessed):
    api_module.override_services(assessed)
    _dual_review(call)
    resp = call("POST", f"/cases/{COMPLETE}/seal", json={"sealed_by": " "})
    assert resp.status_code == 400


def test_seal_export_verify_round_trip(call, assessed):
    api_module.override_services(assessed)
    _dual_review(call)
    resp = call("POST", f"/cases/{COMPLETE}/seal", json={"sealed_by": SEALER})
    assert resp.status_code == 200, resp.text
    seal = resp.json()
    assert seal["review_state"] == "medically_reviewed"
    assert seal["inventory"]
    assert seal["accepted_acts"] == {
        "medical_reviewer": f"REV-{COMPLETE}-01",
        "safety_reviewer": f"REV-{COMPLETE}-02",
    }

    resp = call("GET", f"/cases/{COMPLETE}/seal")
    assert resp.status_code == 200
    assert resp.json()["seal_id"] == seal["seal_id"]

    resp = call("POST", f"/cases/{COMPLETE}/export", json={"exported_by": SEALER})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["export"]["seal_id"] == seal["seal_id"]
    package = body["package"]
    assert package["package_format"] == "pv-case-export/1"

    exports = call("GET", f"/cases/{COMPLETE}/exports").json()
    assert len(exports) == 1
    assert exports[0]["exported_by"] == SEALER

    report = call("POST", "/exports/verify", json={"package": package}).json()
    assert report["verdict"] == "pass"
    assert report["package_hash"] == body["export"]["package_hash"]
    assert all(c["passed"] for c in report["checks"])

    text = call("GET", f"/cases/{COMPLETE}/export.txt")
    assert text.status_code == 200
    assert "PV CASE EXPORT" in text.text
    assert "Nothing in it has been submitted to a regulator" in text.text


def test_export_refused_for_an_unsealed_case(call, assessed):
    api_module.override_services(assessed)
    _dual_review(call)
    resp = call("POST", f"/cases/{COMPLETE}/export", json={"exported_by": SEALER})
    assert resp.status_code == 409
    assert "not sealed" in resp.json()["detail"]


def test_sealed_case_refuses_further_review_over_http(call, assessed):
    api_module.override_services(assessed)
    _dual_review(call)
    assert call(
        "POST", f"/cases/{COMPLETE}/seal", json={"sealed_by": SEALER}
    ).status_code == 200
    resp = _review(call, MEDICAL, "medical_reviewer")
    assert resp.status_code == 409
    assert "sealed" in resp.json()["detail"]


def test_verify_endpoint_fails_a_tampered_package(call, assessed):
    api_module.override_services(assessed)
    _dual_review(call)
    call("POST", f"/cases/{COMPLETE}/seal", json={"sealed_by": SEALER})
    package = call(
        "POST", f"/cases/{COMPLETE}/export", json={"exported_by": SEALER}
    ).json()["package"]
    for entry in package["artefacts"]:
        if entry["kind"] == "assessment":
            entry["payload"]["clock"]["deadline"] = "2026-12-31"
    report = call("POST", "/exports/verify", json={"package": package}).json()
    assert report["verdict"] == "fail"
    failed = {c["name"] for c in report["checks"] if not c["passed"]}
    assert "artefact_hashes" in failed
