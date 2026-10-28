"""Intake: reports become cases or follow-up tasks — never both,
never neither — and exact-key duplicate suspicion is flagged."""

from tests.conftest import DAY


def test_feed_outcomes(services):
    outcomes = services.intake.ingest_feed(services.feed.fetch_reports(), DAY)
    cases = [o for o in outcomes if o.case is not None]
    follow_ups = [o for o in outcomes if o.follow_up is not None]
    assert len(cases) == 4
    assert len(follow_ups) == 2
    assert {tuple(o.follow_up.missing_criteria) for o in follow_ups} == {
        ("identifiable_reporter",),
        ("identifiable_patient",),
    }


def test_invalid_reports_are_not_cases(services):
    services.intake.ingest_feed(services.feed.fetch_reports(), DAY)
    case_reports = {c.report_id for c in services.store.list_cases()}
    assert "RPT-2026-0003" not in case_reports
    assert "RPT-2026-0004" not in case_reports
    assert len(services.store.list_follow_ups()) == 2


def test_duplicate_pair_flagged_not_merged(fed):
    first = fed.store.get_case("CASE-RPT-2026-0005")
    second = fed.store.get_case("CASE-RPT-2026-0006")
    assert first is not None and second is not None
    assert first.suspected_duplicate_of is None
    assert second.suspected_duplicate_of == "CASE-RPT-2026-0005"
    assert first.dedup_key == second.dedup_key


def test_every_case_starts_with_intake_evidence(fed):
    for case in fed.store.list_cases():
        items = fed.store.list_evidence(case.case_id)
        assert len(items) == 1
        assert items[0].kind == "intake_report"
        payload = fed.chain.payload_of(items[0])
        assert payload["report_id"] == case.report_id


def test_follow_up_payload_preserved(services):
    services.intake.ingest_feed(services.feed.fetch_reports(), DAY)
    data = services.objects.get_object("follow-ups/FU-RPT-2026-0003.json")
    assert b"RPT-2026-0003" in data
