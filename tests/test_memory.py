"""Round 8 gates: the AgentCore Memory port.

Cross-case recall is recorded through the port — strategy,
requesting case, matches by reference (case ids, artefact ids,
coded facts), the subject as a fingerprint — and it never merges
anything: neither case's facts move. Retrievals expire under the
pack's retention rule as tombstones: kept, counted, never
served, never deleted.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from pv_cases.memory import MemoryError
from pv_cases.models import MemoryRetrieval
from pv_cases.reporting import current_report
from pv_cases.store import InMemoryCaseStore

DAY = date(2026, 10, 12)
MEDICAL = "Dr A. Rao"
SAFETY = "Dr B. Lindqvist"
FIRST = "CASE-RPT-2026-0005"
SECOND = "CASE-RPT-2026-0006"


def test_same_patient_recall_matches_by_reference(full):
    retrieval = full.memory.recall_for_case(SECOND, "same_patient", MEDICAL, DAY)
    # The planted book carries several cases for this patient
    # (the feed pair plus the dedup fixtures): the recall names
    # exactly the history view's cases, minus the requester.
    view = full.history.patient_history_for_case(SECOND, DAY)
    matched = {m.case_id for m in retrieval.matches}
    assert matched == set(view.case_ids) - {SECOND}
    assert FIRST in matched and SECOND not in matched
    match = next(m for m in retrieval.matches if m.case_id == FIRST)
    assert match.basis == "same_patient_key"
    assert match.artefact_ids  # the assessment + coding decisions
    # The requesting case is never its own match, and the record
    # carries the subject as a fingerprint — the key itself (and
    # the DOB it is built from) appears nowhere in the artefact.
    blob = json.dumps(retrieval.model_dump(mode="json"))
    assert "1988-03-14" not in blob
    assert retrieval.subject_fingerprint != "dob:1988-03-14|f"
    assert len(retrieval.subject_fingerprint) == 64
    # The recall is recorded on the requesting case's chain.
    kinds = [i.kind for i in full.store.list_evidence(SECOND)]
    assert "memory_retrieval" in kinds
    assert full.chain.verify_chain(SECOND)


def test_recall_never_merges(full):
    before_first = current_report(full.store, full.chain, FIRST).model_dump()
    before_second = current_report(full.store, full.chain, SECOND).model_dump()
    chain_first = len(full.store.list_evidence(FIRST))
    chain_second = len(full.store.list_evidence(SECOND))
    full.memory.recall_for_case(SECOND, "same_patient", MEDICAL, DAY)
    assert current_report(full.store, full.chain, FIRST).model_dump() == before_first
    assert current_report(full.store, full.chain, SECOND).model_dump() == before_second
    # The source case gains nothing at all; the requesting case
    # gains exactly the retrieval's chain note.
    assert len(full.store.list_evidence(FIRST)) == chain_first
    assert len(full.store.list_evidence(SECOND)) == chain_second + 1


def test_same_product_event_and_coding_precedent(full):
    retrieval = full.memory.recall_for_case(
        SECOND, "same_product_event", MEDICAL, DAY
    )
    assert retrieval.matches
    assert all(
        m.basis == "same_product_and_coded_term" for m in retrieval.matches
    )
    assert all(m.coded_term for m in retrieval.matches)

    decision = next(
        d
        for d in full.store.list_coding_decisions()
        if d.subject_kind == "event" and d.preferred_term
    )
    desk = full.memory.recall_coding_precedents(decision.verbatim, MEDICAL, DAY)
    assert desk.case_id is None  # a desk recall writes to no chain
    assert decision.case_id in [m.case_id for m in desk.matches]
    assert all(m.basis == "same_verbatim_term" for m in desk.matches)


def test_recall_refusals(full):
    with pytest.raises(MemoryError):
        full.memory.recall_for_case(SECOND, "same_patient", " ", DAY)
    with pytest.raises(MemoryError):
        full.memory.recall_for_case(SECOND, "telepathy", MEDICAL, DAY)
    with pytest.raises(KeyError):
        full.memory.recall_for_case("CASE-NOPE", "same_patient", MEDICAL, DAY)
    with pytest.raises(MemoryError):
        full.memory.recall_coding_precedents("Headache", "", DAY)


def test_sealed_case_refuses_recall(assessed):
    case_id = "CASE-RPT-2026-0304"
    assessed.coding.code_case(case_id, DAY)
    assessed.assessment.assess_case(case_id, DAY)
    assessed.assembler.assemble(case_id, DAY)
    assessed.review.record_review(
        case_id, MEDICAL, "medical_reviewer", "accept", "", DAY
    )
    assessed.review.record_review(
        case_id, SAFETY, "safety_reviewer", "accept", "", DAY
    )
    assessed.sealing.seal_case(case_id, "PV Operations Lead", DAY)
    with pytest.raises(MemoryError) as exc:
        assessed.memory.recall_for_case(case_id, "same_patient", MEDICAL, DAY)
    assert "sealed" in str(exc.value)


def test_retention_expires_as_tombstones(full):
    retrieval = full.memory.recall_for_case(SECOND, "same_patient", MEDICAL, DAY)
    assert retrieval in full.memory.active_retrievals(DAY, SECOND)
    # A retrieval whose retention has run out stands in the
    # store, counted — and is never served again.
    old = MemoryRetrieval(
        retrieval_id="MEM-OLD-001",
        case_id=SECOND,
        requested_by=MEDICAL,
        requested_on=DAY - timedelta(days=400),
        strategy="same_patient",
        subject_fingerprint="0" * 64,
        subject_label="an old recall",
        matches=[],
        pack_version="2026.10.1",
        expires_on=DAY - timedelta(days=35),
    )
    full.store.add_memory_retrieval(old)
    active = full.memory.active_retrievals(DAY, SECOND)
    assert old not in active and retrieval in active
    view = full.memory.retention_view(DAY)
    assert view["expired_tombstones"] == 1
    assert view["expired_ids"] == ["MEM-OLD-001"]
    assert view["active"] == 1
    # Expired is not deleted.
    assert full.store.get_memory_retrieval("MEM-OLD-001") is not None


def test_memory_artefacts_are_insert_only(services):
    for store in (services.store, InMemoryCaseStore()):
        for verb in ("update_memory_retrieval", "delete_memory_retrieval"):
            assert not hasattr(store, verb), verb
