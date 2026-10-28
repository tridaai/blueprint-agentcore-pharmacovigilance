"""Command line: ``pv-agent feed|assemble|verify`` against the
configured store."""

from __future__ import annotations

import json
import sys
from datetime import date

from pv_cases.runtime_app import handle_invocation
from pv_cases.wiring import build_services
import pv_cases.runtime_app as runtime_app


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    svc = build_services()
    runtime_app._services = svc
    if not argv:
        print("usage: pv-agent feed|assemble <case_id>|verify <case_id>")
        return 2
    cmd = argv[0]
    if cmd == "feed":
        outcomes = svc.intake.ingest_feed(svc.feed.fetch_reports(), date.today())
        for o in outcomes:
            if o.case:
                print(f"{o.report_id} -> case {o.case.case_id}")
            else:
                print(f"{o.report_id} -> follow-up {o.follow_up.task_id}: {o.follow_up.missing_criteria}")
        return 0
    if cmd == "assemble" and len(argv) == 2:
        print(json.dumps(handle_invocation({"action": "assemble_summary", "case_id": argv[1]}), indent=2))
        return 0
    if cmd == "verify" and len(argv) == 2:
        print(json.dumps({"case_id": argv[1], "chain_valid": svc.chain.verify_chain(argv[1])}))
        return 0
    print("usage: pv-agent feed|assemble <case_id>|verify <case_id>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
