"""Round 2 de-duplication: the rule pack grades the planted pairs
into their stated bands, links route to a human queue, decisions
are recorded acts — and nothing is ever merged or deleted."""

import pytest

from pv_cases.dedup import DedupDecisionError, assess_pair
from pv_cases.models import CaseStatus, DedupBand
from tests.conftest import DAY

FIRST = "CASE-RPT-2026-0005"
SECOND = "CASE-RPT-2026-0006"
DEFINITE_LINK = f"LINK-{FIRST}-{SECOND}"
PROBABLE_LINK = "LINK-CASE-RPT-2026-0101-CASE-RPT-2026-0102"


def _assessment(svc, a: str, b: str):
    for item in svc.store.list_dedup_assessments():
        if {item.case_a_id, item.case_b_id} == {a, b}:
            return item
    raise AssertionError(f"no assessment for {a} / {b}")


def _pair(svc, a_id: str, b_id: str):
    case_a, case_b = svc.store.get_case(a_id), svc.store.get_case(b_id)
    return (case_a, svc.dedup.report_of(case_a)), (case_b, svc.dedup.report_of(case_b))


def test_planted_pair_is_definite_with_named_signals(full):
    item = _assessment(full, FIRST, SECOND)
    assert item.band is DedupBand.DEFINITE
    assert item.score == 95  # 40 patient + 25 product + 20 event + 10 onset
    names = {s.name for s in item.signals}
    assert {
        "patient_exact_key",
        "product_same_code",
        "event_term_exact",
        "onset_same_day",
    } <= names
    assert not any(s.gate for s in item.signals)
    assert item.pack_version == "2026.10.1"


def test_probable_pair_lands_in_its_band(full):
    item = _assessment(full, "CASE-RPT-2026-0101", "CASE-RPT-2026-0102")
    assert item.band is DedupBand.PROBABLE
    assert item.score == 63  # 22 patient + 25 product + 10 overlap + 6 onset
    names = {s.name for s in item.signals}
    assert {
        "patient_moderate_identity",
        "product_same_code",
        "event_term_overlap",
        "onset_within_7_days",
    } <= names


@pytest.mark.parametrize(
    "other,gate",
    [
        # A genuine second case: same patient + product, new event.
        ("CASE-RPT-2026-0103", "event_terms_unrelated"),
        # Namesake: same initials/event/onset, contradicting DOB.
        ("CASE-RPT-2026-0104", "patient_dob_contradicts"),
        # Recurrence: same everything, onset a year earlier.
        ("CASE-RPT-2026-0105", "onset_gap_exceeds_window"),
        # New minor event for the same patient.
        ("CASE-RPT-2026-0106", "event_terms_unrelated"),
    ],
)
def test_traps_are_not_duplicates(full, other, gate):
    item = _assessment(full, FIRST, other)
    assert item.band is DedupBand.NOT_DUPLICATE
    assert gate in {s.name for s in item.signals if s.gate}


def test_assess_pair_is_pure_and_deterministic(full):
    (ca, ra), (cb, rb) = _pair(full, FIRST, "CASE-RPT-2026-0103")
    before = len(full.store.list_dedup_assessments())
    one = assess_pair(ca, ra, cb, rb, DAY)
    two = assess_pair(ca, ra, cb, rb, DAY)
    assert one == two
    assert len(full.store.list_dedup_assessments()) == before


def test_intake_links_both_findings_and_marks_later_cases(full):
    queue = full.dedup.pending_queue()
    assert {l.link_id for l in queue} == {DEFINITE_LINK, PROBABLE_LINK}
    assert full.store.get_case(SECOND).status is CaseStatus.DUPLICATE_LINKED
    assert (
        full.store.get_case("CASE-RPT-2026-0102").status
        is CaseStatus.DUPLICATE_LINKED
    )
    # Both members of both pairs stay readable, chains intact.
    for case in full.store.list_cases():
        assert full.chain.verify_chain(case.case_id)


def test_scan_is_idempotent(full):
    assert len(full.store.list_dedup_assessments()) == 45  # C(10, 2)
    assert full.dedup.scan_all(DAY) == []
    assert len(full.store.list_dedup_links()) == 2


def test_confirm_closes_later_case_but_deletes_nothing(full):
    total = len(full.store.list_cases())
    link = full.dedup.confirm_link(DEFINITE_LINK, "Dr A. Reviewer", DAY, note="same fax, twice")
    assert link.status.value == "confirmed"
    assert link.decided_by == "Dr A. Reviewer"
    closed = full.store.get_case(SECOND)
    assert closed.status is CaseStatus.CLOSED_AS_DUPLICATE
    # Closed, not gone: the case, its summary path and its chain all
    # still work, and the decision itself is on the chain.
    assert len(full.store.list_cases()) == total
    kinds = [i.kind for i in full.store.list_evidence(SECOND)]
    assert "dedup_decision" in kinds
    assert full.chain.verify_chain(SECOND)
    decision = next(
        full.chain.payload_of(i)
        for i in full.store.list_evidence(SECOND)
        if i.kind == "dedup_decision"
    )
    assert decision["decided_by"] == "Dr A. Reviewer"
    assert decision["duplicate_of"] == FIRST


def test_reject_restores_the_case_and_it_proceeds(full):
    full.dedup.reject_link(PROBABLE_LINK, "Dr A. Reviewer", DAY, note="two patients, same initials")
    case = full.store.get_case("CASE-RPT-2026-0102")
    assert case.status is CaseStatus.OPEN
    summary = full.assembler.assemble(case.case_id, DAY)
    assert summary.statements
    assert (
        full.store.get_case(case.case_id).status is CaseStatus.SUMMARY_ASSEMBLED
    )


def test_decisions_need_a_named_human(full):
    for bad in ("", "  ", "unknown", "n/a", None):
        with pytest.raises(DedupDecisionError):
            full.dedup.confirm_link(DEFINITE_LINK, bad, DAY)
    assert full.store.get_dedup_link(DEFINITE_LINK).status.value == "pending"


def test_a_decided_link_does_not_move(full):
    full.dedup.confirm_link(DEFINITE_LINK, "Dr A. Reviewer", DAY)
    with pytest.raises(DedupDecisionError):
        full.dedup.confirm_link(DEFINITE_LINK, "Dr B. Other", DAY)
    with pytest.raises(DedupDecisionError):
        full.dedup.reject_link(DEFINITE_LINK, "Dr B. Other", DAY)


def test_unknown_link_is_a_key_error(full):
    with pytest.raises(KeyError):
        full.dedup.confirm_link("LINK-GHOST", "Dr A. Reviewer", DAY)


def test_there_is_no_merge_or_delete_surface(full):
    # The structural half of "auto-merges = 0": neither the service
    # nor the store port grows a merge/delete verb for cases.
    for obj in (full.dedup, full.store):
        for verb in ("merge_cases", "merge", "delete_case", "remove_case"):
            assert not hasattr(obj, verb), f"{obj!r} unexpectedly has {verb}"
