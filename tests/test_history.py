"""Round 2 history views: derived in code from stored artefacts,
content-addressed, conflicts shown never merged — and consumed by
the assembler with citations that resolve to the view."""

from datetime import date

from pv_cases.models import (
    AdverseEvent,
    IntakeReport,
    Patient,
    Reporter,
    ReportSource,
)
from tests.conftest import DAY

AB_KEY = "dob:1988-03-14|f"


def test_patient_history_figures(full):
    view = full.history.build_patient_history(AB_KEY, DAY)
    assert view.case_count == 5
    assert view.event_count == 5
    assert set(view.case_ids) == {
        "CASE-RPT-2026-0005",
        "CASE-RPT-2026-0006",
        "CASE-RPT-2026-0103",
        "CASE-RPT-2026-0105",
        "CASE-RPT-2026-0106",
    }
    assert view.event_term_counts == {
        "blistering rash on hands": 3,
        "severe headache": 1,
        "itchy skin where cream applied": 1,
    }
    assert view.product_codes == ["NBT-317"]
    assert view.follow_up_task_ids == []


def test_patient_history_shows_conflicts_never_merged(full):
    view = full.history.build_patient_history(AB_KEY, DAY)
    # RPT-2026-0105 records age 38 (consistent with the DOB);
    # RPT-2026-0106 records 52. Both stand.
    assert view.identity_conflicts == {"age": ["38", "52"]}
    ages = {f.value for f in view.identity_facts if f.field == "age"}
    assert ages == {"38", "52"}
    # Every fact keeps the case it was read from.
    assert all(f.case_id.startswith("CASE-") for f in view.identity_facts)


def test_namesake_is_a_different_patient_history(full):
    view = full.history.build_patient_history("dob:1991-07-02|f", DAY)
    assert view.case_ids == ["CASE-RPT-2026-0104"]
    assert view.identity_conflicts == {}


def test_product_history_figures(full):
    view = full.history.build_product_history("NBT-317", DAY)
    assert view.product_name == "Dermaclear"
    assert view.case_count == 6
    assert view.event_count == 6
    assert view.event_term_counts["blistering rash on hands"] == 4
    # The invalid Dermaclear report is follow-up work, and it is
    # part of the product's history.
    assert view.follow_up_task_ids == ["FU-RPT-2026-0004"]
    assert set(view.patient_keys) == {AB_KEY, "dob:1991-07-02|f"}


def test_follow_up_only_patient_history(full):
    # The J.T. report never became a case (no identifiable
    # reporter), but the chase is part of that patient's history.
    view = full.history.build_patient_history("init:j t|m", DAY)
    assert view.case_count == 0
    assert view.follow_up_task_ids == ["FU-RPT-2026-0003"]


def test_rebuild_is_content_addressed(full):
    one = full.history.build_patient_history(AB_KEY, DAY)
    two = full.history.build_patient_history(AB_KEY, DAY)
    assert one.view_id == two.view_id
    assert two.version == 1
    assert len(full.store.list_history_views("patient", AB_KEY)) == 1


def test_new_case_makes_a_new_version(full):
    one = full.history.build_patient_history(AB_KEY, DAY)
    extra = IntakeReport(
        report_id="RPT-2026-0200",
        source=ReportSource.PATIENT,
        received_on=date(2026, 10, 20),
        patient=Patient(initials="A.B.", date_of_birth=date(1988, 3, 14), sex="F"),
        reporter=Reporter(name="Patient self report", email="ab@example.net"),
        suspect_product_code="NBT-317",
        suspect_product_name="Dermaclear",
        events=[
            AdverseEvent(
                verbatim_term="Dry skin patches",
                onset_date=date(2026, 10, 19),
                outcome="recovering",
            )
        ],
    )
    full.intake.ingest(extra, DAY)
    two = full.history.build_patient_history(AB_KEY, DAY)
    assert two.view_id != one.view_id
    assert two.version == 2
    assert two.case_count == 6


def test_assembler_consumes_the_history_view(full):
    full.history.build_patient_history(AB_KEY, DAY)
    summary = full.assembler.assemble("CASE-RPT-2026-0005", DAY)
    texts = [s.text for s in summary.statements]
    assert "Patient history: 5 case(s) on record for this patient." in texts
    assert "Patient history: 5 event(s) on record for this patient." in texts
    # The history statements cite the captured view artefact, and
    # every citation in the summary resolves.
    assert full.assembler.resolver.problems("CASE-RPT-2026-0005", summary.statements) == []
    kinds = [i.kind for i in full.store.list_evidence("CASE-RPT-2026-0005")]
    assert "patient_history" in kinds
    assert full.chain.verify_chain("CASE-RPT-2026-0005")


def test_assembler_without_a_built_view_is_unchanged(full):
    summary = full.assembler.assemble("CASE-RPT-2026-0001", DAY)
    assert not any(s.text.startswith("Patient history") for s in summary.statements)
    kinds = [i.kind for i in full.store.list_evidence("CASE-RPT-2026-0001")]
    assert "patient_history" not in kinds


def test_case_history_lookup_by_case(full):
    view = full.history.patient_history_for_case("CASE-RPT-2026-0104", DAY)
    assert view.patient_key == "dob:1991-07-02|f"
