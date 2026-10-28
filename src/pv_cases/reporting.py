"""The report of record — Round 5.

A case's facts do not freeze at intake: a follow-up report amends
them. The report of record is therefore **derived in code**: the
intake report payload folded with every ``follow_up_report``
evidence item on the case's chain, in chain order, under one
stated merge rule:

- A follow-up amends facts; it does not re-identity the case. The
  founding report's ``report_id``, ``source`` and ``received_on``
  stand — which is why a follow-up can never move the case's day
  zero (the awareness date lives on the case record and is the
  merged report's receipt date only at founding).
- Patient, reporter, product and narrative fields: the follow-up's
  value wins when it is *present* (for text, non-blank and not a
  placeholder; an outcome recorded as "unknown" carries no fact
  and never overwrites one). Absent fields keep the earlier value.
- Events are matched by position: event *i* of a follow-up amends
  event *i* of the record under the same field rule; events beyond
  the record's count are appended.

Every consumer that reads a case's report — de-duplication,
coding, assessment, history — reads it through
:func:`current_report`, so a landed follow-up fact is visible to
all of them at once, and a case with no follow-ups reads exactly
as it did in Rounds 1–4.
"""

from __future__ import annotations

from pv_cases.models import (
    _PLACEHOLDERS,
    AdverseEvent,
    IntakeReport,
    Patient,
    Reporter,
)


def _present(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _PLACEHOLDERS


def _win_text(earlier: str | None, later: str | None) -> str | None:
    return later if _present(later) else earlier


def _merge_patient(base: Patient, fu: Patient) -> Patient:
    return Patient(
        initials=_win_text(base.initials, fu.initials),
        age=fu.age if fu.age is not None else base.age,
        age_group=_win_text(base.age_group, fu.age_group),
        sex=_win_text(base.sex, fu.sex),
        date_of_birth=(
            fu.date_of_birth if fu.date_of_birth is not None else base.date_of_birth
        ),
        reporter_patient_id=_win_text(
            base.reporter_patient_id, fu.reporter_patient_id
        ),
    )


def _merge_reporter(base: Reporter, fu: Reporter) -> Reporter:
    return Reporter(
        name=_win_text(base.name, fu.name),
        qualification=_win_text(base.qualification, fu.qualification),
        email=_win_text(base.email, fu.email),
        phone=_win_text(base.phone, fu.phone),
        country=_win_text(base.country, fu.country),
    )


def _merge_event(base: AdverseEvent, fu: AdverseEvent) -> AdverseEvent:
    return AdverseEvent(
        verbatim_term=(
            fu.verbatim_term if _present(fu.verbatim_term) else base.verbatim_term
        ),
        onset_date=fu.onset_date if fu.onset_date is not None else base.onset_date,
        outcome=_win_text(base.outcome, fu.outcome),
        description=_win_text(base.description, fu.description),
    )


def merge_reports(base: IntakeReport, follow_up: IntakeReport) -> IntakeReport:
    """Fold one follow-up report over a report of record. The
    result keeps the base report's identity (report id, source,
    receipt date) — see the module docstring for the full rule."""
    events: list[AdverseEvent] = []
    for i, event in enumerate(base.events):
        if i < len(follow_up.events):
            events.append(_merge_event(event, follow_up.events[i]))
        else:
            events.append(event)
    events.extend(follow_up.events[len(base.events):])
    return IntakeReport(
        report_id=base.report_id,
        source=base.source,
        received_on=base.received_on,
        patient=_merge_patient(base.patient, follow_up.patient),
        reporter=_merge_reporter(base.reporter, follow_up.reporter),
        suspect_product_code=_win_text(
            base.suspect_product_code, follow_up.suspect_product_code
        ),
        suspect_product_name=_win_text(
            base.suspect_product_name, follow_up.suspect_product_name
        ),
        events=events,
        narrative=_win_text(base.narrative, follow_up.narrative),
    )


def current_report(store, chain, case_id: str) -> IntakeReport:
    """The case's report of record: its intake payload folded with
    every follow-up report on its chain, in chain order."""
    report: IntakeReport | None = None
    for item in store.list_evidence(case_id):
        if item.kind == "intake_report" and report is None:
            report = IntakeReport.model_validate(chain.payload_of(item))
        elif item.kind == "follow_up_report" and report is not None:
            follow_up = IntakeReport.model_validate(chain.payload_of(item))
            report = merge_reports(report, follow_up)
    if report is None:
        raise KeyError(f"case {case_id} has no intake evidence")
    return report
