"""Command line: ``pv-agent feed|assemble|verify|dedup|confirm|
reject|history-*`` against the configured store."""

from __future__ import annotations

import json
import sys
from datetime import date

from pv_cases.dedup import DedupDecisionError
from pv_cases.runtime_app import handle_invocation
from pv_cases.wiring import build_services
import pv_cases.runtime_app as runtime_app

USAGE = (
    "usage: pv-agent feed|assemble <case_id>|verify <case_id>|"
    "dedup|dedup-scan|confirm <link_id> <decided_by>|"
    "reject <link_id> <decided_by>|history-patient <patient_key>|"
    "history-product <product_code>|history-case <case_id>"
)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    svc = build_services()
    runtime_app._services = svc
    if not argv:
        print(USAGE)
        return 2
    cmd = argv[0]
    today = date.today()
    if cmd == "feed":
        outcomes = svc.intake.ingest_feed(svc.feed.fetch_reports(), today)
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
    if cmd == "dedup":
        queue = svc.dedup.pending_queue()
        if not queue:
            print("dedup queue: empty")
        for link in queue:
            print(
                f"{link.link_id}: {link.case_b_id} duplicate of {link.case_a_id}"
                f" [{link.band.value}] — awaiting a named decision"
            )
        return 0
    if cmd == "dedup-scan":
        created = svc.dedup.scan_all(today)
        print(f"dedup scan: {len(created)} new link(s)")
        for link in created:
            print(f"  {link.link_id} [{link.band.value}]")
        return 0
    if cmd in ("confirm", "reject") and len(argv) >= 3:
        decided_by = " ".join(argv[2:])
        try:
            if cmd == "confirm":
                link = svc.dedup.confirm_link(argv[1], decided_by, today)
            else:
                link = svc.dedup.reject_link(argv[1], decided_by, today)
        except KeyError:
            print(f"no such dedup link: {argv[1]}")
            return 1
        except DedupDecisionError as exc:
            print(f"decision refused: {exc}")
            return 1
        print(f"{link.link_id}: {link.status.value} by {link.decided_by}")
        return 0
    if cmd == "history-patient" and len(argv) == 2:
        view = svc.history.build_patient_history(argv[1], today)
        print(json.dumps(view.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "history-product" and len(argv) == 2:
        view = svc.history.build_product_history(argv[1], today)
        print(json.dumps(view.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "history-case" and len(argv) == 2:
        try:
            view = svc.history.patient_history_for_case(argv[1], today)
        except KeyError:
            print(f"no such case: {argv[1]}")
            return 1
        print(json.dumps(view.model_dump(mode="json"), indent=2))
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
