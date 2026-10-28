"""Demo: the planted feed end to end, offline.

Walks every planted report through the validity gate, shows which
became cases and which became follow-up tasks, flags the suspected
duplicate, assembles the main case's summary with its citations, and
verifies the evidence chain. Run with ``make demo``.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from pv_cases.config import Settings
from pv_cases.wiring import Services

DEMO_DAY = date(2026, 10, 12)


def run_demo(out=print) -> Services:
    tmp = Path(tempfile.mkdtemp(prefix="pv-demo-"))
    settings = Settings(
        sqlite_path=str(tmp / "pv.db"),
        object_store_dir=str(tmp / "objects"),
    )
    svc = Services(settings)

    out("Northbridge Therapeutics — pharmacovigilance intake demo")
    out("=" * 62)
    outcomes = svc.intake.ingest_feed(svc.feed.fetch_reports(), DEMO_DAY)
    for outcome in outcomes:
        if outcome.case is not None:
            dup = (
                f"  [suspected duplicate of {outcome.case.suspected_duplicate_of}]"
                if outcome.case.suspected_duplicate_of
                else ""
            )
            out(f"{outcome.report_id}: CASE {outcome.case.case_id}{dup}")
        else:
            missing = ", ".join(outcome.follow_up.missing_criteria)
            out(f"{outcome.report_id}: FOLLOW-UP {outcome.follow_up.task_id} (missing: {missing})")

    out("-" * 62)
    summary = svc.assembler.assemble("CASE-RPT-2026-0001", DEMO_DAY)
    out(f"Summary for CASE-RPT-2026-0001 (profile: {summary.model_profile}):")
    for stmt in summary.statements:
        cites = ", ".join(f"{c.evidence_item_id}:{c.field}" for c in stmt.citations)
        out(f"  - {stmt.text}  [{cites}]")
    ok = svc.chain.verify_chain("CASE-RPT-2026-0001")
    out(f"Evidence chain valid: {ok}")
    return svc


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()
