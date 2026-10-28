"""The validity gate: the four criteria, each failed alone and in
combination, over synthetic reports and the planted feed."""

from datetime import date

from pv_cases.models import (
    AdverseEvent,
    IntakeReport,
    Patient,
    Reporter,
    ReportSource,
)
from pv_cases.seed import reference_feed
from pv_cases.validity import assess_validity


def _report(**over) -> IntakeReport:
    base = dict(
        report_id="RPT-T-1",
        source=ReportSource.PATIENT,
        received_on=date(2026, 10, 1),
        patient=Patient(initials="Z.Y.", age=30, sex="F"),
        reporter=Reporter(name="Reporter One", email="r1@example.net"),
        suspect_product_code="NBT-101",
        suspect_product_name="Cardivane",
        events=[AdverseEvent(verbatim_term="Headache", onset_date=date(2026, 9, 30))],
    )
    base.update(over)
    return IntakeReport(**base)


def test_complete_report_is_valid():
    result = assess_validity(_report())
    assert result.valid and result.missing == []


def test_each_criterion_fails_alone():
    assert assess_validity(_report(patient=Patient())).missing == ["identifiable_patient"]
    assert assess_validity(_report(reporter=Reporter(qualification="nurse"))).missing == [
        "identifiable_reporter"
    ]
    assert assess_validity(
        _report(suspect_product_code=None, suspect_product_name=None)
    ).missing == ["suspect_product"]
    assert assess_validity(_report(events=[])).missing == ["adverse_event"]


def test_placeholder_values_do_not_identify():
    report = _report(
        patient=Patient(initials="unknown"),
        reporter=Reporter(name="unknown", email="n/a"),
    )
    result = assess_validity(report)
    assert result.missing == ["identifiable_patient", "identifiable_reporter"]


def test_patient_identifiable_by_any_single_characteristic():
    for patient in (
        Patient(age_group="30-39"),
        Patient(sex="M"),
        Patient(date_of_birth=date(1990, 1, 1)),
        Patient(reporter_patient_id="P-1"),
    ):
        assert assess_validity(_report(patient=patient)).valid


def test_reporter_identifiable_by_contact_channel_alone():
    assert assess_validity(_report(reporter=Reporter(phone="+1-555-0100"))).valid


def test_planted_feed_validity_shape():
    by_id = {r.report_id: assess_validity(r) for r in reference_feed()}
    assert by_id["RPT-2026-0001"].valid
    assert by_id["RPT-2026-0002"].valid
    assert by_id["RPT-2026-0003"].missing == ["identifiable_reporter"]
    assert by_id["RPT-2026-0004"].missing == ["identifiable_patient"]
    assert by_id["RPT-2026-0005"].valid
    assert by_id["RPT-2026-0006"].valid
