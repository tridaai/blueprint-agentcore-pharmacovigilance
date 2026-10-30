"""Draft-vs-final review measurement — Round 18.

The design brief's eighth law has stood unbuilt through
seventeen rounds: *draft-vs-final diffs are recorded per
case (rubber-stamp measurement)*. Rounds 1–17 built the
record a reviewer decides — the summaries the agent
assembles, the dual-control gate that binds one of them
by content hash, the seal that freezes it — but nothing
anywhere measured the distance between the first summary
the agent drafted and the summary the reviewers actually
approved. A book where every approval lands on the first
draft, byte for byte, may be a book of stable cases — or
a book nobody is reading. Until this round the system
could not tell the difference, because the comparison
existed nowhere. This module is that comparison, built
under the same disciplines as everything it reads:

- **The two ends are artefacts of record, identified the
  way the record identifies them.** The draft is the
  first summary ever assembled for the case. The final
  is the summary the completing dual-control accept acts
  bound — identified by the review layer's own rule: a
  summary's version is its content hash, so the final is
  stated by the id the last completing act bound and
  verified against that hash. A case whose review has
  not completed has no final, and a review that bound no
  summary approved no draft: both absences refuse the
  measurement by name rather than measuring nothing.
- **The diff is computed in code under a versioned
  pack.** A statement is its text and its citations —
  the same content the review layer hashes. Statements
  pair first by identical content (unchanged), then by
  identical text with different citations (citation-
  changed); what is left over left the draft (dropped)
  or entered the final (added). A reworded statement
  therefore reads as one dropped plus one added — the
  pack states this plainly rather than guessing which
  new sentence "is" an old one. Statement order is not
  identity: a reordered draft is an unchanged draft.
- **Verbatim is a measurement, not an accusation.** When
  the final's content hash equals the draft's, the gate
  approved the agent's first draft exactly as written,
  and the measurement says so. On a stable case that is
  the correct outcome; the figure exists so the *book's*
  rate is visible — the roll-up states how many measured
  cases were approved verbatim, with its denominator on
  the figure, like every rate in this system.
- **The artefact carries hashes and counts, never
  text.** A changed statement is named by the sha256 of
  its content, never by its words: the measurement is
  evidence about the review, and the narrative it
  measures stays in the summaries it points at.
- **Measurements are insert-only artefacts**
  (migration 0014), content-addressed over the case,
  the two content hashes, a fingerprint of the input
  artefacts and the pack version. Re-measuring over an
  unmoved record returns the standing artefact;
  re-measuring after the record moves mints a new one
  linked by `supersedes`. Every measurement is
  re-performed before it is believed: `verify_measurement`
  re-derives the completing acts, the two summaries and
  the diff from raw store reads by a second walk and
  compares figure for figure.
- **A measurement writes nothing to the case.** It is
  an oversight artefact *about* the record, struck over
  it — not a case artefact in it. It binds no part of a
  seal's inventory, appears in no export, and striking
  one over a sealed case leaves the sealed file and its
  package hash exactly as they stood (graded). Sealing
  neither requires nor forbids a measurement.
- **Access follows the Round 10 pack.** The measurement
  is a case-scoped read on its own surface (the
  case-working roles and the quality auditor; not
  intake, not case admin), existence-hidden like every
  case surface, and striking one is the safety
  scientist's named act — the auditor reads measurements
  and strikes none.
- **A measurement is an internal review-quality
  artefact.** It states so on its face. It is not a
  regulatory submission, and nothing in this system is
  ever submitted to a regulator.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field

from pv_cases.access import (
    ACT_MEASURE_DRAFT_FINAL,
    ROLE_MEDICAL_REVIEWER,
    ROLE_QUALITY_AUDITOR,
    ROLE_SAFETY_REVIEWER,
    ROLE_SAFETY_SCIENTIST,
    SURFACE_DRAFT_FINAL,
    Principal,
    guard_act,
)
from pv_cases.assessment import current_assessment_of
from pv_cases.evidence import canonical_json
from pv_cases.models import CaseSummary, SummaryStatement
from pv_cases.review import (
    REVIEW_ACCEPT_DECISIONS,
    REVIEW_REJECT,
    REVIEW_ROLES,
    REVIEW_STATE_REVIEWED,
    ReviewService,
    artefact_hash,
    summary_content_hash,
)

DRAFT_FINAL_PACK_VERSION = "2026.10.1"

NOTICE = (
    "Internal review-quality artefact: the distance, "
    "computed in code, between the first summary the agent "
    "assembled for this case and the summary its "
    "dual-control review approved. A verbatim approval is "
    "a measurement, not an accusation — on a stable case "
    "the first draft should stand. It is not a regulatory "
    "submission, and nothing in this system is ever "
    "submitted to a regulator."
)


class DraftFinalError(RuntimeError):
    """A measurement that cannot be struck as asked — the
    case does not exist, its review has not completed, or
    its review bound no summary. Named, never guessed
    around."""


# ---------------------------------------------------------------------
# The draft-final pack — the definitions a figure's meaning is
# versioned by. Every measurement names the pack that produced it.
# ---------------------------------------------------------------------


class DraftFinalPack(BaseModel):
    """The draft-final pack: which summaries are the draft
    and the final, what a statement is, how statements
    pair across the two, what verbatim means, and the role
    rule for the surface (mirrored by the access pack's
    grants — this statement is the meaning, that one is
    the enforcement)."""

    version: str
    read_roles: list[str]
    generate_roles: list[str]
    draft_rule: str
    final_rule: str
    statement_rule: str
    matching_rule: str
    verbatim_rule: str


DRAFT_FINAL_PACK_V1 = DraftFinalPack(
    version=DRAFT_FINAL_PACK_VERSION,
    read_roles=[
        ROLE_SAFETY_SCIENTIST,
        ROLE_SAFETY_REVIEWER,
        ROLE_MEDICAL_REVIEWER,
        ROLE_QUALITY_AUDITOR,
    ],
    generate_roles=[ROLE_SAFETY_SCIENTIST],
    draft_rule=(
        "The draft is the first summary ever assembled for "
        "the case, in assembly order."
    ),
    final_rule=(
        "The final is the summary the case's completing "
        "dual-control accept acts bound, identified by the "
        "id the last completing act bound and verified "
        "against the bound content hash. A case whose "
        "review has not completed has no final; a review "
        "that bound no summary approved no draft."
    ),
    statement_rule=(
        "A statement is its text and its citations — the "
        "content the review layer hashes. A statement's "
        "identity in this artefact is the sha256 of that "
        "content: the measurement carries hashes and "
        "counts, never the text itself."
    ),
    matching_rule=(
        "Statements pair first by identical content "
        "(unchanged), then by identical text with "
        "different citations (citation-changed). What is "
        "left over left the draft (dropped) or entered "
        "the final (added), counted as a multiset — a "
        "statement carried twice pairs twice. A reworded "
        "statement reads as one dropped plus one added: "
        "the pack does not guess which new sentence is an "
        "old one. Statement order is not identity."
    ),
    verbatim_rule=(
        "Verbatim means the final's content hash equals "
        "the draft's: the gate approved the agent's first "
        "draft exactly as written, hash for hash."
    ),
)


def load_draft_final_pack(version: str | None = None) -> DraftFinalPack:
    """The loader, fail-closed like every pack loader in this
    repo: an unknown version is an error, never a silent fall
    back to v1."""
    if version is None or version == DRAFT_FINAL_PACK_V1.version:
        return DRAFT_FINAL_PACK_V1
    raise ValueError(f"unknown draft-final pack version: {version!r}")


# ---------------------------------------------------------------------
# The measurement artefact.
# ---------------------------------------------------------------------


class StatementChange(BaseModel):
    """One statement that did not cross from draft to final
    unchanged, named by the sha256 of its content — the
    draft-side statement for `dropped` and
    `citation_changed`, the final-side statement for
    `added`. The text itself is never carried."""

    statement_hash: str
    kind: str  # citation_changed | dropped | added


class DraftFinalMeasurement(BaseModel):
    """One case's draft-vs-final measurement: an insert-only
    artefact.

    `assemblies` counts the summaries the case had been
    given when its review completed — every stored summary
    up to and including the bound final, in assembly
    order — and `distinct_drafts` how many different
    narratives those assemblies carried. The counts are
    the diff itself, computed under the pack.
    `record_fingerprint` hashes the sorted ids of the
    input artefacts the figures derive from (the summaries
    counted and the completing review acts);
    `measurement_id` is content-addressed over the case,
    the two content hashes, that fingerprint and the pack
    version, so identical inputs mint the identical
    measurement and a moved input mints a new one linked
    by `supersedes`. `verified` is the re-performance
    mark: it is set only when `verify_measurement`'s
    second path agrees with every figure, and
    `verification_problems` names any figure it refused."""

    measurement_id: str
    case_id: str
    pack_version: str
    measured_by: str
    measured_on: date
    notice: str = NOTICE
    draft_summary_id: str
    draft_content_hash: str
    final_summary_id: str
    final_content_hash: str
    completing_act_ids: list[str] = Field(default_factory=list)
    assemblies: int
    distinct_drafts: int
    draft_statements: int
    final_statements: int
    unchanged: int
    citation_changed: int
    added: int
    dropped: int
    verbatim: bool
    changes: list[StatementChange] = Field(default_factory=list)
    record_fingerprint: str
    supersedes: str | None = None
    verified: bool = False
    verification_problems: list[str] = Field(default_factory=list)


def _statement_hash(statement: SummaryStatement) -> str:
    """The identity of one statement in this artefact:
    sha256 over the canonical JSON of its content (text +
    citations) — the same content the review layer hashes
    a whole summary by."""
    return hashlib.sha256(
        canonical_json(statement.model_dump(mode="json"))
    ).hexdigest()


def _statement_key(statement: SummaryStatement):
    return (
        statement.text,
        tuple(
            (c.evidence_item_id, c.field)
            for c in statement.citations
        ),
    )


def diff_statements(
    draft: list[SummaryStatement], final: list[SummaryStatement]
) -> dict[str, Any]:
    """The pack's diff, as a pure function of the two
    statement lists: pair by identical content, then by
    identical text, and count what is left. Returns the
    counts and the changed statements' hashes. This is
    the one piece of arithmetic both measurement paths
    share — the paths differ in how they *select* the
    draft and the final from the record, never in what a
    diff is."""
    remaining = list(final)
    leftover: list[SummaryStatement] = []
    unchanged = 0
    for statement in draft:
        key = _statement_key(statement)
        hit = next(
            (
                i
                for i, other in enumerate(remaining)
                if _statement_key(other) == key
            ),
            None,
        )
        if hit is None:
            leftover.append(statement)
        else:
            remaining.pop(hit)
            unchanged += 1
    citation_changed = 0
    changes: list[StatementChange] = []
    dropped: list[SummaryStatement] = []
    for statement in leftover:
        hit = next(
            (
                i
                for i, other in enumerate(remaining)
                if other.text == statement.text
            ),
            None,
        )
        if hit is None:
            dropped.append(statement)
        else:
            remaining.pop(hit)
            citation_changed += 1
            changes.append(
                StatementChange(
                    statement_hash=_statement_hash(statement),
                    kind="citation_changed",
                )
            )
    for statement in dropped:
        changes.append(
            StatementChange(
                statement_hash=_statement_hash(statement),
                kind="dropped",
            )
        )
    for statement in remaining:
        changes.append(
            StatementChange(
                statement_hash=_statement_hash(statement),
                kind="added",
            )
        )
    changes.sort(key=lambda c: (c.kind, c.statement_hash))
    return {
        "draft_statements": len(draft),
        "final_statements": len(final),
        "unchanged": unchanged,
        "citation_changed": citation_changed,
        "added": len(remaining),
        "dropped": len(dropped),
        "changes": changes,
    }


def _fingerprint(input_ids) -> str:
    blob = canonical_json({"artefact_ids": sorted(input_ids)})
    return hashlib.sha256(blob).hexdigest()


def _measurement_id(
    case_id: str,
    draft_content_hash: str,
    final_content_hash: str,
    record_fingerprint: str,
    pack_version: str,
) -> str:
    blob = canonical_json(
        {
            "case_id": case_id,
            "draft_content_hash": draft_content_hash,
            "final_content_hash": final_content_hash,
            "record_fingerprint": record_fingerprint,
            "pack_version": pack_version,
        }
    )
    return "DFM-" + hashlib.sha256(blob).hexdigest()[:16]


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    """A rate as a stated figure: four places, HALF_UP, with
    its population on the figure — or `no_cases` when there
    is nothing to divide by, the oversight pack's stated
    empty-population value."""
    if denominator == 0:
        return {
            "value": "no_cases",
            "numerator": numerator,
            "denominator": denominator,
        }
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return {
        "value": format(value, ".4f"),
        "numerator": numerator,
        "denominator": denominator,
    }


# ---------------------------------------------------------------------
# The builder path: the draft and the final selected through the
# review layer's derived view — the completing acts are the ones
# the gate itself counts, over the bindings the gate itself
# derived.
# ---------------------------------------------------------------------


def _figures_from_selection(
    case_id: str,
    summaries: list[CaseSummary],
    bound_id: str,
    bound_hash: str,
    act_ids: list[str],
) -> dict[str, Any]:
    """Assemble the measurement figures from a selection:
    the case's summaries in assembly order, the bound
    final's id and content hash, and the completing acts.
    Shared by both paths — the paths differ in how the
    selection is derived, not in what the figures are."""
    draft = summaries[0]
    final = next(s for s in summaries if s.summary_id == bound_id)
    if summary_content_hash(final) != bound_hash:
        raise DraftFinalError(
            f"case {case_id}: the summary the review bound "
            f"({bound_id}) does not carry the bound content hash"
        )
    counted = summaries[
        : [s.summary_id for s in summaries].index(bound_id) + 1
    ]
    figures = diff_statements(draft.statements, final.statements)
    input_ids = [s.summary_id for s in counted] + list(act_ids)
    return {
        "draft_summary_id": draft.summary_id,
        "draft_content_hash": summary_content_hash(draft),
        "final_summary_id": final.summary_id,
        "final_content_hash": bound_hash,
        "completing_act_ids": sorted(act_ids),
        "assemblies": len(counted),
        "distinct_drafts": len(
            {summary_content_hash(s) for s in counted}
        ),
        "verbatim": summary_content_hash(draft) == bound_hash,
        "record_fingerprint": _fingerprint(input_ids),
        **figures,
    }


def build_measurement_figures(store, review: ReviewService, case_id: str):
    """Select the draft and the final through the review
    service's derived view and compute the figures. Raises
    DraftFinalError naming the absence when the case has
    no approved summary to measure."""
    view = review.review_for_case(case_id)
    if view.state != REVIEW_STATE_REVIEWED:
        raise DraftFinalError(
            f"case {case_id}: the review has not completed "
            f"(the case stands {view.state}) — there is no "
            "approved summary to measure the draft against"
        )
    accepts = [
        entry.act
        for entry in view.acts
        if not entry.stale
        and entry.act.decision in REVIEW_ACCEPT_DECISIONS
    ]
    bound: dict[str, tuple[str, str]] = {}
    for act in accepts:
        for binding in act.bindings:
            if binding.artefact_kind == "summary":
                bound[act.act_id] = (
                    binding.artefact_id,
                    binding.artefact_hash,
                )
    if not bound:
        raise DraftFinalError(
            f"case {case_id}: the completing review bound no "
            "summary — it approved no draft, so there is "
            "nothing to measure"
        )
    hashes = {h for _i, h in bound.values()}
    if len(hashes) != 1:
        raise DraftFinalError(
            f"case {case_id}: the completing acts bound "
            "different summary contents — the gate's own "
            "record does not name one final"
        )
    # The stated final is the binding of the completing
    # accept recorded last (by recorded date, then act
    # id): the act that completed the gate decided the
    # narrative as it then stood.
    by_act = {act.act_id: act for act in accepts}
    ordered = sorted(
        bound,
        key=lambda act_id: (
            by_act[act_id].recorded_on,
            act_id,
        ),
    )
    bound_id = bound[ordered[-1]][0]
    summaries = store.list_summaries(case_id)
    if not summaries:
        raise DraftFinalError(
            f"case {case_id}: the review bound a summary the "
            "store does not hold"
        )
    return _figures_from_selection(
        case_id,
        summaries,
        bound_id,
        hashes.pop(),
        [act.act_id for act in accepts],
    )


# ---------------------------------------------------------------------
# The verifier path: the same selection re-derived from raw store
# reads — the acts' currency re-walked by hand over the raw
# artefact lists, instead of the review service's derived view —
# so a bug in the builder's selection cannot ratify itself.
# ---------------------------------------------------------------------


def _raw_selection(store, case_id: str):
    """Re-derive (bound_id, bound_hash, act_ids) from the raw
    store, or raise DraftFinalError naming the absence,
    exactly as the builder path does."""
    summaries = store.list_summaries(case_id)
    # The record a review would decide now, from raw lists:
    # the standing assessment (nothing supersedes it), the
    # standing coding decisions, the latest summary's
    # content.
    assessment = current_assessment_of(store.list_assessments(case_id))
    decisions = store.list_coding_decisions(case_id)
    superseded = {d.supersedes_id for d in decisions if d.supersedes_id}
    standing = [d for d in decisions if d.decision_id not in superseded]
    current: dict[str, dict[str, str]] = {}
    if assessment is not None:
        current["assessment"] = {
            assessment.assessment_id: artefact_hash(
                assessment.model_dump(mode="json")
            )
        }
    if standing:
        current["coding_decision"] = {
            d.decision_id: artefact_hash(d.model_dump(mode="json"))
            for d in standing
        }
    if summaries:
        current["summary"] = {
            summaries[-1].summary_id: summary_content_hash(summaries[-1])
        }

    def is_current(act) -> bool:
        bound: dict[str, dict[str, str]] = {}
        for binding in act.bindings:
            bound.setdefault(binding.artefact_kind, {})[
                binding.artefact_id
            ] = binding.artefact_hash
        for kind in set(bound) | set(current):
            b_map = bound.get(kind, {})
            n_map = current.get(kind, {})
            if kind == "summary":
                if set(b_map.values()) != set(n_map.values()):
                    return False
                continue
            if b_map != n_map:
                return False
        return True

    acts = store.list_review_acts(case_id)
    current_acts = [a for a in acts if is_current(a)]
    if any(a.decision == REVIEW_REJECT for a in current_acts):
        raise DraftFinalError(
            f"case {case_id}: the review has not completed "
            "(a current reject holds the case) — there is no "
            "approved summary to measure the draft against"
        )
    accepts = [
        a for a in current_acts if a.decision in REVIEW_ACCEPT_DECISIONS
    ]
    roles = {a.role for a in accepts}
    if not set(REVIEW_ROLES) <= roles:
        raise DraftFinalError(
            f"case {case_id}: the review has not completed "
            "(dual control is not satisfied) — there is no "
            "approved summary to measure the draft against"
        )
    bound: dict[str, tuple[str, str]] = {}
    for act in accepts:
        for binding in act.bindings:
            if binding.artefact_kind == "summary":
                bound[act.act_id] = (
                    binding.artefact_id,
                    binding.artefact_hash,
                )
    if not bound:
        raise DraftFinalError(
            f"case {case_id}: the completing review bound no "
            "summary — it approved no draft, so there is "
            "nothing to measure"
        )
    hashes = {h for _i, h in bound.values()}
    if len(hashes) != 1:
        raise DraftFinalError(
            f"case {case_id}: the completing acts bound "
            "different summary contents — the gate's own "
            "record does not name one final"
        )
    by_act = {act.act_id: act for act in accepts}
    ordered = sorted(
        bound,
        key=lambda act_id: (by_act[act_id].recorded_on, act_id),
    )
    return bound[ordered[-1]][0], hashes.pop(), [
        act.act_id for act in accepts
    ]


def _raw_measurement_figures(store, case_id: str):
    summaries = store.list_summaries(case_id)
    bound_id, bound_hash, act_ids = _raw_selection(store, case_id)
    if not summaries:
        raise DraftFinalError(
            f"case {case_id}: the review bound a summary the "
            "store does not hold"
        )
    return _figures_from_selection(
        case_id, summaries, bound_id, bound_hash, act_ids
    )


def verify_measurement(store, measurement: DraftFinalMeasurement) -> list[str]:
    """Re-perform a measurement: re-derive the selection and
    the diff from the raw store by the second path and
    compare, figure for figure. Returns the list of
    problems — empty only when the measurement is exactly
    what the record says. A figure the re-performance
    cannot reproduce is named, never summarised away."""
    problems: list[str] = []
    try:
        want = _raw_measurement_figures(store, measurement.case_id)
    except DraftFinalError as exc:
        return [f"the record no longer supports a measurement: {exc}"]
    for field in (
        "draft_summary_id",
        "draft_content_hash",
        "final_summary_id",
        "final_content_hash",
        "completing_act_ids",
        "assemblies",
        "distinct_drafts",
        "draft_statements",
        "final_statements",
        "unchanged",
        "citation_changed",
        "added",
        "dropped",
        "verbatim",
        "record_fingerprint",
    ):
        got = getattr(measurement, field)
        if got != want[field]:
            problems.append(
                f"{field}: measurement states {got}, "
                f"the record re-performs {want[field]}"
            )
    want_changes = [
        (c.kind, c.statement_hash) for c in want["changes"]
    ]
    got_changes = [
        (c.kind, c.statement_hash) for c in measurement.changes
    ]
    if got_changes != want_changes:
        problems.append(
            f"changes: measurement states {got_changes}, "
            f"the record re-performs {want_changes}"
        )
    return problems


def book_rollup(store) -> dict[str, Any]:
    """The book's rubber-stamp figure, folded at read time
    from the standing measurement of every measured case:
    how many cases stand measured, how many of them the
    gate approved verbatim on the first draft, and the
    rate with its denominator on the figure. A fold, not
    an artefact — the measurements are the evidence; this
    is their count, recomputed on every read."""
    standing: dict[str, DraftFinalMeasurement] = {}
    for measurement in store.list_draft_final_measurements():
        current = standing.get(measurement.case_id)
        if current is None or (
            measurement.measured_on,
            measurement.measurement_id,
        ) > (current.measured_on, current.measurement_id):
            standing[measurement.case_id] = measurement
    measured = len(standing)
    verbatim = sum(1 for m in standing.values() if m.verbatim)
    return {
        "measured_cases": measured,
        "verbatim_approvals": verbatim,
        "changed_approvals": measured - verbatim,
        "verbatim_rate": _rate(verbatim, measured),
    }


# ---------------------------------------------------------------------
# The service.
# ---------------------------------------------------------------------


class DraftFinalService:
    """Strikes and reads draft-vs-final measurements. Holds
    the store and the review service — the figures derive
    from the record those two hold — and the access
    control is bound by the wiring, like every
    act-recording service since Round 10."""

    def __init__(self, store, review: ReviewService | None = None):
        self.store = store
        self.review = review
        self.access = None  # bound by the wiring (Round 10 pattern)

    def measure(
        self,
        case_id: str,
        measured_by: str,
        *,
        principal: Principal | None = None,
        today: date | None = None,
    ) -> DraftFinalMeasurement:
        """Strike the case's draft-vs-final measurement as a
        named human's act. When the call carries a
        principal, the access pack judges it first — the
        safety scientist's act, the principal bound to the
        named measurer — and a refusal is recorded in the
        denial log before it propagates. A measurement
        whose inputs have not moved returns the standing
        artefact; a moved record mints a new measurement
        superseding the case's standing one. The stored
        measurement carries the verified mark only when
        the independent re-performance agrees with every
        figure."""
        guard_act(
            self,
            principal,
            ACT_MEASURE_DRAFT_FINAL,
            surface=SURFACE_DRAFT_FINAL,
            target=case_id,
            actor=measured_by,
            on=today,
        )
        if self.store.get_case(case_id) is None:
            raise DraftFinalError(f"no such case: {case_id}")
        if self.review is None:
            raise DraftFinalError(
                "the measurement service is not wired to the "
                "review layer"
            )
        figures = build_measurement_figures(self.store, self.review, case_id)
        fingerprint = figures["record_fingerprint"]
        measurement_id = _measurement_id(
            case_id,
            figures["draft_content_hash"],
            figures["final_content_hash"],
            fingerprint,
            DRAFT_FINAL_PACK_VERSION,
        )
        standing = self.store.get_draft_final_measurement(measurement_id)
        if standing is not None:
            return standing
        prior = [
            m
            for m in self.store.list_draft_final_measurements(case_id)
        ]
        supersedes = None
        if prior:
            latest = sorted(
                prior, key=lambda m: (m.measured_on, m.measurement_id)
            )[-1]
            supersedes = latest.measurement_id
        measurement = DraftFinalMeasurement(
            measurement_id=measurement_id,
            case_id=case_id,
            pack_version=DRAFT_FINAL_PACK_VERSION,
            measured_by=measured_by,
            measured_on=today or date.today(),
            draft_summary_id=figures["draft_summary_id"],
            draft_content_hash=figures["draft_content_hash"],
            final_summary_id=figures["final_summary_id"],
            final_content_hash=figures["final_content_hash"],
            completing_act_ids=figures["completing_act_ids"],
            assemblies=figures["assemblies"],
            distinct_drafts=figures["distinct_drafts"],
            draft_statements=figures["draft_statements"],
            final_statements=figures["final_statements"],
            unchanged=figures["unchanged"],
            citation_changed=figures["citation_changed"],
            added=figures["added"],
            dropped=figures["dropped"],
            verbatim=figures["verbatim"],
            changes=figures["changes"],
            record_fingerprint=fingerprint,
            supersedes=supersedes,
        )
        problems = verify_measurement(self.store, measurement)
        measurement.verified = not problems
        measurement.verification_problems = problems
        self.store.add_draft_final_measurement(measurement)
        return measurement

    def get(self, case_id: str) -> DraftFinalMeasurement | None:
        """The case's standing measurement — the latest
        struck — or None when none has been struck."""
        prior = self.store.list_draft_final_measurements(case_id)
        if not prior:
            return None
        return sorted(
            prior, key=lambda m: (m.measured_on, m.measurement_id)
        )[-1]

    def measurements(self) -> list[DraftFinalMeasurement]:
        return self.store.list_draft_final_measurements()

    def verify(self, measurement: DraftFinalMeasurement) -> list[str]:
        return verify_measurement(self.store, measurement)

    def rollup(self) -> dict[str, Any]:
        return book_rollup(self.store)
