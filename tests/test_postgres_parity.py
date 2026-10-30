"""Dialect parity (Round 16): the core walk's figure sheet.

The SQLite half runs always — it pins the walk itself: the
export verifies, every evidence chain is valid, the seal
stands over a non-empty inventory, and the sheet is plain
JSON that survives its canonical serialization.

The Postgres half runs when PV_TEST_DATABASE_URL names a
UTF-8 database the suite may use exclusively (the walk runs
in its own schema, dropped afterwards): the same walk on
the production store must produce a figure sheet that agrees
with the SQLite sheet field for field. Any dialect defect —
a placeholder, an ordering, a type the row mappers do not
normalize — lands here as a named divergence, on both
profiles, instead of in production.
"""

import os

import pytest

from pv_cases.config import Settings
from pv_cases.parity import compare_sheets, run_core_walk, sheet_bytes
from pv_cases.wiring import Services

SCHEMA = "pv_parity_test"


def _sqlite_walk(tmp_path) -> dict:
    return run_core_walk(
        Services(
            Settings(
                sqlite_path=str(tmp_path / "pv.db"),
                object_store_dir=str(tmp_path / "objects"),
            )
        )
    )


def test_figure_sheet_invariants_on_sqlite(tmp_path):
    sheet = _sqlite_walk(tmp_path)
    assert sheet["export"]["verdict"] == "pass"
    assert all(c["passed"] for c in sheet["export"]["checks"])
    assert sheet["seal"]["inventory"], "the seal binds no artefacts"
    assert len(sheet["cases"]) >= 20
    for case_id, entry in sheet["cases"].items():
        assert entry["chain_valid"], f"chain invalid for {case_id}"
    # The sheet is the contract: canonical bytes, and a sheet
    # compared with itself has no divergences.
    assert sheet_bytes(sheet)
    assert compare_sheets(sheet, sheet) == []


@pytest.mark.skipif(
    not os.environ.get("PV_TEST_DATABASE_URL"),
    reason="PV_TEST_DATABASE_URL not set — the Postgres parity"
    " leg runs where a UTF-8 database is provided",
)
def test_postgres_parity(tmp_path):
    import psycopg

    url = os.environ["PV_TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        encoding = conn.execute("SHOW server_encoding").fetchone()[0]
        assert encoding == "UTF8", (
            f"supported profile is a UTF-8 database, got {encoding}"
        )
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
    try:
        pg = run_core_walk(
            Services(
                Settings(
                    database_url=url
                    + "?options=-c%20search_path%3D"
                    + SCHEMA,
                    object_store_dir=str(tmp_path / "objects-pg"),
                )
            )
        )
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    lite = _sqlite_walk(tmp_path / "lite")
    divergences = compare_sheets(lite, pg)
    assert divergences == [], "dialect divergences:\n" + "\n".join(
        divergences[:25]
    )
    assert sheet_bytes(lite) == sheet_bytes(pg)
