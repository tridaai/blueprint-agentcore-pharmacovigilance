"""Round 3 CLI parity: coding reads, the review queue, a coder
resolution, and dictionary versions through the pv-agent command
line (same store the API serves)."""

import json

import pytest

from pv_cases import cli


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "pv.db"))
    monkeypatch.setenv("OBJECT_STORE_DIR", str(tmp_path / "objects"))
    return tmp_path


def _ingest_coded_book(tmp_path):
    from pv_cases.config import Settings
    from pv_cases.seed import coding_fixtures, dedup_fixtures
    from pv_cases.wiring import Services
    from tests.conftest import DAY

    svc = Services(
        Settings(
            sqlite_path=str(tmp_path / "pv.db"),
            object_store_dir=str(tmp_path / "objects"),
        )
    )
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures() + coding_fixtures():
        svc.intake.ingest(report, DAY)


def test_cli_coding_reads_and_queue(cli_env, capsys):
    _ingest_coded_book(cli_env)
    assert cli.main(["coding", "CASE-RPT-2026-0204"]) == 0
    out = capsys.readouterr().out
    assert "'Feeling drained' -> UNCODED (ambiguous_match" in out
    assert "'Cardivane' -> Cardivane (NBT-101)" in out
    assert cli.main(["coding", "CASE-GHOST"]) == 1

    assert cli.main(["coding-queue"]) == 0
    out = capsys.readouterr().out
    assert "CT-CASE-RPT-2026-0203-E0" in out
    assert "CT-CASE-RPT-2026-0205-P0" in out
    assert "candidates: Asthenia, Fatigue" in out


def test_cli_resolve_and_dictionary(cli_env, capsys):
    _ingest_coded_book(cli_env)
    assert cli.main(["dictionary"]) == 0
    assert "dictionary 2026.10.1" in capsys.readouterr().out

    task = "CT-CASE-RPT-2026-0204-E0"
    assert cli.main(["resolve-coding", task, "J.Coder", "Fatigue"]) == 0
    assert "coded to Fatigue by J.Coder" in capsys.readouterr().out
    # A decided task refuses to move again.
    assert cli.main(["resolve-coding", task, "J.Coder", "Fatigue"]) == 1

    entry = json.dumps(
        {
            "verbatim": "Blistering on hands and feet",
            "preferred_term": "Rash vesicular",
            "soc": "Skin and subcutaneous tissue disorders",
        }
    )
    assert cli.main(["dictionary-add", "J.Coder", "event", entry]) == 0
    assert "dictionary 2026.10.2 created" in capsys.readouterr().out
