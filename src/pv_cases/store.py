"""The case store port and its SQL implementations.

One port, two production dialects: SQLite is the local default
(``./data/pv.db``), PostgreSQL is the production store on AWS (RDS /
Aurora PostgreSQL, pgvector available in the compose profile for the
later Memory-adjacent workloads). Payloads are stored as JSON text
in both dialects — one contract, no dialect drift. An in-memory
implementation backs the eval harness.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from pv_cases.migrations import apply_migrations
from pv_cases.models import Case, CaseSummary, EvidenceItem, FollowUpTask


class CaseStore(Protocol):
    def add_case(self, case: Case) -> None: ...
    def get_case(self, case_id: str) -> Case | None: ...
    def list_cases(self) -> list[Case]: ...
    def find_case_by_dedup_key(self, dedup_key: str) -> Case | None: ...
    def update_case(self, case: Case) -> None: ...
    def add_follow_up(self, task: FollowUpTask) -> None: ...
    def list_follow_ups(self) -> list[FollowUpTask]: ...
    def add_evidence(self, item: EvidenceItem) -> None: ...
    def list_evidence(self, case_id: str) -> list[EvidenceItem]: ...
    def add_summary(self, summary: CaseSummary) -> None: ...
    def get_summary(self, summary_id: str) -> CaseSummary | None: ...
    def list_summaries(self, case_id: str) -> list[CaseSummary]: ...


def _case_params(case: Case) -> tuple:
    return (
        case.case_id,
        case.report_id,
        case.source.value,
        case.received_on.isoformat(),
        case.awareness_date.isoformat(),
        case.product_code,
        case.status.value,
        case.dedup_key,
        case.suspected_duplicate_of,
    )


_CASE_COLS = (
    "case_id, report_id, source, received_on, awareness_date, product_code,"
    " status, dedup_key, suspected_duplicate_of"
)


class SqlCaseStore:
    """SQLite and PostgreSQL behind one implementation.

    ``dialect`` is ``"sqlite"`` or ``"postgres"``. For PostgreSQL pass
    a psycopg connection factory via ``pg_conn`` — connections use
    ``?``-style placeholders translated here, and rows are read by
    position in a fixed column order (the JSON-text contract every
    dialect shares).
    """

    def __init__(self, dialect: str, sqlite_path: str | None = None, pg_conn=None):
        self.dialect = dialect
        self._pg_conn = pg_conn
        self._sqlite_path = sqlite_path
        if dialect == "sqlite":
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(sqlite_path)
            try:
                apply_migrations(conn, "sqlite")
            finally:
                conn.close()
        elif dialect == "postgres":
            conn = self._connect()
            try:
                apply_migrations(conn, "postgres")
            finally:
                conn.close()
        else:
            raise ValueError(f"unknown dialect: {dialect}")

    def _connect(self):
        if self.dialect == "sqlite":
            return sqlite3.connect(self._sqlite_path)
        return self._pg_conn()

    def _run(self, sql: str, params: tuple = (), fetch: str | None = None):
        conn = self._connect()
        try:
            cur = conn.execute(sql, params) if self.dialect == "sqlite" else self._pg_execute(conn, sql, params)
            result = None
            if fetch == "one":
                row = cur.fetchone()
                result = tuple(row) if row is not None else None
            elif fetch == "all":
                result = [tuple(r) for r in cur.fetchall()]
            conn.commit()
            return result
        finally:
            conn.close()

    def _pg_execute(self, conn, sql: str, params: tuple):
        cur = conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    # -- cases ------------------------------------------------------
    def add_case(self, case: Case) -> None:
        self._run(
            f"INSERT INTO cases ({_CASE_COLS}) VALUES (?,?,?,?,?,?,?,?,?)",
            _case_params(case),
        )

    def get_case(self, case_id: str) -> Case | None:
        row = self._run(f"SELECT {_CASE_COLS} FROM cases WHERE case_id = ?", (case_id,), fetch="one")
        return self._row_to_case(row) if row else None

    def list_cases(self) -> list[Case]:
        rows = self._run(f"SELECT {_CASE_COLS} FROM cases ORDER BY case_id", fetch="all")
        return [self._row_to_case(r) for r in rows]

    def find_case_by_dedup_key(self, dedup_key: str) -> Case | None:
        row = self._run(
            f"SELECT {_CASE_COLS} FROM cases WHERE dedup_key = ? ORDER BY case_id LIMIT 1",
            (dedup_key,),
            fetch="one",
        )
        return self._row_to_case(row) if row else None

    def update_case(self, case: Case) -> None:
        self._run(
            "UPDATE cases SET status = ?, suspected_duplicate_of = ? WHERE case_id = ?",
            (case.status.value, case.suspected_duplicate_of, case.case_id),
        )

    @staticmethod
    def _row_to_case(row) -> Case:
        from datetime import date

        return Case(
            case_id=row[0],
            report_id=row[1],
            source=row[2],
            received_on=date.fromisoformat(row[3]),
            awareness_date=date.fromisoformat(row[4]),
            product_code=row[5],
            status=row[6],
            dedup_key=row[7],
            suspected_duplicate_of=row[8],
        )

    # -- follow-up tasks ---------------------------------------------
    def add_follow_up(self, task: FollowUpTask) -> None:
        self._run(
            "INSERT INTO follow_up_tasks (task_id, report_id, missing_criteria, status, created_on)"
            " VALUES (?,?,?,?,?)",
            (
                task.task_id,
                task.report_id,
                json.dumps(task.missing_criteria),
                task.status.value,
                task.created_on.isoformat(),
            ),
        )

    def list_follow_ups(self) -> list[FollowUpTask]:
        from datetime import date

        rows = self._run(
            "SELECT task_id, report_id, missing_criteria, status, created_on"
            " FROM follow_up_tasks ORDER BY task_id",
            fetch="all",
        )
        return [
            FollowUpTask(
                task_id=r[0],
                report_id=r[1],
                missing_criteria=json.loads(r[2]),
                status=r[3],
                created_on=date.fromisoformat(r[4]),
            )
            for r in rows
        ]

    # -- evidence -----------------------------------------------------
    def add_evidence(self, item: EvidenceItem) -> None:
        self._run(
            "INSERT INTO evidence_items (item_id, case_id, kind, object_key,"
            " payload_sha256, prev_hash, item_hash, recorded_on)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                item.item_id,
                item.case_id,
                item.kind,
                item.object_key,
                item.payload_sha256,
                item.prev_hash,
                item.item_hash,
                item.recorded_on.isoformat(),
            ),
        )

    def list_evidence(self, case_id: str) -> list[EvidenceItem]:
        from datetime import date

        rows = self._run(
            "SELECT item_id, case_id, kind, object_key, payload_sha256,"
            " prev_hash, item_hash, recorded_on FROM evidence_items"
            " WHERE case_id = ? ORDER BY rowid" if self.dialect == "sqlite" else
            "SELECT item_id, case_id, kind, object_key, payload_sha256,"
            " prev_hash, item_hash, recorded_on FROM evidence_items"
            " WHERE case_id = ? ORDER BY seq",
            (case_id,),
            fetch="all",
        )
        return [
            EvidenceItem(
                item_id=r[0],
                case_id=r[1],
                kind=r[2],
                object_key=r[3],
                payload_sha256=r[4],
                prev_hash=r[5],
                item_hash=r[6],
                recorded_on=date.fromisoformat(r[7]),
            )
            for r in rows
        ]

    # -- summaries ----------------------------------------------------
    def add_summary(self, summary: CaseSummary) -> None:
        self._run(
            "INSERT INTO case_summaries (summary_id, case_id, model_profile, payload)"
            " VALUES (?,?,?,?)",
            (
                summary.summary_id,
                summary.case_id,
                summary.model_profile,
                summary.model_dump_json(),
            ),
        )

    def get_summary(self, summary_id: str) -> CaseSummary | None:
        row = self._run(
            "SELECT payload FROM case_summaries WHERE summary_id = ?", (summary_id,), fetch="one"
        )
        return CaseSummary.model_validate_json(row[0]) if row else None

    def list_summaries(self, case_id: str) -> list[CaseSummary]:
        rows = self._run(
            "SELECT payload FROM case_summaries WHERE case_id = ? ORDER BY summary_id",
            (case_id,),
            fetch="all",
        )
        return [CaseSummary.model_validate_json(r[0]) for r in rows]


class InMemoryCaseStore:
    """The eval/test double implementing the same port."""

    def __init__(self):
        self.cases: dict[str, Case] = {}
        self.follow_ups: dict[str, FollowUpTask] = {}
        self.evidence: dict[str, list[EvidenceItem]] = {}
        self.summaries: dict[str, CaseSummary] = {}

    def add_case(self, case: Case) -> None:
        self.cases[case.case_id] = case

    def get_case(self, case_id: str) -> Case | None:
        return self.cases.get(case_id)

    def list_cases(self) -> list[Case]:
        return [self.cases[k] for k in sorted(self.cases)]

    def find_case_by_dedup_key(self, dedup_key: str) -> Case | None:
        for case in self.list_cases():
            if case.dedup_key == dedup_key:
                return case
        return None

    def update_case(self, case: Case) -> None:
        self.cases[case.case_id] = case

    def add_follow_up(self, task: FollowUpTask) -> None:
        self.follow_ups[task.task_id] = task

    def list_follow_ups(self) -> list[FollowUpTask]:
        return [self.follow_ups[k] for k in sorted(self.follow_ups)]

    def add_evidence(self, item: EvidenceItem) -> None:
        self.evidence.setdefault(item.case_id, []).append(item)

    def list_evidence(self, case_id: str) -> list[EvidenceItem]:
        return list(self.evidence.get(case_id, []))

    def add_summary(self, summary: CaseSummary) -> None:
        self.summaries[summary.summary_id] = summary

    def get_summary(self, summary_id: str) -> CaseSummary | None:
        return self.summaries.get(summary_id)

    def list_summaries(self, case_id: str) -> list[CaseSummary]:
        return [s for s in self.summaries.values() if s.case_id == case_id]
