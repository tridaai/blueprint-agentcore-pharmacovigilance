"""Round 8 gates: the Gateway + Policy ports.

Every tool call is judged by the policy pack before the tool
runs. A denial is the platform's shape — HTTP 200 carrying a
JSON-RPC error with code -32002, never a 403 — and the denied
tool never executes, graded with a counting double. Every call,
allow or deny, is recorded with the rule that decided it. The
Cedar artefact in infra/ and the executable pack in code carry
exactly the same rule ids.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pv_cases.gateway import (
    POLICY_PACK,
    GatewayError,
    GatewayService,
    evaluate_policy,
)
from pv_cases.store import InMemoryCaseStore

DAY = date(2026, 10, 12)
MEDICAL = "Dr A. Rao"
SAFETY = "Dr B. Lindqvist"
ANALYST = "P. Analyst"
AUDITOR = "Q. Auditor"


def counting_gateway(store):
    """A gateway over one counting double: the counter is how
    the grade knows whether the tool behind a call executed."""
    gateway = GatewayService(store)
    counter = {"n": 0}

    def tool(case_id=None):
        counter["n"] += 1
        return {"case_id": case_id}

    gateway.register_tool("case_record", tool)
    gateway.register_tool("patient_history", tool)
    gateway.register_tool("dictionary_lookup", lambda verbatim: {"v": verbatim})
    return gateway, counter


def test_denied_tool_never_executes(services):
    gateway, counter = counting_gateway(services.store)
    envelope = gateway.invoke(
        AUDITOR, "external_auditor", "case_record", DAY,
        case_id="CASE-RPT-2026-0001",
    )
    assert counter["n"] == 0  # the tool behind the denial never ran
    assert envelope["http_status"] == 200
    assert envelope["error"]["code"] == -32002
    assert envelope["error"]["data"]["rule_id"] == "deny_auditor_phi"
    # And the denial stands recorded on the same terms as an allow.
    calls = gateway.calls()
    assert len(calls) == 1
    assert calls[0].decision == "deny"
    assert calls[0].phi_family == "case_record"


def test_allowed_tool_executes_and_is_recorded(services):
    gateway, counter = counting_gateway(services.store)
    envelope = gateway.invoke(
        MEDICAL, "medical_reviewer", "case_record", DAY,
        case_id="CASE-RPT-2026-0001",
    )
    assert counter["n"] == 1
    assert envelope["result"] == {"case_id": "CASE-RPT-2026-0001"}
    assert "error" not in envelope
    call = gateway.calls()[0]
    assert call.decision == "allow" and call.rule_id == "permit_role_tool"


def test_policy_rules():
    cases = [
        # (principal, role, tool, decision, rule)
        (" ", "medical_reviewer", "case_record", "deny", "deny_unnamed_principal"),
        (MEDICAL, "superuser", "case_record", "deny", "deny_unknown_role"),
        (MEDICAL, "medical_reviewer", "email_send", "deny", "deny_unknown_tool"),
        (AUDITOR, "external_auditor", "case_record", "deny", "deny_auditor_phi"),
        (AUDITOR, "external_auditor", "patient_history", "deny", "deny_auditor_phi"),
        (SAFETY, "safety_reviewer", "patient_history", "deny", "deny_patient_history_role"),
        (ANALYST, "pv_analyst", "patient_history", "deny", "deny_patient_history_role"),
        (AUDITOR, "external_auditor", "dictionary_lookup", "allow", "permit_role_tool"),
        (MEDICAL, "medical_reviewer", "patient_history", "allow", "permit_role_tool"),
        (SAFETY, "safety_reviewer", "case_record", "allow", "permit_role_tool"),
        (ANALYST, "pv_analyst", "dictionary_lookup", "allow", "permit_role_tool"),
    ]
    for principal, role, tool, decision, rule_id in cases:
        assert evaluate_policy(principal, role, tool) == (decision, rule_id), (
            principal,
            role,
            tool,
        )


def test_default_deny_is_the_backstop(services, monkeypatch):
    # With the permit list emptied, a call no rule speaks for is
    # denied by the last rule — the pack's stated default.
    import pv_cases.gateway as gateway_module

    monkeypatch.setitem(
        gateway_module.POLICY_PACK["role_tools"], "medical_reviewer", []
    )
    assert evaluate_policy(MEDICAL, "medical_reviewer", "case_record") == (
        "deny",
        "default_deny",
    )


def test_unregistered_tool_is_refused_at_wiring(services):
    gateway = GatewayService(services.store)
    with pytest.raises(GatewayError):
        gateway.register_tool("email_send", lambda: None)


def test_cedar_artefact_and_code_pack_agree():
    cedar = (
        Path(__file__).resolve().parents[1]
        / "infra"
        / "policies"
        / "pv-gateway-policy.cedar"
    ).read_text(encoding="utf-8")
    for rule_id in POLICY_PACK["rules"]:
        assert rule_id in cedar, rule_id
    for role in POLICY_PACK["roles"]:
        assert role in cedar, role
    for tool in POLICY_PACK["tools"]:
        assert tool in cedar, tool
    assert POLICY_PACK["version"] in cedar


def test_wired_tools_end_to_end(fed):
    envelope = fed.gateway.invoke(
        MEDICAL, "medical_reviewer", "case_record", DAY,
        case_id="CASE-RPT-2026-0001",
    )
    assert envelope["result"]["report_id"] == "RPT-2026-0001"
    envelope = fed.gateway.invoke(
        MEDICAL, "medical_reviewer", "dictionary_lookup", DAY,
        verbatim="Severe headache",
    )
    assert envelope["result"]["status"] == "coded"
    assert envelope["result"]["preferred_term"] == "Headache"
    envelope = fed.gateway.invoke(
        MEDICAL, "medical_reviewer", "patient_history", DAY,
        case_id="CASE-RPT-2026-0005",
    )
    assert envelope["result"]["case_count"] >= 2
    # All three calls stand recorded, in order.
    assert [c.decision for c in fed.gateway.calls()] == ["allow"] * 3
    assert fed.gateway.tools() == [
        "case_record",
        "dictionary_lookup",
        "patient_history",
    ]


def test_gateway_calls_are_insert_only(services):
    for store in (services.store, InMemoryCaseStore()):
        for verb in ("update_gateway_call", "delete_gateway_call"):
            assert not hasattr(store, verb), verb
