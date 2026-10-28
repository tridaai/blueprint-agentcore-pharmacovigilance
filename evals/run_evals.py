"""Eval gate — Rounds 1–2 (pharmacovigilance case agent).

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

Run: uv run python evals/run_evals.py  (exit 0 = GATE: PASS)
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pv_cases.config import Settings  # noqa: E402
from pv_cases.models import CaseStatus, DedupBand  # noqa: E402
from pv_cases.seed import dedup_fixtures, reference_feed  # noqa: E402
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

    print("Rounds 1–2 evals — pharmacovigilance case agent")
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
