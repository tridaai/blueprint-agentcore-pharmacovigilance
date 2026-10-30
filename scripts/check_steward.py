"""Re-performance check for the Round 14 dictionary
stewardship layer.

Builds the planted book on a throwaway store, strikes the
steward worklist, then re-opens the store on a fresh
service graph and re-performs the stored worklist row for
row by the verifier's second path. It tampers with one
row's count on a copy and requires the verifier to name
the broken figure — a check that cannot fail is not a
check. Finally it walks the candidate route end to end:
a proposal against the worklist's leading wording, a
self-decision refused by dual control, a second steward's
confirmation minting a new dictionary version — and the
cases coded before it standing exactly as coded.

Run: uv run python scripts/check_steward.py
Exit 0 = the stored worklist re-performs exactly,
tampering is detected, and the steward route behaves;
1 otherwise.
"""

from __future__ import annotations

import copy
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pv_cases.config import Settings
from pv_cases.seed import assessment_fixtures, dedup_fixtures
from pv_cases.steward import StewardError
from pv_cases.wiring import Services

DAY = date(2026, 10, 12)
AS_OF = date(2026, 11, 20)
DECIDE_DAY = date(2026, 11, 21)


def _services(tmp: Path) -> Services:
    return Services(
        Settings(
            sqlite_path=str(tmp / "pv.db"),
            object_store_dir=str(tmp / "objects"),
        )
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pv-steward-check-"))
    svc = _services(tmp)
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures():
        svc.intake.ingest(report, DAY)
    for report in assessment_fixtures():
        svc.intake.ingest(report, DAY)

    worklist = svc.steward.generate(
        AS_OF, "Dr S. Okafor", today=AS_OF
    )
    print(
        f"generated {worklist.worklist_id} (verified at"
        f" generation: {worklist.verified}) against dictionary"
        f" {worklist.base_dictionary_version}"
    )
    if not worklist.verified:
        for problem in worklist.verification_problems:
            print(f"  generation problem: {problem}")
        print("STEWARD CHECK: FAIL")
        return 1
    if not worklist.rows:
        print("STEWARD CHECK: FAIL — the planted book produced no rows")
        return 1

    # A fresh graph over the same store: the stored artefact
    # must re-perform exactly, from the record alone.
    reopened = _services(tmp)
    stored = reopened.steward.get_worklist(worklist.worklist_id)
    if stored is None:
        print("STEWARD CHECK: FAIL — the stored worklist is missing")
        return 1
    problems = reopened.steward.verify(stored)
    if problems:
        print("STEWARD CHECK: FAIL — the stored worklist does not re-perform:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("stored worklist re-performs exactly on a fresh graph")

    # Tamper: one count moved on a copy must be named.
    tampered_rows = copy.deepcopy(stored.rows)
    tampered_rows[0].reports += 1
    tampered = stored.model_copy(update={"rows": tampered_rows})
    problems = reopened.steward.verify(tampered)
    first = stored.rows[0]
    from pv_cases.models import normalise_text

    key = (
        f"{first.subject_kind}|"
        f"{normalise_text(first.reported_wording)}"
    )
    if not any(f"rows[{key}].reports" in p for p in problems):
        print("STEWARD CHECK: FAIL — a tampered count verified clean")
        return 1
    print("a tampered count is refused and named:")
    for problem in problems:
        print(f"  - {problem}")

    # The candidate route, end to end.
    lead = stored.rows[0]
    print(
        "planted book, worklist as of"
        f" {stored.as_of}:"
        f" {stored.totals['distinct_wordings']['value']} wording(s),"
        f" {stored.totals['uncoded_decisions']['value']} uncoded"
        f" decision(s) over"
        f" {stored.totals['uncoded_cases']['value']} case(s);"
        f" leading: {lead.reported_wording!r}"
        f" ({lead.reports} report(s))"
    )
    candidate = reopened.steward.propose(
        stored.worklist_id,
        lead.reported_wording,
        lead.subject_kind,
        "Dr S. Okafor",
        preferred_term="Paraesthesia",
        soc="Nervous system disorders",
        today=DECIDE_DAY,
    )
    before = {
        d.decision_id: d.model_dump_json()
        for d in reopened.store.list_coding_decisions()
        if d.status.value == "uncoded"
        and d.verbatim == lead.reported_wording
    }
    try:
        reopened.steward.decide(
            candidate.candidate_id,
            "confirm",
            "Dr S. Okafor",
            today=DECIDE_DAY,
        )
        print("STEWARD CHECK: FAIL — a proposer decided their own candidate")
        return 1
    except StewardError as exc:
        print(f"dual control holds: {exc}")
    record = reopened.steward.decide(
        candidate.candidate_id,
        "confirm",
        "Ms R. Chen",
        "Recurring wording with a clean mapping.",
        today=DECIDE_DAY,
    )
    print(
        f"second steward confirms: dictionary"
        f" {record.minted_dictionary_version} minted, candidate"
        f" {candidate.candidate_id} stands"
        f" {reopened.steward.candidate_state(candidate.candidate_id)}"
    )
    after = {
        d.decision_id: d.model_dump_json()
        for d in reopened.store.list_coding_decisions()
        if d.decision_id in before
    }
    if after != before:
        print(
            "STEWARD CHECK: FAIL — the confirmation rewrote a"
            " coded decision of record"
        )
        return 1
    print(
        "history stands: every decision of record on the"
        " wording is byte-identical after the confirmation"
    )
    print("STEWARD CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
