"""Round 5 CLI parity: the follow-up board, per-case requests,
the request detail, the pack, and the follow-up intake through
the pv-agent command line (same store the API serves)."""

import json

import pytest

from pv_cases import cli
from pv_cases.seed import followup_reports


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "pv.db"))
    monkeypatch.setenv("OBJECT_STORE_DIR", str(tmp_path / "objects"))
    return tmp_path


def _ingest_followed_book(tmp_path):
    from pv_cases.config import Settings
    from pv_cases.seed import (
        assessment_fixtures,
        coding_fixtures,
        dedup_fixtures,
        followup_fixtures,
    )
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
    for report in followup_fixtures():
        svc.intake.ingest(report, DAY)


def test_cli_board_tasks_and_pack(cli_env, capsys):
    _ingest_followed_book(cli_env)
    assert cli.main(["follow-up-board", "2026-11-20"]) == 0
    out = capsys.readouterr().out
    assert "FUP-RPT-2026-0003: [validity] — overdue, cycle 1" in out
    assert "due 2026-10-26 (25d overdue) — missing: reporter" in out

    assert cli.main(["follow-up-tasks", "CASE-RPT-2026-0401"]) == 0
    out = capsys.readouterr().out
    assert "FUP-CASE-RPT-2026-0401-A: [assessment]" in out
    assert "missing: events.0.outcome" in out
    assert cli.main(["follow-up-tasks", "CASE-GHOST"]) == 1

    assert cli.main(["follow-up-task", "FUP-CASE-RPT-2026-0401-A"]) == 0
    out = capsys.readouterr().out
    assert '"source": "assessment"' in out
    assert '"field": "events.0.outcome"' in out
    assert cli.main(["follow-up-task", "FUP-GHOST"]) == 1

    assert cli.main(["follow-up-pack"]) == 0
    out = capsys.readouterr().out
    assert "follow-up pack 2026.10.1" in out
    assert "identifiable_reporter 14d" in out
    assert "max cycles 3" in out


def test_cli_submit_follow_up(cli_env, capsys):
    _ingest_followed_book(cli_env)
    answers = followup_reports()
    payload = json.dumps(answers["RPT-2026-0402"].model_dump(mode="json"))
    assert (
        cli.main(["submit-follow-up", "FUP-CASE-RPT-2026-0401-A", payload]) == 0
    )
    out = capsys.readouterr().out
    assert "FUP-CASE-RPT-2026-0401-A: resolution by RPT-2026-0402" in out
    assert "state resolved" in out

    # Decided work refuses a second report; a ghost is refused too.
    assert (
        cli.main(["submit-follow-up", "FUP-CASE-RPT-2026-0401-A", payload]) == 1
    )
    out = capsys.readouterr().out
    assert "follow-up refused" in out
    assert cli.main(["submit-follow-up", "FUP-GHOST", payload]) == 1
    out = capsys.readouterr().out
    assert "no such follow-up request" in out
