"""Command line: ``pv-agent feed|assemble|verify|dedup|confirm|
reject|history-*|coding*|resolve-coding|dictionary*`` against the
configured store."""

from __future__ import annotations

import json
import sys
from datetime import date

from pv_cases.coding import CodingError
from pv_cases.dedup import DedupDecisionError
from pv_cases.models import SUBJECT_EVENT, SUBJECT_PRODUCT
from pv_cases.runtime_app import handle_invocation
from pv_cases.wiring import build_services
import pv_cases.runtime_app as runtime_app

USAGE = (
    "usage: pv-agent feed|assemble <case_id>|verify <case_id>|"
    "dedup|dedup-scan|confirm <link_id> <decided_by>|"
    "reject <link_id> <decided_by>|history-patient <patient_key>|"
    "history-product <product_code>|history-case <case_id>|"
    "coding <case_id>|coding-queue|"
    "resolve-coding <task_id> <coder> <term-or-code...>|"
    "dictionary|dictionary-add <proposed_by> <event|product> <entry-json>"
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
    if cmd == "coding" and len(argv) == 2:
        if svc.store.get_case(argv[1]) is None:
            print(f"no such case: {argv[1]}")
            return 1
        for d in svc.coding.current_decisions(argv[1]):
            if d.status.value == "coded":
                label = (
                    d.preferred_term
                    if d.subject_kind == SUBJECT_EVENT
                    else f"{d.product_name} ({d.product_code})"
                )
                print(
                    f"{d.subject_kind} {d.verbatim!r} -> {label}"
                    f" [{d.match_path.value}, dictionary {d.dictionary_version}]"
                )
            else:
                print(
                    f"{d.subject_kind} {d.verbatim!r} -> UNCODED"
                    f" ({d.uncoded_reason}, dictionary {d.dictionary_version})"
                )
        return 0
    if cmd == "coding-queue":
        queue = svc.coding.uncoded_queue()
        if not queue:
            print("coding queue: empty")
        for task in queue:
            candidates = (
                f" candidates: {', '.join(task.candidates)}" if task.candidates else ""
            )
            print(
                f"{task.task_id}: {task.case_id} {task.subject_kind}"
                f" {task.verbatim!r} [{task.reason}]{candidates}"
                " — awaiting a named coder"
            )
        return 0
    if cmd == "resolve-coding" and len(argv) >= 4:
        task_id, coder, choice = argv[1], argv[2], " ".join(argv[3:])
        task = svc.store.get_coding_task(task_id)
        if task is None:
            print(f"no such coding task: {task_id}")
            return 1
        try:
            if task.subject_kind == SUBJECT_EVENT:
                decision = svc.coding.resolve(
                    task_id, coder, today, preferred_term=choice
                )
            else:
                decision = svc.coding.resolve(
                    task_id, coder, today, product_code=choice
                )
        except CodingError as exc:
            print(f"resolution refused: {exc}")
            return 1
        label = (
            decision.preferred_term
            if decision.subject_kind == SUBJECT_EVENT
            else decision.product_code
        )
        print(f"{task_id}: coded to {label} by {decision.coded_by} [{decision.decision_id}]")
        return 0
    if cmd == "dictionary":
        dv = svc.coding.dictionary(today=today)
        print(
            f"dictionary {dv.version} (by {dv.created_by}):"
            f" {len(dv.event_entries)} event entries,"
            f" {len(dv.product_entries)} product entries"
        )
        return 0
    if cmd == "dictionary-add" and len(argv) == 4:
        from pv_cases.models import DictionaryEntry, ProductDictionaryEntry

        try:
            payload = json.loads(argv[3])
            if argv[2] == SUBJECT_EVENT:
                entry = DictionaryEntry.model_validate(payload)
            elif argv[2] == SUBJECT_PRODUCT:
                entry = ProductDictionaryEntry.model_validate(payload)
            else:
                print(f"unknown addition kind: {argv[2]!r}")
                return 1
            version = svc.coding.propose_addition(argv[1], today, argv[2], entry)
        except (ValueError, CodingError) as exc:
            print(f"addition refused: {exc}")
            return 1
        print(f"dictionary {version.version} created by {version.created_by}")
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
