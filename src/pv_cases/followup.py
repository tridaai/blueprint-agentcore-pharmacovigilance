"""The follow-up workflow — Round 5.

Rounds 1–4 name what a case is missing: the validity gate fails a
report on named criteria, the dictionary leaves a term uncoded,
the assessment engine records a criterion as *cannot assess* with
the absent fact named. This round turns each of those named
absences into tracked work with a lifecycle:

- A **follow-up request** (the workflow task) is raised in code
  from exactly those outputs — never from a model's impression.
  Each request names the specific fields/facts a follow-up report
  must supply (a :class:`MissingItem` list), is priced a due date
  by the **versioned follow-up pack** below (days per missing
  class; the slowest class governs), and belongs to a case — or,
  for a validity request, to the report that will become one.
- A request is answered only by a **follow-up report**: a new
  intake linked to the request. For an existing case the report
  joins the case's evidence chain and folds into its report of
  record (:mod:`pv_cases.reporting`) — follow-ups **extend** the
  case; they never create a parallel one. The case is then
  recoded and reassessed over the folded facts. For a validity
  request, the follow-up merges cumulatively over the original
  report, and the merged report, once valid, founds the case
  through the ordinary intake pipeline.
- Every answer is recorded as an insert-only **act**: a
  *resolution* when the missing fact has landed, an *attempt*
  (rolling the due date into the next cycle) when it has not, and
  an *escalation* when an attempt fails at the pack's cycle cap —
  the task lands in the named `escalated` state on the board,
  never silently dropped. Requests and acts are never edited;
  state, cycle and ageing are derived from the acts in code.
- **Day zero does not move.** A follow-up amends facts, not
  identity (see reporting.py), and the case's awareness date is
  untouched — so a seriousness fact that lands on day 20 produces
  an expedited clock whose deadline is already past, and the
  clock board says *breached*, out loud.

Follow-up requests are internal task artefacts: nothing here
sends anything anywhere, and nothing is ever submitted to a
regulator.
"""

from __future__ import annotations

from datetime import date, timedelta

from pv_cases.assessment import AssessmentService
from pv_cases.coding import CodingService
from pv_cases.dedup import DedupService
from pv_cases.evidence import EvidenceChain, canonical_json
from pv_cases.models import (
    CRITERION_EVENT,
    CRITERION_PATIENT,
    CRITERION_PRODUCT,
    CRITERION_REPORTER,
    EXP_PRODUCT_UNIDENTIFIED,
    FU_ACT_ATTEMPT,
    FU_ACT_ESCALATION,
    FU_ACT_RESOLUTION,
    FU_CLASS_MISSING_FACT,
    FU_CLASS_UNCODED_EVENT,
    FU_SOURCE_ASSESSMENT,
    FU_SOURCE_CODING,
    FU_SOURCE_VALIDITY,
    FU_STATE_ESCALATED,
    FU_STATE_OPEN,
    FU_STATE_OVERDUE,
    FU_STATE_RESOLVED,
    SUBJECT_EVENT,
    UNCODED_AMBIGUOUS,
    CaseAssessment,
    CodingStatus,
    CriterionStatus,
    FollowUpAct,
    FollowUpBoardEntry,
    FollowUpRequest,
    FollowUpSubmission,
    FollowUpTaskView,
    IntakeReport,
    MissingItem,
    seal_refusal_text,
)
from pv_cases.reporting import merge_reports
from pv_cases.services import IntakeService
from pv_cases.validity import assess_validity

# ---------------------------------------------------------------------
# The follow-up pack — data. Bump the version when any day count or
# the cycle cap changes; requests record the version they were
# raised under.
# ---------------------------------------------------------------------

FOLLOW_UP_PACK = {
    "version": "2026.10.1",
    "days_by_class": {
        # Validity classes (the gate's criterion names): a missing
        # reporter is a phone call; a missing patient identity can
        # take a records request.
        CRITERION_PATIENT: 30,
        CRITERION_REPORTER: 14,
        CRITERION_PRODUCT: 21,
        CRITERION_EVENT: 21,
        # A codable wording waits on source documents at the site.
        FU_CLASS_UNCODED_EVENT: 30,
        # A fact the assessment named (an outcome, a product
        # identity) is usually one query back to the reporter.
        FU_CLASS_MISSING_FACT: 14,
    },
    # A request lives for at most this many cycles: the initial ask
    # plus rolled attempts. An attempt that fails in the last cycle
    # escalates instead of rolling.
    "max_cycles": 3,
}

FOLLOW_UP_PACK_VERSION = FOLLOW_UP_PACK["version"]


class FollowUpError(ValueError):
    """A follow-up act that cannot be taken: the request is already
    resolved or escalated, or the report was already recorded."""


# ---------------------------------------------------------------------
# Missing-item derivations — pure functions over the gate's, the
# dictionary's and the assessment engine's own outputs.
# ---------------------------------------------------------------------

_VALIDITY_DETAILS = {
    CRITERION_PATIENT: (
        "patient",
        "an identifiable patient — none of initials, age, age group,"
        " sex, date of birth or a reporter-held patient id is recorded",
    ),
    CRITERION_REPORTER: (
        "reporter",
        "an identifiable reporter — no reporter name, email or phone"
        " is recorded",
    ),
    CRITERION_PRODUCT: (
        "suspect_product",
        "the suspect product — neither a product code nor a product"
        " name is recorded",
    ),
    CRITERION_EVENT: (
        "events",
        "an adverse event — no event with a verbatim term is recorded",
    ),
}


def validity_missing_items(missing_criteria: list[str]) -> list[MissingItem]:
    """The gate's missing list, made specific: one item per unmet
    criterion, naming the field and exactly what would satisfy it."""
    items = []
    for criterion in missing_criteria:
        field, detail = _VALIDITY_DETAILS[criterion]
        items.append(
            MissingItem(
                item_class=criterion,
                field=field,
                detail=detail,
                blocks=[criterion],
            )
        )
    return items


def coding_missing_item(decision) -> MissingItem:
    """The follow-up item for one uncoded event decision: the
    verbatim as reported, its uncoded reason, and what wording the
    follow-up must supply."""
    if decision.uncoded_reason == UNCODED_AMBIGUOUS:
        detail = (
            f"the reporter's intended meaning of {decision.verbatim!r}"
            f" — as reported it maps to more than one dictionary term"
            f" ({', '.join(decision.candidates)}); the follow-up must"
            " restate the event in the reporter's own clarified wording"
        )
    else:
        detail = (
            f"a codable wording of the event as reported,"
            f" {decision.verbatim!r} — the dictionary has no entry for"
            " it; the follow-up must restate the event in words the"
            " reporter can stand behind"
        )
    return MissingItem(
        item_class=FU_CLASS_UNCODED_EVENT,
        field=f"events.{decision.subject_index}.verbatim_term",
        detail=detail,
        blocks=["coding"],
        event_index=decision.subject_index,
        verbatim=decision.verbatim,
    )


def assessment_missing_items(assessment: CaseAssessment) -> list[MissingItem]:
    """The follow-up items an assessment's cannot-assess verdicts
    name — minus the absences the coding workflow already owns. A
    criterion blocked by an uncoded term (`…coding`) raises no
    assessment item: the coding request for that event is the
    follow-up work. What remains is a fact the record itself lacks
    (an outcome, a product identity)."""
    items: list[MissingItem] = []
    by_fact: dict[str, list[str]] = {}
    for finding in assessment.criteria:
        if finding.status is not CriterionStatus.CANNOT_ASSESS:
            continue
        if not finding.missing_fact or finding.missing_fact.endswith(".coding"):
            continue
        by_fact.setdefault(finding.missing_fact, []).append(finding.criterion)
    for fact, criteria in by_fact.items():
        items.append(
            MissingItem(
                item_class=FU_CLASS_MISSING_FACT,
                field=fact,
                detail=(
                    f"the fact {fact} — absent from the record; it"
                    f" decides the {', '.join(criteria)}"
                    f" {'criterion' if len(criteria) == 1 else 'criteria'}"
                ),
                blocks=list(criteria),
            )
        )
    for entry in assessment.event_expectedness:
        if entry.basis == EXP_PRODUCT_UNIDENTIFIED:
            items.append(
                MissingItem(
                    item_class=FU_CLASS_MISSING_FACT,
                    field="suspect_product",
                    detail=(
                        "the suspect product's identity — the reported"
                        " product is not identified by a coded decision,"
                        " so expectedness cannot be checked against the"
                        " reference safety information"
                    ),
                    blocks=["expectedness"],
                )
            )
            break
    return items


# ---------------------------------------------------------------------
# The service.
# ---------------------------------------------------------------------


class FollowUpService:
    """Raises follow-up requests from the gate's, dictionary's and
    assessment engine's outputs; takes follow-up reports; derives
    every request's state from its recorded acts."""

    def __init__(
        self,
        store,
        objects,
        chain: EvidenceChain,
        intake: IntakeService | None = None,
        coding: CodingService | None = None,
        assessment: AssessmentService | None = None,
        dedup: DedupService | None = None,
    ):
        self.store = store
        self.objects = objects
        self.chain = chain
        self.intake = intake
        self.coding = coding
        self.assessment = assessment
        self.dedup = dedup
        # Round 7: the sealing guard, bound by wiring — while it
        # is set, a sealed case refuses new follow-up requests
        # and follow-up reports: a sealed case is a closed record.
        self.seal_lookup = None

    def _refuse_if_sealed(self, case_id: str) -> None:
        if self.seal_lookup is not None:
            seal = self.seal_lookup(case_id)
            if seal is not None:
                raise FollowUpError(
                    seal_refusal_text(seal, "follow-up work")
                )

    # -- raising -----------------------------------------------------
    def _class_days(self, items: list[MissingItem]) -> int:
        days = FOLLOW_UP_PACK["days_by_class"]
        return max(days[item.item_class] for item in items)

    def _fresh_id(self, base: str) -> str:
        if self.store.get_follow_up_request(base) is None:
            return base
        n = 2
        while self.store.get_follow_up_request(f"{base}-{n}") is not None:
            n += 1
        return f"{base}-{n}"

    def _raise(self, task: FollowUpRequest) -> FollowUpRequest | None:
        """Store a request unless one with the same key (source +
        case/report + subject) already stands open — or escalated.
        The raising scan is idempotent over unchanged deficiencies;
        a resolved deficiency that genuinely recurs is new work,
        but an escalated request is already with the human queue
        and is never silently duplicated by a later scan."""
        for existing in self.store.list_follow_up_requests():
            if (
                existing.source == task.source
                and existing.report_id == task.report_id
                and existing.case_id == task.case_id
                and existing.subject_index == task.subject_index
            ):
                if self._derive(existing, task.raised_on).state != FU_STATE_RESOLVED:
                    return None
        self.store.add_follow_up_request(task)
        return task

    def raise_for_invalid(
        self, report: IntakeReport, today: date
    ) -> FollowUpRequest | None:
        """The validity request for a report the gate just failed:
        one item per missing criterion, each naming its field."""
        verdict = assess_validity(report)
        if verdict.valid:
            return None
        items = validity_missing_items(verdict.missing)
        days = self._class_days(items)
        return self._raise(
            FollowUpRequest(
                task_id=self._fresh_id(f"FUP-{report.report_id}"),
                source=FU_SOURCE_VALIDITY,
                report_id=report.report_id,
                case_id=None,
                items=items,
                pack_version=FOLLOW_UP_PACK_VERSION,
                class_days=days,
                raised_on=today,
                due_on=today + timedelta(days=days),
            )
        )

    def raise_for_case(self, case_id: str, today: date) -> list[FollowUpRequest]:
        """Scan one case's current coding decisions and assessment
        for deficiencies a follow-up report can cure, and raise a
        request for each: one per uncoded event, one for the
        assessment's named missing facts."""
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        self._refuse_if_sealed(case_id)
        created: list[FollowUpRequest] = []
        if self.coding is not None:
            for decision in self.coding.current_decisions(case_id):
                if decision.subject_kind != SUBJECT_EVENT:
                    continue
                if decision.status is not CodingStatus.UNCODED:
                    continue
                items = [coding_missing_item(decision)]
                days = self._class_days(items)
                task = self._raise(
                    FollowUpRequest(
                        task_id=self._fresh_id(
                            f"FUP-{case_id}-E{decision.subject_index}"
                        ),
                        source=FU_SOURCE_CODING,
                        report_id=case.report_id,
                        case_id=case_id,
                        subject_index=decision.subject_index,
                        decision_id=decision.decision_id,
                        items=items,
                        pack_version=FOLLOW_UP_PACK_VERSION,
                        class_days=days,
                        raised_on=today,
                        due_on=today + timedelta(days=days),
                    )
                )
                if task is not None:
                    created.append(task)
        if self.assessment is not None:
            current = self.assessment.current_assessment(case_id)
            if current is not None:
                items = assessment_missing_items(current)
                if items:
                    days = self._class_days(items)
                    task = self._raise(
                        FollowUpRequest(
                            task_id=self._fresh_id(f"FUP-{case_id}-A"),
                            source=FU_SOURCE_ASSESSMENT,
                            report_id=case.report_id,
                            case_id=case_id,
                            items=items,
                            pack_version=FOLLOW_UP_PACK_VERSION,
                            class_days=days,
                            raised_on=today,
                            due_on=today + timedelta(days=days),
                        )
                    )
                    if task is not None:
                        created.append(task)
        return created

    # -- derived state -------------------------------------------------
    def _derive(self, task: FollowUpRequest, as_of: date) -> FollowUpTaskView:
        acts = self.store.list_follow_up_acts(task.task_id)
        state = FU_STATE_OPEN
        cycle = 1
        due_on = task.due_on
        case_id = task.case_id
        for act in acts:
            if act.kind == FU_ACT_RESOLUTION:
                state = FU_STATE_RESOLVED
                if act.case_id is not None:
                    case_id = act.case_id
            elif act.kind == FU_ACT_ESCALATION:
                state = FU_STATE_ESCALATED
            elif act.kind == FU_ACT_ATTEMPT:
                cycle = act.cycle + 1
                if act.new_due_on is not None:
                    due_on = act.new_due_on
        days_overdue = 0
        if state == FU_STATE_OPEN and as_of > due_on:
            state = FU_STATE_OVERDUE
            days_overdue = (as_of - due_on).days
        return FollowUpTaskView(
            task=task,
            state=state,
            cycle=cycle,
            due_on=due_on,
            days_overdue=days_overdue,
            case_id=case_id,
            acts=acts,
        )

    def view(self, task_id: str, as_of: date) -> FollowUpTaskView:
        task = self.store.get_follow_up_request(task_id)
        if task is None:
            raise KeyError(f"no such follow-up request: {task_id}")
        return self._derive(task, as_of)

    def list_views(self, as_of: date) -> list[FollowUpTaskView]:
        return [
            self._derive(task, as_of)
            for task in self.store.list_follow_up_requests()
        ]

    def tasks_for_case(self, case_id: str, as_of: date) -> list[FollowUpTaskView]:
        """Every request belonging to a case: the ones raised
        against it, plus a validity request whose follow-up founded
        it (named by the resolution act)."""
        views = [
            self._derive(task, as_of)
            for task in self.store.list_follow_up_requests(case_id=case_id)
        ]
        for task in self.store.list_follow_up_requests():
            if task.case_id is not None:
                continue
            view = self._derive(task, as_of)
            if view.case_id == case_id:
                views.append(view)
        return sorted(views, key=lambda v: v.task.task_id)

    def board(self, as_of: date) -> list[FollowUpBoardEntry]:
        """The follow-up board at ``as_of``: escalated first, then
        overdue by days, then open by due date, then resolved —
        ageing derived at read time, never stored."""
        entries = []
        for view in self.list_views(as_of):
            entries.append(
                FollowUpBoardEntry(
                    task_id=view.task.task_id,
                    source=view.task.source,
                    case_id=view.case_id,
                    report_id=view.task.report_id,
                    state=view.state,
                    cycle=view.cycle,
                    due_on=view.due_on,
                    days_overdue=view.days_overdue,
                    missing_fields=[item.field for item in view.task.items],
                )
            )
        rank = {FU_STATE_ESCALATED: 0, FU_STATE_OVERDUE: 1, FU_STATE_OPEN: 2,
                FU_STATE_RESOLVED: 3}
        entries.sort(
            key=lambda e: (
                rank[e.state],
                -e.days_overdue,
                e.due_on,
                e.task_id,
            )
        )
        return entries

    # -- submission ----------------------------------------------------
    def submit_follow_up(
        self, task_id: str, report: IntakeReport, today: date
    ) -> FollowUpSubmission:
        """Answer one request with one follow-up report. Refused —
        in code — when the request already stands resolved or
        escalated: decided work does not take more reports."""
        task = self.store.get_follow_up_request(task_id)
        if task is None:
            raise KeyError(f"no such follow-up request: {task_id}")
        if task.case_id is not None:
            self._refuse_if_sealed(task.case_id)
        standing = self._derive(task, today)
        if standing.state in (FU_STATE_RESOLVED, FU_STATE_ESCALATED):
            raise FollowUpError(
                f"follow-up request {task_id} is {standing.state} —"
                " it takes no further reports"
            )
        if task.case_id is None:
            return self._submit_pre_case(task, report, today)
        return self._submit_for_case(task, report, today)

    def _record_act(
        self,
        task: FollowUpRequest,
        report: IntakeReport,
        today: date,
        resolved: bool,
        case_id: str | None,
        detail: str,
    ) -> FollowUpAct:
        acts = self.store.list_follow_up_acts(task.task_id)
        cycle = 1 + sum(1 for a in acts if a.kind == FU_ACT_ATTEMPT)
        if resolved:
            kind, new_due = FU_ACT_RESOLUTION, None
        elif cycle >= FOLLOW_UP_PACK["max_cycles"]:
            kind, new_due = FU_ACT_ESCALATION, None
        else:
            kind = FU_ACT_ATTEMPT
            new_due = today + timedelta(days=task.class_days)
        act = FollowUpAct(
            act_id=f"FUA-{task.task_id}-{len(acts) + 1:02d}",
            task_id=task.task_id,
            kind=kind,
            follow_up_report_id=report.report_id,
            case_id=case_id,
            cycle=cycle,
            new_due_on=new_due,
            recorded_on=today,
            detail=detail,
        )
        self.store.add_follow_up_act(act)
        return act

    def _submission(
        self,
        task: FollowUpRequest,
        report: IntakeReport,
        act: FollowUpAct,
        today: date,
        case_id: str | None,
        new_case_id: str | None = None,
        links_created: list[str] | None = None,
    ) -> FollowUpSubmission:
        after = self._derive(task, today)
        return FollowUpSubmission(
            task_id=task.task_id,
            follow_up_report_id=report.report_id,
            act_id=act.act_id,
            outcome=act.kind,
            state=after.state,
            cycle=after.cycle,
            due_on=after.due_on,
            case_id=case_id,
            new_case_id=new_case_id,
            links_created=links_created or [],
        )

    def _submit_for_case(
        self, task: FollowUpRequest, report: IntakeReport, today: date
    ) -> FollowUpSubmission:
        case_id = task.case_id
        item_id = f"EV-{case_id}-FU-{report.report_id}"
        if any(i.item_id == item_id for i in self.store.list_evidence(case_id)):
            raise FollowUpError(
                f"report {report.report_id} is already recorded on {case_id}"
            )
        # The follow-up joins the case's chain; from here the
        # folded report of record carries its facts.
        self.chain.append(
            case_id=case_id,
            kind="follow_up_report",
            payload=report.model_dump(mode="json"),
            recorded_on=today,
            item_id=item_id,
        )
        if self.coding is not None:
            self.coding.code_case(case_id, today)
        if self.assessment is not None:
            self.assessment.assess_case(case_id, today)
        links = []
        if self.dedup is not None:
            links = [
                link.link_id
                for link in self.dedup.link_report_matches(report, case_id, today)
            ]
        resolved = self._fact_landed(task)
        if resolved:
            detail = (
                f"follow-up {report.report_id} supplied what the"
                " request named; the fact is on the case's record"
            )
        else:
            detail = (
                f"follow-up {report.report_id} did not supply what"
                " the request named"
            )
        act = self._record_act(task, report, today, resolved, case_id, detail)
        # The follow-up may have exposed a new deficiency (a landed
        # outcome over a still-uncoded term, say): raise for it.
        self.raise_for_case(case_id, today)
        return self._submission(
            task, report, act, today, case_id, links_created=links
        )

    def _fact_landed(self, task: FollowUpRequest) -> bool:
        """Has the fact this request names reached the case's
        record? Judged from the current artefacts, per source —
        never from the follow-up report's own claims."""
        if task.source == FU_SOURCE_CODING:
            if self.coding is None:
                return False
            for decision in self.coding.current_decisions(task.case_id):
                if (
                    decision.subject_kind == SUBJECT_EVENT
                    and decision.subject_index == task.subject_index
                ):
                    return decision.status is CodingStatus.CODED
            return False
        if task.source == FU_SOURCE_ASSESSMENT:
            if self.assessment is None:
                return False
            current = self.assessment.current_assessment(task.case_id)
            if current is None:
                return False
            still_missing = {
                item.field for item in assessment_missing_items(current)
            }
            return not ({item.field for item in task.items} & still_missing)
        return False

    def _submit_pre_case(
        self, task: FollowUpRequest, report: IntakeReport, today: date
    ) -> FollowUpSubmission:
        """A validity request: the follow-up merges cumulatively
        over the original report and every earlier answer. Still
        invalid, it is an attempt (or the escalation); valid, the
        merged report founds the case through ordinary intake and
        the request resolves, naming the case it created."""
        original_key = f"follow-ups/FU-{task.report_id}.json"
        original = IntakeReport.model_validate_json(
            self.objects.get_object(original_key)
        )
        self.objects.put_object(
            f"follow-ups/{report.report_id}.json",
            canonical_json(report.model_dump(mode="json")),
        )
        merged = original
        for act in self.store.list_follow_up_acts(task.task_id):
            prior = IntakeReport.model_validate_json(
                self.objects.get_object(f"follow-ups/{act.follow_up_report_id}.json")
            )
            merged = merge_reports(merged, prior)
        merged = merge_reports(merged, report)
        if not assess_validity(merged).valid:
            act = self._record_act(
                task,
                report,
                today,
                resolved=False,
                case_id=None,
                detail=(
                    f"follow-up {report.report_id} still leaves the"
                    " report short of the validity criteria"
                ),
            )
            return self._submission(task, report, act, today, case_id=None)
        if self.intake is None:
            raise FollowUpError("no intake service wired to found the case")
        founding = merged.model_copy(
            update={
                "report_id": report.report_id,
                "source": report.source,
                "received_on": report.received_on,
            }
        )
        outcome = self.intake.ingest(founding, today)
        case = outcome.case
        if case is None:  # the gate disagrees with the merge — refuse loudly
            raise FollowUpError(
                "the merged report founded no case; the merge and the"
                " gate disagree, which must not happen silently"
            )
        # Provenance: the original report and this follow-up both
        # join the founded case's chain, behind the merged intake.
        self.chain.append(
            case_id=case.case_id,
            kind="original_report",
            payload=original.model_dump(mode="json"),
            recorded_on=today,
            item_id=f"EV-{case.case_id}-ORIG-{task.report_id}",
        )
        self.chain.append(
            case_id=case.case_id,
            kind="follow_up_report",
            payload=report.model_dump(mode="json"),
            recorded_on=today,
            item_id=f"EV-{case.case_id}-FU-{report.report_id}",
        )
        act = self._record_act(
            task,
            report,
            today,
            resolved=True,
            case_id=case.case_id,
            detail=(
                f"follow-up {report.report_id} completed the report;"
                f" it founded case {case.case_id}"
            ),
        )
        return self._submission(
            task, report, act, today, case_id=case.case_id,
            new_case_id=case.case_id,
        )
