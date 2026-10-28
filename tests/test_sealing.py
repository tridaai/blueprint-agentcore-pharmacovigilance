"""Round 7 gates: sealing, post-seal immutability, the export
package, and the offline verifier.

The seal binds the artefact inventory by content hash over the
dual-reviewed record; a sealed case refuses every later
artefact at the service layer; the export is assembled only
from the sealed inventory, byte-deterministically; and the
verifier re-performs every guarantee from the package alone,
failing tampered packages with the broken check named.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import date

import pytest

from pv_cases.agent import CitationRefusal
from pv_cases.assessment import AssessmentError
from pv_cases.coding import CodingError
from pv_cases.evidence import canonical_json, item_hash
from pv_cases.followup import FollowUpError
from pv_cases.models import SealedArtefact
from pv_cases.review import ReviewError
from pv_cases.sealing import SealError, inventory_hash
from pv_cases.verify import verify_package

DAY = date(2026, 10, 12)
LATER = date(2026, 10, 25)
COMPLETE = "CASE-RPT-2026-0304"
NO_OUTCOME = "CASE-RPT-2026-0305"
FATAL_FOLLOWED = "CASE-RPT-2026-0401"
MEDICAL = "Dr A. Rao"
SAFETY = "Dr B. Lindqvist"
SEALER = "PV Operations Lead"


def reviewed(svc, case_id: str, day: date = DAY):
    """Drive one coded, assessed, assembled case to
    medically_reviewed under dual control."""
    svc.coding.code_case(case_id, day)
    svc.assessment.assess_case(case_id, day)
    svc.assembler.assemble(case_id, day)
    svc.review.record_review(case_id, MEDICAL, "medical_reviewer", "accept", "", day)
    svc.review.record_review(case_id, SAFETY, "safety_reviewer", "accept", "", day)
    assert svc.review.review_for_case(case_id).state == "medically_reviewed"


def sealed(svc, case_id: str = COMPLETE, day: date = DAY):
    reviewed(svc, case_id, day)
    return svc.sealing.seal_case(case_id, SEALER, day)


# ---------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------


def test_seal_refuses_an_unreviewed_case_naming_the_missing_roles(assessed):
    assessed.coding.code_case(COMPLETE, DAY)
    assessed.assessment.assess_case(COMPLETE, DAY)
    assessed.assembler.assemble(COMPLETE, DAY)
    with pytest.raises(SealError) as exc:
        assessed.sealing.seal_case(COMPLETE, SEALER, DAY)
    assert "medically_reviewed" in str(exc.value)
    assert "medical_reviewer" in str(exc.value)
    assert "safety_reviewer" in str(exc.value)


def test_seal_refuses_a_case_on_review_hold(assessed):
    assessed.coding.code_case(NO_OUTCOME, DAY)
    assessed.assessment.assess_case(NO_OUTCOME, DAY)
    assessed.assembler.assemble(NO_OUTCOME, DAY)
    assessed.review.record_review(
        NO_OUTCOME, SAFETY, "safety_reviewer", "reject_with_reason",
        "Seriousness cannot be confirmed.", DAY,
    )
    assessed.review.record_review(
        NO_OUTCOME, MEDICAL, "medical_reviewer", "accept", "", DAY
    )
    view = assessed.review.review_for_case(NO_OUTCOME)
    assert view.state == "review_hold"
    with pytest.raises(SealError) as exc:
        assessed.sealing.seal_case(NO_OUTCOME, SEALER, DAY)
    assert "review_hold" in str(exc.value)
    assert SAFETY in str(exc.value)


def test_seal_refuses_an_unnamed_sealer(assessed):
    reviewed(assessed, COMPLETE)
    with pytest.raises(SealError):
        assessed.sealing.seal_case(COMPLETE, " ", DAY)


def _land_outcome(svc, today: date = LATER):
    """Answer 0305's assessment follow-up: the outcome lands and
    the assessment is superseded — the R6 staleness trigger."""
    from pv_cases.models import (
        AdverseEvent,
        IntakeReport,
        Patient,
        Reporter,
        ReportSource,
    )

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
    return svc.followup.submit_follow_up("FUP-CASE-RPT-2026-0305-A", answer, today)


def test_seal_refuses_a_stale_review(assessed):
    """A case reviewed over a record that has since been
    superseded is not medically reviewed over its CURRENT
    record — the seal names the staleness."""
    reviewed(assessed, NO_OUTCOME)
    _land_outcome(assessed)
    view = assessed.review.review_for_case(NO_OUTCOME)
    assert view.state != "medically_reviewed"
    assert any(a.stale for a in view.acts)
    with pytest.raises(SealError) as exc:
        assessed.sealing.seal_case(NO_OUTCOME, SEALER, LATER)
    assert "stale" in str(exc.value)


def test_seal_is_taken_once(assessed):
    seal = sealed(assessed)
    with pytest.raises(SealError) as exc:
        assessed.sealing.seal_case(COMPLETE, SEALER, DAY)
    assert seal.seal_id in str(exc.value)


# ---------------------------------------------------------------------
# What the seal binds
# ---------------------------------------------------------------------


def test_seal_binds_the_full_inventory_and_chain_head(assessed):
    seal = sealed(assessed)
    kinds = {line.artefact_kind for line in seal.inventory}
    assert {
        "case",
        "coding_decision",
        "assessment",
        "summary",
        "review_act",
        "evidence_item",
    } <= kinds
    # The inventory is exact: one line per artefact, ids unique.
    keys = [(l.artefact_kind, l.artefact_id) for l in seal.inventory]
    assert len(keys) == len(set(keys))
    # The chain head bound is the item recorded BEFORE the seal's
    # own chain item — the seal never binds itself.
    items = assessed.store.list_evidence(COMPLETE)
    assert items[-1].kind == "case_seal"
    assert seal.chain_head_hash == items[-2].item_hash
    assert seal.chain_items == len(items) - 1
    # The accepted acts are the two distinct reviewers' acts.
    assert set(seal.accepted_acts) == {"medical_reviewer", "safety_reviewer"}
    # Pack versions in force are recorded.
    assert seal.criteria_pack_version and seal.rsi_version
    assert seal.dictionary_versions
    # The inventory hash recomputes.
    assert inventory_hash(seal.inventory) == seal.inventory_hash


def test_seal_binds_follow_up_history(followed):
    """The day-20 case: its follow-up request and the resolution
    act that landed the fatal outcome are part of the sealed
    record, requests and acts alike."""
    from pv_cases.seed import followup_reports

    case_id = FATAL_FOLLOWED
    submission = followed.followup.submit_follow_up(
        "FUP-CASE-RPT-2026-0401-A",
        followup_reports()["RPT-2026-0402"],
        LATER,
    )
    assert submission.outcome == "resolution"
    followed.assembler.assemble(case_id, LATER)
    followed.review.record_review(
        case_id, MEDICAL, "medical_reviewer", "accept", "", LATER
    )
    followed.review.record_review(
        case_id, SAFETY, "safety_reviewer", "accept", "", LATER
    )
    seal = followed.sealing.seal_case(case_id, SEALER, LATER)
    kinds = {line.artefact_kind for line in seal.inventory}
    assert "follow_up_request" in kinds
    assert "follow_up_act" in kinds
    # And the package verifies end to end, clock included.
    _, package = followed.sealing.export_case(case_id, SEALER, LATER)
    assert verify_package(package).verdict == "pass"


# ---------------------------------------------------------------------
# Post-seal immutability
# ---------------------------------------------------------------------


def test_sealed_case_refuses_new_review_acts(assessed):
    seal = sealed(assessed)
    with pytest.raises(ReviewError) as exc:
        assessed.review.record_review(
            COMPLETE, MEDICAL, "medical_reviewer", "accept", "", DAY
        )
    assert seal.seal_id in str(exc.value)


def test_sealed_case_refuses_recodes(assessed):
    sealed(assessed)
    with pytest.raises(CodingError):
        assessed.coding.code_case(COMPLETE, DAY)


def test_sealed_case_refuses_new_assessments(assessed):
    sealed(assessed)
    with pytest.raises(AssessmentError):
        assessed.assessment.assess_case(COMPLETE, DAY)


def test_sealed_case_refuses_new_summaries(assessed):
    sealed(assessed)
    with pytest.raises(CitationRefusal):
        assessed.assembler.assemble(COMPLETE, DAY)


def test_sealed_case_refuses_follow_up_work(assessed):
    """0305 seals with its assessment follow-up still open; both
    raising new requests and answering the open one are refused,
    naming the seal."""
    seal = sealed(assessed, NO_OUTCOME)
    with pytest.raises(FollowUpError) as exc:
        assessed.followup.raise_for_case(NO_OUTCOME, LATER)
    assert seal.seal_id in str(exc.value)
    from pv_cases.models import (
        AdverseEvent,
        IntakeReport,
        Patient,
        Reporter,
        ReportSource,
    )

    report = IntakeReport(
        report_id="RPT-SEALED-PROBE",
        source=ReportSource.CALL_CENTRE,
        received_on=LATER,
        patient=Patient(initials="H.B.", age=62, sex="M"),
        reporter=Reporter(name="Call centre agent R. Patel", phone="+1-555-0199"),
        suspect_product_code="NBT-101",
        suspect_product_name="Cardivane",
        events=[
            AdverseEvent(
                verbatim_term="Severe headache",
                outcome="recovered",
            )
        ],
    )
    with pytest.raises(FollowUpError) as exc:
        assessed.followup.submit_follow_up(
            "FUP-CASE-RPT-2026-0305-A", report, LATER
        )
    assert seal.seal_id in str(exc.value)


# ---------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------


def test_export_of_an_unsealed_case_is_refused_naming_the_seal(assessed):
    reviewed(assessed, COMPLETE)
    with pytest.raises(SealError) as exc:
        assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    assert "not sealed" in str(exc.value)


def test_export_is_byte_deterministic_and_recorded(assessed):
    seal = sealed(assessed)
    record1, package1 = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    record2, package2 = assessed.sealing.export_case(COMPLETE, "Q. Auditor", LATER)
    assert assessed.sealing.package_bytes(package1) == assessed.sealing.package_bytes(package2)
    assert record1.package_hash == record2.package_hash
    assert record1.export_id != record2.export_id
    exports = assessed.sealing.list_exports(COMPLETE)
    assert [e.export_id for e in exports] == [record1.export_id, record2.export_id]
    # Both servings stand on the case's evidence chain.
    kinds = [i.kind for i in assessed.store.list_evidence(COMPLETE)]
    assert kinds.count("case_export") == 2
    assert record1.seal_id == seal.seal_id


def test_export_package_carries_only_the_sealed_inventory(assessed):
    seal = sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    package_keys = {
        (e["kind"], e["id"]) for e in package["artefacts"]
    } | {
        ("evidence_item", e["item"]["item_id"]) for e in package["evidence"]
    }
    seal_keys = {(l.artefact_kind, l.artefact_id) for l in seal.inventory}
    assert package_keys == seal_keys
    # The case facts ride as recorded: report of record + clocks.
    case_entry = next(e for e in package["artefacts"] if e["kind"] == "case")
    assert case_entry["payload"]["case_id"] == COMPLETE
    assessment = next(e for e in package["artefacts"] if e["kind"] == "assessment")
    assert assessment["payload"]["clock"]["day_zero"]


def test_text_rendering_is_deterministic(assessed):
    sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    text1 = assessed.sealing.render_text(package)
    text2 = assessed.sealing.render_text(package)
    assert text1 == text2
    assert "PV CASE EXPORT" in text1
    assert "Nothing in it has been submitted to a regulator" in text1


# ---------------------------------------------------------------------
# The offline verifier
# ---------------------------------------------------------------------


def _checks_by_name(report):
    return {c.name: c for c in report.checks}


def test_verifier_passes_a_real_package_check_by_check(assessed):
    seal = sealed(assessed)
    record, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    report = verify_package(package)
    assert report.verdict == "pass"
    assert all(c.passed for c in report.checks)
    assert {c.name for c in report.checks} == {
        "package_shape",
        "artefact_hashes",
        "chain_linkage",
        "seal_inventory_exact",
        "review_gate",
        "clock_arithmetic",
    }
    # The report's hash is computed over the package and equals
    # the export record's — the anchor to the serving system.
    assert report.package_hash == record.package_hash
    assert report.seal_id == seal.seal_id


def test_verifier_fails_an_altered_figure(assessed):
    sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    tampered = copy.deepcopy(package)
    for entry in tampered["artefacts"]:
        if entry["kind"] == "assessment":
            entry["payload"]["clock"]["deadline"] = "2026-12-31"
    report = verify_package(tampered)
    assert report.verdict == "fail"
    checks = _checks_by_name(report)
    assert not checks["artefact_hashes"].passed
    assert not checks["clock_arithmetic"].passed


def test_verifier_fails_a_dropped_artefact(assessed):
    sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    tampered = copy.deepcopy(package)
    tampered["artefacts"] = [
        e for e in tampered["artefacts"] if e["kind"] != "coding_decision"
    ]
    report = verify_package(tampered)
    assert report.verdict == "fail"
    checks = _checks_by_name(report)
    assert not checks["seal_inventory_exact"].passed
    assert "missing" in checks["seal_inventory_exact"].detail


def test_verifier_fails_a_swapped_review_act(assessed):
    sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    tampered = copy.deepcopy(package)
    acts = [e for e in tampered["artefacts"] if e["kind"] == "review_act"]
    assert len(acts) == 2
    acts[0]["payload"], acts[1]["payload"] = acts[1]["payload"], acts[0]["payload"]
    report = verify_package(tampered)
    assert report.verdict == "fail"
    checks = _checks_by_name(report)
    assert not checks["review_gate"].passed


def test_verifier_fails_same_person_dual_control_even_rehashed(assessed):
    """The honest-forgery probe: the safety act's reviewer is
    rewritten to the medical reviewer's name and every hash the
    seal carries is recomputed to match. The review gate still
    fails — on the fact, not on a hash."""
    sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    tampered = copy.deepcopy(package)
    safety_id = tampered["seal"]["accepted_acts"]["safety_reviewer"]
    new_hash = None
    for entry in tampered["artefacts"]:
        if entry["kind"] == "review_act" and entry["id"] == safety_id:
            entry["payload"]["reviewer"] = MEDICAL
            new_hash = hashlib.sha256(
                canonical_json(entry["payload"])
            ).hexdigest()
    inventory = []
    for line in tampered["seal"]["inventory"]:
        line = dict(line)
        if line["artefact_id"] == safety_id:
            line["artefact_hash"] = new_hash
        inventory.append(SealedArtefact(**line))
    tampered["seal"]["inventory"] = [
        i.model_dump(mode="json") for i in inventory
    ]
    tampered["seal"]["inventory_hash"] = inventory_hash(inventory)
    report = verify_package(tampered)
    assert report.verdict == "fail"
    checks = _checks_by_name(report)
    assert not checks["review_gate"].passed
    assert "same person" in checks["review_gate"].detail


def test_verifier_fails_a_relinked_chain(assessed):
    """One evidence payload is altered and its own digests are
    recomputed honestly — but its successor still points at the
    old hash, so the linkage check names the break."""
    sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    tampered = copy.deepcopy(package)
    first = tampered["evidence"][0]
    first["payload"]["report_id"] = "RPT-FORGED"
    first["item"]["payload_sha256"] = hashlib.sha256(
        canonical_json(first["payload"])
    ).hexdigest()
    first["item"]["item_hash"] = item_hash(
        first["item"]["prev_hash"],
        first["item"]["payload_sha256"],
        first["item"]["kind"],
        first["item"]["object_key"],
    )
    report = verify_package(tampered)
    assert report.verdict == "fail"
    checks = _checks_by_name(report)
    assert not checks["chain_linkage"].passed


def test_verifier_main_reads_a_package_file(assessed, tmp_path, capsys):
    sealed(assessed)
    _, package = assessed.sealing.export_case(COMPLETE, SEALER, DAY)
    path = tmp_path / "package.json"
    path.write_bytes(assessed.sealing.package_bytes(package))
    from pv_cases.verify import main

    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "verdict: PASS" in out
    assert "review_gate" in out
