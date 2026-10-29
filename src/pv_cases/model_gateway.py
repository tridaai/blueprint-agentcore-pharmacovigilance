"""The governed model gateway and its cost ledger — Round 12.

Survey of the model-shaped call sites, before this round: exactly
one. The assembler's summary step (`CaseAssembler.assemble`) called
its model port bare — `model.propose_summary(facts)` — with the two
profiles behind it (`offline`, the deterministic composer, and
`bedrock`, the fail-closed hosted profile that refuses loudly until
configured). The AgentCore Gateway of Round 8 fronts *tools*, not
the model; nothing recorded what a model call was handed, what it
returned, or what it cost, and no budget bounded it. This module is
that governance, in this repo's idiom, following the pattern the
sibling AgentCore blueprints proved in their gateway rounds:

- **One door.** Every model call routes through
  `ModelGateway.propose_summary_for_case`. The gateway wraps the
  configured port — offline by default, the fail-closed Bedrock
  profile when selected — and the wrapped port's behaviour is
  unchanged: a hosted profile that is not configured refuses, the
  gateway records that refusal as an error record, and it
  propagates. There is no silent fallback to offline, ever.
- **Every call is a record.** Completed, budget-refused and
  errored calls each land as one insert-only `ModelCallRecord`
  (migration 0011) before control returns to the caller. A
  completed record carries the measured canonical sizes of the
  facts handed over and the statements returned, the pack's token
  estimates derived from those sizes, the pack's rates, and the
  exact integer cost in micro-dollars. The record carries sizes
  and counts only — never the prompt text itself.
- **Costs are pack reference rates, never provider bills.** The
  rates are stated in the versioned model pack below as integer
  micro-dollars per token. They are this system's own accounting
  rule; nothing here claims a provider charged this amount.
- **Budgets refuse, on the record, before the port is touched.**
  The pack states a per-case call budget and a per-case cost
  budget. A call that would be made after either budget is spent
  is refused before the wrapped port is invoked; the refusal is
  itself a ledger record naming the budget it hit, and
  `ModelBudgetRefusal` propagates to the caller.
- **PHI discipline holds on this path, and is judged at the
  door.** The facts a model sees are the assembler's facts — the
  only road from source text to a prompt — and the assembler's
  field list is the case's minimised form by construction (age
  and sex, coded terms, the assessment artefact — never
  initials, a date of birth, a reporter-held patient id, or a
  reporter's name, email or phone). The gateway does not trust
  that construction: it scans every proposed payload for the
  pack's direct-identifier field paths and for identifier keys
  at any depth of a fact's value, and refuses the call whole
  when one is present — before budget, before measurement,
  before the port. The refusal is a ledger record naming the
  offending field paths and key names only, never an
  identifier value. The gateway also enforces the Round 8
  quarantine on that road itself: when the wiring binds the
  quarantine onto the gateway, a fact whose (evidence item,
  field) pair is held is dropped before measurement and before
  the port is touched, so held text counts as absent to the
  model exactly as Round 9 (RT-01) taught the assessment. The
  ledger adds no new exposure: a record names a case, a
  profile, sizes and a cost — no patient field, no narrative,
  no prompt.
- **Totals fold, and re-perform.** No total is ever stored.
  `ledger_view` folds a case's records; `verify_ledger`
  re-derives every record's tokens and cost from its stored
  sizes and the pack, and re-folds by a second accumulation —
  a record whose arithmetic does not recompute, or a fold that
  disagrees, is named. `scripts/check_model_ledger.py` runs the
  same reconciliation from raw store rows.

The ledger is a guarded read surface under the Round 10 access
pack (`model_ledger`): it follows the pack's case-read discipline
— the case-working roles and the quality auditor read a case's
ledger, intake and case admin do not, a refused read is answered
with existence hiding, and the denial is recorded in the denial
log before it propagates, as in Rounds 10 and 11.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from pydantic import BaseModel, Field

from pv_cases.access import (
    SURFACE_MODEL_LEDGER,
    Principal,
    control_for,
)
from pv_cases.evidence import canonical_json

MODEL_PACK = {
    "version": "2026.10.1",
    # The token estimate is a stated measurement rule, not a
    # claim about any provider's tokenizer: canonical JSON
    # bytes divided by this many bytes per token, rounded up.
    "bytes_per_token": 4,
    # Per-case budgets. The call budget binds every profile;
    # the cost budget binds priced profiles (the offline
    # profile is priced at zero in this pack, so its cost can
    # never cross on its own).
    "max_calls_per_case": 25,
    "max_cost_micro_per_case": 2_500_000,
    # Rates in integer micro-dollars per token, stated by the
    # pack as reference rates for this system's own accounting.
    # They are never a provider's bill: a deployment states its
    # own contracted rates by superseding the pack. Integer
    # rates keep every cost an exact integer fold. The offline
    # profile is priced at zero — it makes no provider call.
    "rates_micro_per_token": {
        "offline": {"input": 0, "output": 0},
        "bedrock": {"input": 3, "output": 15},
    },
    # PHI minimisation at the door: the direct-identifier field
    # paths of the report record, and the identifier keys
    # refused at any depth of a fact's value. The assembler's
    # fact list — the minimised form — carries none of these;
    # a payload that does is refused whole, on the record.
    "direct_identifier_fields": [
        "patient.initials",
        "patient.date_of_birth",
        "patient.reporter_patient_id",
        "reporter.name",
        "reporter.email",
        "reporter.phone",
    ],
    "direct_identifier_keys": [
        "initials",
        "date_of_birth",
        "dob",
        "reporter_patient_id",
        "patient_name",
        "reporter_name",
        "email",
        "phone",
    ],
    "purpose": "summary",
}

MODEL_PACK_VERSION = MODEL_PACK["version"]

STATUS_COMPLETED = "completed"
STATUS_REFUSED = "refused_budget"
STATUS_REFUSED_PHI = "refused_phi"
STATUS_ERROR = "error"

BUDGET_CALL = "call_budget"
BUDGET_COST = "cost_budget"
PHI_DOOR = "phi_minimisation"


class ModelPhiRefusal(RuntimeError):
    """A call the minimisation door refuses: the proposed
    payload carries a direct identifier beyond the case's
    minimised form. The refusal is a ledger record before this
    propagates — naming field paths and key names only, never
    an identifier value — and the wrapped port was never
    touched."""

    def __init__(self, case_id: str, problems: list[str]):
        super().__init__(
            "model call refused at the minimisation door: "
            + "; ".join(problems)
        )
        self.case_id = case_id
        self.problems = problems


def phi_problems(facts: list[dict], pack: dict = MODEL_PACK) -> list[str]:
    """The minimisation scan over one proposed payload: the
    field paths and key names that carry direct identifiers —
    never the values. An empty list is the only pass."""
    fields = set(pack["direct_identifier_fields"])
    keys = set(pack["direct_identifier_keys"])
    problems: list[str] = []
    for fact in facts:
        field = fact.get("field", "")
        if field in fields or field.rsplit(".", 1)[-1] in keys:
            problems.append(f"fact field {field!r} is a direct identifier")
        for key in _identifier_keys(fact.get("value"), keys):
            problems.append(f"fact {field!r} carries identifier key {key!r}")
    return sorted(set(problems))


def _identifier_keys(value, keys: set) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in keys:
                found.append(key)
            found.extend(_identifier_keys(sub, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_identifier_keys(item, keys))
    return found


def model_pack_fingerprint(pack: dict | None = None) -> str:
    """A content fingerprint of the model pack: sha256 over its
    canonical JSON. The recorded `pack_version` is the pack's
    self-declared version; this fingerprint is its content, so
    an in-place rate or budget edit without a version bump is
    detectable after the fact, on the RT-15 pattern."""
    return hashlib.sha256(
        canonical_json(pack if pack is not None else MODEL_PACK)
    ).hexdigest()


class ModelBudgetRefusal(RuntimeError):
    """A call the case's budget refuses. The refusal is a ledger
    record before this propagates, and the wrapped port was
    never touched."""

    def __init__(self, case_id: str, budget: str, detail: str):
        super().__init__(detail)
        self.case_id = case_id
        self.budget = budget


class ModelCallRecord(BaseModel):
    """One model call through the gateway, as a ledger record.

    Insert-only: a record is history, never state to rewrite.
    `input_bytes` / `output_bytes` are the measured canonical
    sizes of the facts handed over (after held facts were
    dropped) and of the statements returned; the token figures
    derive from those sizes under the pack's stated rule, and
    `cost_micro` is the exact integer fold of the tokens under
    the pack's reference rates for the record's profile.
    Refused and errored records carry no sizes, no tokens and
    no cost — nothing was produced — and state why in `detail`.
    No prompt text, patient field or narrative stands on a
    record, by construction of this shape."""

    call_id: str
    case_id: str
    profile: str
    purpose: str = "summary"
    status: str  # STATUS_COMPLETED | STATUS_REFUSED | STATUS_ERROR
    input_bytes: int = 0
    output_bytes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro: int = 0
    pack_version: str
    # The content fingerprint of the pack this call was judged
    # under (the RT-15 pattern): the version string is the
    # pack's self-declaration; the fingerprint is its content.
    pack_fingerprint: str = ""
    detail: str = ""
    recorded_on: date


def _canonical_bytes(payload) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def estimate_tokens(nbytes: int, pack: dict) -> int:
    per = int(pack["bytes_per_token"])
    return -(-int(nbytes) // per)  # ceil division, exact


def cost_micro(
    profile: str, input_tokens: int, output_tokens: int, pack: dict
) -> int:
    rates = pack["rates_micro_per_token"][profile]
    return input_tokens * int(rates["input"]) + output_tokens * int(
        rates["output"]
    )


class ModelGateway:
    """The one door for model calls. Wraps a model port; the
    assembler calls the gateway exactly where it called the
    port, and the summary it stores still names the wrapped
    profile."""

    def __init__(self, store, port, pack: dict = MODEL_PACK):
        self.store = store
        self.port = port
        self.pack = pack
        self.access = None  # bound by the wiring (Round 10 pattern)
        # Round 8 quarantine, bound by the wiring: while it is
        # set, a held fact never reaches measurement or the port.
        self.quarantine = None

    @property
    def profile(self) -> str:
        return self.port.profile

    # -- the door ------------------------------------------------

    def propose_summary_for_case(
        self, case_id: str, facts: list[dict], *, today: date | None = None
    ) -> list:
        """One governed model call for one case: held facts
        dropped, minimisation judged at the door, budget
        second, the port last, the record always.

        A minimisation or budget refusal is recorded and raised
        before the port is touched. A port error — the hosted
        profile's fail-closed refusal included — is recorded as
        an error record and propagates unchanged; nothing falls
        back."""
        recorded_on = today or date.today()
        if self.quarantine is not None:
            held = self.quarantine.held_fields(case_id)
            if held:
                facts = [
                    fact
                    for fact in facts
                    if (fact.get("item_id"), fact.get("field")) not in held
                ]
        problems = phi_problems(facts, self.pack)
        if problems:
            self._record(
                case_id,
                STATUS_REFUSED_PHI,
                PHI_DOOR,
                "; ".join(problems),
                recorded_on,
            )
            raise ModelPhiRefusal(case_id, problems)
        records = self.store.list_model_calls(case_id)
        completed = [r for r in records if r.status == STATUS_COMPLETED]
        calls_used = len(completed)
        cost_used = sum(r.cost_micro for r in completed)
        if calls_used >= int(self.pack["max_calls_per_case"]):
            detail = (
                f"case {case_id} has used {calls_used} of "
                f"{self.pack['max_calls_per_case']} budgeted model "
                "calls; the call is refused before the port is touched"
            )
            self._record(
                case_id, STATUS_REFUSED, BUDGET_CALL, detail, recorded_on
            )
            raise ModelBudgetRefusal(case_id, BUDGET_CALL, detail)
        if cost_used >= int(self.pack["max_cost_micro_per_case"]):
            detail = (
                f"case {case_id} has spent {cost_used} of "
                f"{self.pack['max_cost_micro_per_case']} budgeted "
                "micro-dollars; the call is refused before the "
                "port is touched"
            )
            self._record(
                case_id, STATUS_REFUSED, BUDGET_COST, detail, recorded_on
            )
            raise ModelBudgetRefusal(case_id, BUDGET_COST, detail)
        input_bytes = len(_canonical_bytes(facts))
        try:
            statements = self.port.propose_summary(facts)
        except Exception as exc:  # recorded, then propagated as-is
            self._record(
                case_id,
                STATUS_ERROR,
                type(exc).__name__,
                str(exc)[:400],
                recorded_on,
            )
            raise
        output_bytes = len(
            _canonical_bytes(
                [s.model_dump(mode="json") for s in statements]
            )
        )
        in_tok = estimate_tokens(input_bytes, self.pack)
        out_tok = estimate_tokens(output_bytes, self.pack)
        record = ModelCallRecord(
            call_id=self._next_call_id(case_id, records),
            case_id=case_id,
            profile=self.port.profile,
            purpose=self.pack.get("purpose", "summary"),
            status=STATUS_COMPLETED,
            input_bytes=input_bytes,
            output_bytes=output_bytes,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_micro=cost_micro(
                self.port.profile, in_tok, out_tok, self.pack
            ),
            pack_version=self.pack["version"],
            pack_fingerprint=model_pack_fingerprint(self.pack),
            recorded_on=recorded_on,
        )
        self.store.add_model_call(record)
        return statements

    def _record(
        self,
        case_id: str,
        status: str,
        detail_kind: str,
        detail: str,
        recorded_on: date,
    ) -> ModelCallRecord:
        records = self.store.list_model_calls(case_id)
        record = ModelCallRecord(
            call_id=self._next_call_id(case_id, records),
            case_id=case_id,
            profile=self.port.profile,
            purpose=self.pack.get("purpose", "summary"),
            status=status,
            detail=f"{detail_kind}: {detail}"[:400],
            pack_version=self.pack["version"],
            pack_fingerprint=model_pack_fingerprint(self.pack),
            recorded_on=recorded_on,
        )
        self.store.add_model_call(record)
        return record

    @staticmethod
    def _next_call_id(case_id: str, records: list) -> str:
        return f"MCALL-{case_id}-{len(records) + 1:04d}"

    # -- the ledger read surface ---------------------------------

    def ledger_view(
        self,
        case_id: str,
        principal: Principal | None = None,
        *,
        on: date | None = None,
    ) -> dict:
        """The case's ledger, folded from its records. A
        principal-carrying read is judged on the model_ledger
        surface with the case surfaces' existence hiding: a
        role that may not read cases, or may not read this
        surface, is refused as a hidden denial — recorded in
        the denial log before the refusal propagates."""
        if principal is not None:
            control = control_for(self)
            control.require_case_read(
                principal,
                SURFACE_MODEL_LEDGER,
                case_id=case_id,
                on=on,
            )
        records = self.store.list_model_calls(case_id)
        return fold_records(records)


def fold_records(records: list[ModelCallRecord]) -> dict:
    """The one fold: counts by status and exact totals over the
    completed records. Refused and errored calls count as
    attempts and contribute no tokens and no cost."""
    completed = [r for r in records if r.status == STATUS_COMPLETED]
    return {
        "calls_recorded": len(records),
        "calls_completed": len(completed),
        "calls_refused_budget": sum(
            1 for r in records if r.status == STATUS_REFUSED
        ),
        "calls_refused_phi": sum(
            1 for r in records if r.status == STATUS_REFUSED_PHI
        ),
        "calls_errored": sum(1 for r in records if r.status == STATUS_ERROR),
        "input_tokens": sum(r.input_tokens for r in completed),
        "output_tokens": sum(r.output_tokens for r in completed),
        "cost_micro": sum(r.cost_micro for r in completed),
        "records": [r.model_dump(mode="json") for r in records],
    }


def verify_ledger(store, pack: dict = MODEL_PACK) -> list[str]:
    """The independent path: re-derive every record's tokens and
    cost from its stored sizes under the pack, and re-fold every
    case's totals a second way (a running accumulation in record
    order, not the fold's comprehensions). Any record whose
    arithmetic does not recompute, any non-completed record
    carrying tokens or cost, and any fold disagreement is
    named. An empty list is a clean ledger."""
    problems: list[str] = []
    records = store.list_model_calls()
    for record in records:
        if record.pack_version != pack["version"]:
            problems.append(
                f"{record.call_id}: pack version {record.pack_version} "
                f"is not the grading pack {pack['version']}"
            )
            continue
        if record.pack_fingerprint != model_pack_fingerprint(pack):
            problems.append(
                f"{record.call_id}: pack fingerprint does not match "
                "the pack it names — the pack's content moved "
                "under its version, or the record was tampered with"
            )
        if record.status == STATUS_COMPLETED:
            in_tok = estimate_tokens(record.input_bytes, pack)
            out_tok = estimate_tokens(record.output_bytes, pack)
            if in_tok != record.input_tokens or out_tok != record.output_tokens:
                problems.append(
                    f"{record.call_id}: token estimates do not recompute "
                    f"from the stored sizes ({in_tok}/{out_tok} vs "
                    f"{record.input_tokens}/{record.output_tokens})"
                )
            expected = cost_micro(record.profile, in_tok, out_tok, pack)
            if expected != record.cost_micro:
                problems.append(
                    f"{record.call_id}: cost does not recompute from the "
                    f"pack rates ({expected} vs {record.cost_micro})"
                )
        else:
            if record.input_tokens or record.output_tokens or record.cost_micro:
                problems.append(
                    f"{record.call_id}: a {record.status} record carries "
                    "tokens or cost — nothing was produced"
                )
    by_case: dict[str, list[ModelCallRecord]] = {}
    for record in records:
        by_case.setdefault(record.case_id, []).append(record)
    for case_id, rows in by_case.items():
        folded = fold_records(rows)
        total_cost = 0
        total_in = 0
        total_out = 0
        n_completed = 0
        for row in rows:
            if row.status == STATUS_COMPLETED:
                n_completed += 1
                total_in += row.input_tokens
                total_out += row.output_tokens
                total_cost += row.cost_micro
        if (
            folded["cost_micro"] != total_cost
            or folded["input_tokens"] != total_in
            or folded["output_tokens"] != total_out
            or folded["calls_completed"] != n_completed
        ):
            problems.append(
                f"case {case_id}: the ledger fold disagrees "
                "with the second accumulation"
            )
    return problems
