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


def _render_seriousness(assessment: dict[str, Any]) -> str:
    """One summary statement for the assessment's seriousness
    verdict. The wording is the claim the presentation check in
    :mod:`pv_cases.assessment` grades every summary against."""
    outcome = assessment["seriousness"]
    if outcome == "serious":
        fired = [
            f["criterion"].replace("_", " ")
            for f in assessment["criteria"]
            if f["status"] == "fired"
        ]
        return f"Seriousness: serious (criteria fired: {', '.join(fired)})."
    if outcome == "non_serious":
        return "Seriousness: non-serious (no criterion fired)."
    missing = [
        f["missing_fact"]
        for f in assessment["criteria"]
        if f["status"] == "cannot_assess" and f.get("missing_fact")
    ]
    return f"Seriousness: cannot be assessed (missing: {', '.join(missing)})."


def _render_expectedness(assessment: dict[str, Any]) -> str:
    outcome = assessment["expectedness"]
    if outcome == "expected":
        return (
            "Expectedness: expected (every coded event is labelled"
            " in the reference safety information)."
        )
    if outcome == "unexpected":
        bad = next(
            e for e in assessment["event_expectedness"] if e["status"] == "unexpected"
        )
        if bad["basis"] == "more_specific_than_labelled":
            return (
                f"Expectedness: unexpected (event {bad['event_index']}:"
                f" {bad['preferred_term']} is more specific than the"
                f" labelled term {bad['labelled_term']})."
            )
        return (
            f"Expectedness: unexpected (event {bad['event_index']}:"
            f" {bad['preferred_term']} is not in the reference safety"
            " information)."
        )
    bad = next(
        e
        for e in assessment["event_expectedness"]
        if e["status"] == "cannot_assess"
    )
    return (
        f"Expectedness: cannot be assessed (event {bad['event_index']}:"
        f" {bad['basis'].replace('_', ' ')})."
    )


def _render_clock(assessment: dict[str, Any]) -> str:
    clock = assessment["clock"]
    cls = clock["obligation_class"]
    if cls == "expedited_7_day":
        return (
            f"Reporting clock: 7-day expedited — deadline {clock['deadline']}"
            f" (day zero {clock['day_zero']})."
        )
    if cls == "expedited_15_day":
        return (
            f"Reporting clock: 15-day expedited — deadline {clock['deadline']}"
            f" (day zero {clock['day_zero']})."
        )
    if cls == "periodic":
        return "Reporting clock: periodic (no expedited deadline)."
    return "Reporting clock: awaiting assessment (no deadline computed)."


def _render_coding(decision: dict[str, Any]) -> str:
    """One summary statement for one coding decision. A coded term
    is presented as coded, with its label; an uncoded term is
    presented as uncoded — the wording the presentation check in
    :mod:`pv_cases.coding` grades every summary against."""
    verbatim = decision["verbatim"]
    if decision["subject_kind"] == "product":
        if decision["status"] == "coded":
            return (
                f"Product coded: {verbatim} → {decision['product_name']}"
                f" ({decision['product_code']}, {decision['active_substance']})."
            )
        return (
            f"Product uncoded: {verbatim} — no dictionary entry;"
            " held for coding review."
        )
    if decision["status"] == "coded":
        return (
            f"Event coded: {verbatim} → {decision['preferred_term']}"
            f" ({decision['soc']})."
        )
    return f"Event uncoded: {verbatim} — no dictionary term; held for coding review."


def _render_fact(fact: dict[str, Any]) -> str | None:
    field, value = fact["field"], fact.get("value")
    if value is None:
        return None
    if field.startswith("coding.") and isinstance(value, dict):
        return _render_coding(value)
    if field == "assessment.seriousness" and isinstance(value, dict):
        return _render_seriousness(value)
    if field == "assessment.expectedness" and isinstance(value, dict):
        return _render_expectedness(value)
    if field == "assessment.clock" and isinstance(value, dict):
        return _render_clock(value)
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

# Field paths read out of a captured assessment evidence item
# (Round 4): the payload is {"assessment": <artefact json>}; each
# fact's value is the whole artefact and the renderer picks its
# part, while the citation path names the field claimed.
_ASSESSMENT_FIELDS = [
    "assessment.seriousness",
    "assessment.expectedness",
    "assessment.clock",
]


class CaseAssembler:
    """The agent step: facts → model proposal → citation check →
    stored summary. The Strands-shaped loop of R1: its tools are the
    evidence reads below; its only write is the verified summary.

    Round 2: when a patient history view has been built for the
    case's patient, the assembler captures it on the case's own
    evidence chain and reads its headline counts as facts, so the
    summary's history statements cite the view artefact itself —
    the same citation discipline as every other statement."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        model: ModelPort,
        history=None,
        coding=None,
        assessment=None,
    ):
        self.store = store
        self.chain = chain
        self.model = model
        self.history = history
        self.coding = coding
        self.assessment = assessment
        self.resolver = CitationResolver(chain)
        # Round 7: the sealing guard, bound by wiring — while it
        # is set, a sealed case refuses new summaries: a sealed
        # case is a closed record.
        self.seal_lookup = None
        # Round 8: the injection quarantine, bound by wiring —
        # while it is set, a held field never becomes a fact, so
        # quarantined text reaches neither a summary statement
        # nor a model profile's prompt. This is the only road
        # from source text to either surface, which is what makes
        # the hold a hold.
        self.quarantine = None

    # -- tools (plain functions, Strands shape) ----------------------
    def read_case_facts(self, case_id: str) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        held = (
            self.quarantine.held_fields(case_id)
            if self.quarantine is not None
            else set()
        )
        items = self.store.list_evidence(case_id)
        coding_items = [i for i in items if i.kind == "coding"]
        latest_coding = coding_items[-1] if coding_items else None
        assessment_items = [i for i in items if i.kind == "assessment"]
        latest_assessment = assessment_items[-1] if assessment_items else None
        for item in items:
            if item.kind == "intake_report":
                fields = _FACT_FIELDS
            elif item.kind == "patient_history":
                fields = _HISTORY_FIELDS
            elif item.kind == "assessment":
                if item is not latest_assessment:
                    continue  # only the latest assessment capture speaks
                payload = self.chain.payload_of(item)
                value = payload.get("assessment")
                if value is not None:
                    for field in _ASSESSMENT_FIELDS:
                        facts.append(
                            {"item_id": item.item_id, "field": field, "value": value}
                        )
                continue
            elif item.kind == "coding":
                if item is not latest_coding:
                    continue  # only the latest coding capture speaks
                payload = self.chain.payload_of(item)
                decisions = payload.get("coding", [])
                for i in range(len(decisions)):
                    value = _dig(payload, f"coding.{i}")
                    if value is not None:
                        facts.append(
                            {"item_id": item.item_id, "field": f"coding.{i}", "value": value}
                        )
                continue
            else:
                continue
            payload = self.chain.payload_of(item)
            for field in fields:
                if (item.item_id, field) in held:
                    continue  # Round 8: a held field is not a fact
                value = _dig(payload, field)
                if value is not None:
                    facts.append({"item_id": item.item_id, "field": field, "value": value})
        return facts

    def _attach_coding(self, case_id: str, today: date) -> None:
        """Capture the case's current coding decisions on its
        evidence chain, once per coding state — the same pattern
        as the patient-history capture. A state already captured
        is not re-appended; a resolution or recode is a new state
        and is captured anew at the next assembly."""
        if self.coding is None:
            return
        import hashlib
        import json

        decisions = self.coding.current_decisions(case_id)
        if not decisions:
            return
        state = hashlib.sha256(
            json.dumps(
                [d.decision_id for d in decisions], separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()[:12]
        item_id = f"EV-{case_id}-CODING-{state}"
        if any(i.item_id == item_id for i in self.store.list_evidence(case_id)):
            return
        self.chain.append(
            case_id=case_id,
            kind="coding",
            payload={"coding": [d.model_dump(mode="json") for d in decisions]},
            recorded_on=today,
            item_id=item_id,
        )

    def _attach_assessment(self, case_id: str, today: date) -> None:
        """Refresh the case's assessment and capture the current
        artefact on its evidence chain, once per artefact — the
        same pattern as the coding capture. The service's
        fingerprint guard means an unchanged case writes nothing;
        a case whose facts moved supersedes, and the new artefact
        is captured anew at this assembly, so a summary never
        presents a superseded verdict as current."""
        if self.assessment is None:
            return
        artefact = self.assessment.assess_case(case_id, today)
        suffix = artefact.assessment_id.rsplit("-", 1)[-1]
        item_id = f"EV-{case_id}-ASSESS-{suffix}"
        if any(i.item_id == item_id for i in self.store.list_evidence(case_id)):
            return
        self.chain.append(
            case_id=case_id,
            kind="assessment",
            payload={"assessment": artefact.model_dump(mode="json")},
            recorded_on=today,
            item_id=item_id,
        )

    def _attach_history(self, case_id: str, today: date) -> None:
        """Capture the current patient history view on this case's
        chain, once per view version. A view the case already
        carries is not re-appended."""
        if self.history is None:
            return
        from pv_cases.models import patient_key
        from pv_cases.reporting import current_report

        try:
            report = current_report(self.store, self.chain, case_id)
        except KeyError:
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
        if self.seal_lookup is not None:
            seal = self.seal_lookup(case_id)
            if seal is not None:
                from pv_cases.models import seal_refusal_text

                raise CitationRefusal([seal_refusal_text(seal, "summaries")])
        self._attach_history(case_id, today)
        self._attach_coding(case_id, today)
        self._attach_assessment(case_id, today)
        facts = self.read_case_facts(case_id)
        # Round 12: when the model behind this assembler is the
        # governed gateway, the call routes through it — case,
        # facts and the day — so the ledger records it. A bare
        # port (the shape unit tests construct) is called as
        # before.
        propose_for_case = getattr(
            self.model, "propose_summary_for_case", None
        )
        if propose_for_case is not None:
            statements = propose_for_case(case_id, facts, today=today)
        else:
            statements = self.model.propose_summary(facts)
        problems = self.resolver.problems(case_id, statements)
        if self.coding is not None:
            # Round 3: the coded-term discipline — a summary that
            # presents an uncoded term as coded (or misstates a
            # coded one) is refused in code, on the same wholesale
            # terms as an unresolvable citation.
            from pv_cases.coding import coding_presentation_problems

            decisions = self.coding.current_decisions(case_id)
            if decisions:
                dictionary = self.coding.dictionary(today=today)
                problems += coding_presentation_problems(
                    decisions, statements, dictionary
                )
        if self.assessment is not None:
            # Round 4: the assessment discipline — a summary that
            # states a seriousness, expectedness, obligation class
            # or deadline different from the current assessment
            # artefact is refused in code, on the same wholesale
            # terms as an unresolvable citation.
            from pv_cases.assessment import assessment_presentation_problems

            current = self.assessment.current_assessment(case_id)
            if current is not None:
                problems += assessment_presentation_problems(current, statements)
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
