"""Re-performance check for the Round 13 signal tables.

Builds the planted book on a throwaway store, generates the
October 2026 signal table, then re-opens the store on a
fresh service graph and re-performs the stored table cell
for cell by the verifier's second path. Finally it tampers
with one cell on a copy of the table and requires the
verifier to name the broken cell — a check that cannot
fail is not a check.

Run: uv run python scripts/check_signals.py
Exit 0 = the stored table re-performs exactly and tampering
is detected; 1 otherwise.
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
from pv_cases.wiring import Services

DAY = date(2026, 10, 12)
WINDOW = (date(2026, 10, 1), date(2026, 10, 31))
AS_OF = date(2026, 11, 20)


def _services(tmp: Path) -> Services:
    return Services(
        Settings(
            sqlite_path=str(tmp / "pv.db"),
            object_store_dir=str(tmp / "objects"),
        )
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pv-signals-check-"))
    svc = _services(tmp)
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures():
        svc.intake.ingest(report, DAY)
    for report in assessment_fixtures():
        svc.intake.ingest(report, DAY)

    table = svc.signals.generate(
        WINDOW[0], WINDOW[1], AS_OF, "Dr S. Okafor", today=AS_OF
    )
    print(f"generated {table.table_id} (verified at generation: {table.verified})")
    if not table.verified:
        for problem in table.verification_problems:
            print(f"  generation problem: {problem}")
        print("SIGNALS CHECK: FAIL")
        return 1

    # A fresh graph over the same store: the stored artefact
    # must re-perform exactly, from the record alone.
    reopened = _services(tmp)
    stored = reopened.signals.get(table.table_id)
    if stored is None:
        print("SIGNALS CHECK: FAIL — the stored table is missing")
        return 1
    problems = reopened.signals.verify(stored)
    if problems:
        print("SIGNALS CHECK: FAIL — the stored table does not re-perform:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("stored table re-performs exactly on a fresh graph")

    # Tamper: one cell moved on a copy must be named.
    if not stored.pairs:
        print("SIGNALS CHECK: FAIL — the planted book produced no pairs")
        return 1
    tampered_pairs = copy.deepcopy(stored.pairs)
    tampered_pairs[0].a += 1
    tampered = stored.model_copy(update={"pairs": tampered_pairs})
    problems = reopened.signals.verify(tampered)
    first = stored.pairs[0]
    key = f"{first.product_code}|{first.preferred_term}|{first.soc}"
    if not any(f"pairs[{key}].a" in p for p in problems):
        print("SIGNALS CHECK: FAIL — a tampered cell verified clean")
        return 1
    print("a tampered cell is refused and named:")
    for problem in problems:
        print(f"  - {problem}")

    totals = stored.totals
    flagged = [p for p in stored.pairs if p.meets_criteria]
    print(
        "planted book, October window:"
        f" {totals['attributable_reports']['value']} attributable reports,"
        f" {totals['pairs_counted']['value']} pairs,"
        f" {totals['pairs_flagged']['value']} flagged"
    )
    for pair in flagged:
        print(
            f"  flagged: {pair.product_name} + {pair.preferred_term}"
            f" — a={pair.a} b={pair.b} c={pair.c} d={pair.d},"
            f" PRR {pair.prr}, chi-square {pair.chi_square}"
        )
    print("SIGNALS CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
