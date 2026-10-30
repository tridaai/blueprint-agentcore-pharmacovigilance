"""Production smoke + dialect parity for the Postgres profile.

Round 16. Fifteen rounds of gates were graded on the SQLite
profile while the Postgres profile — the production store —
existed in code and was never stood up by a standing harness.
This check is that harness. It grades, against a real
PostgreSQL database:

- the profile is the supported one: the server reports a
  UTF-8 encoding (the repo's JSON-text contract assumes it
  end to end). A non-UTF-8 database is refused as the
  production profile, named, not graded over;
- the full migration chain stands on the real database: the
  walk's store applies every file in migrations/postgres/
  and the ledger on the server lists exactly those files;
- the demo's core walk — intake through the validity gate,
  dedup, coding, the assessment packs and clocks, dual
  review, sealing, export, quarantine, gateway, the model
  ledger, the three standing aggregates and the console —
  runs on the Postgres store end to end, and the export it
  produces passes the offline verifier;
- parity: the same walk on the SQLite profile produces a
  figure sheet that agrees with the Postgres sheet field
  for field. Every divergence is printed by its path with
  both values — a divergence is a defect with an address,
  never a statistic.

The walk runs inside its own schema (``pv_parity_check``)
in the database named by PV_POSTGRES_URL, so it never
touches the database's standing tables; the schema is
dropped on success and left in place, named, on failure.

Run: PV_POSTGRES_URL=postgresql://... uv run python scripts/check_postgres.py
Exit 0 = the Postgres profile serves the walk and the two
dialects agree exactly; 1 otherwise.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SCHEMA = "pv_parity_check"


def main() -> int:
    problems: list[str] = []
    url = os.environ.get("PV_POSTGRES_URL")
    if not url:
        print("PV_POSTGRES_URL is not set — the Postgres profile")
        print("cannot be graded without a database. Point it at a")
        print("UTF-8 PostgreSQL database this check may use")
        print("exclusively (it works in its own schema).")
        return 1

    import psycopg

    from pv_cases.config import Settings
    from pv_cases.migrations import migration_files
    from pv_cases.parity import compare_sheets, run_core_walk, sheet_bytes
    from pv_cases.wiring import Services

    with psycopg.connect(url, autocommit=True) as conn:
        encoding = conn.execute("SHOW server_encoding").fetchone()[0]
    print(f"server encoding: {encoding}")
    if encoding != "UTF8":
        print(
            "REFUSED: the supported Postgres profile is a UTF-8"
            " database — the JSON-text contract assumes it end"
            " to end, and this database is not one."
        )
        return 1

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
    pg_url = url + "?options=-c%20search_path%3D" + SCHEMA

    tmp = Path(tempfile.mkdtemp(prefix="pv-parity-check-"))
    print("walking the SQLite profile…")
    lite = run_core_walk(
        Services(
            Settings(
                sqlite_path=str(tmp / "pv.db"),
                object_store_dir=str(tmp / "objects-lite"),
            )
        )
    )
    print("walking the Postgres profile…")
    pg = run_core_walk(
        Services(
            Settings(
                database_url=pg_url,
                object_store_dir=str(tmp / "objects-pg"),
            )
        )
    )

    with psycopg.connect(pg_url) as conn:
        ledger = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM schema_migrations ORDER BY name"
            ).fetchall()
        ]
    expected = [p.name for p in migration_files("postgres")]
    if ledger != expected:
        problems.append(
            f"migration ledger on Postgres is {ledger}, expected {expected}"
        )
    else:
        print(f"migration chain: all {len(expected)} files stand on Postgres")

    if pg["export"]["verdict"] != "pass":
        problems.append(
            f"Postgres export verdict is {pg['export']['verdict']}, not pass"
        )
    invalid = [
        case_id
        for case_id, entry in pg["cases"].items()
        if not entry["chain_valid"]
    ]
    if invalid:
        problems.append(f"evidence chains invalid on Postgres: {invalid}")

    divergences = compare_sheets(lite, pg)
    for d in divergences:
        problems.append(f"divergence — {d}")

    lite_bytes, pg_bytes = sheet_bytes(lite), sheet_bytes(pg)
    print(
        f"figure sheets: SQLite {len(lite_bytes)} bytes"
        f" sha256 {hashlib.sha256(lite_bytes).hexdigest()[:16]}…;"
        f" Postgres {len(pg_bytes)} bytes"
        f" sha256 {hashlib.sha256(pg_bytes).hexdigest()[:16]}…"
    )

    if problems:
        print(f"FAIL — {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        print(f"schema {SCHEMA} left in place for inspection")
        return 1

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    print(
        "PASS — the Postgres profile serves the whole walk, the"
        " export verifies, and the two dialects' figure sheets"
        " agree field for field."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
