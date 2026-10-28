// Northbridge Therapeutics — pharmacovigilance Gateway policy pack
// Version: 2026.10.1
//
// The platform artefact for the Gateway port (Round 8). In
// production this pack is deployed to Amazon Bedrock AgentCore
// Policy in ENFORCE mode and judges every tool call the Gateway
// carries; the executable mirror is the data pack in
// src/pv_cases/gateway.py, and the test suite grades that the two
// carry exactly the same rule ids, roles and tool catalogue, so
// the artefact and the enforcement cannot drift apart.
//
// Evaluation is first-match deny over the rules below, in the
// order listed, with a default deny at the bottom. A denial is
// answered by the platform as HTTP 200 with a JSON-RPC error
// body, code -32002 — never a 403 — and the tool behind the call
// does not execute.
//
// Principals are named humans acting in one of the pack's roles:
// medical_reviewer, safety_reviewer, pv_analyst, external_auditor.
// Tools and the PHI family each touches:
//   case_record       -> case_record      (one case's record)
//   patient_history   -> patient_history  (cross-case patient PHI)
//   dictionary_lookup -> none

// deny_unnamed_principal: an unnamed or placeholder principal
// is denied every tool.
forbid (
    principal,
    action,
    resource
) when {
    principal has name == false || principal.name == ""
};

// deny_unknown_role: a role the pack does not name is denied
// every tool.
forbid (
    principal,
    action,
    resource
) when {
    !(principal.role in [
        "medical_reviewer", "safety_reviewer",
        "pv_analyst", "external_auditor"
    ])
};

// deny_unknown_tool: a tool the pack does not catalogue is
// denied to everyone.
forbid (
    principal,
    action,
    resource
) when {
    !(action in [
        Action::"case_record", Action::"patient_history",
        Action::"dictionary_lookup"
    ])
};

// deny_auditor_phi: the external auditor role reaches no tool
// that touches PHI, of either family.
forbid (
    principal,
    action == Action::"case_record",
    resource
) when { principal.role == "external_auditor" };
forbid (
    principal,
    action == Action::"patient_history",
    resource
) when { principal.role == "external_auditor" };

// deny_patient_history_role: cross-case patient history is the
// medical reviewer role only.
forbid (
    principal,
    action == Action::"patient_history",
    resource
) when {
    principal.role != "medical_reviewer"
};

// permit_role_tool: the permit list — each role reaches exactly
// the tools named for it here, and nothing else.
permit (
    principal,
    action == Action::"case_record",
    resource
) when {
    principal.role in [
        "medical_reviewer", "safety_reviewer", "pv_analyst"
    ]
};
permit (
    principal,
    action == Action::"patient_history",
    resource
) when { principal.role == "medical_reviewer" };
permit (
    principal,
    action == Action::"dictionary_lookup",
    resource
) when {
    principal.role in [
        "medical_reviewer", "safety_reviewer",
        "pv_analyst", "external_auditor"
    ]
};

// default_deny: anything no statement above permits is denied.
// (Cedar's own default is deny; the rule is named here because
// the pack's evaluation order is part of its contract.)
