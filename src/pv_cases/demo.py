"""Demo: the planted feed end to end, offline.

Walks every planted report through the validity gate, shows which
became cases and which became follow-up tasks, flags the suspected
duplicate, assembles the main case's summary with its citations, and
verifies the evidence chain. Round 2 adds the dedup queue (the
planted pair, assessed definite by the rule pack, awaiting a human)
and the A.B. patient's history view. Round 3 adds the coding layer:
the dictionary version in force, the main case's coded terms, and
the coding-review queue. Round 4 adds the assessment layer: the
planted seriousness/expectedness/clock fixtures graded by the three
packs, and the clock board read a few days later. Round 5 adds the
follow-up workflow: the requests the book's deficiencies raise, and
one case whose missing outcome lands on day 20 — after its 7-day
clock has already run out. Round 6 adds the medical-reviewer
gate: dual control over the assembled record, a reject that holds
a case until the record itself changes, and the review queue.
Run with ``make demo``.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from pv_cases.config import Settings
from pv_cases.wiring import Services

DEMO_DAY = date(2026, 10, 12)


def run_demo(out=print) -> Services:
    tmp = Path(tempfile.mkdtemp(prefix="pv-demo-"))
    settings = Settings(
        sqlite_path=str(tmp / "pv.db"),
        object_store_dir=str(tmp / "objects"),
    )
    svc = Services(settings)

    out("Northbridge Therapeutics — pharmacovigilance intake demo")
    out("=" * 62)
    outcomes = svc.intake.ingest_feed(svc.feed.fetch_reports(), DEMO_DAY)
    for outcome in outcomes:
        if outcome.case is not None:
            dup = (
                f"  [suspected duplicate of {outcome.case.suspected_duplicate_of}]"
                if outcome.case.suspected_duplicate_of
                else ""
            )
            out(f"{outcome.report_id}: CASE {outcome.case.case_id}{dup}")
        else:
            missing = ", ".join(outcome.follow_up.missing_criteria)
            out(f"{outcome.report_id}: FOLLOW-UP {outcome.follow_up.task_id} (missing: {missing})")

    out("-" * 62)
    summary = svc.assembler.assemble("CASE-RPT-2026-0001", DEMO_DAY)
    out(f"Summary for CASE-RPT-2026-0001 (profile: {summary.model_profile}):")
    for stmt in summary.statements:
        cites = ", ".join(f"{c.evidence_item_id}:{c.field}" for c in stmt.citations)
        out(f"  - {stmt.text}  [{cites}]")
    ok = svc.chain.verify_chain("CASE-RPT-2026-0001")
    out(f"Evidence chain valid: {ok}")

    out("-" * 62)
    queue = svc.dedup.pending_queue()
    out(f"Dedup queue: {len(queue)} link(s) awaiting a named human decision")
    for link in queue:
        out(f"  {link.link_id}: {link.case_b_id} duplicates {link.case_a_id} [{link.band.value}]")
    view = svc.history.patient_history_for_case("CASE-RPT-2026-0005", DEMO_DAY)
    terms = ", ".join(f"{t} x{n}" for t, n in sorted(view.event_term_counts.items()))
    out(
        f"History for patient {view.patient_key}: {view.case_count} case(s), "
        f"{view.event_count} event(s) — {terms}"
    )

    out("-" * 62)
    dictionary = svc.coding.dictionary(today=DEMO_DAY)
    out(
        f"Coding dictionary {dictionary.version} (repo-built extract,"
        " not licensed MedDRA):"
    )
    for decision in svc.coding.current_decisions("CASE-RPT-2026-0001"):
        if decision.status.value == "coded":
            label = (
                decision.preferred_term
                if decision.subject_kind == "event"
                else f"{decision.product_name} ({decision.product_code})"
            )
            out(
                f"  {decision.subject_kind} {decision.verbatim!r} -> {label}"
                f" [{decision.match_path.value}]"
            )
        else:
            out(f"  {decision.subject_kind} {decision.verbatim!r} -> UNCODED")
    queue = svc.coding.uncoded_queue()
    out(f"Coding-review queue: {len(queue)} uncoded term(s) awaiting a named coder")
    for task in queue:
        out(f"  {task.task_id}: {task.verbatim!r} [{task.reason}]")

    out("-" * 62)
    from pv_cases.seed import assessment_fixtures

    out("Round 4 fixtures — seriousness, expectedness, clocks (in code):")
    for report in assessment_fixtures():
        svc.intake.ingest(report, DEMO_DAY)
        artefact = svc.assessment.current_assessment(f"CASE-{report.report_id}")
        fired = [
            f.criterion
            for f in artefact.criteria
            if f.status.value == "fired"
        ]
        clock = artefact.clock
        deadline = f", deadline {clock.deadline}" if clock.deadline else ""
        out(
            f"  CASE-{report.report_id}: {artefact.seriousness.value}"
            f" [{', '.join(fired) or 'no criterion fired'}],"
            f" {artefact.expectedness.value},"
            f" {clock.obligation_class}{deadline}"
        )
    board_day = date(2026, 10, 22)
    out(f"Clock board at {board_day}:")
    for entry in svc.assessment.clock_board(board_day):
        if entry.deadline is None:
            continue
        out(
            f"  {entry.case_id}: {entry.obligation_class},"
            f" deadline {entry.deadline} — {entry.state}"
            f" ({entry.days_remaining:+d}d)"
        )

    out("-" * 62)
    from pv_cases.seed import followup_fixtures, followup_reports

    out("Round 5 — the follow-up workflow (requests name the missing fact):")
    for report in followup_fixtures():
        svc.intake.ingest(report, DEMO_DAY)
    for view in svc.followup.list_views(DEMO_DAY):
        fields = ", ".join(item.field for item in view.task.items)
        case = view.case_id or "no case yet"
        out(
            f"  {view.task.task_id} [{view.task.source}] {case}:"
            f" missing {fields} — due {view.due_on} (cycle {view.cycle})"
        )
    out("The day-20 story: CASE-RPT-2026-0401 cannot be assessed —")
    out("its event has no outcome. The follow-up lands on 2026-10-25:")
    answers = followup_reports()
    submission = svc.followup.submit_follow_up(
        "FUP-CASE-RPT-2026-0401-A", answers["RPT-2026-0402"], date(2026, 10, 25)
    )
    artefact = svc.assessment.current_assessment("CASE-RPT-2026-0401")
    out(
        f"  request {submission.task_id}: {submission.outcome}"
        f" by {submission.follow_up_report_id}"
    )
    out(
        f"  assessment now: {artefact.seriousness.value},"
        f" {artefact.expectedness.value},"
        f" {artefact.clock.obligation_class} — day zero"
        f" {artefact.clock.day_zero} (unmoved), deadline"
        f" {artefact.clock.deadline}"
    )
    entry = next(
        e
        for e in svc.assessment.clock_board(date(2026, 10, 25))
        if e.case_id == "CASE-RPT-2026-0401"
    )
    out(
        f"  clock at 2026-10-25: {entry.state}"
        f" ({entry.days_remaining:+d}d) — the breach is stated,"
        " not hidden"
    )

    out("-" * 62)
    from pv_cases.models import AdverseEvent, IntakeReport, Patient, Reporter, ReportSource

    out("Round 6 — medical review (dual control, in code):")
    queue = svc.review.queue(DEMO_DAY)
    buckets: dict[str, int] = {}
    for entry in queue:
        buckets[entry.bucket] = buckets.get(entry.bucket, 0) + 1
    out(
        "  review queue: "
        + ", ".join(f"{b} {n}" for b, n in sorted(buckets.items()))
    )
    act = svc.review.record_review(
        "CASE-RPT-2026-0001", "Dr A. Rao", "medical_reviewer",
        "accept", "", DEMO_DAY,
    )
    view = svc.review.review_for_case("CASE-RPT-2026-0001")
    out(
        f"  {act.act_id}: medical accept by {act.reviewer}"
        f" — case is {view.state} (missing:"
        f" {', '.join(view.missing_roles)})"
    )
    svc.review.record_review(
        "CASE-RPT-2026-0001", "Dr B. Lindqvist", "safety_reviewer",
        "accept_with_comment", "Clock and coding verified against source.",
        DEMO_DAY,
    )
    view = svc.review.review_for_case("CASE-RPT-2026-0001")
    out(f"  safety accept recorded — case is {view.state}")
    out("The hold: CASE-RPT-2026-0305 cannot be assessed — its")
    out("event has no outcome. The safety reviewer rejects it:")
    svc.review.record_review(
        "CASE-RPT-2026-0305", "Dr B. Lindqvist", "safety_reviewer",
        "reject_with_reason",
        "Seriousness cannot be confirmed until the outcome is on record.",
        DEMO_DAY,
    )
    svc.review.record_review(
        "CASE-RPT-2026-0305", "Dr A. Rao", "medical_reviewer",
        "accept", "", DEMO_DAY,
    )
    view = svc.review.review_for_case("CASE-RPT-2026-0305")
    out(
        f"  a medical accept over the same record does not clear it:"
        f" {view.state} — {view.hold_reason}"
    )
    answer = IntakeReport(
        report_id="RPT-2026-0305-FU1",
        source=ReportSource.CALL_CENTRE,
        received_on=date(2026, 10, 25),
        patient=Patient(initials="H.B.", age=62, sex="M"),
        reporter=Reporter(name="Call centre agent R. Patel", phone="+1-555-0199"),
        suspect_product_code="NBT-101",
        suspect_product_name="Cardivane",
        events=[
            AdverseEvent(
                verbatim_term="Severe headache",
                onset_date=date(2026, 10, 19),
                outcome="recovered",
                description="Recovered fully within two days;"
                " no hospital care was needed.",
            )
        ],
    )
    svc.followup.submit_follow_up(
        "FUP-CASE-RPT-2026-0305-A", answer, date(2026, 10, 25)
    )
    view = svc.review.review_for_case("CASE-RPT-2026-0305")
    stale = sum(1 for a in view.acts if a.stale)
    out(
        f"  the follow-up lands the outcome; the assessment is"
        f" rewritten — hold cleared, {stale} act(s) now stale,"
        f" case {view.state}"
    )
    svc.review.record_review(
        "CASE-RPT-2026-0305", "Dr A. Rao", "medical_reviewer",
        "accept", "", date(2026, 10, 25),
    )
    svc.review.record_review(
        "CASE-RPT-2026-0305", "Dr B. Lindqvist", "safety_reviewer",
        "accept", "", date(2026, 10, 25),
    )
    view = svc.review.review_for_case("CASE-RPT-2026-0305")
    out(f"  a fresh dual review decides the new record: {view.state}")
    return svc


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()
