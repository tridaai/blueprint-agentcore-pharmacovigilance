"""Eval gate — Rounds 1–3 (pharmacovigilance case agent).

Runs the planted feed through the real service graph on a throwaway
SQLite store and grades the guarantees. Every counter is
zero-tolerance unless marked otherwise:

Round 1:
  invalid_reports_became_cases   a report failing the validity gate
                                 must never exist as a case
  valid_cases_missing            every valid planted report is a case
  follow_ups_missing             every invalid planted report is
                                 tracked follow-up work
  duplicate_suspicion_missed     the planted duplicate pair shares a
                                 key and the second case is flagged
  uncited_statements             summary statements without citations
  citation_resolution_failures   citations not resolving to evidence
  chain_verification_failures    any case whose evidence chain fails
  summaries_missing              the graded case has no summary

Round 2 (de-duplication + history views), graded over the reference
feed plus the dedup fixture set:
  dedup_missed_definite          the planted pair is not assessed
                                 definite, or its link is missing
  dedup_wrongful_definite        any other pair assessed definite
  dedup_probable_band_wrong      the probable fixture pair lands
                                 outside probable_duplicate
  dedup_trap_band_wrong          a near-miss trap pair lands outside
                                 not_duplicate
  dedup_links_missing            pending links are not exactly the
                                 definite + probable findings
  dedup_auto_merges              cases lost, or a confirmed-closed
                                 case unreadable / not closed — the
                                 structural guarantee is behavioural
                                 here: confirm a link, lose nothing
  dedup_decision_chain_failures  the confirmed case's chain fails
                                 after the decision is recorded
  history_patient_figures_wrong  patient view figures vs hand truth
  history_product_figures_wrong  product view figures vs hand truth
  history_conflicts_merged       an identity conflict collapsed to
                                 one value instead of showing both
  history_rebuild_not_idempotent rebuilding a view changed its id
  history_citation_failures      assembler history statements
                                 missing, or their citations fail

Round 3 (dictionary coding), graded over the full book — the
reference feed, the dedup fixtures and the coding fixtures:
  coding_wrongful_codings        any current coding decision that
                                 differs from the hand-derived truth
                                 (status, match path, label, uncoded
                                 reason, pinned dictionary version,
                                 verbatim as reported)
  coding_review_tasks_missing    an uncoded current decision with
                                 no open review task, or a task
                                 pointing anywhere else
  uncoded_presented_as_coded     a summary statement presenting an
                                 uncoded term as coded (or misstating
                                 a coded one), checked in code over
                                 every assembled case
  history_coded_figures_wrong    history views' coded-term counts
                                 and named uncoded terms vs truth
  dictionary_in_place_edits      the store port growing an update
                                 or delete for dictionary versions
                                 or coding decisions, a stored
                                 version changing after an addition,
                                 or a decision pinned to the old
                                 version moving

Run: uv run python evals/run_evals.py  (exit 0 = GATE: PASS)
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pv_cases.coding import coding_presentation_problems  # noqa: E402
from pv_cases.config import Settings  # noqa: E402
from pv_cases.models import (  # noqa: E402
    DictionaryEntry,
    CaseStatus,
    DedupBand,
)
from pv_cases.seed import coding_fixtures, dedup_fixtures, reference_feed  # noqa: E402
from pv_cases.validity import assess_validity  # noqa: E402
from pv_cases.wiring import Services  # noqa: E402

DAY = date(2026, 10, 12)
AB_KEY = "dob:1988-03-14|f"
C = "CASE-RPT-2026-"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pv-evals-"))
    svc = Services(
        Settings(sqlite_path=str(tmp / "pv.db"), object_store_dir=str(tmp / "objects"))
    )
    counters: dict[str, int] = {}

    # ---------------- Round 1 ----------------
    reports = reference_feed()
    expected_valid = {r.report_id for r in reports if assess_validity(r).valid}
    expected_invalid = {r.report_id for r in reports if not assess_validity(r).valid}

    svc.intake.ingest_feed(reports, DAY)
    cases = svc.store.list_cases()
    case_reports = {c.report_id for c in cases}
    follow_ups = svc.store.list_follow_ups()
    follow_up_reports = {t.report_id for t in follow_ups}

    counters["invalid_reports_became_cases"] = len(case_reports & expected_invalid)
    counters["valid_cases_missing"] = len(expected_valid - case_reports)
    counters["follow_ups_missing"] = len(expected_invalid - follow_up_reports)
    counters["follow_up_for_valid_reports"] = len(follow_up_reports & expected_valid)

    second = svc.store.get_case("CASE-RPT-2026-0006")
    counters["duplicate_suspicion_missed"] = (
        0
        if second and second.suspected_duplicate_of == "CASE-RPT-2026-0005"
        else 1
    )

    uncited = 0
    unresolved = 0
    chain_failures = 0
    summaries_missing = 0
    for case in cases:
        summary = svc.assembler.assemble(case.case_id, DAY)
        if not summary.statements:
            summaries_missing += 1
        problems = svc.assembler.resolver.problems(case.case_id, summary.statements)
        unresolved += len(problems)
        uncited += sum(1 for s in summary.statements if not s.citations)
        if not svc.chain.verify_chain(case.case_id):
            chain_failures += 1
    counters["uncited_statements"] = uncited
    counters["citation_resolution_failures"] = unresolved
    counters["chain_verification_failures"] = chain_failures
    counters["summaries_missing"] = summaries_missing

    # ---------------- Round 2: dedup ----------------
    for report in dedup_fixtures():
        svc.intake.ingest(report, DAY)
    svc.dedup.scan_all(DAY)

    by_pair = {
        frozenset((a.case_a_id, a.case_b_id)): a
        for a in svc.store.list_dedup_assessments()
    }

    def band_of(x: str, y: str):
        item = by_pair.get(frozenset((x, y)))
        return item.band if item else None

    definite_pair = (f"{C}0005", f"{C}0006")
    probable_pair = (f"{C}0101", f"{C}0102")
    trap_pairs = [
        (f"{C}0005", f"{C}0103"),  # different event — genuine 2nd case
        (f"{C}0005", f"{C}0104"),  # namesake — DOB contradicts
        (f"{C}0005", f"{C}0105"),  # recurrence — onset a year apart
        (f"{C}0005", f"{C}0106"),  # same patient, unrelated event
        (f"{C}0006", f"{C}0104"),  # namesake against the pair's twin
    ]
    counters["dedup_missed_definite"] = (
        0 if band_of(*definite_pair) is DedupBand.DEFINITE else 1
    )
    counters["dedup_wrongful_definite"] = sum(
        1
        for pair, item in by_pair.items()
        if item.band is DedupBand.DEFINITE and pair != frozenset(definite_pair)
    )
    counters["dedup_probable_band_wrong"] = (
        0 if band_of(*probable_pair) is DedupBand.PROBABLE else 1
    )
    counters["dedup_trap_band_wrong"] = sum(
        1 for pair in trap_pairs if band_of(*pair) is not DedupBand.NOT_DUPLICATE
    )
    pending_ids = {l.link_id for l in svc.dedup.pending_queue()}
    expected_links = {
        f"LINK-{definite_pair[0]}-{definite_pair[1]}",
        f"LINK-{probable_pair[0]}-{probable_pair[1]}",
    }
    counters["dedup_links_missing"] = len(expected_links - pending_ids) + len(
        pending_ids - expected_links
    )

    # Confirming the definite link must lose nothing: the case
    # closes, stays readable, and its chain still verifies.
    total_before = len(svc.store.list_cases())
    svc.dedup.confirm_link(
        f"LINK-{definite_pair[0]}-{definite_pair[1]}", "Eval Reviewer", DAY
    )
    closed = svc.store.get_case(definite_pair[1])
    counters["dedup_auto_merges"] = (total_before - len(svc.store.list_cases())) + (
        0
        if closed is not None and closed.status is CaseStatus.CLOSED_AS_DUPLICATE
        else 1
    )
    counters["dedup_decision_chain_failures"] = (
        0 if svc.chain.verify_chain(definite_pair[1]) else 1
    )

    # ---------------- Round 2: history views ----------------
    view = svc.history.build_patient_history(AB_KEY, DAY)
    patient_wrong = 0
    patient_wrong += view.case_count != 5
    patient_wrong += view.event_count != 5
    patient_wrong += view.event_term_counts.get("blistering rash on hands") != 3
    patient_wrong += set(view.case_ids) != {
        f"{C}0005", f"{C}0006", f"{C}0103", f"{C}0105", f"{C}0106",
    }
    counters["history_patient_figures_wrong"] = patient_wrong
    counters["history_conflicts_merged"] = (
        0 if view.identity_conflicts.get("age") == ["38", "52"] else 1
    )
    rebuilt = svc.history.build_patient_history(AB_KEY, DAY)
    counters["history_rebuild_not_idempotent"] = (
        0 if rebuilt.view_id == view.view_id else 1
    )

    product = svc.history.build_product_history("NBT-317", DAY)
    product_wrong = 0
    product_wrong += product.case_count != 6
    product_wrong += product.event_count != 6
    product_wrong += product.event_term_counts.get("blistering rash on hands") != 4
    product_wrong += product.follow_up_task_ids != ["FU-RPT-2026-0004"]
    counters["history_product_figures_wrong"] = product_wrong

    # The assembler consumes the built view; its history statements
    # cite the view artefact and must resolve.
    summary = svc.assembler.assemble(f"{C}0005", DAY)
    history_statements = [
        s for s in summary.statements if s.text.startswith("Patient history:")
    ]
    problems = svc.assembler.resolver.problems(f"{C}0005", summary.statements)
    counters["history_citation_failures"] = len(problems) + (
        0 if len(history_statements) == 2 else 1
    )

    # ---------------- Round 3: coding ----------------
    # History views consume the coding layer (checked here, over
    # the feed + dedup book, before the coding fixtures arrive).
    coded_wrong = 0
    coded_wrong += view.coded_term_counts != {
        "Rash vesicular": 3,
        "Headache": 1,
        "Pruritus": 1,
    }
    coded_wrong += view.uncoded_event_count != 0
    coded_wrong += product.coded_term_counts != {
        "Rash vesicular": 4,
        "Headache": 1,
        "Pruritus": 1,
    }
    sp_view = svc.history.build_patient_history("init:s p|", DAY)
    coded_wrong += sp_view.coded_term_counts != {}
    coded_wrong += sp_view.uncoded_event_count != 1
    coded_wrong += sp_view.uncoded_terms != [
        "Excessive sleepiness in the mornings at work"
    ]
    counters["history_coded_figures_wrong"] = coded_wrong

    for report in coding_fixtures():
        svc.intake.ingest(report, DAY)
    reports_by_case = {}
    for report in reference_feed() + dedup_fixtures() + coding_fixtures():
        reports_by_case[f"CASE-{report.report_id}"] = report

    # Hand-derived truth for every subject of every case in the
    # book: (status, match path, label) with the label a preferred
    # term for events and a product code for products.
    E, P = "event", "product"
    CODED, UNCODED = "coded", "uncoded"
    EXPECTED = {
        f"{C}0001": {E: (CODED, "exact", "Dizziness"), P: (CODED, "exact", "NBT-101")},
        f"{C}0002": {E: (CODED, "exact", "Somnolence"), P: (CODED, "exact", "NBT-204")},
        f"{C}0005": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0006": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0101": {E: (CODED, "exact", "Somnolence"), P: (CODED, "exact", "NBT-204")},
        f"{C}0102": {E: (UNCODED, "none", None), P: (CODED, "exact", "NBT-204")},
        f"{C}0103": {E: (CODED, "exact", "Headache"), P: (CODED, "exact", "NBT-317")},
        f"{C}0104": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0105": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0106": {E: (CODED, "exact", "Pruritus"), P: (CODED, "exact", "NBT-317")},
        f"{C}0201": {E: (CODED, "normalised", "Rash vesicular"), P: (CODED, "exact", "NBT-204")},
        f"{C}0202": {E: (CODED, "synonym", "Rash vesicular"), P: (CODED, "synonym", "NBT-317")},
        f"{C}0203": {E: (UNCODED, "none", None), P: (CODED, "exact", "NBT-317")},
        f"{C}0204": {E: (UNCODED, "none", None), P: (CODED, "exact", "NBT-101")},
        f"{C}0205": {E: (CODED, "exact", "Headache"), P: (UNCODED, "none", None)},
        f"{C}0206": {E: (CODED, "exact", "Nausea"), P: (CODED, "exact", "NBT-204")},
    }
    UNCODED_REASONS = {
        (f"{C}0102", E): "no_dictionary_entry",
        (f"{C}0203", E): "no_dictionary_entry",
        (f"{C}0204", E): "ambiguous_match",
        (f"{C}0205", P): "no_dictionary_entry",
    }
    wrongful = 0
    for case_id, subjects in EXPECTED.items():
        report = reports_by_case[case_id]
        current = {
            (d.subject_kind, d.subject_index): d
            for d in svc.coding.current_decisions(case_id)
        }
        for kind, (status, path, label) in subjects.items():
            decision = current.get((kind, 0))
            if decision is None:
                wrongful += 1
                continue
            verbatim = (
                report.events[0].verbatim_term
                if kind == E
                else (report.suspect_product_name or report.suspect_product_code)
            )
            wrongful += decision.status.value != status
            wrongful += decision.match_path.value != path
            wrongful += decision.dictionary_version != "2026.10.1"
            wrongful += decision.verbatim != verbatim
            if status == CODED:
                actual = (
                    decision.preferred_term if kind == E else decision.product_code
                )
                wrongful += actual != label
            else:
                wrongful += decision.uncoded_reason != UNCODED_REASONS[(case_id, kind)]
    ambiguous = next(
        d
        for d in svc.coding.current_decisions(f"{C}0204")
        if d.subject_kind == E
    )
    wrongful += ambiguous.candidates != ["Asthenia", "Fatigue"]
    counters["coding_wrongful_codings"] = wrongful

    # Every uncoded current decision has exactly one open task,
    # and every open task points at an uncoded current decision.
    open_tasks = svc.coding.uncoded_queue()
    uncoded_ids = {
        d.decision_id
        for case in svc.store.list_cases()
        for d in svc.coding.current_decisions(case.case_id)
        if d.status.value == UNCODED
    }
    counters["coding_review_tasks_missing"] = len(
        {t.decision_id for t in open_tasks} ^ uncoded_ids
    ) + (len(open_tasks) - len({t.decision_id for t in open_tasks}))

    # Assemble every case: no summary may present an uncoded term
    # as coded, and the uncoded cases must say so out loud.
    dictionary = svc.coding.dictionary(today=DAY)
    presented = 0
    for case in svc.store.list_cases():
        summary = svc.assembler.assemble(case.case_id, DAY)
        presented += len(
            coding_presentation_problems(
                svc.coding.current_decisions(case.case_id),
                summary.statements,
                dictionary,
            )
        )
    for case_id, marker in (
        (f"{C}0203", "Event uncoded: Blistering on hands and feet"),
        (f"{C}0204", "Event uncoded: Feeling drained"),
        (f"{C}0205", "Product uncoded: Cardivane Plus"),
    ):
        texts = [
            s.text for s in svc.store.list_summaries(case_id)[-1].statements
        ]
        presented += 0 if any(t.startswith(marker) for t in texts) else 1
    counters["uncoded_presented_as_coded"] = presented

    # Dictionary immutability: the port grows no in-place edit for
    # versions or decisions, an addition leaves the stored base
    # version and its pinned decisions untouched, and a recode
    # under the new version settles the near-miss term.
    edits = 0
    for verb in (
        "update_dictionary_version",
        "delete_dictionary_version",
        "update_coding_decision",
        "delete_coding_decision",
    ):
        edits += 1 if hasattr(svc.store, verb) else 0
    v1_before = svc.coding.dictionary("2026.10.1").model_dump()
    pinned_before = next(
        d
        for d in svc.store.list_coding_decisions(f"{C}0203")
        if d.subject_kind == E
    ).model_dump()
    svc.coding.propose_addition(
        "Eval Coder",
        DAY,
        "event",
        DictionaryEntry(
            verbatim="Blistering on hands and feet",
            preferred_term="Rash vesicular",
            soc="Skin and subcutaneous tissue disorders",
        ),
    )
    edits += svc.coding.dictionary("2026.10.1").model_dump() != v1_before
    edits += (
        next(
            d
            for d in svc.store.list_coding_decisions(f"{C}0203")
            if d.decision_id == pinned_before["decision_id"]
        ).model_dump()
        != pinned_before
    )
    svc.coding.code_case(f"{C}0203", DAY)
    settled = next(
        d
        for d in svc.coding.current_decisions(f"{C}0203")
        if d.subject_kind == E
    )
    edits += settled.status.value != CODED or settled.dictionary_version != "2026.10.2"
    edits += settled.supersedes_id != pinned_before["decision_id"]
    counters["dictionary_in_place_edits"] = edits

    print("Rounds 1–3 evals — pharmacovigilance case agent")
    for name, value in counters.items():
        print(f"  {name}: {value} (required 0)")
    failed = [n for n, v in counters.items() if v != 0]
    if failed:
        print("GATE: FAIL")
        for name in failed:
            print(f"  - {name} = {counters[name]}")
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
