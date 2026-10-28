"""The SQL store on its SQLite dialect: migrations apply once and
are idempotent, records round-trip whole."""

import sqlite3

from pv_cases.migrations import apply_migrations, migration_files
from pv_cases.store import SqlCaseStore
from tests.conftest import DAY


def test_migrations_idempotent(tmp_path):
    path = str(tmp_path / "m.db")
    conn = sqlite3.connect(path)
    first = apply_migrations(conn, "sqlite")
    second = apply_migrations(conn, "sqlite")
    conn.close()
    assert first == [p.name for p in migration_files("sqlite")]
    assert second == []


def test_store_round_trip(tmp_path):
    store = SqlCaseStore("sqlite", sqlite_path=str(tmp_path / "pv.db"))
    from pv_cases.object_store import LocalObjectStore
    from pv_cases.services import IntakeService
    from pv_cases.seed import reference_feed

    svc = IntakeService(store, LocalObjectStore(tmp_path / "objects"))
    svc.ingest_feed(reference_feed(), DAY)
    reopened = SqlCaseStore("sqlite", sqlite_path=str(tmp_path / "pv.db"))
    cases = reopened.list_cases()
    assert len(cases) == 4
    assert len(reopened.list_follow_ups()) == 2
    case = reopened.get_case("CASE-RPT-2026-0006")
    assert case.suspected_duplicate_of == "CASE-RPT-2026-0005"
    evidence = reopened.list_evidence("CASE-RPT-2026-0001")
    assert len(evidence) == 1 and evidence[0].kind == "intake_report"


def test_dialect_rejects_unknown(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        SqlCaseStore("carrier-pigeon", sqlite_path=str(tmp_path / "x.db"))
