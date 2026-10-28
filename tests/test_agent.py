"""The agent step: summaries assemble from evidence, every statement
cites, and a model proposing an unresolvable citation is refused as
a whole."""

import pytest

from pv_cases.agent import BedrockProfile, CaseAssembler, CitationRefusal, ModelNotConfigured
from pv_cases.models import Citation, SummaryStatement
from tests.conftest import DAY


def test_assemble_main_case(fed):
    summary = fed.assembler.assemble("CASE-RPT-2026-0001", DAY)
    assert summary.case_id == "CASE-RPT-2026-0001"
    assert summary.model_profile == "offline"
    assert summary.statements, "summary must carry statements"
    for stmt in summary.statements:
        assert stmt.citations, f"uncited statement: {stmt.text}"
    texts = " ".join(s.text for s in summary.statements)
    assert "Cardivane" in texts
    assert "Dizziness and fall at home" in texts
    case = fed.store.get_case("CASE-RPT-2026-0001")
    assert case.status.value == "summary_assembled"


def test_assemble_unknown_case_raises(fed):
    with pytest.raises(KeyError):
        fed.assembler.assemble("CASE-NOPE", DAY)


class _BadModel:
    profile = "bad-test-double"

    def propose_summary(self, facts):
        return [
            SummaryStatement(
                text="Patient was hospitalised for nine days.",
                citations=[Citation(evidence_item_id="EV-GHOST", field="events.0.outcome")],
            )
        ]


def test_unresolvable_citation_refused_whole(fed):
    assembler = CaseAssembler(fed.store, fed.chain, _BadModel())
    with pytest.raises(CitationRefusal):
        assembler.assemble("CASE-RPT-2026-0001", DAY)
    assert fed.store.list_summaries("CASE-RPT-2026-0001") == []
    case = fed.store.get_case("CASE-RPT-2026-0001")
    assert case.status.value == "open"


class _FieldLieModel:
    profile = "bad-test-double"

    def propose_summary(self, facts):
        item_id = facts[0]["item_id"]
        return [
            SummaryStatement(
                text="The product was administered intravenously.",
                citations=[Citation(evidence_item_id=item_id, field="events.0.route")],
            )
        ]


def test_absent_field_citation_refused(fed):
    assembler = CaseAssembler(fed.store, fed.chain, _FieldLieModel())
    with pytest.raises(CitationRefusal):
        assembler.assemble("CASE-RPT-2026-0001", DAY)


def test_bedrock_profile_refuses_until_configured(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    with pytest.raises(ModelNotConfigured):
        BedrockProfile().propose_summary([])


def test_unknown_profile_refused():
    from pv_cases.agent import model_for_profile

    with pytest.raises(ModelNotConfigured):
        model_for_profile("carrier-pigeon")
