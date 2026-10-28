"""Round 2 CLI parity: dedup queue, decisions, and history views
through the pv-agent command line (same store the API serves)."""

import json

import pytest

from pv_cases import cli


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "pv.db"))
    monkeypatch.setenv("OBJECT_STORE_DIR", str(tmp_path / "objects"))
    return tmp_path


def _ingest_full_book(tmp_path):
    """Feed + fixtures into the CLI's configured store."""
    from pv_cases.config import Settings
    from pv_cases.seed import dedup_fixtures
    from pv_cases.wiring import Services
    from tests.conftest import DAY

    svc = Services(
        Settings(
            sqlite_path=str(tmp_path / "pv.db"),
            object_store_dir=str(tmp_path / "objects"),
        )
    )
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures():
        svc.intake.ingest(report, DAY)


def test_cli_dedup_queue_and_decisions(cli_env, capsys):
    _ingest_full_book(cli_env)
    assert cli.main(["dedup"]) == 0
    out = capsys.readouterr().out
    assert "LINK-CASE-RPT-2026-0005-CASE-RPT-2026-0006" in out
    assert "LINK-CASE-RPT-2026-0101-CASE-RPT-2026-0102" in out

    link = "LINK-CASE-RPT-2026-0005-CASE-RPT-2026-0006"
    assert cli.main(["confirm", link, "Dr", "A.", "Reviewer"]) == 0
    out = capsys.readouterr().out
    assert "confirmed by Dr A. Reviewer" in out
    # A decided link refuses to move again.
    assert cli.main(["reject", link, "Dr B. Other"]) == 1


def test_cli_history_views(cli_env, capsys):
    _ingest_full_book(cli_env)
    assert cli.main(["history-product", "NBT-317"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["case_count"] == 6
    assert cli.main(["history-case", "CASE-RPT-2026-0104"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["patient_key"] == "dob:1991-07-02|f"
    assert cli.main(["history-case", "CASE-GHOST"]) == 1
