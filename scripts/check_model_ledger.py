"""Model ledger check for the Round 12 governed model gateway.

Builds the planted book on a throwaway store — the reference
feed worked through summary assembly on the standard wiring,
so every summary the assembler stores has routed through the
gateway — then grades the ledger claims from raw store reads
(never via the gateway under test):

- every stored summary has exactly one completed ledger
  record, and the records' fold equals an independent
  accumulation computed here from the raw payloads;
- every completed record's token estimates recompute from its
  stored sizes under the pack's stated rule, its cost
  recomputes from the pack's reference rates, and its pack
  fingerprint recomputes from the pack;
- a tampered record (a stored cost edited in place) is named
  by the gateway's verifier;
- a scratch gateway with a two-call budget refuses the third
  call before its counting port is touched, and the refusal
  is on the ledger;
- a payload carrying a direct identifier is refused at the
  minimisation door, on the record, with the port untouched.

Run: uv run python scripts/check_model_ledger.py
Exit 0 = the ledger reconciles, tampering is detected, and
both refusals hold; 1 otherwise.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pv_cases.agent import OfflineComposer
from pv_cases.config import Settings
from pv_cases.model_gateway import (
    MODEL_PACK,
    ModelBudgetRefusal,
    ModelGateway,
    ModelPhiRefusal,
    model_pack_fingerprint,
    verify_ledger,
)
from pv_cases.wiring import Services

DAY = date(2026, 10, 12)
CASE = "CASE-RPT-2026-0001"


class CountingPort:
    """A port double that counts invocations and remembers the
    facts it was handed — the proof a refusal happened before
    the port was touched."""

    profile = "offline"

    def __init__(self):
        self.calls = 0
        self.seen: list = []

    def propose_summary(self, facts):
        self.calls += 1
        self.seen.append(facts)
        return OfflineComposer().propose_summary(facts)


def _services(tmp: Path, name: str) -> Services:
    return Services(
        Settings(
            sqlite_path=str(tmp / name),
            object_store_dir=str(tmp / f"objects-{name}"),
        )
    )


def main() -> int:
    problems: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="pv-model-ledger-check-"))
    svc = _services(tmp, "pv.db")
    svc.intake.ingest_feed(svc.feed.fetch_reports(), DAY)
    svc.assembler.assemble(CASE, DAY)
    svc.assembler.assemble(CASE, DAY)

    summaries = svc.store.list_summaries(CASE)
    records = svc.store.list_model_calls(CASE)
    completed = [r for r in records if r.status == "completed"]
    if len(completed) != len(summaries):
        problems.append(
            f"{len(completed)} completed records for {len(summaries)} summaries"
        )

    # Independent accumulation from the raw payloads.
    rows = svc.store._run(
        "SELECT payload FROM model_calls ORDER BY record_id", fetch="all"
    )
    total_cost = 0
    total_in = 0
    total_out = 0
    for (payload,) in rows:
        raw = json.loads(payload)
        if raw["status"] != "completed":
            if raw["input_tokens"] or raw["output_tokens"] or raw["cost_micro"]:
                problems.append(
                    f"{raw['call_id']}: a {raw['status']} record carries "
                    "tokens or cost"
                )
            continue
        per = MODEL_PACK["bytes_per_token"]
        in_tok = -(-raw["input_bytes"] // per)
        out_tok = -(-raw["output_bytes"] // per)
        if in_tok != raw["input_tokens"] or out_tok != raw["output_tokens"]:
            problems.append(f"{raw['call_id']}: tokens do not recompute")
        rates = MODEL_PACK["rates_micro_per_token"][raw["profile"]]
        cost = in_tok * rates["input"] + out_tok * rates["output"]
        if cost != raw["cost_micro"]:
            problems.append(f"{raw['call_id']}: cost does not recompute")
        if raw["pack_fingerprint"] != model_pack_fingerprint():
            problems.append(f"{raw['call_id']}: pack fingerprint does not recompute")
        total_cost += raw["cost_micro"]
        total_in += raw["input_tokens"]
        total_out += raw["output_tokens"]
    folded = svc.model_gateway.ledger_view(CASE)
    if (
        folded["cost_micro"] != total_cost
        or folded["input_tokens"] != total_in
        or folded["output_tokens"] != total_out
    ):
        problems.append("the ledger fold disagrees with the raw fold")
    clean = verify_ledger(svc.store)
    if clean:
        problems.append(f"the clean ledger does not verify clean: {clean}")

    # A tampered stored cost must be named.
    first = completed[0]
    svc.store._run(
        "UPDATE model_calls SET payload = ? WHERE record_id = ?",
        (
            first.model_copy(
                update={"cost_micro": first.cost_micro + 1}
            ).model_dump_json(),
            first.call_id,
        ),
    )
    named = verify_ledger(svc.store)
    if not any(first.call_id in p for p in named):
        problems.append("a tampered record cost was not named")

    # The budget refuses before the port, on the record.
    svc2 = _services(tmp, "pv2.db")
    port = CountingPort()
    pack = dict(MODEL_PACK, max_calls_per_case=2)
    gateway = ModelGateway(svc2.store, port, pack=pack)
    facts = [{"item_id": "X", "field": "source", "value": "clinician"}]
    gateway.propose_summary_for_case("CASE-CHECK", facts, today=DAY)
    gateway.propose_summary_for_case("CASE-CHECK", facts, today=DAY)
    try:
        gateway.propose_summary_for_case("CASE-CHECK", facts, today=DAY)
        problems.append("the third call was not refused at the budget")
    except ModelBudgetRefusal:
        pass
    if port.calls != 2:
        problems.append(
            f"the budget refusal touched the port ({port.calls} calls)"
        )
    rows2 = svc2.store.list_model_calls("CASE-CHECK")
    if [r.status for r in rows2] != [
        "completed",
        "completed",
        "refused_budget",
    ]:
        problems.append("the budget refusal is not the third ledger record")

    # The minimisation door refuses before the port, on the record.
    port3 = CountingPort()
    gateway3 = ModelGateway(svc2.store, port3)
    bad_facts = [
        {"item_id": "X", "field": "source", "value": "clinician"},
        {
            "item_id": "X",
            "field": "reporter.email",
            "value": "someone@example.org",
        },
    ]
    try:
        gateway3.propose_summary_for_case("CASE-CHECK-PHI", bad_facts, today=DAY)
        problems.append("an identifier-carrying payload was not refused")
    except ModelPhiRefusal:
        pass
    if port3.calls != 0:
        problems.append("the minimisation refusal touched the port")
    rows3 = svc2.store.list_model_calls("CASE-CHECK-PHI")
    if [r.status for r in rows3] != ["refused_phi"]:
        problems.append("the minimisation refusal is not on the ledger")
    elif "someone@example.org" in rows3[0].model_dump_json():
        problems.append("the refusal record carries the identifier value")

    if problems:
        print("MODEL LEDGER CHECK: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"planted book: {len(completed)} completed record(s) for"
        f" {len(summaries)} summaries, fold {total_in} in /"
        f" {total_out} out tokens, {total_cost} micro-USD"
    )
    print("a tampered record cost is refused and named")
    print("the budget and the minimisation door both refuse before the port")
    print("MODEL LEDGER CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
