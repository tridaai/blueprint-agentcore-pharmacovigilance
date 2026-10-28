"""The Gateway + Policy ports — Round 8.

The design maps the safety-database, dictionary and history
sources to AgentCore Gateway targets and puts a Cedar policy
pack in front of them: in production, every tool call traverses
the Gateway and Policy enforces on it. Until this round the
tools were in-process reads and the pack existed only as a row
in the architecture table. This module is the enforcing port,
with the platform's documented gotcha handled in code, as the
architecture page has required since Round 1:

- **Policy is a platform artefact, not a prompt.** The pack of
  record is `infra/policies/pv-gateway-policy.cedar` — Cedar
  statements, one per rule. The executable mirror is the data
  pack below; a test grades that the two carry exactly the same
  rule ids, so the artefact and the enforcement cannot drift.
- **Denials are not errors and are not 403s.** AgentCore Policy
  answers a denial as HTTP 200 with a JSON-RPC error body, code
  **-32002**. The gateway returns exactly that envelope; treating
  it as success is the failure mode this shape invites, so the
  envelope is constructed in one place, named, and graded.
- **A denied tool never runs.** Evaluation happens before the
  tool is invoked, and the gateway holds no path from a denial to
  an execution. Graded with a counting double: a denial leaves
  the counter at zero.
- **Every call is recorded, allow or deny.** The record — who,
  which role, which tool, which PHI family, the decision and the
  rule that decided — is insert-only and is written *before* the
  tool runs, so a denied call (whose wire answer is a bland 200)
  is surfaced in the record, and an allowed call that later fails
  still stands recorded as allowed.
- **Evaluation is first-match deny, default deny.** The rule
  list is ordered; the first rule that speaks decides, a forbid
  always speaks before the permit beneath it, and anything no
  rule permits is denied.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from pv_cases.models import (
    GATEWAY_DECISION_ALLOW,
    GATEWAY_DECISION_DENY,
    GATEWAY_DENIAL_CODE,
    GATEWAY_DENIAL_HTTP_STATUS,
    GATEWAY_PHI_CASE_RECORD,
    GATEWAY_PHI_NONE,
    GATEWAY_PHI_PATIENT_HISTORY,
    GatewayCall,
    is_named_human,
)

TOOL_CASE_RECORD = "case_record"
TOOL_PATIENT_HISTORY = "patient_history"
TOOL_DICTIONARY_LOOKUP = "dictionary_lookup"

ROLE_MEDICAL = "medical_reviewer"
ROLE_SAFETY = "safety_reviewer"
ROLE_ANALYST = "pv_analyst"
ROLE_AUDITOR = "external_auditor"

# ---------------------------------------------------------------------
# The policy pack — data, the executable mirror of
# infra/policies/pv-gateway-policy.cedar. Bump the version when
# a role, a tool, a cap or a rule changes; calls carry the
# version they were judged under.
# ---------------------------------------------------------------------

POLICY_PACK = {
    "version": "2026.10.1",
    "roles": [ROLE_MEDICAL, ROLE_SAFETY, ROLE_ANALYST, ROLE_AUDITOR],
    # Every tool the gateway fronts, and the PHI family it
    # touches. A tool the pack does not name does not exist at
    # the gateway — registration refuses it.
    "tools": {
        TOOL_CASE_RECORD: GATEWAY_PHI_CASE_RECORD,
        TOOL_PATIENT_HISTORY: GATEWAY_PHI_PATIENT_HISTORY,
        TOOL_DICTIONARY_LOOKUP: GATEWAY_PHI_NONE,
    },
    # Cross-case patient history is the widest PHI the gateway
    # carries: the medical reviewer role only (the deny rule
    # below speaks before the permit list is consulted).
    "patient_history_roles": [ROLE_MEDICAL],
    "role_tools": {
        ROLE_MEDICAL: [TOOL_CASE_RECORD, TOOL_PATIENT_HISTORY, TOOL_DICTIONARY_LOOKUP],
        ROLE_SAFETY: [TOOL_CASE_RECORD, TOOL_DICTIONARY_LOOKUP],
        ROLE_ANALYST: [TOOL_CASE_RECORD, TOOL_DICTIONARY_LOOKUP],
        ROLE_AUDITOR: [TOOL_DICTIONARY_LOOKUP],
    },
    "evaluation": "first-match deny over the rule list; default deny",
    "denial_shape": "HTTP 200 + JSON-RPC error, code -32002",
    "rules": [
        "deny_unnamed_principal",
        "deny_unknown_role",
        "deny_unknown_tool",
        "deny_auditor_phi",
        "deny_patient_history_role",
        "permit_role_tool",
        "default_deny",
    ],
}

POLICY_PACK_VERSION = POLICY_PACK["version"]


class GatewayError(ValueError):
    """A gateway registration problem: a tool the policy pack
    does not name, or a PHI family that disagrees with the pack."""


def evaluate_policy(
    principal: str, role: str, tool: str
) -> tuple[str, str]:
    """The pack against one would-be call: (decision, rule_id).
    First match decides, in the pack's rule order; the permit
    rule is reached only when no deny above it spoke."""
    if not is_named_human(principal):
        return GATEWAY_DECISION_DENY, "deny_unnamed_principal"
    if role not in POLICY_PACK["roles"]:
        return GATEWAY_DECISION_DENY, "deny_unknown_role"
    if tool not in POLICY_PACK["tools"]:
        return GATEWAY_DECISION_DENY, "deny_unknown_tool"
    phi_family = POLICY_PACK["tools"][tool]
    if role == ROLE_AUDITOR and phi_family != GATEWAY_PHI_NONE:
        return GATEWAY_DECISION_DENY, "deny_auditor_phi"
    if (
        tool == TOOL_PATIENT_HISTORY
        and role not in POLICY_PACK["patient_history_roles"]
    ):
        return GATEWAY_DECISION_DENY, "deny_patient_history_role"
    if tool in POLICY_PACK["role_tools"][role]:
        return GATEWAY_DECISION_ALLOW, "permit_role_tool"
    return GATEWAY_DECISION_DENY, "default_deny"


def denial_envelope(call: GatewayCall) -> dict:
    """The wire shape of a denial: an HTTP 200 carrying a
    JSON-RPC error, code -32002 — never a 403, never a bare
    exception. ``http_status`` rides in the body so in-process
    callers see the same fact the HTTP transport would state."""
    return {
        "jsonrpc": "2.0",
        "id": call.call_id,
        "http_status": GATEWAY_DENIAL_HTTP_STATUS,
        "error": {
            "code": GATEWAY_DENIAL_CODE,
            "message": (
                f"policy denial ({call.rule_id}): role {call.role}"
                f" may not invoke {call.tool}"
            ),
            "data": {
                "rule_id": call.rule_id,
                "tool": call.tool,
                "role": call.role,
                "pack_version": call.pack_version,
            },
        },
    }


class GatewayService:
    """The enforcing port: policy first, recording always, the
    tool only on an allow."""

    def __init__(self, store):
        self.store = store
        self._tools: dict[str, Callable[..., Any]] = {}

    def register_tool(self, name: str, fn: Callable[..., Any]) -> None:
        """Front one tool. The pack is the catalogue: a tool the
        pack does not name is refused here, at wiring time, not
        discovered at call time."""
        if name not in POLICY_PACK["tools"]:
            raise GatewayError(
                f"tool {name!r} is not in the policy pack's catalogue"
            )
        self._tools[name] = fn

    def tools(self) -> list[str]:
        return sorted(self._tools)

    def invoke(
        self,
        principal: str,
        role: str,
        tool: str,
        today: date,
        case_id: str | None = None,
        **args: Any,
    ) -> dict:
        """One call through the gateway. The decision is recorded
        before anything runs; on a denial the tool is never
        invoked and the -32002 envelope is the answer; on an
        allow the tool runs and its result is the answer."""
        decision, rule_id = evaluate_policy(principal, role, tool)
        call = GatewayCall(
            call_id=f"GWC-{len(self.store.list_gateway_calls()) + 1:04d}",
            tool=tool,
            principal=principal,
            role=role,
            case_id=case_id,
            phi_family=POLICY_PACK["tools"].get(tool, GATEWAY_PHI_NONE),
            decision=decision,
            rule_id=rule_id,
            pack_version=POLICY_PACK_VERSION,
            recorded_on=today,
        )
        self.store.add_gateway_call(call)
        if decision == GATEWAY_DECISION_DENY:
            return denial_envelope(call)
        result = self._tools[tool](**args)
        return {"jsonrpc": "2.0", "id": call.call_id, "result": result}

    def calls(self) -> list[GatewayCall]:
        return self.store.list_gateway_calls()
