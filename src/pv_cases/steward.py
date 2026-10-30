"""Dictionary stewardship — Round 14.

Rounds 1–13 built the case record, its coding layer and
the views over it. The coding layer's own queue is per
case: an uncoded verbatim raises a review task on its own
case (Round 3), and a coder settles that one case. What no
round gave the safety organisation is the *aggregate*
steward view — every uncoded wording on the book on one
ranked page, so the dictionary itself can be governed:
which wordings keep arriving uncoded, how often, since
when, and on which products. Until this round that page
existed nowhere; the per-case queue cannot answer it, and
the Round 13 survey named it the largest remaining gap.
This module is that page, and the dual-controlled route
from it into the dictionary:

- **The worklist aggregates standing uncoded decisions.**
  One row per reported wording (grouped under the repo's
  own text folding): how many reports carry it, how many
  standing decisions stand uncoded on it, when it was
  first and last seen, why it is uncoded, and the coded
  products those reports arrived with — attribution only
  by the standing coded product decision, the Round 13
  discipline (a report whose product stands uncoded is
  counted on the row's face as unattributed, never
  guessed). Each row also states the wording's standing
  against the dictionary in force at the strike: still
  uncoded, or *codes now* — the dictionary has learned it
  since those cases were coded, and the decisions of
  record predate the entry.
- **A worklist is a review aid, never an auto-coder.**
  Striking one codes nothing, changes no case and decides
  no mapping, and it says so on its face. It is an
  insert-only artefact (migration 0013), content-addressed
  over its as-of, the pack version, the dictionary version
  it was struck against and a fingerprint of the input
  artefacts; recomputing over an unmoved record returns
  the standing artefact, and a moved record mints a new
  worklist linked by `supersedes`.
- **Candidates are proposed against a worklist row.** A
  named steward records a proposed mapping for one of the
  worklist's no-entry wordings as a candidate artefact —
  itself insert-only and versioned by the dictionary
  version it was proposed against. A candidate changes
  nothing: no dictionary version moves, no case is coded.
  A wording that stands *ambiguous* takes no candidate:
  it is already a recorded synonym of more than one term,
  so the dictionary cannot take it as a new entry (the
  coding layer's addition rules refuse exactly that
  collision) — ambiguous wordings are settled per case by
  a coder's manual resolution, the Round 3 path, and the
  refusal names it.
- **Dual control decides a candidate.** A second named
  steward — never the proposer, compared under the repo's
  text folding (Round 9's identity discipline) — confirms
  or rejects it. The decision is its own insert-only
  artefact; a candidate's standing state is derived from
  the decision artefacts, never written back, and decided
  work does not move. Confirmation enacts the mapping
  through the coding layer's own collision-checked
  addition, minting a NEW dictionary version that names
  the candidate. The new version governs cases coded on
  or after the decision date; cases already coded keep
  the coding of record, pinned to the version they were
  coded under — history is never rewritten, here or
  anywhere in this repo.
- **Every worklist is re-performed before it is
  believed.** `verify_worklist` recomputes every row and
  total from the raw store by a second aggregation path
  and compares figure for figure; a worklist carries its
  `verified` mark only when the re-performance agrees,
  and `scripts/check_steward.py` re-runs the same check
  over stored worklists.
- **Aggregate-only — no PHI by construction.** A row
  carries the reported wording itself (that wording is
  the artefact's subject — terminology, not identity),
  counts, dates and coded product labels. The model has
  no field that could carry a case id, a patient or
  reporter identifier, or a narrative, and the eval gate
  runs the Round 11 key scan over worklist, candidate and
  decision payloads at zero tolerance.
- **Access follows the Round 10 pack.** Reading the
  stewardship surface takes the case-working roles, the
  quality auditor and the case admin (reference-data
  governance is the admin's own domain); striking a
  worklist, proposing a candidate and deciding one are
  named acts held by the safety scientist and the case
  admin, judged by the access pack like every other act,
  with refusals recorded in the denial log before they
  propagate. The auditor reads everything here and
  performs none of it.
- **A worklist is an internal governance artefact.** It
  states so on its face. It is not a regulatory
  submission, and nothing in this system is ever
  submitted to a regulator.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from pv_cases.access import (
    ACT_DECIDE_STEWARD_MAPPING,
    ACT_GENERATE_STEWARD_WORKLIST,
    ACT_PROPOSE_STEWARD_MAPPING,
    ROLE_CASE_ADMIN,
    ROLE_MEDICAL_REVIEWER,
    ROLE_QUALITY_AUDITOR,
    ROLE_SAFETY_REVIEWER,
    ROLE_SAFETY_SCIENTIST,
    SURFACE_DICTIONARY_STEWARDSHIP,
    Principal,
    guard_act,
)
from pv_cases.coding import current_decisions, lookup_event, lookup_product
from pv_cases.evidence import canonical_json
from pv_cases.models import (
    SUBJECT_EVENT,
    SUBJECT_PRODUCT,
    UNCODED_AMBIGUOUS,
    UNCODED_NO_ENTRY,
    CaseStatus,
    CodingStatus,
    DictionaryEntry,
    ProductDictionaryEntry,
    is_named_human,
    normalise_text,
)
from pv_cases.oversight import phi_problems

STEWARD_PACK_VERSION = "2026.10.1"

# A row's standing against the dictionary in force at the
# strike: the wording still has no path into a term, or the
# dictionary has learned it since the decisions of record
# were taken (those decisions stand as coded — uncoded —
# under their own version).
STATE_STILL_UNCODED = "still_uncoded"
STATE_CODES_NOW = "codes_now"

# The candidate decisions a second steward may record.
DECISION_CONFIRM = "confirm"
DECISION_REJECT = "reject"

# A candidate's derived standing states.
CANDIDATE_PROPOSED = "proposed"
CANDIDATE_CONFIRMED = "confirmed"
CANDIDATE_REJECTED = "rejected"

WORKLIST_NOTICE = (
    "Internal dictionary-stewardship artefact: the uncoded "
    "wordings of this system's coded case record, aggregated "
    "in code. A worklist is a review aid — striking it codes "
    "nothing, changes no case and decides no mapping. It is "
    "not a regulatory submission, and nothing in this system "
    "is ever submitted to a regulator."
)

CANDIDATE_NOTICE = (
    "A proposed dictionary mapping under dual control: it "
    "takes effect only when a second named steward confirms "
    "it, and then only as a new dictionary version governing "
    "cases coded on or after the decision date. Cases already "
    "coded keep the coding of record."
)


class StewardError(RuntimeError):
    """A stewardship act that cannot be taken as asked: a
    proposal against no worklist row, a self-decided
    candidate, decided work asked to move. Named, never
    guessed around."""


# ---------------------------------------------------------------------
# The steward pack — the definitions a figure's meaning is
# versioned by. Every worklist names the pack that produced
# it, and every candidate the pack it was proposed under.
# ---------------------------------------------------------------------


class StewardPack(BaseModel):
    """The steward pack: the population, aggregation and
    ranking rules a worklist's rows mean, the candidate and
    dual-control rules, and the role rule for the surface
    (mirrored by the access pack's grants — this statement
    is the meaning, that one is the enforcement)."""

    version: str
    read_roles: list[str]
    worklist_roles: list[str]
    propose_roles: list[str]
    decide_roles: list[str]
    population_rule: str
    aggregation_rule: str
    ranking_rule: str
    candidate_rule: str
    row_measures: list[str]


STEWARD_PACK_V1 = StewardPack(
    version=STEWARD_PACK_VERSION,
    read_roles=[
        ROLE_SAFETY_SCIENTIST,
        ROLE_SAFETY_REVIEWER,
        ROLE_MEDICAL_REVIEWER,
        ROLE_QUALITY_AUDITOR,
        ROLE_CASE_ADMIN,
    ],
    worklist_roles=[ROLE_SAFETY_SCIENTIST, ROLE_CASE_ADMIN],
    propose_roles=[ROLE_SAFETY_SCIENTIST, ROLE_CASE_ADMIN],
    decide_roles=[ROLE_SAFETY_SCIENTIST, ROLE_CASE_ADMIN],
    population_rule=(
        "Standing decisions are those of record at the "
        "as-of (coded on or before it, current under "
        "supersession), over all cases except those closed "
        "as duplicates and duplicate-linked cases — the "
        "primary case carries the report."
    ),
    aggregation_rule=(
        "One row per reported wording, grouped under the "
        "package's case/punctuation folding, per subject "
        "kind. Reports count distinct cases; decisions "
        "count standing uncoded decisions. Products are "
        "those of the same cases' standing coded "
        "suspect-product decisions; a report whose product "
        "stands uncoded is counted as unattributed. A row "
        "also states the wording's standing against the "
        "dictionary in force at the strike."
    ),
    ranking_rule=(
        "Most reports first, then earliest first seen, then "
        "the folded wording, then the subject kind — a "
        "deterministic order a steward can re-perform."
    ),
    candidate_rule=(
        "A candidate is proposed by a named steward against "
        "a worklist row whose reason is no_dictionary_entry, "
        "and is decided — confirmed or rejected — by a "
        "second named steward who is not its proposer. "
        "Confirmation enacts the mapping as a new dictionary "
        "version through the coding layer's addition rules, "
        "governing cases coded on or after the decision "
        "date; cases already coded keep the coding of "
        "record. An ambiguous wording takes no candidate: "
        "it is settled per case by a coder's manual "
        "resolution."
    ),
    row_measures=[
        "reports (distinct cases)",
        "decisions (standing uncoded decisions)",
        "first_seen / last_seen (decision dates)",
        "reason (no_dictionary_entry | ambiguous_match)",
        "candidates (for an ambiguous wording)",
        "products (coded product + report count)",
        "unattributed_reports",
        "dictionary_state (still_uncoded | codes_now)",
    ],
)


def load_steward_pack(version: str | None = None) -> StewardPack:
    """The loader, fail-closed like every pack loader in this
    repo: an unknown version is an error, never a silent fall
    back to v1."""
    if version is None or version == STEWARD_PACK_V1.version:
        return STEWARD_PACK_V1
    raise ValueError(f"unknown steward pack version: {version!r}")


# ---------------------------------------------------------------------
# The artefacts.
# ---------------------------------------------------------------------


class StewardProductRef(BaseModel):
    """One coded product a row's reports arrived with, and
    how many of the row's reports carried it."""

    product_code: str
    product_name: str
    reports: int


class StewardRow(BaseModel):
    """One uncoded wording's aggregate over the book. The
    wording is stated as first reported; grouping is under
    the package's text folding. `current_label` is the term
    or product code the wording codes to under the
    dictionary in force at the strike, when it codes at all
    now — the decisions of record stand regardless."""

    reported_wording: str
    subject_kind: str  # SUBJECT_EVENT | SUBJECT_PRODUCT
    reason: str  # UNCODED_NO_ENTRY | UNCODED_AMBIGUOUS
    candidates: list[str] = Field(default_factory=list)
    reports: int
    decisions: int
    first_seen: date
    last_seen: date
    products: list[StewardProductRef] = Field(default_factory=list)
    unattributed_reports: int = 0
    dictionary_state: str  # STATE_STILL_UNCODED | STATE_CODES_NOW
    current_label: str | None = None


class StewardWorklist(BaseModel):
    """One steward worklist as of a date: an insert-only
    artefact.

    `totals` states the page honestly before any row is
    read. `base_dictionary_version` is the dictionary the
    strike was judged against — the rows' dictionary
    states mean nothing without it. `record_fingerprint`
    hashes the sorted ids of the input artefacts the
    figures derive from; `worklist_id` is content-addressed
    over the as-of, the pack version, the base dictionary
    version and that fingerprint, so identical inputs mint
    the identical worklist and a moved input mints a new
    one linked by `supersedes`. `verified` is the
    re-performance mark: set only when `verify_worklist`'s
    second path agrees with every figure, and
    `verification_problems` names any figure it refused."""

    worklist_id: str
    pack_version: str
    as_of: date
    generated_by: str
    generated_on: date
    base_dictionary_version: str
    notice: str = WORKLIST_NOTICE
    totals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    rows: list[StewardRow] = Field(default_factory=list)
    record_fingerprint: str
    supersedes: str | None = None
    verified: bool = False
    verification_problems: list[str] = Field(default_factory=list)


class StewardCandidate(BaseModel):
    """One proposed mapping, recorded against a worklist
    row: an insert-only artefact. The mapping is stated in
    stewardship's own flat fields (an event mapping names
    its preferred term and SOC; a product mapping names the
    coded product the reported name should map to) —
    enactment rebuilds the coding layer's entry from them
    at confirmation time. A candidate's standing state is
    NOT a field here: it is derived from the decision
    artefacts, so this artefact stands exactly as proposed,
    forever."""

    candidate_id: str
    worklist_id: str
    reported_wording: str
    subject_kind: str  # SUBJECT_EVENT | SUBJECT_PRODUCT
    preferred_term: str | None = None
    soc: str | None = None
    product_code: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    dictionary_base_version: str
    proposed_by: str
    proposed_on: date
    note: str = ""
    notice: str = CANDIDATE_NOTICE


class StewardDecision(BaseModel):
    """One steward's decision on a candidate: an insert-only
    artefact. `minted_dictionary_version` is set on a
    confirmation — the new dictionary version the
    confirmation enacted, which names this candidate in its
    own note. A rejection mints nothing and says why."""

    decision_id: str
    candidate_id: str
    decision: str  # DECISION_CONFIRM | DECISION_REJECT
    decided_by: str
    decided_on: date
    rationale: str = ""
    minted_dictionary_version: str | None = None


def _count(value: int) -> dict[str, Any]:
    return {"value": value, "numerator": None, "denominator": None}


def _fingerprint(input_ids) -> str:
    blob = canonical_json({"artefact_ids": sorted(input_ids)})
    return hashlib.sha256(blob).hexdigest()


def _worklist_id(
    as_of: date,
    pack_version: str,
    base_dictionary_version: str,
    record_fingerprint: str,
) -> str:
    blob = canonical_json(
        {
            "as_of": as_of.isoformat(),
            "pack_version": pack_version,
            "base_dictionary_version": base_dictionary_version,
            "record_fingerprint": record_fingerprint,
        }
    )
    return "SWL-" + hashlib.sha256(blob).hexdigest()[:16]


def _candidate_id(candidate: StewardCandidate) -> str:
    blob = canonical_json(
        {
            "worklist_id": candidate.worklist_id,
            "reported_wording": normalise_text(candidate.reported_wording),
            "subject_kind": candidate.subject_kind,
            "preferred_term": candidate.preferred_term,
            "soc": candidate.soc,
            "product_code": candidate.product_code,
            "synonyms": sorted(candidate.synonyms),
            "proposed_by": candidate.proposed_by,
        }
    )
    return "SWC-" + hashlib.sha256(blob).hexdigest()[:16]


def _decision_id(candidate_id: str, decision: str, decided_by: str,
                 decided_on: date, seq: int) -> str:
    blob = canonical_json(
        {
            "candidate_id": candidate_id,
            "decision": decision,
            "decided_by": decided_by,
            "decided_on": decided_on.isoformat(),
            "seq": seq,
        }
    )
    return "SWD-" + hashlib.sha256(blob).hexdigest()[:16]


def _dictionary_state(wording: str, subject_kind: str, dictionary):
    """The wording's standing against the dictionary in
    force: (STATE_CODES_NOW, the label it codes to) when the
    coding layer's own tiered lookup takes it now, else
    (STATE_STILL_UNCODED, None)."""
    if subject_kind == SUBJECT_EVENT:
        entry, _path, _reason, _cands = lookup_event(
            wording, dictionary.event_entries
        )
        if entry is not None:
            return STATE_CODES_NOW, entry.preferred_term
    else:
        entry, _path, _reason, _cands = lookup_product(
            wording, dictionary.product_entries
        )
        if entry is not None:
            return STATE_CODES_NOW, entry.product_code
    return STATE_STILL_UNCODED, None


def _rank(rows: list[StewardRow]) -> list[StewardRow]:
    return sorted(
        rows,
        key=lambda r: (
            -r.reports,
            r.first_seen,
            normalise_text(r.reported_wording),
            r.subject_kind,
        ),
    )


def _assemble_rows(groups: dict, dictionary) -> list[StewardRow]:
    """Turn the fold's groups into ranked rows. Shared by
    the builder and verifier paths — the folds differ, the
    row arithmetic over a group does not."""
    rows: list[StewardRow] = []
    for (kind, _norm), group in groups.items():
        first = group["first_decision"]
        state, label = _dictionary_state(
            first.verbatim, kind, dictionary
        )
        products = [
            StewardProductRef(
                product_code=code,
                product_name=info["name"],
                reports=len(info["case_ids"]),
            )
            for code, info in sorted(group["products"].items())
        ]
        rows.append(
            StewardRow(
                reported_wording=first.verbatim,
                subject_kind=kind,
                reason=group["reason"],
                candidates=sorted(group["candidates"]),
                reports=len(group["case_ids"]),
                decisions=len(group["decision_ids"]),
                first_seen=min(group["coded_on"]),
                last_seen=max(group["coded_on"]),
                products=products,
                unattributed_reports=len(group["unattributed"]),
                dictionary_state=state,
                current_label=label,
            )
        )
    return _rank(rows)


def _new_group(decision) -> dict:
    return {
        "first_decision": decision,
        "reason": decision.uncoded_reason or UNCODED_NO_ENTRY,
        "candidates": set(decision.candidates),
        "decision_ids": [],
        "case_ids": set(),
        "coded_on": [],
        "products": {},
        "unattributed": set(),
    }


def _absorb(group: dict, decision, product) -> None:
    """Fold one standing uncoded decision (and its case's
    standing product decision, or None) into its group."""
    first = group["first_decision"]
    if (decision.coded_on, decision.decision_id) < (
        first.coded_on,
        first.decision_id,
    ):
        group["first_decision"] = decision
        group["reason"] = decision.uncoded_reason or UNCODED_NO_ENTRY
    group["candidates"].update(decision.candidates)
    group["decision_ids"].append(decision.decision_id)
    group["case_ids"].add(decision.case_id)
    group["coded_on"].append(decision.coded_on)
    if decision.subject_kind == SUBJECT_EVENT:
        if product is not None and product.status is CodingStatus.CODED:
            info = group["products"].setdefault(
                product.product_code,
                {"name": product.product_name, "case_ids": set()},
            )
            info["case_ids"].add(decision.case_id)
        else:
            group["unattributed"].add(decision.case_id)


# ---------------------------------------------------------------------
# The builder path: a per-case fold. Each population case is
# visited once; its standing decisions (the coding layer's
# current-decision walk) name its uncoded wordings; the
# groups accumulate from those per-case facts. The verifier
# path below folds the raw store globally instead, so a bug
# in this fold cannot ratify itself.
# ---------------------------------------------------------------------


def _open_tasks(store, as_of: date):
    from pv_cases.models import CodingReviewStatus

    return [
        t
        for t in store.list_coding_tasks(
            status=CodingReviewStatus.OPEN.value
        )
        if t.created_on <= as_of
    ]


def build_worklist_figures(store, coding, as_of: date):
    """Compute a worklist's totals and rows over the record.
    Returns (totals, rows, input artefact ids, the base
    dictionary version). This is the builder path;
    `verify_worklist` recomputes by a second path and the
    two must agree figure for figure."""
    dictionary = coding.dictionary(today=as_of)
    groups: dict[tuple[str, str], dict] = {}
    input_ids: set[str] = {f"dictionary:{dictionary.version}"}
    uncoded_cases: set[str] = set()
    for case in store.list_cases():
        if case.status in (
            CaseStatus.DUPLICATE_LINKED,
            CaseStatus.CLOSED_AS_DUPLICATE,
        ):
            continue
        decisions = [
            d
            for d in store.list_coding_decisions(case.case_id)
            if d.coded_on <= as_of
        ]
        standing = current_decisions(decisions)
        product = next(
            (d for d in standing if d.subject_kind == SUBJECT_PRODUCT),
            None,
        )
        if product is not None:
            input_ids.add(product.decision_id)
        for decision in standing:
            if decision.status is not CodingStatus.UNCODED:
                continue
            input_ids.add(decision.decision_id)
            uncoded_cases.add(case.case_id)
            key = (
                decision.subject_kind,
                normalise_text(decision.verbatim),
            )
            group = groups.setdefault(key, _new_group(decision))
            _absorb(group, decision, product)
    tasks = _open_tasks(store, as_of)
    input_ids.update(t.task_id for t in tasks)
    rows = _assemble_rows(groups, dictionary)
    totals = {
        "distinct_wordings": _count(len(rows)),
        "uncoded_decisions": _count(
            sum(len(g["decision_ids"]) for g in groups.values())
        ),
        "uncoded_cases": _count(len(uncoded_cases)),
        "open_coding_tasks": _count(len(tasks)),
    }
    return totals, rows, input_ids, dictionary.version


# ---------------------------------------------------------------------
# The verifier path: the same figures recomputed from the
# raw store by a global fold — every decision in the store
# is grouped by case and walked by hand over the superseded
# set, instead of the coding layer's per-case current walk.
# ---------------------------------------------------------------------


def _raw_worklist_figures(store, coding, as_of: date):
    dictionary = coding.dictionary(today=as_of)
    population = {
        c.case_id
        for c in store.list_cases()
        if c.status
        not in (CaseStatus.DUPLICATE_LINKED, CaseStatus.CLOSED_AS_DUPLICATE)
    }
    all_decisions = [
        d
        for d in store.list_coding_decisions()
        if d.coded_on <= as_of and d.case_id in population
    ]
    superseded = {d.supersedes_id for d in all_decisions if d.supersedes_id}
    standing = [d for d in all_decisions if d.decision_id not in superseded]
    product_of: dict[str, Any] = {}
    for decision in standing:
        if decision.subject_kind == SUBJECT_PRODUCT:
            product_of[decision.case_id] = decision

    groups: dict[tuple[str, str], dict] = {}
    input_ids: set[str] = {f"dictionary:{dictionary.version}"}
    uncoded_cases: set[str] = set()
    for decision in standing:
        product = product_of.get(decision.case_id)
        if product is not None:
            input_ids.add(product.decision_id)
        if decision.status is not CodingStatus.UNCODED:
            continue
        input_ids.add(decision.decision_id)
        uncoded_cases.add(decision.case_id)
        key = (
            decision.subject_kind,
            normalise_text(decision.verbatim),
        )
        group = groups.setdefault(key, _new_group(decision))
        _absorb(group, decision, product)
    tasks = _open_tasks(store, as_of)
    input_ids.update(t.task_id for t in tasks)
    rows = _assemble_rows(groups, dictionary)
    totals = {
        "distinct_wordings": _count(len(rows)),
        "uncoded_decisions": _count(
            sum(len(g["decision_ids"]) for g in groups.values())
        ),
        "uncoded_cases": _count(len(uncoded_cases)),
        "open_coding_tasks": _count(len(tasks)),
    }
    return totals, rows, input_ids, dictionary.version


def _row_key(row: StewardRow) -> str:
    return f"{row.subject_kind}|{normalise_text(row.reported_wording)}"


def verify_worklist(store, coding, worklist: StewardWorklist) -> list[str]:
    """Re-perform a worklist: recompute every figure from
    the raw store by the second path and compare, figure
    for figure, plus the record fingerprint and the base
    dictionary version. Returns the list of problems —
    empty only when the worklist is exactly what the record
    says. A figure the re-performance cannot reproduce is
    named, never summarised away. The payload also runs
    under the Round 11 PHI key scan: a forbidden key
    anywhere in it is a problem of its own."""
    problems: list[str] = []
    problems.extend(
        f"phi: {p}"
        for p in phi_problems(worklist.model_dump(mode="json"))
    )
    totals, rows, input_ids, base_version = _raw_worklist_figures(
        store, coding, worklist.as_of
    )
    if base_version != worklist.base_dictionary_version:
        problems.append(
            f"base_dictionary_version: worklist states "
            f"{worklist.base_dictionary_version}, the dictionary "
            f"in force re-performs {base_version} — strike a new "
            "worklist against the moved dictionary"
        )
    if _fingerprint(input_ids) != worklist.record_fingerprint:
        problems.append(
            "record_fingerprint: the input artefacts the record "
            "presents do not hash to the worklist's fingerprint"
        )
    for figure, want in totals.items():
        got = worklist.totals.get(figure)
        if got != want:
            problems.append(
                f"totals.{figure}: worklist states {got}, "
                f"the record re-performs {want}"
            )
    for figure in worklist.totals:
        if figure not in totals:
            problems.append(
                f"totals.{figure}: stated on the worklist, "
                "not derivable from the record"
            )
    expected = {_row_key(r): r for r in rows}
    stated = {_row_key(r): r for r in worklist.rows}
    for key, want in expected.items():
        got = stated.get(key)
        if got is None:
            problems.append(f"rows[{key}]: missing from the worklist")
            continue
        for field in (
            "reported_wording",
            "reason",
            "candidates",
            "reports",
            "decisions",
            "first_seen",
            "last_seen",
            "products",
            "unattributed_reports",
            "dictionary_state",
            "current_label",
        ):
            if getattr(got, field) != getattr(want, field):
                problems.append(
                    f"rows[{key}].{field}: worklist states "
                    f"{getattr(got, field)}, the record "
                    f"re-performs {getattr(want, field)}"
                )
    for key in stated:
        if key not in expected:
            problems.append(
                f"rows[{key}]: stated on the worklist, "
                "not derivable from the record"
            )
    if [ _row_key(r) for r in worklist.rows] != [
        _row_key(r) for r in rows
    ]:
        problems.append(
            "rows: the worklist's row order is not the pack's "
            "stated ranking over the re-performed rows"
        )
    return problems


# ---------------------------------------------------------------------
# The service.
# ---------------------------------------------------------------------


class StewardService:
    """Strikes worklists and works the candidate route over
    them. Holds the store and the coding service — the
    figures fold from artefacts of record, enactment goes
    through the coding layer's own addition, and the access
    control is bound by the wiring, like every act-recording
    service since Round 10."""

    def __init__(self, store, coding):
        self.store = store
        self.coding = coding
        self.access = None  # bound by the wiring (Round 10 pattern)

    # -- worklists ------------------------------------------------

    def generate(
        self,
        as_of: date,
        generated_by: str,
        *,
        principal: Principal | None = None,
        today: date | None = None,
    ) -> StewardWorklist:
        """Strike the worklist as of a date, as a named
        human's act. When the call carries a principal, the
        access pack judges it first — a stewardship act, the
        principal bound to the named generator — and a
        refusal is recorded in the denial log before it
        propagates. A worklist whose inputs have not moved
        returns the standing artefact; a moved record mints
        a new worklist superseding the standing one for the
        as-of. The stored worklist carries the verified mark
        only when the independent re-performance agrees with
        every figure."""
        if not is_named_human(generated_by):
            raise StewardError(
                "a worklist is struck by a named human steward"
            )
        guard_act(
            self,
            principal,
            ACT_GENERATE_STEWARD_WORKLIST,
            surface=SURFACE_DICTIONARY_STEWARDSHIP,
            target=as_of.isoformat(),
            actor=generated_by,
            on=today or as_of,
        )
        totals, rows, input_ids, base_version = build_worklist_figures(
            self.store, self.coding, as_of
        )
        fingerprint = _fingerprint(input_ids)
        worklist_id = _worklist_id(
            as_of, STEWARD_PACK_VERSION, base_version, fingerprint
        )
        standing = self.store.get_steward_worklist(worklist_id)
        if standing is not None:
            return standing
        same_as_of = [
            w
            for w in self.store.list_steward_worklists()
            if w.as_of == as_of
        ]
        supersedes = None
        if same_as_of:
            latest = sorted(
                same_as_of,
                key=lambda w: (w.generated_on, w.worklist_id),
            )[-1]
            supersedes = latest.worklist_id
        worklist = StewardWorklist(
            worklist_id=worklist_id,
            pack_version=STEWARD_PACK_VERSION,
            as_of=as_of,
            generated_by=generated_by,
            generated_on=today or as_of,
            base_dictionary_version=base_version,
            totals=totals,
            rows=rows,
            record_fingerprint=fingerprint,
            supersedes=supersedes,
        )
        problems = verify_worklist(self.store, self.coding, worklist)
        worklist.verified = not problems
        worklist.verification_problems = problems
        self.store.add_steward_worklist(worklist)
        return worklist

    def get_worklist(self, worklist_id: str) -> StewardWorklist | None:
        return self.store.get_steward_worklist(worklist_id)

    def worklists(self) -> list[StewardWorklist]:
        return self.store.list_steward_worklists()

    def verify(self, worklist: StewardWorklist) -> list[str]:
        return verify_worklist(self.store, self.coding, worklist)

    # -- candidates -------------------------------------------------

    def propose(
        self,
        worklist_id: str,
        reported_wording: str,
        subject_kind: str,
        proposed_by: str,
        *,
        preferred_term: str | None = None,
        soc: str | None = None,
        product_code: str | None = None,
        synonyms: list[str] | None = None,
        note: str = "",
        principal: Principal | None = None,
        today: date | None = None,
    ) -> StewardCandidate:
        """Record a proposed mapping against a worklist row,
        as a named steward's act. The proposal is judged
        against the worklist it names (the wording must be
        one of its rows, uncoded for want of an entry) and
        against the dictionary in force (a wording the
        dictionary already takes needs no mapping). Nothing
        is enacted: the candidate stands as an artefact
        until a second steward decides it."""
        if not is_named_human(proposed_by):
            raise StewardError(
                "a candidate is proposed by a named human steward"
            )
        guard_act(
            self,
            principal,
            ACT_PROPOSE_STEWARD_MAPPING,
            surface=SURFACE_DICTIONARY_STEWARDSHIP,
            target=worklist_id,
            actor=proposed_by,
            on=today,
        )
        worklist = self.store.get_steward_worklist(worklist_id)
        if worklist is None:
            raise StewardError(f"no such worklist: {worklist_id}")
        row = next(
            (
                r
                for r in worklist.rows
                if r.subject_kind == subject_kind
                and normalise_text(r.reported_wording)
                == normalise_text(reported_wording)
            ),
            None,
        )
        if row is None:
            raise StewardError(
                f"{reported_wording!r} is not an uncoded "
                f"{subject_kind} wording on worklist {worklist_id} "
                "— a candidate is recorded against a worklist row"
            )
        if row.reason == UNCODED_AMBIGUOUS:
            raise StewardError(
                f"{reported_wording!r} stands ambiguous — it is "
                "already a recorded synonym of more than one "
                "term, so the dictionary cannot take it as a new "
                "entry; ambiguous wordings are settled per case "
                "by a coder's manual resolution"
            )
        dictionary = self.coding.dictionary(
            today=today or date.today()
        )
        state, label = _dictionary_state(
            row.reported_wording, subject_kind, dictionary
        )
        if state == STATE_CODES_NOW:
            raise StewardError(
                f"{row.reported_wording!r} already codes to "
                f"{label!r} under dictionary {dictionary.version} "
                "— there is nothing to propose"
            )
        if subject_kind == SUBJECT_EVENT:
            if not (preferred_term and preferred_term.strip()):
                raise StewardError(
                    "an event candidate names a preferred term"
                )
            if not (soc and soc.strip()):
                raise StewardError(
                    "an event candidate names its system organ class"
                )
            product_code = None
        elif subject_kind == SUBJECT_PRODUCT:
            if not (product_code and product_code.strip()):
                raise StewardError(
                    "a product candidate names the coded product "
                    "the reported name maps to"
                )
            known = next(
                (
                    e
                    for e in dictionary.product_entries
                    if e.product_code == product_code
                ),
                None,
            )
            if known is None:
                raise StewardError(
                    f"{product_code!r} is not a coded product in "
                    f"dictionary {dictionary.version} — a candidate "
                    "maps a new reported name to a product the "
                    "dictionary already codes"
                )
            preferred_term = None
            soc = None
        else:
            raise StewardError(
                f"unknown subject kind: {subject_kind!r}"
            )
        candidate = StewardCandidate(
            candidate_id="",
            worklist_id=worklist_id,
            reported_wording=row.reported_wording,
            subject_kind=subject_kind,
            preferred_term=preferred_term,
            soc=soc,
            product_code=product_code,
            synonyms=list(synonyms or []),
            dictionary_base_version=dictionary.version,
            proposed_by=proposed_by,
            proposed_on=today or date.today(),
            note=note,
        )
        candidate.candidate_id = _candidate_id(candidate)
        standing = self.store.get_steward_candidate(candidate.candidate_id)
        if standing is not None:
            return standing
        self.store.add_steward_candidate(candidate)
        return candidate

    def decide(
        self,
        candidate_id: str,
        decision: str,
        decided_by: str,
        rationale: str = "",
        *,
        principal: Principal | None = None,
        today: date | None = None,
    ) -> StewardDecision:
        """Decide a candidate, as a second named steward's
        act. Dual control is judged here, in code: the
        decider may not be the proposer, under the repo's
        text folding. A confirmation enacts the mapping
        through the coding layer's collision-checked
        addition BEFORE the decision is recorded — an
        enactment the dictionary refuses records no decision
        and moves nothing. The minted version governs cases
        coded on or after the decision date; no stored
        decision is touched."""
        if decision not in (DECISION_CONFIRM, DECISION_REJECT):
            raise StewardError(
                f"a steward decision is {DECISION_CONFIRM!r} or "
                f"{DECISION_REJECT!r}, not {decision!r}"
            )
        if not is_named_human(decided_by):
            raise StewardError(
                "a candidate is decided by a named human steward"
            )
        guard_act(
            self,
            principal,
            ACT_DECIDE_STEWARD_MAPPING,
            surface=SURFACE_DICTIONARY_STEWARDSHIP,
            target=candidate_id,
            actor=decided_by,
            on=today,
        )
        candidate = self.store.get_steward_candidate(candidate_id)
        if candidate is None:
            raise StewardError(f"no such candidate: {candidate_id}")
        existing = self.store.list_steward_decisions(candidate_id)
        if existing:
            standing = sorted(
                existing, key=lambda d: (d.decided_on, d.decision_id)
            )[0]
            raise StewardError(
                f"candidate {candidate_id} is already "
                f"{standing.decision}ed by {standing.decided_by} "
                f"on {standing.decided_on} — decided work does "
                "not move"
            )
        if normalise_text(decided_by) == normalise_text(
            candidate.proposed_by
        ):
            raise StewardError(
                "a candidate is decided by a second steward — "
                f"{candidate.proposed_by} proposed it and may "
                "not decide it"
            )
        decided_on = today or date.today()
        minted = None
        if decision == DECISION_CONFIRM:
            from pv_cases.coding import CodingError

            if candidate.subject_kind == SUBJECT_EVENT:
                entry: Any = DictionaryEntry(
                    verbatim=candidate.reported_wording,
                    preferred_term=candidate.preferred_term or "",
                    soc=candidate.soc or "",
                    synonyms=list(candidate.synonyms),
                )
            else:
                entry = ProductDictionaryEntry(
                    verbatim_name=candidate.reported_wording,
                    product_code=candidate.product_code or "",
                    product_name="",
                    active_substance="",
                    synonyms=list(candidate.synonyms),
                )
            try:
                version = self.coding.propose_addition(
                    decided_by,
                    decided_on,
                    candidate.subject_kind,
                    entry,
                    note=(
                        f"Dictionary stewardship: candidate "
                        f"{candidate.candidate_id} confirmed — "
                        f"proposed by {candidate.proposed_by} "
                        f"against worklist {candidate.worklist_id}; "
                        "effective for cases coded on or after "
                        f"{decided_on.isoformat()}."
                    ),
                )
            except CodingError as exc:
                raise StewardError(
                    f"the dictionary refuses the enactment: {exc}"
                ) from exc
            minted = version.version
        record = StewardDecision(
            decision_id=_decision_id(
                candidate_id, decision, decided_by, decided_on, 0
            ),
            candidate_id=candidate_id,
            decision=decision,
            decided_by=decided_by,
            decided_on=decided_on,
            rationale=rationale,
            minted_dictionary_version=minted,
        )
        self.store.add_steward_decision(record)
        return record

    def get_candidate(
        self, candidate_id: str
    ) -> StewardCandidate | None:
        return self.store.get_steward_candidate(candidate_id)

    def candidates(self) -> list[StewardCandidate]:
        return self.store.list_steward_candidates()

    def decision_for(
        self, candidate_id: str
    ) -> StewardDecision | None:
        """The standing decision on a candidate, if any: the
        decision artefacts are the record, and at most one
        can stand — the service refuses a second."""
        decisions = self.store.list_steward_decisions(candidate_id)
        if not decisions:
            return None
        return sorted(
            decisions, key=lambda d: (d.decided_on, d.decision_id)
        )[0]

    def candidate_state(self, candidate_id: str) -> str:
        """A candidate's standing state, derived from the
        decision artefacts: proposed until a decision stands,
        then what the decision says."""
        record = self.decision_for(candidate_id)
        if record is None:
            return CANDIDATE_PROPOSED
        return (
            CANDIDATE_CONFIRMED
            if record.decision == DECISION_CONFIRM
            else CANDIDATE_REJECTED
        )
