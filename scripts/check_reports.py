"""Re-performance check for the Round 11 oversight reports.

Builds the planted book on a throwaway store, generates the
October 2026 oversight report, then re-opens the store on a
fresh service graph and re-performs the stored report figure
for figure by the verifier's second path. Finally it tampers
with one figure on a copy of the report and requires the
verifier to name the broken figure — a check that cannot
fail is not a check.

Run: uv run python scripts/check_reports.py
Exit 0 = the stored report re-performs exactly and tampering
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
    tmp = Path(tempfile.mkdtemp(prefix="pv-reports-check-"))
    svc = _services(tmp)
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures():
        svc.intake.ingest(report, DAY)
    for report in assessment_fixtures():
        svc.intake.ingest(report, DAY)

    report = svc.oversight.generate(
        WINDOW[0], WINDOW[1], AS_OF, "Dr A. Rao", today=AS_OF
    )
    print(f"generated {report.report_id} (verified at generation: {report.verified})")
    if not report.verified:
        for problem in report.verification_problems:
            print(f"  generation problem: {problem}")
        print("REPORTS CHECK: FAIL")
        return 1

    # A fresh graph over the same store: the stored artefact
    # must re-perform exactly, from the record alone.
    reopened = _services(tmp)
    stored = reopened.oversight.get(report.report_id)
    if stored is None:
        print("REPORTS CHECK: FAIL — the stored report is missing")
        return 1
    problems = reopened.oversight.verify(stored)
    if problems:
        print("REPORTS CHECK: FAIL — the stored report does not re-perform:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("stored report re-performs exactly on a fresh graph")

    # Tamper: one figure moved on a copy must be named.
    tampered_sections = copy.deepcopy(stored.sections)
    tampered_sections["intake"]["cases_founded"]["value"] += 1
    tampered = stored.model_copy(update={"sections": tampered_sections})
    problems = reopened.oversight.verify(tampered)
    if not any("intake.cases_founded" in p for p in problems):
        print("REPORTS CHECK: FAIL — a tampered figure verified clean")
        return 1
    print("a tampered figure is refused and named:")
    for problem in problems:
        print(f"  - {problem}")

    clocks = stored.sections["clocks"]
    print(
        "planted book, October window:"
        f" 7-day due {clocks['expedited_7_day_due']['value']},"
        f" breached open {clocks['expedited_7_day_breached_open']['value']};"
        f" 15-day due {clocks['expedited_15_day_due']['value']},"
        f" breached open {clocks['expedited_15_day_breached_open']['value']}"
    )
    print("REPORTS CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
