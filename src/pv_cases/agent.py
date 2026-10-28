"""The agent step, in Strands shape, behind a model port.

The blueprint's thesis is the AgentCore-native path: in production
this module's loop is the Strands Agents SDK ``Agent`` (BedrockModel)
running inside the AgentCore Runtime entrypoint. The loop here keeps
the same shape — a system prompt, a set of plain-function tools, and
a model behind a port — so the swap is the model/tool wiring, not a
rewrite.

R1's agent work is deliberately narrow and real: given a valid case,
assemble its summary record from stored evidence. Every statement
the model proposes carries citations; a citation resolver in code
checks each one against the evidence payloads, and a summary with an
unresolvable citation is **refused**, never stored.

Model profiles:
- ``offline`` (default): a deterministic composer. No key, no
  network; used by tests, evals and the demo.
- ``bedrock``: the production profile — a Bedrock Converse call via
  the Strands ``BedrockModel``. It refuses loudly until configured
  (region + model id) and is stub-tested in R1; no live AWS call is
  made by the test or eval gates.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Protocol

from pv_cases.evidence import EvidenceChain
from pv_cases.models import CaseSummary, Citation, SummaryStatement


class ModelNotConfigured(RuntimeError):
    pass


class CitationRefusal(RuntimeError):
    """Raised when a proposed summary cites evidence that does not
    resolve. The summary is refused as a whole — a partially cited
    summary is not a summary."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("unresolvable citations: " + "; ".join(problems))


class ModelPort(Protocol):
    profile: str

    def propose_summary(self, facts: list[dict[str, Any]]) -> list[SummaryStatement]:
        """Propose summary statements over the assembled facts. Each
        fact is a dict with ``field`` (its citation path) and
        ``value``; statements cite facts by evidence item + field."""
        ...


def _dig(payload: dict, field: str) -> Any:
    """Resolve a dotted field path inside an evidence payload.
    Supports ``a.b.0.c`` list indices. Returns ``None`` when absent."""
    cur: Any = payload
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


class CitationResolver:
    """Checks citations against the case's stored evidence payloads."""

    def __init__(self, chain: EvidenceChain):
        self.chain = chain

    def problems(self, case_id: str, statements: list[SummaryStatement]) -> list[str]:
        items = {i.item_id: i for i in self.chain.store.list_evidence(case_id)}
        problems: list[str] = []
        for stmt in statements:
            if not stmt.citations:
                problems.append(f"statement has no citation: {stmt.text[:40]!r}")
                continue
            for cit in stmt.citations:
                item = items.get(cit.evidence_item_id)
                if item is None:
                    problems.append(f"unknown evidence item {cit.evidence_item_id}")
                    continue
                payload = self.chain.payload_of(item)
                if _dig(payload, cit.field) is None:
                    problems.append(
                        f"field {cit.field!r} absent in {cit.evidence_item_id}"
                    )
        return problems


class OfflineComposer:
    """The deterministic offline profile: builds statements directly
    from the assembled facts, each citing the fact it came from."""

    profile = "offline"

    def propose_summary(self, facts: list[dict[str, Any]]) -> list[SummaryStatement]:
        statements: list[SummaryStatement] = []
        for fact in facts:
            text = _render_fact(fact)
            if text is None:
                continue
            statements.append(
                SummaryStatement(
                    text=text,
                    citations=[
                        Citation(evidence_item_id=fact["item_id"], field=fact["field"])
                    ],
                )
            )
        return statements


def _render_fact(fact: dict[str, Any]) -> str | None:
    field, value = fact["field"], fact.get("value")
    if value is None:
        return None
    if field == "suspect_product_name":
        return f"Suspect product: {value}."
    if field == "source":
        return f"Report source: {value}."
    if field == "received_on":
        return f"Report received on {value}."
    if field.startswith("events.0."):
        leaf = field.rsplit(".", 1)[-1]
        if leaf == "verbatim_term":
            return f"Reported event: {value}."
        if leaf == "onset_date":
            return f"Event onset date: {value}."
        if leaf == "outcome":
            return f"Reported outcome: {value}."
        if leaf == "description":
            return f"Event description: {value}" + ("" if str(value).endswith(".") else ".")
    if field == "reporter.qualification":
        return f"Reporter qualification: {value}."
    if field == "patient.age":
        return f"Patient age: {value}."
    if field == "patient.sex":
        return f"Patient sex: {value}."
    if field == "history.case_count":
        return f"Patient history: {value} case(s) on record for this patient."
    if field == "history.event_count":
        return f"Patient history: {value} event(s) on record for this patient."
    return None


class BedrockProfile:
    """The production model profile. Stub-tested in R1: constructing
    a summary refuses loudly until the profile is configured, and no
    code path falls back to the offline composer silently."""

    profile = "bedrock"

    def __init__(self, region: str | None = None, model_id: str | None = None):
        self.region = region or os.environ.get("AWS_REGION")
        self.model_id = model_id or os.environ.get("BEDROCK_MODEL_ID")

    def propose_summary(self, facts: list[dict[str, Any]]) -> list[SummaryStatement]:
        if not self.region or not self.model_id:
            raise ModelNotConfigured(
                "bedrock profile selected but AWS_REGION / BEDROCK_MODEL_ID "
                "are not set — refusing rather than falling back"
            )
        raise ModelNotConfigured(
            "bedrock profile is wired as a port in R1 and exercised against "
            "stub transports; live Converse calls land with the AWS account "
            "integration (see README honest status)"
        )


def model_for_profile(profile: str) -> ModelPort:
    if profile == "offline":
        return OfflineComposer()
    if profile == "bedrock":
        return BedrockProfile()
    raise ModelNotConfigured(f"unknown model profile: {profile!r}")


# Field paths the assembler reads out of the intake evidence payload.
_FACT_FIELDS = [
    "source",
    "received_on",
    "suspect_product_name",
    "reporter.qualification",
    "patient.age",
    "patient.sex",
    "events.0.verbatim_term",
    "events.0.onset_date",
    "events.0.outcome",
    "events.0.description",
]

# Field paths read out of a captured patient-history evidence item
# (Round 2): the payload is {"history": <view json>}.
_HISTORY_FIELDS = ["history.case_count", "history.event_count"]


class CaseAssembler:
    """The agent step: facts → model proposal → citation check →
    stored summary. The Strands-shaped loop of R1: its tools are the
    evidence reads below; its only write is the verified summary.

    Round 2: when a patient history view has been built for the
    case's patient, the assembler captures it on the case's own
    evidence chain and reads its headline counts as facts, so the
    summary's history statements cite the view artefact itself —
    the same citation discipline as every other statement."""

    def __init__(self, store, chain: EvidenceChain, model: ModelPort, history=None):
        self.store = store
        self.chain = chain
        self.model = model
        self.history = history
        self.resolver = CitationResolver(chain)

    # -- tools (plain functions, Strands shape) ----------------------
    def read_case_facts(self, case_id: str) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for item in self.store.list_evidence(case_id):
            if item.kind == "intake_report":
                fields = _FACT_FIELDS
            elif item.kind == "patient_history":
                fields = _HISTORY_FIELDS
            else:
                continue
            payload = self.chain.payload_of(item)
            for field in fields:
                value = _dig(payload, field)
                if value is not None:
                    facts.append({"item_id": item.item_id, "field": field, "value": value})
        return facts

    def _attach_history(self, case_id: str, today: date) -> None:
        """Capture the current patient history view on this case's
        chain, once per view version. A view the case already
        carries is not re-appended."""
        if self.history is None:
            return
        from pv_cases.models import IntakeReport, patient_key

        report = None
        for item in self.store.list_evidence(case_id):
            if item.kind == "intake_report":
                report = IntakeReport.model_validate(self.chain.payload_of(item))
                break
        if report is None:
            return
        view = self.history.latest_patient_view(patient_key(report.patient))
        if view is None:
            return
        item_id = f"EV-{case_id}-HIST-{view.view_id}"
        if any(i.item_id == item_id for i in self.store.list_evidence(case_id)):
            return
        self.chain.append(
            case_id=case_id,
            kind="patient_history",
            payload={"history": view.model_dump(mode="json")},
            recorded_on=today,
            item_id=item_id,
        )

    # -- the step ------------------------------------------------------
    def assemble(self, case_id: str, today: date) -> CaseSummary:
        from pv_cases.models import CaseStatus
        from pv_cases.services import summary_id_for

        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        self._attach_history(case_id, today)
        facts = self.read_case_facts(case_id)
        statements = self.model.propose_summary(facts)
        problems = self.resolver.problems(case_id, statements)
        if problems:
            raise CitationRefusal(problems)
        summary = CaseSummary(
            summary_id=summary_id_for(case_id, len(self.store.list_summaries(case_id))),
            case_id=case_id,
            model_profile=self.model.profile,
            statements=statements,
            assembled_on=today,
        )
        self.store.add_summary(summary)
        # Only an untouched case advances to summary_assembled; a
        # dedup status (duplicate_linked / closed_as_duplicate) is a
        # safety decision and assembly never overwrites it.
        if case.status is CaseStatus.OPEN:
            case.status = CaseStatus.SUMMARY_ASSEMBLED
            self.store.update_case(case)
        return summary
