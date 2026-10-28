"""Medical review — Round 6 (design Law 5: dual control).

Everything before this round *prepares* the case: the gate admits
it, the dictionary codes it, the assessment engine judges its
seriousness, expectedness and clock, the assembler writes its
cited summary. This round is where a human being owns it. A case
is medically reviewed only when two named reviewers — one in the
medical reviewer role, one in the safety reviewer role, two
distinct people — have each recorded an accepting decision on the
case's current record.

- A **review act** is one reviewer's decision, recorded once and
  never edited: the reviewer, their role, the decision, its
  rationale, and **bindings** — the exact artefact versions
  reviewed (the current assessment, the current coding decisions,
  and the assembled summary where one exists), each by id and
  hash. Acts are insert-only artefacts in the store and are
  recorded on the case's evidence chain.
- **Staleness is derived, never declared.** When any bound
  artefact is superseded — a follow-up folds new facts in and the
  assessment is rewritten, a coder settles a term — every act
  bound to the old version stops counting toward the gate. The
  acts stay on the trail, labelled stale with the superseding
  artefacts named; the case falls back to awaiting review.
- **A reject holds the case.** While a current (non-stale) reject
  stands, the case is in `review_hold` with the reason on its
  face — an accept recorded over the same record does not clear
  it. The only way out is the one drug safety actually uses: the
  record changes (a new assessment, a corrected artefact), the
  stale reject falls away with the version it bound, and a fresh
  dual review decides the new record.
- **Dual control is refused in code, at recording time**: the
  same person may not hold a current act in both roles on one
  case. One role's accept alone leaves the case visibly
  `awaiting_second_review` on every read. "Same person" is
  judged under the repo's `normalise_text` folding (Round 9,
  RT-25) — a case or punctuation variant of the same name is
  the same person, at recording, in the derived state, and in
  the offline export verifier.

The review states are internal workflow states. `medically_
reviewed` means two named reviewers accepted this record inside
this system — it is not a submission, and nothing here submits
anything to a regulator, in this round or any other.
"""

from __future__ import annotations

import hashlib
from datetime import date

from pv_cases.access import (
    ACT_RECORD_REVIEW,
    REASON_ACT_NOT_PERMITTED,
    SURFACE_REVIEW,
    Principal,
    control_for,
)
from pv_cases.assessment import AssessmentService
from pv_cases.coding import CodingService
from pv_cases.evidence import EvidenceChain, canonical_json
from pv_cases.models import (
    REVIEW_ACCEPT_DECISIONS,
    REVIEW_ACCEPT_WITH_COMMENT,
    REVIEW_BUCKET_AWAITING_FIRST,
    REVIEW_BUCKET_AWAITING_SECOND,
    REVIEW_BUCKET_HOLD,
    REVIEW_BUCKET_STALE,
    REVIEW_DECISIONS,
    REVIEW_REJECT,
    REVIEW_ROLES,
    REVIEW_STATE_AWAITING_FIRST,
    REVIEW_STATE_AWAITING_SECOND,
    REVIEW_STATE_HOLD,
    REVIEW_STATE_REVIEWED,
    CaseReviewView,
    CaseStatus,
    CaseSummary,
    MedicalReviewAct,
    ReviewActView,
    ReviewBinding,
    ReviewQueueEntry,
    is_named_human,
    normalise_text,
    seal_refusal_text,
)

# ---------------------------------------------------------------------
# The review pack — data. Bump the version when the role list, the
# decision list or any rule below changes; acts record the version
# they were taken under.
# ---------------------------------------------------------------------

REVIEW_PACK = {
    "version": "2026.10.1",
    "roles": list(REVIEW_ROLES),
    "decisions": list(REVIEW_DECISIONS),
    # A review is complete only with a current accept from every
    # role, recorded by distinct people — dual control.
    "completion_rule": (
        "one current accept-class act per role, from two distinct"
        " named reviewers, over the same bound record"
    ),
    # A current reject holds the case. Accepts over the same
    # record do not clear it; the hold clears only when the record
    # the reject bound is superseded and a fresh dual review
    # decides the new record.
    "disagreement_rule": (
        "a current reject_with_reason places the case in"
        " review_hold until the bound record is superseded"
    ),
    # An act binds artefact versions. When a bound artefact is
    # superseded the act is stale: kept on the trail, named, and
    # no longer counted toward the gate.
    "staleness_rule": (
        "an act counts only while every artefact it bound is"
        " still the case's current artefact"
    ),
    # The summary binding hashes the narrative content, because
    # summary ids are per-assembly sequence numbers: identical
    # text re-assembled is the same narrative.
    "summary_binding": "content_hash",
}

REVIEW_PACK_VERSION = REVIEW_PACK["version"]


class ReviewError(ValueError):
    """A review act that cannot be recorded: an unnamed reviewer,
    an unknown role or decision, a reason the decision requires
    and the act lacks, a case that is not reviewable, or the
    dual-control refusal — the same person in both roles."""


# ---------------------------------------------------------------------
# Bindings: the exact artefact versions a review decides.
# ---------------------------------------------------------------------


def artefact_hash(payload: dict) -> str:
    """The hash an act binds for one artefact: sha256 over the
    canonical JSON of its stored form."""
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def summary_content_hash(summary: CaseSummary) -> str:
    """The version of a narrative is its content: the statements
    and the profile that composed them. The summary id and the
    assembly date are excluded — they change on every assembly,
    the text does not."""
    return artefact_hash(
        {
            "model_profile": summary.model_profile,
            "statements": [
                s.model_dump(mode="json") for s in summary.statements
            ],
        }
    )


class ReviewService:
    """Records review acts, derives every case's review state from
    them, and builds the medical-review queue."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        coding: CodingService | None = None,
        assessment: AssessmentService | None = None,
    ):
        self.store = store
        self.chain = chain
        self.coding = coding
        self.assessment = assessment
        # Round 7: the sealing guard. Wiring binds this to the
        # sealing service's lookup; while it is set, a sealed
        # case refuses new review acts — a sealed case is a
        # closed record.
        self.seal_lookup = None
        # Round 10: the access control, bound by the wiring.
        self.access = None

    # -- bindings ------------------------------------------------------
    def current_bindings(self, case_id: str) -> list[ReviewBinding]:
        """The record a review would decide right now: the current
        assessment, the current coding decisions, and the latest
        summary where one has been assembled — each by id + hash."""
        bindings: list[ReviewBinding] = []
        if self.assessment is not None:
            current = self.assessment.current_assessment(case_id)
            if current is not None:
                bindings.append(
                    ReviewBinding(
                        artefact_kind="assessment",
                        artefact_id=current.assessment_id,
                        artefact_hash=artefact_hash(
                            current.model_dump(mode="json")
                        ),
                    )
                )
        if self.coding is not None:
            for decision in self.coding.current_decisions(case_id):
                bindings.append(
                    ReviewBinding(
                        artefact_kind="coding_decision",
                        artefact_id=decision.decision_id,
                        artefact_hash=artefact_hash(
                            decision.model_dump(mode="json")
                        ),
                    )
                )
        summaries = self.store.list_summaries(case_id)
        if summaries:
            latest = summaries[-1]
            bindings.append(
                ReviewBinding(
                    artefact_kind="summary",
                    artefact_id=latest.summary_id,
                    artefact_hash=summary_content_hash(latest),
                )
            )
        return bindings

    def _staleness(
        self, act: MedicalReviewAct, current: list[ReviewBinding]
    ) -> tuple[bool, list[str]]:
        """An act is stale iff the record it bound is not the
        record that stands: any bound artefact superseded (its id
        gone from the current set, or its hash changed), or an
        artefact kind it bound absent now — or present now when
        the act bound none (a summary assembled after the review
        is a narrative nobody reviewed). The summary is the one
        exception to identity-by-id: its version is its content
        hash, so identical text re-assembled under a new
        sequence id is the same narrative and stales nothing,
        while any change to the text is a new version and does."""

        def by_kind(bindings: list[ReviewBinding]) -> dict[str, dict[str, str]]:
            grouped: dict[str, dict[str, str]] = {}
            for b in bindings:
                grouped.setdefault(b.artefact_kind, {})[b.artefact_id] = (
                    b.artefact_hash
                )
            return grouped

        bound = by_kind(act.bindings)
        now = by_kind(current)
        stale = False
        superseded_by: list[str] = []
        for kind in set(bound) | set(now):
            b_map = bound.get(kind, {})
            n_map = now.get(kind, {})
            if kind == "summary":
                if set(b_map.values()) != set(n_map.values()):
                    stale = True
                    superseded_by += sorted(n_map)
                continue
            if b_map != n_map:
                stale = True
                superseded_by += sorted(set(n_map) - set(b_map))
        return stale, superseded_by

    # -- recording -----------------------------------------------------
    def record_review(
        self,
        case_id: str,
        reviewer: str,
        role: str,
        decision: str,
        rationale: str,
        today: date,
        principal: Principal | None = None,
    ) -> MedicalReviewAct:
        """Record one reviewer's decision on the case's current
        record. Refused in code — with the refusal named — when
        the act cannot stand: unnamed reviewer, unknown role or
        decision, a rationale the decision requires, a case with
        no assessment to review, a closed duplicate, or the same
        person already holding a current act in the other role.
        Round 10: where the call carries a principal, the access
        pack judges first — the principal must hold the review
        act, must hold *this* act's role (dual control is two
        pack-authorised roles, not only two names), and must be
        the reviewer the act names."""
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        if case.status is CaseStatus.CLOSED_AS_DUPLICATE:
            raise ReviewError(
                f"case {case_id} is closed as a duplicate of"
                f" {case.suspected_duplicate_of or 'another case'} —"
                " review the surviving case, not the closed one"
            )
        if self.seal_lookup is not None:
            seal = self.seal_lookup(case_id)
            if seal is not None:
                raise ReviewError(seal_refusal_text(seal, "review acts"))
        if not is_named_human(reviewer):
            raise ReviewError("a review act needs a named human reviewer")
        if role not in REVIEW_PACK["roles"]:
            raise ReviewError(
                f"unknown reviewer role: {role!r} — the review pack"
                f" names {', '.join(REVIEW_PACK['roles'])}"
            )
        if decision not in REVIEW_PACK["decisions"]:
            raise ReviewError(
                f"unknown review decision: {decision!r} — the review"
                f" pack names {', '.join(REVIEW_PACK['decisions'])}"
            )
        rationale = (rationale or "").strip()
        if decision == REVIEW_REJECT and not rationale:
            raise ReviewError(
                "a reject_with_reason act must carry its reason"
            )
        if decision == REVIEW_ACCEPT_WITH_COMMENT and not rationale:
            raise ReviewError(
                "an accept_with_comment act must carry its comment"
            )
        if principal is not None:
            control = control_for(self)
            control.require_act(
                principal,
                ACT_RECORD_REVIEW,
                surface=SURFACE_REVIEW,
                target=case_id,
                on=today,
            )
            if principal.role != role:
                control.deny(
                    principal_id=principal.id,
                    role=principal.role,
                    surface=SURFACE_REVIEW,
                    target=case_id,
                    reason=REASON_ACT_NOT_PERMITTED,
                    detail=(
                        f"a {role} review act must be recorded by a"
                        f" principal holding the {role} role — dual"
                        " control is two pack-authorised roles;"
                        f" principal {principal.id} holds"
                        f" {principal.role}"
                    ),
                    on=today,
                )
            control.require_actor(
                principal,
                reviewer,
                act=ACT_RECORD_REVIEW,
                surface=SURFACE_REVIEW,
                target=case_id,
                on=today,
            )
        bindings = self.current_bindings(case_id)
        if not any(b.artefact_kind == "assessment" for b in bindings):
            raise ReviewError(
                f"case {case_id} has no assessment to review — a"
                " review decides the assessed record, and this case"
                " has none yet"
            )
        # Dual control: one person, one role. A current act by the
        # same reviewer in the OTHER role refuses this one; a stale
        # act binds a record that no longer stands and refuses
        # nothing. Reviewer identity is compared under the repo's
        # normalise_text folding (Round 9, RT-25): "Dr A. Rao" and
        # "dr a rao" are the same person for this rule — a case
        # variant must not satisfy dual control alone.
        for other in self.store.list_review_acts(case_id):
            if (
                normalise_text(other.reviewer) != normalise_text(reviewer)
                or other.role == role
            ):
                continue
            other_stale, _ = self._staleness(other, bindings)
            if not other_stale:
                raise ReviewError(
                    f"dual control: {reviewer} already holds a"
                    f" current {other.role} act on {case_id} — the"
                    f" {role} decision must come from a different"
                    " named reviewer"
                )
        existing = self.store.list_review_acts(case_id)
        act = MedicalReviewAct(
            act_id=f"REV-{case_id}-{len(existing) + 1:02d}",
            case_id=case_id,
            reviewer=reviewer,
            role=role,
            decision=decision,
            rationale=rationale,
            pack_version=REVIEW_PACK_VERSION,
            bindings=bindings,
            recorded_on=today,
        )
        self.store.add_review_act(act)
        # The act is recorded on the case's evidence chain, like
        # the dedup and coding decisions before it: the review
        # trail is evidence, hash-chained with everything else.
        self.chain.append(
            case_id=case_id,
            kind="medical_review",
            payload=act.model_dump(mode="json"),
            recorded_on=today,
            item_id=f"EV-{case_id}-REV-{len(existing) + 1:02d}",
        )
        return act

    # -- derived state ---------------------------------------------------
    def review_for_case(self, case_id: str) -> CaseReviewView:
        """The case's review position, derived from its acts
        against the record that stands now."""
        if self.store.get_case(case_id) is None:
            raise KeyError(f"no such case: {case_id}")
        current = self.current_bindings(case_id)
        views: list[ReviewActView] = []
        current_acts: list[MedicalReviewAct] = []
        has_stale = False
        for act in self.store.list_review_acts(case_id):
            stale, superseded_by = self._staleness(act, current)
            views.append(
                ReviewActView(act=act, stale=stale, superseded_by=superseded_by)
            )
            if stale:
                has_stale = True
            else:
                current_acts.append(act)
        holds = [a for a in current_acts if a.decision == REVIEW_REJECT]
        accepted: dict[str, str] = {}
        for act in current_acts:
            if act.decision in REVIEW_ACCEPT_DECISIONS:
                accepted[act.role] = act.reviewer
        missing = [r for r in REVIEW_ROLES if r not in accepted]
        if holds:
            hold = holds[0]
            state = REVIEW_STATE_HOLD
        elif not missing and len(
            {normalise_text(v) for v in accepted.values()}
        ) == len(REVIEW_ROLES):
            state = REVIEW_STATE_REVIEWED
        elif accepted:
            state = REVIEW_STATE_AWAITING_SECOND
        else:
            state = REVIEW_STATE_AWAITING_FIRST
        return CaseReviewView(
            case_id=case_id,
            state=state,
            pack_version=REVIEW_PACK_VERSION,
            accepted_roles=accepted,
            missing_roles=missing if state != REVIEW_STATE_REVIEWED else [],
            hold_act_id=holds[0].act_id if holds else None,
            hold_by=holds[0].reviewer if holds else None,
            hold_role=holds[0].role if holds else None,
            hold_reason=holds[0].rationale if holds else None,
            has_stale_acts=has_stale,
            acts=views,
        )

    def list_act_views(self, case_id: str) -> list[ReviewActView]:
        """The case's full review trail: every act ever recorded,
        each labelled with its staleness against the record that
        stands now."""
        return self.review_for_case(case_id).acts

    # -- the queue -------------------------------------------------------
    def queue(self, as_of: date) -> list[ReviewQueueEntry]:
        """The medical-review queue at ``as_of``: the work, in the
        order a review team works it — holds first (blocked on a
        record change), then cases one decision from done, then
        stale reviews (a fresh review of a moved record), then
        first reviews; deadline-bearing clocks first inside a
        bucket. Reviewed cases are not queue work, and neither are
        cases closed as duplicates. Entries carry no patient- or
        reporter-identifying field — minimisation is structural,
        in the entry model itself."""
        clock_by_case = {}
        if self.assessment is not None:
            clock_by_case = {
                e.case_id: e for e in self.assessment.clock_board(as_of)
            }
        entries: list[ReviewQueueEntry] = []
        for case in self.store.list_cases():
            if case.status is CaseStatus.CLOSED_AS_DUPLICATE:
                continue
            view = self.review_for_case(case.case_id)
            if view.state == REVIEW_STATE_REVIEWED:
                continue
            if view.state == REVIEW_STATE_HOLD:
                bucket = REVIEW_BUCKET_HOLD
            elif view.state == REVIEW_STATE_AWAITING_SECOND:
                bucket = REVIEW_BUCKET_AWAITING_SECOND
            elif view.has_stale_acts:
                bucket = REVIEW_BUCKET_STALE
            else:
                bucket = REVIEW_BUCKET_AWAITING_FIRST
            clock = clock_by_case.get(case.case_id)
            current = (
                self.assessment.current_assessment(case.case_id)
                if self.assessment is not None
                else None
            )
            entries.append(
                ReviewQueueEntry(
                    case_id=case.case_id,
                    bucket=bucket,
                    review_state=view.state,
                    seriousness=(
                        current.seriousness.value if current else None
                    ),
                    expectedness=(
                        current.expectedness.value if current else None
                    ),
                    obligation_class=(
                        clock.obligation_class if clock else None
                    ),
                    clock_state=clock.state if clock else None,
                    deadline=clock.deadline if clock else None,
                    days_remaining=clock.days_remaining if clock else None,
                    has_stale_acts=view.has_stale_acts,
                )
            )
        rank = {
            REVIEW_BUCKET_HOLD: 0,
            REVIEW_BUCKET_AWAITING_SECOND: 1,
            REVIEW_BUCKET_STALE: 2,
            REVIEW_BUCKET_AWAITING_FIRST: 3,
        }
        entries.sort(
            key=lambda e: (
                rank[e.bucket],
                e.deadline is None,
                e.deadline or date.max,
                e.case_id,
            )
        )
        return entries
