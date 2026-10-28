"""Round 3 dictionary coding: the tiered lookup grades every
planted verbatim into its stated path, the traps stay uncoded with
review tasks, coder acts are recorded decisions, and dictionary
additions land only as new versions — nothing is edited in place
and nothing fuzzy is ever guessed."""

import pytest

from pv_cases.agent import CaseAssembler, CitationRefusal
from pv_cases.coding import (
    BASE_VERSION,
    CodingError,
    CodingService,
    CodingTaskClosed,
    base_dictionary,
    lookup_event,
    lookup_product,
)
from pv_cases.evidence import EvidenceChain
from pv_cases.models import (
    Citation,
    CodingMatchPath,
    CodingReviewStatus,
    CodingStatus,
    DictionaryEntry,
    ProductDictionaryEntry,
    SummaryStatement,
)
from pv_cases.object_store import LocalObjectStore
from pv_cases.store import InMemoryCaseStore
from tests.conftest import DAY

AB_KEY = "dob:1988-03-14|f"
SP_KEY = "init:s p|"  # RPT-2026-0102's patient: initials, no sex


def _decision(svc, case_id, kind, index=0):
    for d in svc.coding.current_decisions(case_id):
        if (d.subject_kind, d.subject_index) == (kind, index):
            return d
    raise AssertionError(f"no current {kind} decision for {case_id}")


def _task(svc, case_id, kind):
    for t in svc.coding.uncoded_queue():
        if t.case_id == case_id and t.subject_kind == kind:
            return t
    raise AssertionError(f"no open {kind} task for {case_id}")


# ---------------------------------------------------------------------
# The lookup itself: paths, precedence, and the refusal to guess.
# ---------------------------------------------------------------------

ENTRIES = base_dictionary(DAY).event_entries
PRODUCTS = base_dictionary(DAY).product_entries


def test_lookup_paths():
    entry, path, reason, _ = lookup_event("Blistering rash on hands", ENTRIES)
    assert (entry.preferred_term, path) == ("Rash vesicular", CodingMatchPath.EXACT)
    entry, path, _, _ = lookup_event("blistering rash on hands!", ENTRIES)
    assert (entry.preferred_term, path) == (
        "Rash vesicular",
        CodingMatchPath.NORMALISED,
    )
    entry, path, _, _ = lookup_event("Blistered rash on hands", ENTRIES)
    assert (entry.preferred_term, path) == ("Rash vesicular", CodingMatchPath.SYNONYM)


def test_lookup_exact_beats_synonym_of_another_entry():
    # "Nausea" is one entry's verbatim and the Vomiting entry's
    # synonym. The stated precedence settles it: exact wins.
    entry, path, _, _ = lookup_event("Nausea", ENTRIES)
    assert (entry.preferred_term, path) == ("Nausea", CodingMatchPath.EXACT)


def test_lookup_ambiguous_synonym_goes_to_review():
    entry, path, reason, candidates = lookup_event("Feeling drained", ENTRIES)
    assert entry is None
    assert path is CodingMatchPath.NONE
    assert reason == "ambiguous_match"
    assert candidates == ["Asthenia", "Fatigue"]


def test_lookup_near_miss_is_not_a_match():
    # Resembles "Blistering rash on hands" token by token — and is
    # not in the dictionary. There is no similarity path.
    entry, path, reason, _ = lookup_event("Blistering on hands and feet", ENTRIES)
    assert entry is None and reason == "no_dictionary_entry"
    entry, _, reason, _ = lookup_event("Blistering rash on hand", ENTRIES)
    assert entry is None and reason == "no_dictionary_entry"


def test_lookup_product_paths():
    entry, path, _, _ = lookup_product("Dermaclear cream", PRODUCTS)
    assert (entry.product_code, path) == ("NBT-317", CodingMatchPath.SYNONYM)
    entry, path, _, _ = lookup_product("NBT-101", PRODUCTS)
    assert (entry.product_code, path) == ("NBT-101", CodingMatchPath.EXACT)
    entry, _, reason, _ = lookup_product("Cardivane Plus", PRODUCTS)
    assert entry is None and reason == "no_dictionary_entry"


# ---------------------------------------------------------------------
# Coding at intake, pinned to the dictionary version.
# ---------------------------------------------------------------------


def test_feed_cases_coded_at_intake(fed):
    decision = _decision(fed, "CASE-RPT-2026-0001", "event")
    assert decision.status is CodingStatus.CODED
    assert decision.match_path is CodingMatchPath.EXACT
    assert decision.preferred_term == "Dizziness"
    assert decision.soc == "Nervous system disorders"
    assert decision.dictionary_version == BASE_VERSION
    assert decision.verbatim == "Dizziness and fall at home"
    product = _decision(fed, "CASE-RPT-2026-0001", "product")
    assert (product.product_code, product.product_name) == ("NBT-101", "Cardivane")
    assert product.active_substance == "veldorin"


def test_fixture_paths(coded):
    normalised = _decision(coded, "CASE-RPT-2026-0201", "event")
    assert (normalised.match_path, normalised.preferred_term) == (
        CodingMatchPath.NORMALISED,
        "Rash vesicular",
    )
    synonym_event = _decision(coded, "CASE-RPT-2026-0202", "event")
    synonym_product = _decision(coded, "CASE-RPT-2026-0202", "product")
    assert synonym_event.match_path is CodingMatchPath.SYNONYM
    assert (synonym_product.match_path, synonym_product.product_code) == (
        CodingMatchPath.SYNONYM,
        "NBT-317",
    )
    precedence = _decision(coded, "CASE-RPT-2026-0206", "event")
    assert (precedence.match_path, precedence.preferred_term) == (
        CodingMatchPath.EXACT,
        "Nausea",
    )


def test_traps_are_uncoded_with_tasks(coded):
    near_miss = _decision(coded, "CASE-RPT-2026-0203", "event")
    assert near_miss.status is CodingStatus.UNCODED
    assert near_miss.uncoded_reason == "no_dictionary_entry"
    assert near_miss.verbatim == "Blistering on hands and feet"
    task = _task(coded, "CASE-RPT-2026-0203", "event")
    assert task.decision_id == near_miss.decision_id

    ambiguous = _decision(coded, "CASE-RPT-2026-0204", "event")
    assert ambiguous.uncoded_reason == "ambiguous_match"
    assert ambiguous.candidates == ["Asthenia", "Fatigue"]

    product = _decision(coded, "CASE-RPT-2026-0205", "product")
    assert product.status is CodingStatus.UNCODED
    assert _decision(coded, "CASE-RPT-2026-0205", "event").preferred_term == "Headache"

    # The one uncoded term in the R2 book: a real phrasing the
    # extract simply does not carry.
    sp = _decision(coded, "CASE-RPT-2026-0102", "event")
    assert sp.status is CodingStatus.UNCODED

    queue = coded.coding.uncoded_queue()
    assert len(queue) == 4
    assert {t.case_id for t in queue} == {
        "CASE-RPT-2026-0102",
        "CASE-RPT-2026-0203",
        "CASE-RPT-2026-0204",
        "CASE-RPT-2026-0205",
    }


def test_code_case_is_idempotent_per_version(coded):
    before = len(coded.store.list_coding_decisions())
    assert coded.coding.code_case("CASE-RPT-2026-0203", DAY) == []
    assert len(coded.store.list_coding_decisions()) == before


# ---------------------------------------------------------------------
# The coder acts: resolution and dictionary additions.
# ---------------------------------------------------------------------


def test_resolution_is_a_recorded_manual_decision(coded):
    task = _task(coded, "CASE-RPT-2026-0204", "event")
    decision = coded.coding.resolve(
        task.task_id, "J. Coder", DAY, preferred_term="Fatigue"
    )
    assert decision.match_path is CodingMatchPath.MANUAL
    assert decision.coded_by == "J. Coder"
    assert decision.supersedes_id == task.decision_id
    assert _decision(coded, "CASE-RPT-2026-0204", "event").preferred_term == "Fatigue"
    stored = coded.store.get_coding_task(task.task_id)
    assert stored.status is CodingReviewStatus.RESOLVED
    assert stored.resolution_decision_id == decision.decision_id
    kinds = [i.kind for i in coded.store.list_evidence("CASE-RPT-2026-0204")]
    assert "coding_decision" in kinds
    assert coded.chain.verify_chain("CASE-RPT-2026-0204")


def test_resolution_refusals(coded):
    task = _task(coded, "CASE-RPT-2026-0204", "event")
    with pytest.raises(CodingError):
        coded.coding.resolve(task.task_id, "unknown", DAY, preferred_term="Fatigue")
    with pytest.raises(CodingError):
        coded.coding.resolve(task.task_id, "J. Coder", DAY, preferred_term="Banana")
    # An ambiguous task resolves only to one of its candidates.
    with pytest.raises(CodingError):
        coded.coding.resolve(task.task_id, "J. Coder", DAY, preferred_term="Headache")
    with pytest.raises(CodingError):
        coded.coding.resolve(task.task_id, "J. Coder", DAY)
    coded.coding.resolve(task.task_id, "J. Coder", DAY, preferred_term="Asthenia")
    with pytest.raises(CodingTaskClosed):
        coded.coding.resolve(task.task_id, "J. Coder", DAY, preferred_term="Fatigue")
    with pytest.raises(KeyError):
        coded.coding.resolve("CT-GHOST", "J. Coder", DAY, preferred_term="Fatigue")


def test_product_resolution(coded):
    task = _task(coded, "CASE-RPT-2026-0205", "product")
    decision = coded.coding.resolve(task.task_id, "J. Coder", DAY, product_code="NBT-101")
    assert decision.product_code == "NBT-101"
    assert decision.product_name == "Cardivane"


def test_addition_lands_only_as_a_new_version(coded):
    v1_before = coded.coding.dictionary(BASE_VERSION)
    decision_before = _decision(coded, "CASE-RPT-2026-0203", "event")
    new = coded.coding.propose_addition(
        "J. Coder",
        DAY,
        "event",
        DictionaryEntry(
            verbatim="Blistering on hands and feet",
            preferred_term="Rash vesicular",
            soc="Skin and subcutaneous tissue disorders",
        ),
        note="third report of this phrasing this quarter",
    )
    assert new.version == "2026.10.2"
    assert new.supersedes_version == BASE_VERSION
    # The old version artefact is untouched, and the decision taken
    # under it still pins it — nothing was edited in place.
    v1_after = coded.coding.dictionary(BASE_VERSION)
    assert v1_after.model_dump() == v1_before.model_dump()
    still = _decision(coded, "CASE-RPT-2026-0203", "event")
    assert still.decision_id == decision_before.decision_id
    assert still.status is CodingStatus.UNCODED
    # A recode under the new version settles the term and closes
    # the task by the dictionary's own act. (The product subject is
    # re-coded under the new version too — a recode re-pins every
    # non-manual subject to the version it runs under.)
    written = coded.coding.code_case("CASE-RPT-2026-0203", DAY)
    settled = _decision(coded, "CASE-RPT-2026-0203", "event")
    assert settled.status is CodingStatus.CODED
    assert settled.preferred_term == "Rash vesicular"
    assert settled.dictionary_version == "2026.10.2"
    assert settled.supersedes_id == decision_before.decision_id
    assert {d.subject_kind for d in written} == {"event", "product"}
    task = coded.store.get_coding_task("CT-CASE-RPT-2026-0203-E0")
    assert task.status is CodingReviewStatus.RESOLVED
    assert task.resolved_by == "dictionary:2026.10.2"


def test_addition_refusals(coded):
    with pytest.raises(CodingError):  # unnamed proposer
        coded.coding.propose_addition(
            "n/a", DAY, "event",
            DictionaryEntry(verbatim="X", preferred_term="Rash", soc="Skin"),
        )
    with pytest.raises(CodingError):  # verbatim already coded elsewhere
        coded.coding.propose_addition(
            "J. Coder", DAY, "event",
            DictionaryEntry(
                verbatim="Severe headache", preferred_term="Rash", soc="Skin"
            ),
        )
    with pytest.raises(CodingError):  # synonym that would manufacture ambiguity
        coded.coding.propose_addition(
            "J. Coder", DAY, "event",
            DictionaryEntry(
                verbatim="Drained feeling",
                preferred_term="Headache",
                soc="Nervous system disorders",
                synonyms=["Feeling drained"],
            ),
        )
    with pytest.raises(CodingError):  # product code the dictionary does not code
        coded.coding.propose_addition(
            "J. Coder", DAY, "product",
            ProductDictionaryEntry(
                verbatim_name="Newname",
                product_code="NBT-999",
                product_name="Newname",
                active_substance="nothing",
            ),
        )


def test_manual_decision_is_never_auto_superseded(coded):
    task = _task(coded, "CASE-RPT-2026-0203", "event")
    manual = coded.coding.resolve(
        task.task_id, "J. Coder", DAY, preferred_term="Rash vesicular"
    )
    coded.coding.propose_addition(
        "J. Coder", DAY, "event",
        DictionaryEntry(
            verbatim="Blistering on hands and feet",
            preferred_term="Rash vesicular",
            soc="Skin and subcutaneous tissue disorders",
        ),
    )
    written = coded.coding.code_case("CASE-RPT-2026-0203", DAY)
    assert all(d.subject_kind != "event" for d in written)
    assert _decision(coded, "CASE-RPT-2026-0203", "event").decision_id == manual.decision_id


def test_store_port_has_no_in_place_coding_edits(services):
    # Dictionary versions and coding decisions are insert-only:
    # the port grows no update/delete for them, on either store.
    for store in (services.store, InMemoryCaseStore()):
        for verb in (
            "update_dictionary_version",
            "delete_dictionary_version",
            "update_coding_decision",
            "delete_coding_decision",
        ):
            assert not hasattr(store, verb), verb


def test_coding_service_over_in_memory_store(tmp_path):
    store = InMemoryCaseStore()
    objects = LocalObjectStore(str(tmp_path / "objects"))
    chain = EvidenceChain(store, objects)
    coding = CodingService(store, chain)
    from pv_cases.services import IntakeService

    intake = IntakeService(store, objects, coding=coding)
    from pv_cases.seed import reference_feed

    intake.ingest(reference_feed()[0], DAY)
    decisions = coding.current_decisions("CASE-RPT-2026-0001")
    assert {d.subject_kind for d in decisions} == {"event", "product"}
    assert all(d.status is CodingStatus.CODED for d in decisions)


# ---------------------------------------------------------------------
# Consumers: history views and the assembler's discipline.
# ---------------------------------------------------------------------


def test_history_counts_coded_terms(full):
    view = full.history.build_patient_history(AB_KEY, DAY)
    assert view.coded_term_counts == {
        "Rash vesicular": 3,
        "Headache": 1,
        "Pruritus": 1,
    }
    assert view.uncoded_event_count == 0
    assert view.uncoded_terms == []
    product = full.history.build_product_history("NBT-317", DAY)
    assert product.coded_term_counts == {
        "Rash vesicular": 4,
        "Headache": 1,
        "Pruritus": 1,
    }


def test_history_names_uncoded_terms(full):
    view = full.history.build_patient_history(SP_KEY, DAY)
    assert view.coded_term_counts == {}
    assert view.uncoded_event_count == 1
    assert view.uncoded_terms == ["Excessive sleepiness in the mornings at work"]


def test_summary_presents_uncoded_as_uncoded(coded):
    summary = coded.assembler.assemble("CASE-RPT-2026-0203", DAY)
    texts = [s.text for s in summary.statements]
    assert (
        "Event uncoded: Blistering on hands and feet — no dictionary term;"
        " held for coding review." in texts
    )
    assert not any("→ Rash vesicular" in t and "Blistering on hands" in t for t in texts)
    assert coded.assembler.resolver.problems("CASE-RPT-2026-0203", summary.statements) == []


def test_summary_for_uncoded_product_case_assembles(coded):
    # Regression: "Cardivane Plus" contains the dictionary label
    # "Cardivane" as a substring — the intake statement reporting
    # the product as reported must not trip the discipline.
    summary = coded.assembler.assemble("CASE-RPT-2026-0205", DAY)
    texts = [s.text for s in summary.statements]
    assert any(t.startswith("Product uncoded: Cardivane Plus") for t in texts)
    assert any(t.startswith("Event coded: Severe headache → Headache") for t in texts)
    assert coded.assembler.resolver.problems("CASE-RPT-2026-0205", summary.statements) == []


def test_summary_presents_coded_with_its_label(coded):
    summary = coded.assembler.assemble("CASE-RPT-2026-0202", DAY)
    texts = [s.text for s in summary.statements]
    assert (
        "Event coded: Blistered rash on hands → Rash vesicular"
        " (Skin and subcutaneous tissue disorders)." in texts
    )
    assert (
        "Product coded: Dermaclear cream → Dermaclear (NBT-317, osalide)." in texts
    )


class _PresentsUncodedAsCoded:
    profile = "lying-test-double"

    def propose_summary(self, facts):
        item = next(f["item_id"] for f in facts if f["field"].startswith("coding."))
        return [
            SummaryStatement(
                text=(
                    "Event coded: Blistering on hands and feet → Rash vesicular"
                    " (Skin and subcutaneous tissue disorders)."
                ),
                citations=[Citation(evidence_item_id=item, field="coding.0")],
            )
        ]


class _MisstatesCodedTerm:
    profile = "lying-test-double"

    def propose_summary(self, facts):
        item = next(f["item_id"] for f in facts if f["field"].startswith("coding."))
        return [
            SummaryStatement(
                text=(
                    "Event coded: Blistered rash on hands → Rash"
                    " (Skin and subcutaneous tissue disorders)."
                ),
                citations=[Citation(evidence_item_id=item, field="coding.0")],
            )
        ]


def test_uncoded_presented_as_coded_is_refused(coded):
    assembler = CaseAssembler(coded.store, coded.chain, _PresentsUncodedAsCoded(), coding=coded.coding)
    with pytest.raises(CitationRefusal):
        assembler.assemble("CASE-RPT-2026-0203", DAY)
    assert coded.store.list_summaries("CASE-RPT-2026-0203") == []


def test_misstated_coded_term_is_refused(coded):
    assembler = CaseAssembler(coded.store, coded.chain, _MisstatesCodedTerm(), coding=coded.coding)
    with pytest.raises(CitationRefusal):
        assembler.assemble("CASE-RPT-2026-0202", DAY)
    assert coded.store.list_summaries("CASE-RPT-2026-0202") == []


class _AttachesLabelQuietly:
    profile = "lying-test-double"

    def propose_summary(self, facts):
        item = next(f["item_id"] for f in facts if f["field"].startswith("coding."))
        return [
            SummaryStatement(
                text="Event: Feeling drained (Fatigue).",
                citations=[Citation(evidence_item_id=item, field="coding.0")],
            )
        ]


def test_label_attached_without_claim_words_is_refused(coded):
    # No "coded", no arrow — just a preferred term quietly attached
    # to an uncoded verbatim. Still a presentation of coding.
    assembler = CaseAssembler(coded.store, coded.chain, _AttachesLabelQuietly(), coding=coded.coding)
    with pytest.raises(CitationRefusal):
        assembler.assemble("CASE-RPT-2026-0204", DAY)
    assert coded.store.list_summaries("CASE-RPT-2026-0204") == []
