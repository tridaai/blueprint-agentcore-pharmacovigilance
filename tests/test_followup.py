"""Round 5: the follow-up workflow.

Requests are raised in code from the gate's, the dictionary's and
the assessment engine's own outputs — each naming the exact field
a follow-up report must supply, priced a due date by the versioned
pack. A request is answered only by a follow-up report: the report
extends the case (never a parallel one), the case is recoded and
reassessed over the folded facts, and the answer is recorded as an
insert-only act — resolution, a rolled attempt, or escalation at
the cycle cap. Day zero never moves.
"""

from datetime import date

import pytest

from pv_cases.followup import (
    FOLLOW_UP_PACK_VERSION,
    FollowUpError,
    assessment_missing_items,
    validity_missing_items,
)
from pv_cases.models import (
    AdverseEvent,
    IntakeReport,
    Patient,
    Reporter,
    ReportSource,
)
from pv_cases.reporting import current_report, merge_reports
from pv_cases.seed import followup_reports
from tests.conftest import DAY

F1 = "CASE-RPT-2026-0401"
F1_TASK = "FUP-CASE-RPT-2026-0401-A"
F2 = "CASE-RPT-2026-0411"
F2_TASK = "FUP-CASE-RPT-2026-0411-E0"
F3_TASK = "FUP-CASE-RPT-2026-0421-E0"


def _report(report_id, received, term, **event_kw):
    return IntakeReport(
        report_id=report_id,
        source=ReportSource.PATIENT,
        received_on=received,
        patient=Patient(initials="T.T.", age=50, sex="F"),
        reporter=Reporter(name="Patient self report", email="tt@example.net"),
        suspect_product_code="NBT-101",
        suspect_product_name="Cardivane",
        events=[AdverseEvent(verbatim_term=term, onset_date=date(2026, 10, 3), **event_kw)],
    )


# ---------------------------------------------------------------------
# Raising: the exact requests, for the exact deficiencies.
# ---------------------------------------------------------------------


def test_validity_requests_name_the_missing_fields(followed):
    by_id = {v.task.task_id: v for v in followed.followup.list_views(DAY)}
    reporter_task = by_id["FUP-RPT-2026-0003"].task
    assert reporter_task.source == "validity"
    assert reporter_task.case_id is None
    assert reporter_task.pack_version == FOLLOW_UP_PACK_VERSION
    (item,) = reporter_task.items
    assert item.item_class == "identifiable_reporter"
    assert item.field == "reporter"
    assert "name, email or phone" in item.detail
    # The pack prices a missing reporter at 14 days from raising.
    assert reporter_task.class_days == 14
    assert reporter_task.raised_on == DAY
    assert reporter_task.due_on == date(2026, 10, 26)

    patient_task = by_id["FUP-RPT-2026-0004"].task
    (item,) = patient_task.items
    assert item.item_class == "identifiable_patient"
    assert item.field == "patient"
    assert patient_task.class_days == 30  # the slowest validity class
    assert patient_task.due_on == date(2026, 11, 11)

    fixture_task = by_id["FUP-RPT-2026-0431"].task
    assert [i.item_class for i in fixture_task.items] == ["identifiable_reporter"]


def test_validity_items_cover_every_criterion():
    items = validity_missing_items(
        ["identifiable_patient", "identifiable_reporter", "suspect_product", "adverse_event"]
    )
    assert [i.field for i in items] == ["patient", "reporter", "suspect_product", "events"]
    assert all(i.detail for i in items)


def test_no_request_for_a_complete_case(followed):
    assert followed.followup.tasks_for_case("CASE-RPT-2026-0001", DAY) == []
    assert followed.followup.tasks_for_case("CASE-RPT-2026-0304", DAY) == []


def test_coding_requests_name_the_verbatim_and_reason(followed):
    view = followed.followup.view(F2_TASK, DAY)
    task = view.task
    assert task.source == "coding"
    assert task.case_id == F2
    assert task.subject_index == 0
    assert task.decision_id is not None
    (item,) = task.items
    assert item.item_class == "uncoded_event"
    assert item.field == "events.0.verbatim_term"
    assert item.verbatim == "Rash across both hands"
    assert "no entry" in item.detail
    assert task.class_days == 30
    assert view.state == "open"
    assert view.cycle == 1


def test_assessment_request_names_the_absent_fact(followed):
    view = followed.followup.view(F1_TASK, DAY)
    task = view.task
    assert task.source == "assessment"
    (item,) = task.items
    assert item.item_class == "missing_case_fact"
    assert item.field == "events.0.outcome"
    assert item.blocks == ["death", "hospitalisation", "disability"]
    assert task.class_days == 14
    assert task.due_on == date(2026, 10, 26)


def test_assessment_requests_only_where_a_fact_is_missing(assessed):
    """The book's cannot-assess cases split exactly: 0305 (an
    absent outcome) and 0205 (an unidentified product) get
    assessment requests; the uncoded cases (0102, 0203, 0204, 0306)
    get coding requests only — the coding workflow owns them."""
    views = assessed.followup.list_views(DAY)
    assessment_cases = {
        v.task.case_id for v in views if v.task.source == "assessment"
    }
    assert assessment_cases == {"CASE-RPT-2026-0305", "CASE-RPT-2026-0205"}
    coding_cases = {v.task.case_id for v in views if v.task.source == "coding"}
    assert {
        "CASE-RPT-2026-0102",
        "CASE-RPT-2026-0203",
        "CASE-RPT-2026-0204",
        "CASE-RPT-2026-0306",
    } <= coding_cases
    product_item = next(
        v.task.items[0] for v in views if v.task.case_id == "CASE-RPT-2026-0205"
    )
    assert product_item.field == "suspect_product"


def test_raising_is_idempotent(services):
    from pv_cases.seed import followup_fixtures

    fixtures = followup_fixtures()
    services.intake.ingest(fixtures[0], DAY)
    services.intake.ingest(fixtures[3], DAY)  # invalid: raises its request at intake
    before = services.store.list_follow_up_requests()
    assert services.followup.raise_for_case(F1, DAY) == []
    assert services.followup.raise_for_invalid(fixtures[3], DAY) is None
    assert services.store.list_follow_up_requests() == before


# ---------------------------------------------------------------------
# The merge rule (pv_cases.reporting).
# ---------------------------------------------------------------------


def test_merge_rule_present_wins_identity_stands():
    base = _report("RPT-A", date(2026, 10, 1), "Vomiting", outcome=None)
    fu = _report(
        "RPT-B", date(2026, 10, 20), "Vomiting", outcome="fatal",
        description="Died at home.",
    )
    merged = merge_reports(base, fu)
    assert merged.report_id == "RPT-A"  # identity stands
    assert merged.received_on == date(2026, 10, 1)  # the receipt date stands
    assert merged.events[0].outcome == "fatal"
    assert merged.events[0].description == "Died at home."
    # An outcome of "unknown" in a follow-up carries no fact.
    fu2 = _report("RPT-C", date(2026, 10, 21), "Vomiting", outcome="unknown")
    assert merge_reports(merged, fu2).events[0].outcome == "fatal"
    # Extra events append; carried events keep unamended fields.
    fu3 = _report("RPT-D", date(2026, 10, 22), "Vomiting")
    fu3.events.append(AdverseEvent(verbatim_term="Nausea", onset_date=date(2026, 10, 21)))
    merged3 = merge_reports(merged, fu3)
    assert [e.verbatim_term for e in merged3.events] == ["Vomiting", "Nausea"]
    assert merged3.events[0].outcome == "fatal"


# ---------------------------------------------------------------------
# F1: the day-20 story (assessment request, breach stated).
# ---------------------------------------------------------------------


def test_day20_followup_resolves_and_the_breach_is_stated(followed):
    answers = followup_reports()
    first = followed.assessment.current_assessment(F1)
    assert first.seriousness.value == "cannot_assess"
    assert first.clock.obligation_class == "awaiting_assessment"

    submission = followed.followup.submit_follow_up(
        F1_TASK, answers["RPT-2026-0402"], date(2026, 10, 25)
    )
    assert submission.outcome == "resolution"
    assert submission.state == "resolved"
    (act,) = followed.store.list_follow_up_acts(F1_TASK)
    assert act.kind == "resolution"
    assert act.follow_up_report_id == "RPT-2026-0402"
    assert act.cycle == 1

    second = followed.assessment.current_assessment(F1)
    assert second.supersedes_id == first.assessment_id
    assert second.change == {
        "seriousness": ["cannot_assess", "serious"],
        "obligation_class": ["awaiting_assessment", "expedited_7_day"],
        "deadline": [None, "2026-10-12"],
    }
    # Day zero is the first valid receipt date and did not move.
    assert second.clock.day_zero == date(2026, 10, 5)
    assert second.clock.deadline == date(2026, 10, 12)
    board = {
        e.case_id: e
        for e in followed.assessment.clock_board(date(2026, 10, 25))
    }
    assert board[F1].state == "breached"
    assert board[F1].days_remaining == -13

    # The summary speaks the new verdict and its real deadline.
    from pv_cases.assessment import assessment_presentation_problems

    summary = followed.assembler.assemble(F1, date(2026, 10, 25))
    assert assessment_presentation_problems(second, summary.statements) == []
    texts = [s.text for s in summary.statements]
    assert any(
        t.startswith("Reporting clock: 7-day expedited — deadline 2026-10-12")
        for t in texts
    )
    assert followed.chain.verify_chain(F1)
    # The follow-up is on the chain as itself, not folded invisibly.
    kinds = [i.kind for i in followed.store.list_evidence(F1)]
    assert "follow_up_report" in kinds


# ---------------------------------------------------------------------
# F2: cycles — an attempt rolls, a landed fact resolves.
# ---------------------------------------------------------------------


def test_attempt_rolls_the_cycle_and_due_date(followed):
    answers = followup_reports()
    submission = followed.followup.submit_follow_up(
        F2_TASK, answers["RPT-2026-0412"], date(2026, 10, 16)
    )
    assert submission.outcome == "attempt"
    assert submission.cycle == 2
    assert submission.due_on == date(2026, 11, 15)  # attempt date + 30
    view = followed.followup.view(F2_TASK, date(2026, 10, 16))
    assert view.state == "open"
    assert view.cycle == 2
    # The folded report now carries the restated term — still uncoded.
    report = current_report(followed.store, followed.chain, F2)
    assert report.events[0].verbatim_term == "Rash over hands and wrists"
    (decision,) = [
        d
        for d in followed.coding.current_decisions(F2)
        if d.subject_kind == "event"
    ]
    assert decision.status.value == "uncoded"
    assert decision.verbatim == "Rash over hands and wrists"


def test_second_followup_resolves_the_coding_request(followed):
    answers = followup_reports()
    followed.followup.submit_follow_up(F2_TASK, answers["RPT-2026-0412"], date(2026, 10, 16))
    submission = followed.followup.submit_follow_up(
        F2_TASK, answers["RPT-2026-0413"], date(2026, 10, 28)
    )
    assert submission.outcome == "resolution"
    assert submission.cycle == 2
    acts = followed.store.list_follow_up_acts(F2_TASK)
    assert [a.kind for a in acts] == ["attempt", "resolution"]
    assert [a.follow_up_report_id for a in acts] == ["RPT-2026-0412", "RPT-2026-0413"]
    (decision,) = [
        d
        for d in followed.coding.current_decisions(F2)
        if d.subject_kind == "event"
    ]
    assert decision.status.value == "coded"
    assert decision.preferred_term == "Rash vesicular"
    artefact = followed.assessment.current_assessment(F2)
    assert artefact.seriousness.value == "serious"
    assert artefact.clock.obligation_class == "expedited_15_day"
    assert artefact.clock.day_zero == date(2026, 10, 6)
    assert artefact.clock.deadline == date(2026, 10, 21)
    # A resolved request takes no further reports.
    with pytest.raises(FollowUpError):
        followed.followup.submit_follow_up(
            F2_TASK, answers["RPT-2026-0413"], date(2026, 10, 29)
        )


# ---------------------------------------------------------------------
# F2b: escalation at the cycle cap.
# ---------------------------------------------------------------------


def test_third_failed_attempt_escalates(followed):
    answers = followup_reports()
    days = {
        "RPT-2026-0422": date(2026, 10, 17),
        "RPT-2026-0423": date(2026, 10, 27),
        "RPT-2026-0424": date(2026, 11, 6),
    }
    outcomes = [
        followed.followup.submit_follow_up(F3_TASK, answers[rid], day).outcome
        for rid, day in days.items()
    ]
    assert outcomes == ["attempt", "attempt", "escalation"]
    view = followed.followup.view(F3_TASK, date(2026, 11, 6))
    assert view.state == "escalated"
    assert view.cycle == 3
    acts = followed.store.list_follow_up_acts(F3_TASK)
    assert [a.kind for a in acts] == ["attempt", "attempt", "escalation"]
    assert acts[-1].new_due_on is None
    # Escalated work is on the board, named — never dropped, and
    # the raising scan never duplicates it.
    board = {e.task_id: e for e in followed.followup.board(date(2026, 11, 20))}
    assert board[F3_TASK].state == "escalated"
    assert followed.followup.raise_for_case("CASE-RPT-2026-0421", date(2026, 11, 20)) == []
    # And it takes no further reports.
    with pytest.raises(FollowUpError):
        followed.followup.submit_follow_up(
            F3_TASK, answers["RPT-2026-0424"], date(2026, 11, 7)
        )


# ---------------------------------------------------------------------
# F3: a validity follow-up founds the case — and links, never merges.
# ---------------------------------------------------------------------


def test_validity_followup_founds_the_case(followed):
    answers = followup_reports()
    cases_before = len(followed.store.list_cases())
    submission = followed.followup.submit_follow_up(
        "FUP-RPT-2026-0431", answers["RPT-2026-0432"], date(2026, 10, 15)
    )
    assert submission.outcome == "resolution"
    assert submission.new_case_id == "CASE-RPT-2026-0432"
    case = followed.store.get_case("CASE-RPT-2026-0432")
    assert case is not None
    assert len(followed.store.list_cases()) == cases_before + 1  # extended, not merged
    # Day zero is the date the report became valid — the follow-up's receipt.
    assert case.awareness_date == date(2026, 10, 15)
    # The duplicate is flagged and linked, the case itself untouched.
    assert case.suspected_duplicate_of == "CASE-RPT-2026-0005"
    link = followed.store.get_dedup_link(
        "LINK-CASE-RPT-2026-0005-CASE-RPT-2026-0432"
    )
    assert link is not None and link.band.value == "definite_duplicate"
    # The resolution act names the follow-up report and the case.
    (act,) = followed.store.list_follow_up_acts("FUP-RPT-2026-0431")
    assert act.follow_up_report_id == "RPT-2026-0432"
    assert act.case_id == "CASE-RPT-2026-0432"
    view = followed.followup.view("FUP-RPT-2026-0431", date(2026, 10, 15))
    assert view.state == "resolved"
    assert view.case_id == "CASE-RPT-2026-0432"
    assert followed.followup.tasks_for_case("CASE-RPT-2026-0432", date(2026, 10, 15))[
        0
    ].task.task_id == "FUP-RPT-2026-0431"
    # Provenance: original report and follow-up are both on the chain.
    kinds = [
        i.kind for i in followed.store.list_evidence("CASE-RPT-2026-0432")
    ]
    assert kinds[0] == "intake_report"
    assert "original_report" in kinds and "follow_up_report" in kinds
    assert followed.chain.verify_chain("CASE-RPT-2026-0432")


def test_validity_followup_that_still_fails_is_an_attempt(services):
    from pv_cases.seed import followup_fixtures

    original = followup_fixtures()[3]
    services.intake.ingest(original, DAY)
    # A follow-up that adds a phone-less second contact attempt but
    # still no identifiable reporter.
    weak = IntakeReport(
        report_id="RPT-2026-0471",
        source=ReportSource.CALL_CENTRE,
        received_on=date(2026, 10, 20),
        patient=Patient(initials="A.B.", date_of_birth=date(1988, 3, 14), sex="F"),
        reporter=Reporter(qualification="second call, caller hung up again"),
        suspect_product_code="NBT-317",
        suspect_product_name="Dermaclear",
        events=[
            AdverseEvent(
                verbatim_term="Blistering rash on hands",
                onset_date=date(2026, 10, 9),
                outcome="recovering",
            )
        ],
    )
    submission = services.followup.submit_follow_up(
        "FUP-RPT-2026-0431", weak, date(2026, 10, 20)
    )
    assert submission.outcome == "attempt"
    assert submission.new_case_id is None
    assert services.store.get_case("CASE-RPT-2026-0471") is None
    assert services.store.list_cases() == []
    # A later, qualifying follow-up still founds the case —
    # cumulative merge over the original and both answers.
    answers = followup_reports()
    submission = services.followup.submit_follow_up(
        "FUP-RPT-2026-0431", answers["RPT-2026-0432"], date(2026, 10, 22)
    )
    assert submission.outcome == "resolution"
    assert submission.new_case_id == "CASE-RPT-2026-0432"


# ---------------------------------------------------------------------
# Dedup interplay on the extending path.
# ---------------------------------------------------------------------


def _qr_home(services):
    home = IntakeReport(
        report_id="RPT-2026-0451",
        source=ReportSource.PATIENT,
        received_on=date(2026, 10, 10),
        patient=Patient(initials="Q.R.", age=60, sex="M"),
        reporter=Reporter(name="Patient self report", email="qr@example.net"),
        suspect_product_code="NBT-204",
        suspect_product_name="Somnirel",
        events=[
            AdverseEvent(
                verbatim_term="Woke up groggy and heavy headed",
                onset_date=date(2026, 10, 10),
                outcome="recovering",
            )
        ],
    )
    other = IntakeReport(
        report_id="RPT-2026-0452",
        source=ReportSource.CLINICIAN,
        received_on=date(2026, 10, 11),
        patient=Patient(initials="Q.R.", age=60, sex="M"),
        reporter=Reporter(
            name="Dr Mara Lindqvist",
            qualification="GP",
            email="mlindqvist@example-clinic.org",
        ),
        suspect_product_code="NBT-204",
        suspect_product_name="Somnirel",
        events=[
            AdverseEvent(
                verbatim_term="Severe headache",
                onset_date=date(2026, 10, 11),
                outcome="recovered",
            )
        ],
    )
    services.intake.ingest(home, DAY)
    services.intake.ingest(other, DAY)
    return home, other


def test_followup_links_its_case_to_a_matching_case(services):
    _qr_home(services)
    # The two cases do not band against each other: unrelated terms.
    assert services.store.list_dedup_links() == []
    answer = IntakeReport(
        report_id="RPT-2026-0453",
        source=ReportSource.PATIENT,
        received_on=date(2026, 10, 12),
        patient=Patient(initials="Q.R.", age=60, sex="M"),
        reporter=Reporter(name="Patient self report", email="qr@example.net"),
        suspect_product_code="NBT-204",
        suspect_product_name="Somnirel",
        events=[
            AdverseEvent(
                verbatim_term="Severe headache",
                onset_date=date(2026, 10, 11),
                outcome="recovered",
            )
        ],
    )
    submission = services.followup.submit_follow_up(
        "FUP-CASE-RPT-2026-0451-E0", answer, date(2026, 10, 12)
    )
    assert submission.outcome == "resolution"  # the term now codes
    assert submission.links_created == [
        "LINK-CASE-RPT-2026-0451-CASE-RPT-2026-0452"
    ]
    link = services.store.get_dedup_link(submission.links_created[0])
    assert link.band.value == "definite_duplicate"
    assert link.assessment_id.startswith("DEDUP-FU-RPT-2026-0453")
    # Linked, never merged: both cases stand, the later is flagged.
    assert len(services.store.list_cases()) == 2
    assert (
        services.store.get_case("CASE-RPT-2026-0452").status.value
        == "duplicate_linked"
    )
    assert services.chain.verify_chain("CASE-RPT-2026-0451")


# ---------------------------------------------------------------------
# Artefact discipline + the board.
# ---------------------------------------------------------------------


def test_requests_and_acts_are_insert_only(followed):
    store = followed.store
    for verb in (
        "update_follow_up_request",
        "delete_follow_up_request",
        "update_follow_up_act",
        "delete_follow_up_act",
    ):
        assert not hasattr(store, verb), verb
    before = store.get_follow_up_request(F2_TASK).model_dump()
    answers = followup_reports()
    followed.followup.submit_follow_up(F2_TASK, answers["RPT-2026-0412"], date(2026, 10, 16))
    assert store.get_follow_up_request(F2_TASK).model_dump() == before


def test_board_ageing_is_derived(followed):
    board = {e.task_id: e for e in followed.followup.board(date(2026, 11, 20))}
    overdue = board["FUP-RPT-2026-0003"]
    assert overdue.state == "overdue"
    assert overdue.days_overdue == 25  # due 2026-10-26
    assert board["FUP-RPT-2026-0004"].days_overdue == 9  # due 2026-11-11
    assert board[F2_TASK].days_overdue == 9  # due 2026-11-11, unanswered here
    # Unresolved work leads the board; a request read before its
    # due date is plainly open.
    assert board["FUP-RPT-2026-0003"].state == "overdue"
    assert followed.followup.board(date(2026, 11, 20))[0].state in (
        "escalated",
        "overdue",
    )
    view = followed.followup.view("FUP-RPT-2026-0003", DAY)
    assert view.state == "open" and view.days_overdue == 0


def test_assessment_missing_items_skip_coding_owned_absences(assessed):
    artefact = assessed.assessment.current_assessment("CASE-RPT-2026-0306")
    assert artefact.seriousness.value == "cannot_assess"
    assert assessment_missing_items(artefact) == []
    artefact = assessed.assessment.current_assessment("CASE-RPT-2026-0305")
    (item,) = assessment_missing_items(artefact)
    assert item.field == "events.0.outcome"


def test_unknown_request_and_case_errors(services):
    with pytest.raises(KeyError):
        services.followup.view("FUP-GHOST", DAY)
    with pytest.raises(KeyError):
        services.followup.submit_follow_up(
            "FUP-GHOST", followup_reports()["RPT-2026-0402"], DAY
        )
    with pytest.raises(KeyError):
        services.followup.raise_for_case("CASE-GHOST", DAY)
