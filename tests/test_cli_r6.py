"""Round 6 CLI parity: recording a review, the per-case review
position, the queue and the pack through the pv-agent command
line (same store the API serves)."""

import json

import pytest

from pv_cases import cli
from tests.conftest import DAY

COMPLETE = "CASE-RPT-2026-0304"
NO_OUTCOME = "CASE-RPT-2026-0305"


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "pv.db"))
    monkeypatch.setenv("OBJECT_STORE_DIR", str(tmp_path / "objects"))
    return tmp_path


def _ingest_assessed_book(tmp_path):
    from pv_cases.config import Settings
    from pv_cases.seed import assessment_fixtures, coding_fixtures, dedup_fixtures
    from pv_cases.wiring import Services

    svc = Services(
        Settings(
            sqlite_path=str(tmp_path / "pv.db"),
            object_store_dir=str(tmp_path / "objects"),
        )
    )
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    for report in dedup_fixtures() + coding_fixtures() + assessment_fixtures():
        svc.intake.ingest(report, DAY)
    return svc


def test_review_flow_through_the_cli(cli_env, capsys):
    _ingest_assessed_book(cli_env)
    assert (
        cli.main(["review", COMPLETE, "Dr A. Rao", "medical_reviewer", "accept"])
        == 0
    )
    out = capsys.readouterr().out
    assert "awaiting_second_review" in out

    # Dual control is the CLI's refusal too, not just the API's.
    assert (
        cli.main(["review", COMPLETE, "Dr A. Rao", "safety_reviewer", "accept"])
        == 1
    )
    assert "dual control" in capsys.readouterr().out

    assert (
        cli.main(
            ["review", COMPLETE, "Dr B. Lindqvist", "safety_reviewer", "accept"]
        )
        == 0
    )
    assert "medically_reviewed" in capsys.readouterr().out

    assert cli.main(["review-case", COMPLETE]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["state"] == "medically_reviewed"
    assert len(view["acts"]) == 2


def test_review_queue_and_pack_through_the_cli(cli_env, capsys):
    _ingest_assessed_book(cli_env)
    assert cli.main(["review-pack"]) == 0
    out = capsys.readouterr().out
    assert "2026.10.1" in out
    assert "medical_reviewer" in out

    assert (
        cli.main(
            [
                "review", NO_OUTCOME, "Dr B. Lindqvist", "safety_reviewer",
                "reject_with_reason",
                "Seriousness cannot be confirmed until the outcome lands.",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["review-queue", "2026-10-12"]) == 0
    out = capsys.readouterr().out
    first_line = out.strip().splitlines()[0]
    assert NO_OUTCOME in first_line
    assert "review_hold" in first_line
    # PHI minimisation on the CLI surface too.
    assert "H.B." not in out
    assert "R. Patel" not in out


def test_review_unknown_case_through_the_cli(cli_env, capsys):
    _ingest_assessed_book(cli_env)
    assert (
        cli.main(["review", "CASE-NOPE", "Dr A. Rao", "medical_reviewer", "accept"])
        == 1
    )
    assert "no such case" in capsys.readouterr().out
    assert cli.main(["review-case", "CASE-NOPE"]) == 1
