"""Round 4 CLI parity: assessment reads, the re-assessment act,
the clock board and the RSI through the pv-agent command line
(same store the API serves)."""

import pytest

from pv_cases import cli


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "pv.db"))
    monkeypatch.setenv("OBJECT_STORE_DIR", str(tmp_path / "objects"))
    return tmp_path


def _ingest_assessed_book(tmp_path):
    from pv_cases.config import Settings
    from pv_cases.seed import assessment_fixtures, coding_fixtures, dedup_fixtures
    from pv_cases.wiring import Services
    from tests.conftest import DAY

    svc = Services(
        Settings(
            sqlite_path=str(tmp_path / "pv.db"),
            object_store_dir=str(tmp_path / "objects"),
        )
    )
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures() + coding_fixtures() + assessment_fixtures():
        svc.intake.ingest(report, DAY)


def test_cli_assess_and_assessment(cli_env, capsys):
    _ingest_assessed_book(cli_env)
    assert cli.main(["assess", "CASE-RPT-2026-0301"]) == 0
    out = capsys.readouterr().out
    assert "seriousness=serious" in out
    assert "expectedness=unexpected" in out
    assert "clock=expedited_7_day deadline 2026-10-21" in out
    assert cli.main(["assess", "CASE-GHOST"]) == 1

    assert cli.main(["assessment", "CASE-RPT-2026-0302"]) == 0
    out = capsys.readouterr().out
    assert '"obligation_class": "expedited_15_day"' in out
    assert '"deadline": "2026-10-31"' in out
    assert cli.main(["assessment", "CASE-GHOST"]) == 1


def test_cli_clocks_and_rsi(cli_env, capsys):
    _ingest_assessed_book(cli_env)
    assert cli.main(["clocks", "2026-10-22"]) == 0
    out = capsys.readouterr().out
    assert "CASE-RPT-2026-0301: expedited_7_day deadline 2026-10-21 — breached (-1d)" in out
    assert "CASE-RPT-2026-0305: awaiting_assessment — awaiting_assessment" in out

    assert cli.main(["rsi"]) == 0
    out = capsys.readouterr().out
    assert "reference safety information 2026.10.1" in out
    assert "stand-in" in out
