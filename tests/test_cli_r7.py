"""Round 7 CLI gates: seal, seal-case, export-case, verify-export."""

from __future__ import annotations

import json
from datetime import date

import pytest

import pv_cases.cli as cli_module
import pv_cases.runtime_app as runtime_app

DAY = date(2026, 10, 12)
COMPLETE = "CASE-RPT-2026-0304"
MEDICAL = "Dr A. Rao"
SAFETY = "Dr B. Lindqvist"


@pytest.fixture()
def wired(assessed, monkeypatch):
    monkeypatch.setattr(cli_module, "build_services", lambda: assessed)
    monkeypatch.setattr(runtime_app, "_services", assessed)
    return assessed


def dual_review(svc) -> None:
    svc.coding.code_case(COMPLETE, DAY)
    svc.assessment.assess_case(COMPLETE, DAY)
    svc.assembler.assemble(COMPLETE, DAY)
    svc.review.record_review(COMPLETE, MEDICAL, "medical_reviewer", "accept", "", DAY)
    svc.review.record_review(COMPLETE, SAFETY, "safety_reviewer", "accept", "", DAY)


def test_cli_seal_refuses_unreviewed_then_seals(wired, capsys):
    assert cli_module.main(["seal", COMPLETE, "PV", "Operations", "Lead"]) == 1
    assert "seal refused" in capsys.readouterr().out
    dual_review(wired)
    assert cli_module.main(["seal", COMPLETE, "PV", "Operations", "Lead"]) == 0
    out = capsys.readouterr().out
    assert "SEAL-" in out and COMPLETE in out


def test_cli_seal_case_and_export_and_verify(wired, capsys, tmp_path):
    dual_review(wired)
    assert cli_module.main(["seal", COMPLETE, "PV Operations Lead"]) == 0
    capsys.readouterr()
    assert cli_module.main(["seal-case", COMPLETE]) == 0
    seal = json.loads(capsys.readouterr().out)
    assert seal["case_id"] == COMPLETE

    assert cli_module.main(["export-case", COMPLETE, "PV Operations Lead"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("export EXP-") and "hash" in lines[0]
    package = json.loads(lines[1])
    assert package["package_format"] == "pv-case-export/1"

    path = tmp_path / "package.json"
    path.write_text(lines[1], encoding="utf-8")
    assert cli_module.main(["verify-export", str(path)]) == 0
    out = capsys.readouterr().out
    assert "verdict: PASS" in out

    # A tampered package file fails with exit 1.
    package["seal"]["case_id"] = "CASE-ELSEWHERE"
    path.write_text(json.dumps(package), encoding="utf-8")
    assert cli_module.main(["verify-export", str(path)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_export_refuses_unsealed(wired, capsys):
    dual_review(wired)
    assert cli_module.main(["export-case", COMPLETE, "PV Operations Lead"]) == 1
    assert "export refused" in capsys.readouterr().out


def test_cli_sealing_pack(wired, capsys):
    assert cli_module.main(["sealing-pack"]) == 0
    assert "pv-case-export/1" in capsys.readouterr().out
