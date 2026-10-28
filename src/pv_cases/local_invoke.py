"""Local harness: invoke the agent in-process, no HTTP, no AWS.

``pv-agent-invoke`` ingests the reference feed into a throwaway
SQLite store (or the configured one) and runs the Runtime
invocation handler for one planted case — the offline development
loop for the exact handler AgentCore Runtime calls in production.
"""

from __future__ import annotations

import json
import sys
from datetime import date

from pv_cases.runtime_app import handle_invocation
from pv_cases.wiring import build_services
import pv_cases.runtime_app as runtime_app


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    case_id = argv[0] if argv else "CASE-RPT-2026-0001"
    svc = build_services()
    runtime_app._services = svc  # the harness drives the same graph
    svc.intake.ingest_feed(svc.feed.fetch_reports(), date(2026, 10, 12))
    result = handle_invocation({"action": "assemble_summary", "case_id": case_id})
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "assembled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
