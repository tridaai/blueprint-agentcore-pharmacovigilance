"""Command line: ``pv-agent feed|assemble|verify|dedup|confirm|
reject|history-*|coding*|resolve-coding|dictionary*|assess|
assessment|clocks|rsi|follow-up*|submit-follow-up|review|
review-case|review-queue|review-pack|seal|seal-case|
export-case|verify-export|sealing-pack|quarantine|
release-quarantine|quarantine-pack|memory-recall|
memory-retrievals|memory-retention|memory-precedents|
memory-pack|gateway-invoke|gateway-calls|gateway-pack|
oversight-pack|oversight-generate|oversight-reports|
oversight-report|oversight-verify|model-pack|model-ledger|
model-ledger-verify|signals-pack|signals-generate|
signals-tables|signals-table|signals-verify``
against the configured store."""

from __future__ import annotations

import json
import sys
from datetime import date

from pv_cases.coding import CodingError
from pv_cases.dedup import DedupDecisionError
from pv_cases.followup import FollowUpError
from pv_cases.models import SUBJECT_EVENT, SUBJECT_PRODUCT, IntakeReport
from pv_cases.review import ReviewError
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
    "dictionary|dictionary-add <proposed_by> <event|product> <entry-json>|"
    "assess <case_id>|assessment <case_id>|clocks [as-of]|rsi|"
    "follow-up-board [as-of]|follow-up-tasks <case_id>|"
    "follow-up-task <task_id>|follow-up-pack|"
    "submit-follow-up <task_id> <report-json>|"
    "review <case_id> <reviewer> <role> <decision> [rationale...]|"
    "review-case <case_id>|review-queue [as-of]|review-pack|"
    "seal <case_id> <sealed_by...>|seal-case <case_id>|"
    "export-case <case_id> <exported_by...>|"
    "verify-export <package.json>|sealing-pack|"
    "quarantine <case_id>|"
    "release-quarantine <record_id> <released_by> <rationale...>|"
    "quarantine-pack|memory-recall <case_id> <strategy> <requested_by>|"
    "memory-retrievals <case_id>|memory-retention|"
    "memory-precedents <requested_by> <verbatim...>|memory-pack|"
    "gateway-invoke <principal> <role> <tool> <args...>|"
    "gateway-calls|gateway-pack|"
    "oversight-pack|"
    "oversight-generate <window-start> <window-end> <as-of> <generated_by...>|"
    "oversight-reports|oversight-report <report_id>|"
    "oversight-verify <report_id>|model-pack|"
    "model-ledger <case_id>|model-ledger-verify|signals-pack|"
    "signals-generate <window-start> <window-end> <as-of> <generated_by...>|"
    "signals-tables|signals-table <table_id>|"
    "signals-verify <table_id>"
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
    if cmd == "assess" and len(argv) == 2:
        try:
            artefact = svc.assessment.assess_case(argv[1], today)
        except KeyError:
            print(f"no such case: {argv[1]}")
            return 1
        clock = artefact.clock
        deadline = f" deadline {clock.deadline}" if clock.deadline else ""
        print(
            f"{artefact.assessment_id}: seriousness={artefact.seriousness.value}"
            f" expectedness={artefact.expectedness.value}"
            f" clock={clock.obligation_class}{deadline}"
        )
        return 0
    if cmd == "assessment" and len(argv) == 2:
        if svc.store.get_case(argv[1]) is None:
            print(f"no such case: {argv[1]}")
            return 1
        current = svc.assessment.current_assessment(argv[1])
        if current is None:
            print(f"{argv[1]}: no assessment yet")
            return 1
        print(json.dumps(current.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "clocks" and len(argv) <= 2:
        from datetime import date as _date

        as_of = _date.fromisoformat(argv[1]) if len(argv) == 2 else today
        board = svc.assessment.clock_board(as_of)
        if not board:
            print(f"clock board at {as_of}: empty")
        for entry in board:
            deadline = f" deadline {entry.deadline}" if entry.deadline else ""
            remaining = (
                f" ({entry.days_remaining:+d}d)" if entry.days_remaining is not None else ""
            )
            print(
                f"{entry.case_id}: {entry.obligation_class}{deadline}"
                f" — {entry.state}{remaining}"
            )
        return 0
    if cmd == "rsi":
        rsi = svc.assessment.rsi(today=today)
        labelled = sum(len(v) for v in rsi.products.values())
        print(
            f"reference safety information {rsi.version} (repo-built"
            f" stand-in, not a real label): {labelled} labelled terms"
            f" over {len(rsi.products)} products"
        )
        return 0
    if cmd == "follow-up-board" and len(argv) <= 2:
        from datetime import date as _date

        as_of = _date.fromisoformat(argv[1]) if len(argv) == 2 else today
        board = svc.followup.board(as_of)
        if not board:
            print(f"follow-up board at {as_of}: empty")
        for entry in board:
            case = f" case {entry.case_id}" if entry.case_id else ""
            overdue = (
                f" ({entry.days_overdue}d overdue)" if entry.days_overdue else ""
            )
            print(
                f"{entry.task_id}: [{entry.source}]{case}"
                f" — {entry.state}, cycle {entry.cycle},"
                f" due {entry.due_on}{overdue}"
                f" — missing: {', '.join(entry.missing_fields)}"
            )
        return 0
    if cmd == "follow-up-tasks" and len(argv) == 2:
        if svc.store.get_case(argv[1]) is None:
            print(f"no such case: {argv[1]}")
            return 1
        views = svc.followup.tasks_for_case(argv[1], today)
        if not views:
            print(f"{argv[1]}: no follow-up requests")
        for view in views:
            fields = ", ".join(i.field for i in view.task.items)
            print(
                f"{view.task.task_id}: [{view.task.source}] {view.state},"
                f" cycle {view.cycle}, due {view.due_on} — missing: {fields}"
            )
        return 0
    if cmd == "follow-up-task" and len(argv) == 2:
        try:
            view = svc.followup.view(argv[1], today)
        except KeyError:
            print(f"no such follow-up request: {argv[1]}")
            return 1
        print(json.dumps(view.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "follow-up-pack":
        from pv_cases.followup import FOLLOW_UP_PACK

        days = ", ".join(
            f"{k} {v}d" for k, v in FOLLOW_UP_PACK["days_by_class"].items()
        )
        print(
            f"follow-up pack {FOLLOW_UP_PACK['version']}: {days};"
            f" max cycles {FOLLOW_UP_PACK['max_cycles']}"
        )
        return 0
    if cmd == "submit-follow-up" and len(argv) == 3:
        try:
            report = IntakeReport.model_validate(json.loads(argv[2]))
            submission = svc.followup.submit_follow_up(argv[1], report, today)
        except KeyError:
            print(f"no such follow-up request: {argv[1]}")
            return 1
        except (ValueError, FollowUpError) as exc:
            print(f"follow-up refused: {exc}")
            return 1
        founded = (
            f" — founded case {submission.new_case_id}"
            if submission.new_case_id
            else ""
        )
        print(
            f"{submission.task_id}: {submission.outcome}"
            f" by {submission.follow_up_report_id} — state"
            f" {submission.state}, cycle {submission.cycle}{founded}"
        )
        return 0
    if cmd == "review" and len(argv) >= 5:
        rationale = " ".join(argv[5:])
        try:
            act = svc.review.record_review(
                argv[1], argv[2], argv[3], argv[4], rationale, today
            )
        except KeyError:
            print(f"no such case: {argv[1]}")
            return 1
        except ReviewError as exc:
            print(f"review refused: {exc}")
            return 1
        view = svc.review.review_for_case(argv[1])
        print(
            f"{act.act_id}: {act.decision} by {act.reviewer}"
            f" [{act.role}] — case state {view.state}"
        )
        return 0
    if cmd == "review-case" and len(argv) == 2:
        try:
            view = svc.review.review_for_case(argv[1])
        except KeyError:
            print(f"no such case: {argv[1]}")
            return 1
        print(json.dumps(view.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "review-queue" and len(argv) <= 2:
        from datetime import date as _date

        as_of = _date.fromisoformat(argv[1]) if len(argv) == 2 else today
        queue = svc.review.queue(as_of)
        if not queue:
            print(f"review queue at {as_of}: empty")
        for entry in queue:
            seriousness = entry.seriousness or "unassessed"
            clock = entry.obligation_class or "no clock"
            deadline = (
                f", deadline {entry.deadline}" if entry.deadline else ""
            )
            stale = " [stale acts on trail]" if entry.has_stale_acts else ""
            print(
                f"{entry.case_id}: [{entry.bucket}] {seriousness},"
                f" {clock}{deadline} — {entry.review_state}{stale}"
            )
        return 0
    if cmd == "review-pack":
        from pv_cases.review import REVIEW_PACK

        print(
            f"review pack {REVIEW_PACK['version']}: roles"
            f" {', '.join(REVIEW_PACK['roles'])}; decisions"
            f" {', '.join(REVIEW_PACK['decisions'])}"
        )
        return 0
    if cmd == "seal" and len(argv) >= 3:
        # Round 7: the named seal act over a medically reviewed
        # case; a refusal names its blocker and exits 1.
        from pv_cases.sealing import SealError

        try:
            seal = svc.sealing.seal_case(argv[1], " ".join(argv[2:]), today)
        except KeyError:
            print(f"no such case: {argv[1]}")
            return 1
        except SealError as exc:
            print(f"seal refused: {exc}")
            return 1
        print(
            f"{seal.seal_id}: case {seal.case_id} sealed by {seal.sealed_by}"
            f" — {len(seal.inventory)} artefacts, chain head"
            f" {seal.chain_head_hash[:16]}…"
        )
        return 0
    if cmd == "seal-case" and len(argv) == 2:
        if svc.store.get_case(argv[1]) is None:
            print(f"no such case: {argv[1]}")
            return 1
        seal = svc.sealing.get_seal(argv[1])
        if seal is None:
            print(f"{argv[1]}: not sealed")
            return 1
        print(json.dumps(seal.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "export-case" and len(argv) >= 3:
        # The export serving: the record line first, then the
        # package JSON — the exact bytes the record's hash names.
        from pv_cases.sealing import SealError

        try:
            record, package = svc.sealing.export_case(
                argv[1], " ".join(argv[2:]), today
            )
        except KeyError:
            print(f"no such case: {argv[1]}")
            return 1
        except SealError as exc:
            print(f"export refused: {exc}")
            return 1
        print(f"export {record.export_id} hash {record.package_hash}")
        print(svc.sealing.package_bytes(package).decode("utf-8"))
        return 0
    if cmd == "verify-export" and len(argv) == 2:
        # The offline verifier over a package file — the package
        # is all it reads; no store is consulted.
        from pv_cases.verify import main as verify_main

        return verify_main([argv[1]])
    if cmd == "sealing-pack":
        from pv_cases.sealing import SEALING_PACK

        print(
            f"sealing pack {SEALING_PACK['version']}: package format"
            f" {SEALING_PACK['package_format']} —"
            f" {SEALING_PACK['gate_rule']}"
        )
        return 0
    if cmd == "quarantine" and len(argv) == 2:
        # Round 8: the case's screened fields — held or released,
        # with the findings that hold each one.
        if svc.store.get_case(argv[1]) is None:
            print(f"no such case: {argv[1]}")
            return 1
        views = svc.quarantine.views_for_case(argv[1])
        if not views:
            print(f"{argv[1]}: no screened fields")
        for view in views:
            rules = ", ".join(f.rule_id for f in view.record.findings)
            state = (
                f"released by {view.released_by}"
                if view.released
                else "HELD"
            )
            print(
                f"{view.record.record_id}: {view.record.field}"
                f" [{rules}] — {state}"
            )
        return 0
    if cmd == "release-quarantine" and len(argv) >= 4:
        from pv_cases.quarantine import QuarantineError

        try:
            act = svc.quarantine.release(
                argv[1], argv[2], " ".join(argv[3:]), today
            )
        except KeyError:
            print(f"no such quarantine record: {argv[1]}")
            return 1
        except QuarantineError as exc:
            print(f"release refused: {exc}")
            return 1
        print(
            f"{act.act_id}: record {act.record_id} released by"
            f" {act.released_by}"
        )
        return 0
    if cmd == "quarantine-pack":
        from pv_cases.quarantine import QUARANTINE_PACK

        rules = ", ".join(r["rule_id"] for r in QUARANTINE_PACK["rules"])
        print(
            f"quarantine pack {QUARANTINE_PACK['version']}:"
            f" fields {', '.join(QUARANTINE_PACK['fields'])};"
            f" rules {rules}"
        )
        return 0
    if cmd == "memory-recall" and len(argv) == 4:
        from pv_cases.memory import MemoryError

        try:
            retrieval = svc.memory.recall_for_case(
                argv[1], argv[2], argv[3], today
            )
        except KeyError:
            print(f"no such case: {argv[1]}")
            return 1
        except MemoryError as exc:
            print(f"recall refused: {exc}")
            return 1
        print(
            f"{retrieval.retrieval_id}: {retrieval.strategy} recall"
            f" for {retrieval.case_id} — {len(retrieval.matches)}"
            f" match(es), expires {retrieval.expires_on}"
        )
        for match in retrieval.matches:
            term = f" [{match.coded_term}]" if match.coded_term else ""
            print(f"  {match.case_id} ({match.basis}){term}")
        return 0
    if cmd == "memory-retrievals" and len(argv) == 2:
        if svc.store.get_case(argv[1]) is None:
            print(f"no such case: {argv[1]}")
            return 1
        retrievals = svc.memory.active_retrievals(today, argv[1])
        if not retrievals:
            print(f"{argv[1]}: no active retrievals")
        for r in retrievals:
            print(
                f"{r.retrieval_id}: {r.strategy} by {r.requested_by}"
                f" — {len(r.matches)} match(es), expires {r.expires_on}"
            )
        return 0
    if cmd == "memory-retention":
        view = svc.memory.retention_view(today)
        print(
            f"memory retention at {view['as_of']}: {view['active']}"
            f" active, {view['expired_tombstones']} expired"
            f" tombstone(s) (pack {view['pack_version']},"
            f" {view['retention_days']} days)"
        )
        return 0
    if cmd == "memory-precedents" and len(argv) >= 3:
        from pv_cases.memory import MemoryError

        try:
            retrieval = svc.memory.recall_coding_precedents(
                " ".join(argv[2:]), argv[1], today
            )
        except MemoryError as exc:
            print(f"recall refused: {exc}")
            return 1
        print(
            f"{retrieval.retrieval_id}: coding precedent recall —"
            f" {len(retrieval.matches)} case(s) decided this term"
        )
        for match in retrieval.matches:
            print(f"  {match.case_id}: {match.coded_term}")
        return 0
    if cmd == "memory-pack":
        from pv_cases.memory import MEMORY_PACK

        print(
            f"memory pack {MEMORY_PACK['version']}: strategies"
            f" {', '.join(MEMORY_PACK['strategies'])};"
            f" retention {MEMORY_PACK['retention_days']} days"
        )
        return 0
    if cmd == "gateway-invoke" and len(argv) >= 4:
        # Round 8: one call through the gateway. The envelope is
        # printed either way — on a denial it carries the -32002
        # error, which is the platform's shape, not a CLI failure.
        principal, role, tool = argv[1], argv[2], argv[3]
        rest = argv[4:]
        if tool == "dictionary_lookup":
            envelope = svc.gateway.invoke(
                principal, role, tool, today, verbatim=" ".join(rest)
            )
        else:
            case_id = rest[0] if rest else None
            envelope = svc.gateway.invoke(
                principal, role, tool, today, case_id=case_id
            )
        print(json.dumps(envelope, indent=2))
        return 0
    if cmd == "gateway-calls":
        calls = svc.gateway.calls()
        if not calls:
            print("gateway calls: none recorded")
        for call in calls:
            print(
                f"{call.call_id}: {call.tool} by {call.principal}"
                f" [{call.role}] — {call.decision} ({call.rule_id})"
            )
        return 0
    if cmd == "gateway-pack":
        from pv_cases.gateway import POLICY_PACK

        tools = ", ".join(
            f"{t} ({phi})" for t, phi in POLICY_PACK["tools"].items()
        )
        print(
            f"policy pack {POLICY_PACK['version']}: roles"
            f" {', '.join(POLICY_PACK['roles'])}; tools {tools};"
            f" {POLICY_PACK['evaluation']}; denials are"
            f" {POLICY_PACK['denial_shape']}"
        )
        return 0
    if cmd == "oversight-pack":
        from pv_cases.oversight import load_oversight_pack

        print(json.dumps(load_oversight_pack().model_dump(mode="json"), indent=2))
        return 0
    if cmd == "oversight-generate" and len(argv) >= 5:
        from pv_cases.access import AccessDeniedError
        from pv_cases.oversight import OversightError

        try:
            report = svc.oversight.generate(
                date.fromisoformat(argv[1]),
                date.fromisoformat(argv[2]),
                date.fromisoformat(argv[3]),
                " ".join(argv[4:]),
                today=today,
            )
        except (ValueError, OversightError) as exc:
            print(f"generation refused: {exc}")
            return 1
        except AccessDeniedError as exc:
            print(f"generation denied: {exc}")
            return 1
        print(
            f"{report.report_id}: window {report.window_start}.."
            f"{report.window_end} as of {report.as_of}, generated"
            f" by {report.generated_by} — verified:"
            f" {report.verified}"
            + (f", supersedes {report.supersedes}" if report.supersedes else "")
        )
        for problem in report.verification_problems:
            print(f"  - {problem}")
        return 0
    if cmd == "oversight-reports":
        reports = svc.oversight.reports()
        if not reports:
            print("oversight reports: none")
        for report in reports:
            print(
                f"{report.report_id}: {report.window_start}.."
                f"{report.window_end} as of {report.as_of} by"
                f" {report.generated_by} — verified: {report.verified}"
            )
        return 0
    if cmd == "oversight-report" and len(argv) == 2:
        report = svc.oversight.get(argv[1])
        if report is None:
            print(f"no such report: {argv[1]}")
            return 1
        print(json.dumps(report.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "oversight-verify" and len(argv) == 2:
        report = svc.oversight.get(argv[1])
        if report is None:
            print(f"no such report: {argv[1]}")
            return 1
        problems = svc.oversight.verify(report)
        if problems:
            print(f"{report.report_id}: DOES NOT RE-PERFORM")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"{report.report_id}: re-performs exactly")
        return 0
    if cmd == "model-pack":
        from pv_cases.model_gateway import MODEL_PACK, model_pack_fingerprint

        pack = dict(MODEL_PACK)
        pack["fingerprint"] = model_pack_fingerprint()
        print(json.dumps(pack, indent=2))
        return 0
    if cmd == "model-ledger" and len(argv) == 2:
        if svc.store.get_case(argv[1]) is None:
            print(f"no such case: {argv[1]}")
            return 1
        view = svc.model_gateway.ledger_view(argv[1])
        print(
            f"{argv[1]}: {view['calls_completed']} completed,"
            f" {view['calls_refused_budget']} budget-refused,"
            f" {view['calls_refused_phi']} phi-refused,"
            f" {view['calls_errored']} errored —"
            f" {view['input_tokens']} in / {view['output_tokens']}"
            f" out tokens, {view['cost_micro']} micro-USD"
            " (pack-stated measurement, never provider-billed)"
        )
        for record in view["records"]:
            print(
                f"  {record['call_id']}: {record['status']}"
                f" [{record['profile']}]"
                + (f" — {record['detail']}" if record["detail"] else "")
            )
        return 0
    if cmd == "model-ledger-verify":
        from pv_cases.model_gateway import verify_ledger

        problems = verify_ledger(svc.store)
        if problems:
            print("model ledger DOES NOT RECONCILE:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        n = svc.store.count_model_calls()
        print(f"model ledger reconciles: {n} record(s) re-derived clean")
        return 0
    if cmd == "signals-pack":
        from pv_cases.signals import load_signal_pack

        print(json.dumps(load_signal_pack().model_dump(mode="json"), indent=2))
        return 0
    if cmd == "signals-generate" and len(argv) >= 5:
        from pv_cases.access import AccessDeniedError
        from pv_cases.signals import SignalError

        try:
            table = svc.signals.generate(
                date.fromisoformat(argv[1]),
                date.fromisoformat(argv[2]),
                date.fromisoformat(argv[3]),
                " ".join(argv[4:]),
                today=today,
            )
        except (ValueError, SignalError) as exc:
            print(f"generation refused: {exc}")
            return 1
        except AccessDeniedError as exc:
            print(f"generation denied: {exc}")
            return 1
        flagged = [p for p in table.pairs if p.meets_criteria]
        print(
            f"{table.table_id}: window {table.window_start}.."
            f"{table.window_end} as of {table.as_of}, generated"
            f" by {table.generated_by} — verified:"
            f" {table.verified},"
            f" {table.totals['pairs_counted']['value']} pairs,"
            f" {len(flagged)} flagged"
            + (f", supersedes {table.supersedes}" if table.supersedes else "")
        )
        for pair in flagged:
            print(
                f"  flagged: {pair.product_name} + {pair.preferred_term}"
                f" — a={pair.a} b={pair.b} c={pair.c} d={pair.d},"
                f" PRR {pair.prr}, chi-square {pair.chi_square}"
            )
        for problem in table.verification_problems:
            print(f"  - {problem}")
        return 0
    if cmd == "signals-tables":
        tables = svc.signals.tables()
        if not tables:
            print("signal tables: none")
        for table in tables:
            print(
                f"{table.table_id}: {table.window_start}.."
                f"{table.window_end} as of {table.as_of} by"
                f" {table.generated_by} — verified: {table.verified},"
                f" {table.totals['pairs_flagged']['value']} flagged"
            )
        return 0
    if cmd == "signals-table" and len(argv) == 2:
        table = svc.signals.get(argv[1])
        if table is None:
            print(f"no such table: {argv[1]}")
            return 1
        print(json.dumps(table.model_dump(mode="json"), indent=2))
        return 0
    if cmd == "signals-verify" and len(argv) == 2:
        table = svc.signals.get(argv[1])
        if table is None:
            print(f"no such table: {argv[1]}")
            return 1
        problems = svc.signals.verify(table)
        if problems:
            print(f"{table.table_id}: DOES NOT RE-PERFORM")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"{table.table_id}: re-performs exactly")
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
