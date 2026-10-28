"""Round 6: medical review — dual control.

A case is medically reviewed only when two named reviewers, one
per role and two distinct people, have recorded accepting acts
over the case's current record — the assessment, the coding
decisions and the summary, each bound by id and hash. A reject
holds the case until the record it bound is superseded; a
superseded record makes every act bound to it stale, and stale
acts count for nothing. Acts are insert-only, recorded on the
evidence chain, and the queue shows review work without a single
patient- or reporter-identifying field.
"""

from datetime import date

import pytest

from pv_cases.models import (
    REVIEW_STATE_AWAITING_FIRST,
    REVIEW_STATE_AWAITING_SECOND,
    REVIEW_STATE_HOLD,
    REVIEW_STATE_REVIEWED,
    AdverseEvent,
    IntakeReport,
    Patient,
    Reporter,
    ReportSource,
)
from pv_cases.review import REVIEW_PACK, REVIEW_PACK_VERSION, ReviewError
from tests.conftest import DAY

MEDICAL = "medical_reviewer"
SAFETY = "safety_reviewer"
RAO = "Dr A. Rao"
LINDQVIST = "Dr B. Lindqvist"
COMPLETE = "CASE-RPT-2026-0304"  # a complete, assessable case
NO_OUTCOME = "CASE-RPT-2026-0305"  # assessment stands, facts missing
NO_OUTCOME_TASK = "FUP-CASE-RPT-2026-0305-A"


def _accept(svc, case_id, reviewer=RAO, role=MEDICAL, today=DAY):
    return svc.review.record_review(case_id, reviewer, role, "accept", "", today)


def _dual_review(svc, case_id, today=DAY):
    _accept(svc, case_id, today=today)
    return svc.review.record_review(
        case_id, LINDQVIST, SAFETY, "accept", "", today
    )


# ---------------------------------------------------------------------
# The gate: two roles, two people, one record.
# ---------------------------------------------------------------------


def test_dual_control_completes_the_gate(assessed):
    svc = assessed
    view = svc.review.review_for_case(COMPLETE)
    assert view.state == REVIEW_STATE_AWAITING_FIRST
    assert view.missing_roles == [MEDICAL, SAFETY]

    _accept(svc, COMPLETE)
    view = svc.review.review_for_case(COMPLETE)
    assert view.state == REVIEW_STATE_AWAITING_SECOND
    assert view.accepted_roles == {MEDICAL: RAO}
    assert view.missing_roles == [SAFETY]

    _accept(svc, COMPLETE, reviewer=LINDQVIST, role=SAFETY)
    view = svc.review.review_for_case(COMPLETE)
    assert view.state == REVIEW_STATE_REVIEWED
    assert view.accepted_roles == {MEDICAL: RAO, SAFETY: LINDQVIST}
    assert view.pack_version == REVIEW_PACK_VERSION


def test_either_role_may_review_first(assessed):
    _accept(assessed, COMPLETE, reviewer=LINDQVIST, role=SAFETY)
    view = assessed.review.review_for_case(COMPLETE)
    assert view.state == REVIEW_STATE_AWAITING_SECOND
    assert view.missing_roles == [MEDICAL]


def test_act_binds_the_record_it_reviewed(assessed):
    svc = assessed
    svc.assembler.assemble(COMPLETE, DAY)
    act = _accept(svc, COMPLETE)
    kinds = {b.artefact_kind for b in act.bindings}
    assert kinds == {"assessment", "coding_decision", "summary"}
    assessment = svc.assessment.current_assessment(COMPLETE)
    bound = next(b for b in act.bindings if b.artefact_kind == "assessment")
    assert bound.artefact_id == assessment.assessment_id
    assert all(b.artefact_hash for b in act.bindings)
    # Recorded on the case's evidence chain, which still verifies.
    kinds_on_chain = [i.kind for i in svc.store.list_evidence(COMPLETE)]
    assert "medical_review" in kinds_on_chain
    assert svc.chain.verify_chain(COMPLETE)


def test_reassembly_of_identical_narrative_does_not_stale_acts(assessed):
    svc = assessed
    svc.assembler.assemble(COMPLETE, DAY)
    _dual_review(svc, COMPLETE)
    assert svc.review.review_for_case(COMPLETE).state == REVIEW_STATE_REVIEWED
    # A re-assembly writes a new summary id, but the narrative
    # text is unchanged — the same version, so the acts stand.
    svc.assembler.assemble(COMPLETE, DAY)
    view = svc.review.review_for_case(COMPLETE)
    assert view.state == REVIEW_STATE_REVIEWED
    assert not any(a.stale for a in view.acts)


# ---------------------------------------------------------------------
# Refusals, named in code.
# ---------------------------------------------------------------------


def test_same_person_in_both_roles_is_refused(assessed):
    svc = assessed
    _accept(svc, COMPLETE)
    with pytest.raises(ReviewError, match="dual control"):
        svc.review.record_review(
            COMPLETE, RAO, SAFETY, "accept", "", DAY
        )
    # The refused act was never recorded.
    assert len(svc.store.list_review_acts(COMPLETE)) == 1


def test_same_person_may_act_again_once_their_act_is_stale(assessed):
    svc = assessed
    _accept(svc, NO_OUTCOME)
    # The follow-up rewrites the record; the earlier act is stale
    # and refuses nothing — the same person may take either role
    # on the new record (but still only one of them).
    _land_outcome(svc)
    act = svc.review.record_review(
        NO_OUTCOME, RAO, SAFETY, "accept", "", date(2026, 10, 25)
    )
    assert act.role == SAFETY


def test_recording_refusals_are_named(assessed):
    svc = assessed
    with pytest.raises(ReviewError, match="named human reviewer"):
        svc.review.record_review(COMPLETE, "unknown", MEDICAL, "accept", "", DAY)
    with pytest.raises(ReviewError, match="unknown reviewer role"):
        svc.review.record_review(COMPLETE, RAO, "chief", "accept", "", DAY)
    with pytest.raises(ReviewError, match="unknown review decision"):
        svc.review.record_review(COMPLETE, RAO, MEDICAL, "shrug", "", DAY)
    with pytest.raises(ReviewError, match="must carry its reason"):
        svc.review.record_review(COMPLETE, RAO, MEDICAL, "reject_with_reason", "", DAY)
    with pytest.raises(ReviewError, match="must carry its comment"):
        svc.review.record_review(
            COMPLETE, RAO, MEDICAL, "accept_with_comment", "  ", DAY
        )
    with pytest.raises(KeyError):
        svc.review.record_review("CASE-NOPE", RAO, MEDICAL, "accept", "", DAY)
    assert svc.store.list_review_acts(COMPLETE) == []


def test_review_of_a_case_with_no_assessment_is_refused(services):
    from pv_cases.models import Case, CaseStatus

    services.store.add_case(
        Case(
            case_id="CASE-BARE",
            report_id="RPT-BARE",
            source=ReportSource.PATIENT,
            received_on=DAY,
            awareness_date=DAY,
            status=CaseStatus.OPEN,
            dedup_key="bare",
        )
    )
    with pytest.raises(ReviewError, match="no assessment to review"):
        services.review.record_review("CASE-BARE", RAO, MEDICAL, "accept", "", DAY)


def test_closed_duplicate_is_not_reviewable(full):
    svc = full
    link = next(
        l for l in svc.store.list_dedup_links() if l.status.value == "pending"
    )
    svc.dedup.confirm_link(link.link_id, "Dr D. Osei", DAY)
    closed_id = link.case_b_id
    with pytest.raises(ReviewError, match="closed as a duplicate"):
        svc.review.record_review(closed_id, RAO, MEDICAL, "accept", "", DAY)
    queue_ids = {e.case_id for e in svc.review.queue(DAY)}
    assert closed_id not in queue_ids


# ---------------------------------------------------------------------
# Staleness: the record moves, the acts stop counting.
# ---------------------------------------------------------------------


def _land_outcome(svc, today=date(2026, 10, 25)):
    """Answer 0305's assessment follow-up: the outcome lands, the
    case is reassessed, and its assessment is superseded."""
    answer = IntakeReport(
        report_id="RPT-2026-0305-FU1",
        source=ReportSource.CALL_CENTRE,
        received_on=today,
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
    return svc.followup.submit_follow_up(NO_OUTCOME_TASK, answer, today)


def test_superseded_record_stales_every_act(followed):
    svc = followed
    old_assessment = svc.assessment.current_assessment(NO_OUTCOME)
    _dual_review(svc, NO_OUTCOME)
    assert svc.review.review_for_case(NO_OUTCOME).state == REVIEW_STATE_REVIEWED

    _land_outcome(svc)
    new_assessment = svc.assessment.current_assessment(NO_OUTCOME)
    assert new_assessment.assessment_id != old_assessment.assessment_id

    view = svc.review.review_for_case(NO_OUTCOME)
    assert view.state == REVIEW_STATE_AWAITING_FIRST
    assert view.has_stale_acts
    assert all(a.stale for a in view.acts)
    # The superseding artefact is named on each stale act.
    for act_view in view.acts:
        assert new_assessment.assessment_id in act_view.superseded_by
    # Nothing was deleted or rewritten: both acts stand as recorded.
    assert len(svc.store.list_review_acts(NO_OUTCOME)) == 2


# ---------------------------------------------------------------------
# Disagreement: a reject holds until the record changes.
# ---------------------------------------------------------------------


def test_reject_holds_and_accept_over_same_record_does_not_clear(followed):
    svc = followed
    svc.review.record_review(
        NO_OUTCOME, LINDQVIST, SAFETY, "reject_with_reason",
        "Seriousness cannot be confirmed until the outcome is on record.",
        DAY,
    )
    _accept(svc, NO_OUTCOME)
    view = svc.review.review_for_case(NO_OUTCOME)
    assert view.state == REVIEW_STATE_HOLD
    assert view.hold_by == LINDQVIST
    assert view.hold_role == SAFETY
    assert "outcome is on record" in view.hold_reason
    assert view.hold_act_id is not None


def test_hold_clears_only_by_a_new_record_and_a_fresh_dual_review(followed):
    svc = followed
    svc.review.record_review(
        NO_OUTCOME, LINDQVIST, SAFETY, "reject_with_reason",
        "Seriousness cannot be confirmed until the outcome is on record.",
        DAY,
    )
    _land_outcome(svc)
    view = svc.review.review_for_case(NO_OUTCOME)
    assert view.state == REVIEW_STATE_AWAITING_FIRST  # hold gone with its record
    assert view.hold_act_id is None

    later = date(2026, 10, 25)
    _accept(svc, NO_OUTCOME, today=later)
    svc.review.record_review(NO_OUTCOME, LINDQVIST, SAFETY, "accept", "", later)
    view = svc.review.review_for_case(NO_OUTCOME)
    assert view.state == REVIEW_STATE_REVIEWED
    # The rejecting act itself was never edited — it stands, stale.
    reject = next(
        a for a in svc.store.list_review_acts(NO_OUTCOME)
        if a.decision == "reject_with_reason"
    )
    assert reject.rationale.startswith("Seriousness cannot be confirmed")


def test_a_reviewer_may_reject_after_accepting_the_same_record(assessed):
    svc = assessed
    _accept(svc, COMPLETE)
    svc.review.record_review(
        COMPLETE, RAO, MEDICAL, "reject_with_reason",
        "On re-reading, the narrative overstates the outcome.",
        DAY,
    )
    assert svc.review.review_for_case(COMPLETE).state == REVIEW_STATE_HOLD


# ---------------------------------------------------------------------
# The queue: work, bucketed, with no identifying field.
# ---------------------------------------------------------------------


def test_queue_buckets_and_phi_minimisation(followed):
    svc = followed
    _accept(svc, COMPLETE)  # one decision from done
    svc.review.record_review(
        NO_OUTCOME, LINDQVIST, SAFETY, "reject_with_reason",
        "Seriousness cannot be confirmed until the outcome is on record.",
        DAY,
    )
    queue = svc.review.queue(DAY)
    by_id = {e.case_id: e for e in queue}
    assert by_id[COMPLETE].bucket == "awaiting_second_review"
    assert by_id[NO_OUTCOME].bucket == "review_hold"
    assert by_id[NO_OUTCOME].review_state == REVIEW_STATE_HOLD
    # Holds lead the queue.
    assert queue[0].bucket == "review_hold"
    # Clock position rides along; identity does not.
    entry = by_id[COMPLETE]
    assert entry.seriousness == "non_serious"
    assert entry.obligation_class == "periodic"
    import json

    blob = json.dumps([e.model_dump(mode="json") for e in queue])
    for phi in (
        "H.B.", "D.K.", "1988-03-14", "dk@example.net", "hb@",
        "R. Patel", "+1-555-0199", "initials", "date_of_birth",
        "reporter",
    ):
        assert phi not in blob
    # Reviewed cases are not queue work.
    _dual_review(svc, "CASE-RPT-2026-0307")
    assert "CASE-RPT-2026-0307" not in {e.case_id for e in svc.review.queue(DAY)}


def test_queue_names_stale_reviews_as_their_own_bucket(followed):
    svc = followed
    _dual_review(svc, NO_OUTCOME)
    _land_outcome(svc)
    by_id = {e.case_id: e for e in svc.review.queue(DAY)}
    entry = by_id[NO_OUTCOME]
    assert entry.bucket == "stale_review"
    assert entry.review_state == REVIEW_STATE_AWAITING_FIRST
    assert entry.has_stale_acts


# ---------------------------------------------------------------------
# Insert-only: the trail is the trail.
# ---------------------------------------------------------------------


def test_acts_are_insert_only_and_never_rewritten(assessed):
    svc = assessed
    act = _accept(svc, COMPLETE)
    before = act.model_dump()
    _accept(svc, COMPLETE, reviewer=LINDQVIST, role=SAFETY)
    stored = next(
        a for a in svc.store.list_review_acts(COMPLETE)
        if a.act_id == act.act_id
    )
    assert stored.model_dump() == before
    for verb in (
        "update_review_act",
        "delete_review_act",
        "update_medical_review_act",
        "delete_medical_review_act",
    ):
        assert not hasattr(svc.store, verb)


def test_pack_is_versioned():
    assert REVIEW_PACK["version"] == "2026.10.1"
    assert REVIEW_PACK["roles"] == [MEDICAL, SAFETY]
    assert "reject_with_reason" in REVIEW_PACK["decisions"]
