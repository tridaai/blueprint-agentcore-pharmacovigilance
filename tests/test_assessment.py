"""Round 4 seriousness + expectedness + regulatory clocks: the
criteria pack grades every planted criterion fired / not-fired /
cannot-assess exactly, the specificity trap lands unexpected, the
clock arithmetic is to the day, re-assessment supersedes with the
change recorded and day zero unmoved, and a summary that misstates
the artefact — verdict or deadline — is refused in code."""

from datetime import date

import pytest

from pv_cases.agent import CaseAssembler, CitationRefusal
from pv_cases.assessment import (
    assessment_presentation_problems,
    clock_state,
    evaluate_expectedness,
    evaluate_seriousness,
)
from pv_cases.models import (
    Citation,
    CriterionStatus,
    Expectedness,
    SeriousnessOutcome,
    SummaryStatement,
)
from pv_cases.store import InMemoryCaseStore
from tests.conftest import DAY

C = "CASE-RPT-2026-"


def _art(svc, case_id):
    artefact = svc.assessment.current_assessment(case_id)
    assert artefact is not None, f"no assessment for {case_id}"
    return artefact


def _finding(artefact, criterion):
    return next(f for f in artefact.criteria if f.criterion == criterion)


# ---------------------------------------------------------------------
# The planted matrix: verdicts, fired criteria, clocks to the day.
# ---------------------------------------------------------------------

# case -> (seriousness, fired criteria, expectedness, obligation, deadline)
MATRIX = {
    f"{C}0301": ("serious", {"death"}, "unexpected", "expedited_7_day", "2026-10-21"),
    f"{C}0302": (
        "serious",
        {"hospitalisation", "other_medically_important"},
        "unexpected",
        "expedited_15_day",
        "2026-10-31",
    ),
    f"{C}0303": ("serious", {"hospitalisation"}, "expected", "periodic", None),
    f"{C}0304": ("non_serious", set(), "expected", "periodic", None),
    f"{C}0305": ("cannot_assess", set(), "expected", "awaiting_assessment", None),
    f"{C}0306": ("cannot_assess", set(), "cannot_assess", "awaiting_assessment", None),
    f"{C}0307": (
        "serious",
        {"life_threatening"},
        "unexpected",
        "expedited_7_day",
        "2026-10-27",
    ),
    f"{C}0308": ("serious", {"disability"}, "expected", "periodic", None),
    f"{C}0309": ("serious", {"congenital_anomaly"}, "expected", "periodic", None),
}


def test_planted_matrix_exact(assessed):
    for case_id, (seriousness, fired, expectedness, obligation, deadline) in MATRIX.items():
        artefact = _art(assessed, case_id)
        assert artefact.seriousness.value == seriousness, case_id
        assert {
            f.criterion for f in artefact.criteria if f.status is CriterionStatus.FIRED
        } == fired, case_id
        assert artefact.expectedness.value == expectedness, case_id
        assert artefact.clock.obligation_class == obligation, case_id
        actual_deadline = (
            artefact.clock.deadline.isoformat() if artefact.clock.deadline else None
        )
        assert actual_deadline == deadline, case_id
        # Day zero is the first valid receipt date, always.
        case = assessed.store.get_case(case_id)
        assert artefact.clock.day_zero == case.received_on == case.awareness_date


def test_criteria_pack_versions_pinned(assessed):
    artefact = _art(assessed, f"{C}0301")
    assert artefact.criteria_pack_version == "2026.10.1"
    assert artefact.rsi_version == "2026.10.1"
    assert artefact.clock_pack_version == "2026.10.1"
    assert artefact.product_code == "NBT-101"
    # The artefact names the coding decisions it read.
    current_ids = {
        d.decision_id for d in assessed.coding.current_decisions(f"{C}0301")
    }
    assert set(artefact.coding_decision_ids) == current_ids


def test_cannot_assess_names_the_missing_fact(assessed):
    artefact = _art(assessed, f"{C}0305")
    for criterion in ("death", "hospitalisation", "disability"):
        finding = _finding(artefact, criterion)
        assert finding.status is CriterionStatus.CANNOT_ASSESS
        assert finding.missing_fact == "events.0.outcome"
        assert finding.deciding_fact is None
    # The coded-term criteria could still be assessed — and were.
    assert _finding(artefact, "other_medically_important").status is (
        CriterionStatus.NOT_FIRED
    )
    # Nothing was assumed negative: the case is unassessed, not clear.
    assert artefact.seriousness is SeriousnessOutcome.CANNOT_ASSESS

    artefact = _art(assessed, f"{C}0306")
    finding = _finding(artefact, "other_medically_important")
    assert finding.status is CriterionStatus.CANNOT_ASSESS
    assert finding.missing_fact == "events.0.coding"


def test_fired_criterion_records_its_deciding_fact(assessed):
    artefact = _art(assessed, f"{C}0301")
    finding = _finding(artefact, "death")
    assert finding.status is CriterionStatus.FIRED
    assert finding.deciding_fact == "events.0.outcome = 'fatal'"
    artefact = _art(assessed, f"{C}0302")
    finding = _finding(artefact, "hospitalisation")
    assert "admitted to hospital" in finding.deciding_fact


def test_outcome_recorded_unknown_is_an_absence_not_a_negative():
    # A report whose outcome field literally says "unknown" has
    # no outcome for the engine: death cannot be assessed.
    from pv_cases.models import AdverseEvent, IntakeReport, Patient, Reporter, ReportSource

    report = IntakeReport(
        report_id="RPT-X-1",
        source=ReportSource.PATIENT,
        received_on=DAY,
        patient=Patient(initials="Z.Z.", age=40),
        reporter=Reporter(name="Self", email="z@example.net"),
        suspect_product_code="NBT-101",
        suspect_product_name="Cardivane",
        events=[AdverseEvent(verbatim_term="Nausea", outcome="unknown")],
    )
    findings, outcome = evaluate_seriousness(report, [])
    death = next(f for f in findings if f.criterion == "death")
    assert death.status is CriterionStatus.CANNOT_ASSESS
    assert death.missing_fact == "events.0.outcome"
    assert outcome is SeriousnessOutcome.CANNOT_ASSESS


# ---------------------------------------------------------------------
# Expectedness: the traps.
# ---------------------------------------------------------------------


def test_specificity_trap_more_specific_than_labelled_is_unexpected(assessed):
    artefact = _art(assessed, f"{C}0302")
    (entry,) = artefact.event_expectedness
    assert entry.status is Expectedness.UNEXPECTED
    assert entry.basis == "more_specific_than_labelled"
    assert entry.labelled_term == "Rash"
    assert entry.preferred_term == "Rash vesicular"


def test_not_in_label_and_labelled(assessed):
    artefact = _art(assessed, f"{C}0301")
    (entry,) = artefact.event_expectedness
    assert (entry.status, entry.basis) == (
        Expectedness.UNEXPECTED,
        "not_in_label",
    )
    artefact = _art(assessed, f"{C}0304")
    (entry,) = artefact.event_expectedness
    assert (entry.status, entry.basis, entry.labelled_term) == (
        Expectedness.EXPECTED,
        "labelled_term",
        "Nausea",
    )


def test_uncoded_event_and_unidentified_product_cannot_assess(assessed):
    artefact = _art(assessed, f"{C}0306")
    (entry,) = artefact.event_expectedness
    assert (entry.status, entry.basis) == (
        Expectedness.CANNOT_ASSESS,
        "event_uncoded",
    )
    # CASE-0205's product name is not in the dictionary: there is
    # no RSI to check its (coded) event against.
    artefact = _art(assessed, f"{C}0205")
    (entry,) = artefact.event_expectedness
    assert (entry.status, entry.basis) == (
        Expectedness.CANNOT_ASSESS,
        "product_not_identified",
    )
    assert artefact.product_code is None
    # ...so even a non-serious case waits for assessment on its clock.
    assert artefact.seriousness is SeriousnessOutcome.NON_SERIOUS
    assert artefact.clock.obligation_class == "awaiting_assessment"


def test_feed_case_verdicts(assessed):
    # The R1 pair: blistering rash on Dermaclear — medically
    # important, more specific than the label, on 15-day clocks
    # running from each report's own receipt date.
    for case_id, deadline in ((f"{C}0005", "2026-10-25"), (f"{C}0006", "2026-10-26")):
        artefact = _art(assessed, case_id)
        assert artefact.seriousness is SeriousnessOutcome.SERIOUS
        assert artefact.expectedness is Expectedness.UNEXPECTED
        assert artefact.clock.obligation_class == "expedited_15_day"
        assert artefact.clock.deadline.isoformat() == deadline
    artefact = _art(assessed, f"{C}0001")
    assert artefact.seriousness is SeriousnessOutcome.NON_SERIOUS
    assert artefact.expectedness is Expectedness.EXPECTED
    assert artefact.clock.obligation_class == "periodic"


def test_rsi_is_a_labelled_stand_in(services):
    rsi = services.assessment.rsi(today=DAY)
    assert "stand-in" in rsi.note and "not a real product label" in rsi.note
    dermaclear = {e.preferred_term: e for e in rsi.products["NBT-317"]}
    assert dermaclear["Rash"].more_specific_terms == ["Rash vesicular"]


# ---------------------------------------------------------------------
# Clock states, derived at read time.
# ---------------------------------------------------------------------


def test_clock_state_derivation(assessed):
    artefact = _art(assessed, f"{C}0301")
    clock = artefact.clock
    assert clock_state(clock, date(2026, 10, 20)) == ("within_clock", 1)
    assert clock_state(clock, date(2026, 10, 21)) == ("due_today", 0)
    assert clock_state(clock, date(2026, 10, 22)) == ("breached", -1)
    periodic = _art(assessed, f"{C}0304").clock
    assert clock_state(periodic, date(2026, 10, 22)) == ("periodic", None)


def test_clock_board_orders_by_urgency(assessed):
    board = assessed.assessment.clock_board(date(2026, 10, 22))
    by_case = {e.case_id: e for e in board}
    assert by_case[f"{C}0301"].state == "breached"
    assert by_case[f"{C}0301"].days_remaining == -1
    assert by_case[f"{C}0305"].state == "awaiting_assessment"
    assert by_case[f"{C}0305"].days_remaining is None
    deadline_bearing = [e for e in board if e.days_remaining is not None]
    assert [e.days_remaining for e in deadline_bearing] == sorted(
        e.days_remaining for e in deadline_bearing
    )
    # The most-breached clock leads: the recurrence case's 15-day
    # clock ran from its 2025 receipt and is a year past deadline.
    assert board[0].case_id == f"{C}0105"
    assert board[0].state == "breached"


# ---------------------------------------------------------------------
# Artefact discipline: idempotence, supersession, insert-only.
# ---------------------------------------------------------------------


def test_reassessment_over_unchanged_inputs_writes_nothing(assessed):
    before = assessed.store.list_assessments(f"{C}0301")
    again = assessed.assessment.assess_case(f"{C}0301", DAY)
    assert again.assessment_id == before[-1].assessment_id
    assert assessed.store.list_assessments(f"{C}0301") == before


def test_case_that_becomes_assessable_supersedes_with_change_recorded(coded):
    case_id = f"{C}0204"  # "Feeling drained" — ambiguous, uncoded
    first = _art(coded, case_id)
    assert first.seriousness is SeriousnessOutcome.CANNOT_ASSESS
    assert first.expectedness is Expectedness.CANNOT_ASSESS
    assert first.clock.obligation_class == "awaiting_assessment"
    assert first.change is None and first.supersedes_id is None

    task = next(
        t for t in coded.coding.uncoded_queue() if t.case_id == case_id
    )
    coded.coding.resolve(task.task_id, "J. Coder", DAY, preferred_term="Fatigue")
    second = coded.assessment.assess_case(case_id, DAY)
    assert second.assessment_id != first.assessment_id
    assert second.supersedes_id == first.assessment_id
    assert second.seriousness is SeriousnessOutcome.NON_SERIOUS
    assert second.expectedness is Expectedness.EXPECTED
    assert second.clock.obligation_class == "periodic"
    assert second.change == {
        "seriousness": ["cannot_assess", "non_serious"],
        "expectedness": ["cannot_assess", "expected"],
        "obligation_class": ["awaiting_assessment", "periodic"],
    }
    # Day zero never moves, and the old verdict stays readable.
    assert second.clock.day_zero == first.clock.day_zero == date(2026, 10, 21)
    trail = coded.assessment.list_assessments(case_id)
    assert [a.assessment_id for a in trail] == [
        first.assessment_id,
        second.assessment_id,
    ]
    assert coded.assessment.current_assessment(case_id).assessment_id == (
        second.assessment_id
    )


def test_assessment_artefacts_are_insert_only():
    for verb in (
        "update_assessment",
        "delete_assessment",
        "update_rsi_version",
        "delete_rsi_version",
    ):
        assert not hasattr(InMemoryCaseStore, verb), verb


# ---------------------------------------------------------------------
# The assembler: statements in, misstatements refused.
# ---------------------------------------------------------------------


def test_summary_carries_the_assessment_statements(assessed):
    summary = assessed.assembler.assemble(f"{C}0302", DAY)
    texts = [s.text for s in summary.statements]
    assert (
        "Seriousness: serious (criteria fired: hospitalisation,"
        " other medically important)." in texts
    )
    assert (
        "Expectedness: unexpected (event 0: Rash vesicular is more"
        " specific than the labelled term Rash)." in texts
    )
    assert (
        "Reporting clock: 15-day expedited — deadline 2026-10-31"
        " (day zero 2026-10-16)." in texts
    )
    assert assessed.assembler.resolver.problems(f"{C}0302", summary.statements) == []


def test_summary_for_unassessable_case_says_so(assessed):
    summary = assessed.assembler.assemble(f"{C}0305", DAY)
    texts = [s.text for s in summary.statements]
    assert (
        "Seriousness: cannot be assessed (missing: events.0.outcome,"
        " events.0.outcome, events.0.outcome)." in texts
    )
    assert "Reporting clock: awaiting assessment (no deadline computed)." in texts


class _MisstatesDeadline:
    profile = "lying-test-double"

    def propose_summary(self, facts):
        item = next(
            f["item_id"] for f in facts if f["field"] == "assessment.clock"
        )
        return [
            SummaryStatement(
                text=(
                    "Reporting clock: 15-day expedited — deadline 2026-11-15"
                    " (day zero 2026-10-16)."
                ),
                citations=[Citation(evidence_item_id=item, field="assessment.clock")],
            )
        ]


class _MisstatesSeriousness:
    profile = "lying-test-double"

    def propose_summary(self, facts):
        item = next(
            f["item_id"] for f in facts if f["field"] == "assessment.seriousness"
        )
        return [
            SummaryStatement(
                text="Seriousness: non-serious (no criterion fired).",
                citations=[
                    Citation(evidence_item_id=item, field="assessment.seriousness")
                ],
            )
        ]


def test_misstated_deadline_is_refused(assessed):
    assembler = CaseAssembler(
        assessed.store,
        assessed.chain,
        _MisstatesDeadline(),
        coding=assessed.coding,
        assessment=assessed.assessment,
    )
    with pytest.raises(CitationRefusal):
        assembler.assemble(f"{C}0302", DAY)
    assert assessed.store.list_summaries(f"{C}0302") == []


def test_misstated_seriousness_is_refused(assessed):
    assembler = CaseAssembler(
        assessed.store,
        assessed.chain,
        _MisstatesSeriousness(),
        coding=assessed.coding,
        assessment=assessed.assessment,
    )
    with pytest.raises(CitationRefusal):
        assembler.assemble(f"{C}0301", DAY)
    assert assessed.store.list_summaries(f"{C}0301") == []


def test_stale_clock_presentation_is_a_problem(coded):
    # A summary assembled while the case was unassessable states
    # the awaiting clock. Once a coder settles the term and the
    # case is re-assessed, that text no longer matches the
    # current artefact — the check must say so.
    case_id = f"{C}0204"
    summary = coded.assembler.assemble(case_id, DAY)
    assert any(
        s.text.startswith("Reporting clock: awaiting assessment")
        for s in summary.statements
    )
    task = next(t for t in coded.coding.uncoded_queue() if t.case_id == case_id)
    coded.coding.resolve(task.task_id, "J. Coder", DAY, preferred_term="Fatigue")
    current = coded.assessment.assess_case(case_id, DAY)
    problems = assessment_presentation_problems(current, summary.statements)
    assert any("clock misstated" in p for p in problems)
    assert any("seriousness misstated" in p for p in problems)
    # And a fresh assembly speaks the new verdict.
    fresh = coded.assembler.assemble(case_id, DAY)
    assert assessment_presentation_problems(current, fresh.statements) == []
    assert any(
        s.text == "Reporting clock: periodic (no expedited deadline)."
        for s in fresh.statements
    )
