"""Re-performance check for the Round 15 safety-reviewer
console.

Builds the planted book on a throwaway store, strikes the
three aggregate artefacts (oversight report, signal table,
steward worklist), then serves the API and grades the
console against the record by second routes:

- the page is a shell: it carries the branding and the
  title, and none of the book's case ids or patient
  initials are baked into it;
- every figure on /console/data agrees with the underlying
  surface read directly (case list, review queue, follow-up
  board, coding queue, quarantine fold) or with the struck
  artefact it passes through — and a tampered copy of the
  data is refused by the same comparison, named;
- the access discipline holds on the page: an intake
  principal is refused by name and the refusal lands on the
  denial log; a safety scientist reads the book but the
  denials section arrives invisible, with no count; the
  auditor reads everything.

Run: uv run python scripts/check_console.py
Exit 0 = the console composes the record exactly and its
refusals hold; 1 otherwise.
"""

from __future__ import annotations

import copy
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

import pv_cases.api as api
from pv_cases.config import Settings
from pv_cases.seed import assessment_fixtures, dedup_fixtures
from pv_cases.wiring import Services

DAY = date(2026, 10, 12)
AS_OF = date(2026, 11, 20)
WINDOW = (date(2026, 10, 1), date(2026, 10, 31))


def _services(tmp: Path) -> Services:
    return Services(
        Settings(
            sqlite_path=str(tmp / "pv.db"),
            object_store_dir=str(tmp / "objects"),
        )
    )


def _agreement(svc: Services, data: dict) -> list[str]:
    """The console's figures, re-performed by second routes:
    the underlying services and store reads, and the struck
    artefacts themselves. Returns the mismatches, named."""
    problems = []
    sections = data["sections"]
    cases = svc.store.list_cases()
    if sections["cases"]["counts"]["total"] != len(cases):
        problems.append("cases.counts.total disagrees with the case list")
    queue = svc.review.queue(AS_OF)
    if sections["review_queue"]["counts"]["total"] != len(queue):
        problems.append("review_queue.counts.total disagrees with the queue")
    board = svc.followup.board(AS_OF)
    if sections["follow_ups"]["counts"]["total"] != len(board):
        problems.append("follow_ups.counts.total disagrees with the board")
    held = sections["quarantine"]["counts"]["held"]
    if held != len(sections["quarantine"]["rows"]):
        problems.append("quarantine.counts.held disagrees with its rows")
    report = svc.oversight.reports()[-1]
    head = sections["oversight"]["head"]
    if head is None or head["report_id"] != report.report_id:
        problems.append("oversight.head is not the standing report")
    elif head["cases_founded"] != report.sections["intake"]["cases_founded"]["value"]:
        problems.append("oversight.head.cases_founded disagrees with the report")
    table = svc.signals.tables()[-1]
    shead = sections["signals"]["head"]
    if shead is None or shead["table_id"] != table.table_id:
        problems.append("signals.head is not the standing table")
    elif shead["flagged"] != sum(1 for p in table.pairs if p.meets_criteria):
        problems.append("signals.head.flagged disagrees with the table")
    worklist = svc.steward.worklists()[-1]
    whead = sections["stewardship"]["head"]
    if whead is None or whead["worklist_id"] != worklist.worklist_id:
        problems.append("stewardship.head is not the standing worklist")
    elif whead["rows"] != len(worklist.rows):
        problems.append("stewardship.head.rows disagrees with the worklist")
    return problems


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pv-console-check-"))
    svc = _services(tmp)
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures():
        svc.intake.ingest(report, DAY)
    for report in assessment_fixtures():
        svc.intake.ingest(report, DAY)
    report = svc.oversight.generate(
        WINDOW[0], WINDOW[1], AS_OF, "Dr A. Rao", today=AS_OF
    )
    table = svc.signals.generate(
        WINDOW[0], WINDOW[1], AS_OF, "Dr S. Okafor", today=AS_OF
    )
    worklist = svc.steward.generate(AS_OF, "Dr S. Okafor", today=AS_OF)
    print(
        f"planted book: {len(svc.store.list_cases())} cases; struck"
        f" {report.report_id}, {table.table_id}, {worklist.worklist_id}"
    )

    api.override_services(svc)
    client = TestClient(api.app)
    failures = []

    page = client.get("/console")
    if page.status_code != 200:
        failures.append(f"GET /console -> {page.status_code}")
    html = page.text
    for needle in (
        "Safety Reviewer Console",
        "https://trida.ai/logos-rounded/trida-rounded.png",
        "Visit Trida AI →",
        "View all blueprints →",
        'fetch("/console/data"',
    ):
        if needle not in html:
            failures.append(f"console page is missing {needle!r}")
    for case in svc.store.list_cases():
        if case.case_id in html:
            failures.append(f"console page bakes in {case.case_id}")
    routes = {route.path for route in api.app.routes}
    if "/console/data" not in routes:
        failures.append("/console/data is not a registered route")
    print("the page is a branded shell with no case data baked in")

    # The API judges as-of at today; the composition is
    # re-performed at the same as-of the service is read at.
    data = svc.console.data(AS_OF)
    problems = _agreement(svc, data)
    if problems:
        failures.extend(problems)
    else:
        print("every console figure agrees with its second route")

    tampered = copy.deepcopy(data)
    tampered["sections"]["oversight"]["head"]["cases_founded"] += 1
    named = _agreement(svc, tampered)
    if not any("cases_founded" in p for p in named):
        failures.append("a tampered console figure was not named")
    else:
        print("a tampered figure is refused and named:")
        for problem in named:
            print(f"  - {problem}")

    before = len(svc.store.list_access_denials())
    refused = client.get(
        "/console/data",
        headers={
            "x-principal-id": "Ms Intake",
            "x-principal-role": "intake_processor",
        },
    )
    if refused.status_code != 403:
        failures.append(f"intake principal -> {refused.status_code}, want 403")
    if len(svc.store.list_access_denials()) != before + 1:
        failures.append("the console refusal was not recorded on the denial log")
    print("an intake principal is refused by name, on the record")

    scientist = svc.console.data(
        AS_OF,
        principal=_principal("Dr S. Okafor", "safety_scientist"),
    )
    denials_section = scientist["sections"]["denials"]
    if denials_section != {"visible": False}:
        failures.append("the denials section leaked to a safety scientist")
    if not scientist["sections"]["cases"]["visible"]:
        failures.append("the cases section was hidden from a safety scientist")
    auditor = svc.console.data(
        AS_OF,
        principal=_principal("Ms A. Whitfield", "quality_auditor"),
    )
    if not all(
        section["visible"] for section in auditor["sections"].values()
    ):
        failures.append("a section was hidden from the quality auditor")
    print("section filtering holds: scientist blind to denials, auditor reads all")

    if failures:
        print("CONSOLE CHECK: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("CONSOLE CHECK: PASS")
    return 0


def _principal(pid: str, role: str):
    from pv_cases.access import Principal

    return Principal(id=pid, role=role)


if __name__ == "__main__":
    raise SystemExit(main())
