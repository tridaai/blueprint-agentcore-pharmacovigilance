"""Case sealing + export — Round 7 (design map R7).

Rounds 1–6 build the case file and put it through the
dual-control review gate. This round closes it:

- **Sealing** is a named act over a *medically reviewed* case.
  The seal binds the case's full artefact inventory — every
  artefact by kind, id and content hash — the head of the
  evidence chain at seal time, the exact review bindings the
  gate was satisfied over (and the acts that satisfied it), and
  the pack versions in force. A case that is not medically
  reviewed cannot be sealed, and the refusal names the blocker:
  the missing role, the standing hold and its reason, the stale
  acts. A case closed as a duplicate is not sealable either —
  the surviving case is the record.
- **A sealed case is a closed record.** The service layer
  refuses new follow-ups, recodes, assessments, review acts and
  summaries for it, each refusal naming the seal. There is no
  unseal surface, in this round or any other: a correction is a
  new case.
- **Export serves the sealed inventory, exactly.** The package
  is a deterministic function of what the seal binds — two
  exports of one seal are byte-identical — and every serving is
  recorded (exporter, date, package hash) in the store and on
  the case's evidence chain. An unsealed case has no export;
  the attempt is refused, naming the seal requirement.
- The package is built to be checked **offline**: the verifier
  in :mod:`pv_cases.verify` takes the package alone — no store,
  no database — and re-performs every hash, the chain linkage,
  the seal's exactness, the review gate and the clock
  arithmetic.

A seal and an export are internal records. Nothing here submits
anything to a regulator or a safety database of record — the
system ends, as designed, at an approved, exportable case file.
"""

from __future__ import annotations

import hashlib
from datetime import date

from pv_cases.assessment import AssessmentService
from pv_cases.coding import CodingService
from pv_cases.evidence import GENESIS_HASH, EvidenceChain, canonical_json
from pv_cases.models import (
    PACKAGE_FORMAT,
    REVIEW_ACCEPT_DECISIONS,
    REVIEW_STATE_HOLD,
    REVIEW_STATE_REVIEWED,
    SEAL_KIND_ASSESSMENT,
    SEAL_KIND_CASE,
    SEAL_KIND_CODING,
    SEAL_KIND_EVIDENCE,
    SEAL_KIND_FU_ACT,
    SEAL_KIND_FU_REQUEST,
    SEAL_KIND_REVIEW,
    SEAL_KIND_SUMMARY,
    CaseExport,
    CaseSeal,
    CaseStatus,
    SealedArtefact,
    is_named_human,
)
from pv_cases.review import ReviewService, artefact_hash

# ---------------------------------------------------------------------
# The sealing pack — data. Bump the version when the format, the
# gate rule or the inventory kinds change; seals and exports
# record the version they were taken under.
# ---------------------------------------------------------------------

SEALING_PACK = {
    "version": "2026.10.1",
    "package_format": PACKAGE_FORMAT,
    "gate_rule": (
        "a case seals only from medically_reviewed, over the"
        " current record, by a named sealer"
    ),
    "immutability_rule": (
        "a sealed case takes no new follow-ups, recodes,"
        " assessments, review acts or summaries — there is no"
        " unseal; a correction is a new case"
    ),
    "export_rule": (
        "export serves the sealed inventory exactly and is"
        " recorded per serving; an unsealed case has no export"
    ),
    "submission_rule": (
        "a seal and an export are internal records — nothing is"
        " submitted to a regulator"
    ),
}

SEALING_PACK_VERSION = SEALING_PACK["version"]


class SealError(ValueError):
    """A seal or export act that cannot be taken: an unnamed
    sealer or exporter, a case that is not medically reviewed
    (with the blocker named), a case already sealed, a case
    closed as a duplicate, or an export of an unsealed case."""


def inventory_hash(inventory: list[SealedArtefact]) -> str:
    """One hash over the whole inventory, in the inventory's
    stored order — the quick exactness check the offline
    verifier re-performs."""
    return hashlib.sha256(
        canonical_json(
            {"inventory": [i.model_dump(mode="json") for i in inventory]}
        )
    ).hexdigest()


class SealingService:
    """Seals medically reviewed cases, serves their export
    packages, and answers ``get_seal`` for the service-layer
    guards the other rounds consult before writing to a case."""

    def __init__(
        self,
        store,
        chain: EvidenceChain,
        review: ReviewService,
        coding: CodingService | None = None,
        assessment: AssessmentService | None = None,
    ):
        self.store = store
        self.chain = chain
        self.review = review
        self.coding = coding
        self.assessment = assessment

    # -- lookups -----------------------------------------------------
    def get_seal(self, case_id: str) -> CaseSeal | None:
        return self.store.get_seal(case_id)

    def list_exports(self, case_id: str) -> list[CaseExport]:
        return self.store.list_exports(case_id)

    # -- the inventory -------------------------------------------------
    def _follow_up_requests_for(self, case_id: str):
        """The follow-up requests that belong to the case: the
        ones raised against it, plus a validity request whose
        resolution founded it (named by its resolution act)."""
        requests = list(self.store.list_follow_up_requests(case_id=case_id))
        seen = {r.task_id for r in requests}
        for request in self.store.list_follow_up_requests():
            if request.task_id in seen or request.case_id is not None:
                continue
            acts = self.store.list_follow_up_acts(request.task_id)
            if any(a.case_id == case_id for a in acts):
                requests.append(request)
                seen.add(request.task_id)
        return sorted(requests, key=lambda r: r.task_id)

    def _artefact_payloads(self, case_id: str) -> list[tuple[str, str, dict]]:
        """Every case-owned artefact as (kind, id, stored-form
        payload): the case record, all coding decisions, all
        assessments, all summaries, all review acts, and the
        case's follow-up requests with their acts — superseded
        artefacts included, because the sealed file is the whole
        trail, not just the current record."""
        case = self.store.get_case(case_id)
        out: list[tuple[str, str, dict]] = [
            (SEAL_KIND_CASE, case.case_id, case.model_dump(mode="json"))
        ]
        for d in sorted(
            self.store.list_coding_decisions(case_id),
            key=lambda d: d.decision_id,
        ):
            out.append(
                (SEAL_KIND_CODING, d.decision_id, d.model_dump(mode="json"))
            )
        for a in sorted(
            self.store.list_assessments(case_id), key=lambda a: a.assessment_id
        ):
            out.append(
                (SEAL_KIND_ASSESSMENT, a.assessment_id, a.model_dump(mode="json"))
            )
        for s in sorted(
            self.store.list_summaries(case_id), key=lambda s: s.summary_id
        ):
            out.append(
                (SEAL_KIND_SUMMARY, s.summary_id, s.model_dump(mode="json"))
            )
        for act in sorted(
            self.store.list_review_acts(case_id), key=lambda a: a.act_id
        ):
            out.append(
                (SEAL_KIND_REVIEW, act.act_id, act.model_dump(mode="json"))
            )
        requests = self._follow_up_requests_for(case_id)
        for request in requests:
            out.append(
                (
                    SEAL_KIND_FU_REQUEST,
                    request.task_id,
                    request.model_dump(mode="json"),
                )
            )
        for request in requests:
            for act in sorted(
                self.store.list_follow_up_acts(request.task_id),
                key=lambda a: a.act_id,
            ):
                out.append(
                    (SEAL_KIND_FU_ACT, act.act_id, act.model_dump(mode="json"))
                )
        return out

    def build_inventory(self, case_id: str) -> list[SealedArtefact]:
        """The inventory a seal over the case's current state
        would bind: every artefact by kind, id and content hash,
        then every evidence item in chain order, bound by its
        chain hash."""
        inventory = [
            SealedArtefact(
                artefact_kind=kind,
                artefact_id=artefact_id,
                artefact_hash=artefact_hash(payload),
            )
            for kind, artefact_id, payload in self._artefact_payloads(case_id)
        ]
        for item in self.store.list_evidence(case_id):
            inventory.append(
                SealedArtefact(
                    artefact_kind=SEAL_KIND_EVIDENCE,
                    artefact_id=item.item_id,
                    artefact_hash=item.item_hash,
                )
            )
        return inventory

    # -- sealing -------------------------------------------------------
    def seal_case(
        self, case_id: str, sealed_by: str, today: date
    ) -> CaseSeal:
        """Seal one case's file. Refused in code — with the
        blocker named — when the sealer is unnamed, the case is
        closed as a duplicate, the case is already sealed, or
        its review gate does not stand complete over the current
        record (missing roles, a standing hold, stale acts)."""
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        if not is_named_human(sealed_by):
            raise SealError("a seal needs a named human sealer")
        if case.status is CaseStatus.CLOSED_AS_DUPLICATE:
            raise SealError(
                f"case {case_id} is closed as a duplicate of"
                f" {case.suspected_duplicate_of or 'another case'} —"
                " seal the surviving case, not the closed one"
            )
        existing = self.store.get_seal(case_id)
        if existing is not None:
            raise SealError(
                f"case {case_id} is already sealed ({existing.seal_id},"
                f" sealed by {existing.sealed_by} on"
                f" {existing.sealed_on}) — a seal is taken once"
            )
        view = self.review.review_for_case(case_id)
        if view.state != REVIEW_STATE_REVIEWED:
            blockers = []
            if view.state == REVIEW_STATE_HOLD:
                blockers.append(
                    f"the case is in review_hold — {view.hold_by}"
                    f" ({view.hold_role}) rejected the record:"
                    f" {view.hold_reason}"
                )
            else:
                blockers.append(
                    f"the case is {view.state} — no current accept"
                    f" from: {', '.join(view.missing_roles)}"
                )
            if view.has_stale_acts:
                stale = [a for a in view.acts if a.stale]
                superseded = sorted(
                    {s for a in stale for s in a.superseded_by}
                )
                blockers.append(
                    f"{len(stale)} recorded act(s) are stale against"
                    " the current record (superseded by:"
                    f" {', '.join(superseded) or 'a changed record'})"
                    " and count for nothing — the case needs a fresh"
                    " dual review"
                )
            raise SealError(
                f"case {case_id} cannot be sealed: a seal is taken"
                " only from medically_reviewed — "
                + "; ".join(blockers)
            )
        bindings = self.review.current_bindings(case_id)
        accepted_acts: dict[str, str] = {}
        for act_view in view.acts:
            act = act_view.act
            if not act_view.stale and act.decision in REVIEW_ACCEPT_DECISIONS:
                accepted_acts[act.role] = act.act_id
        current = (
            self.assessment.current_assessment(case_id)
            if self.assessment is not None
            else None
        )
        if current is None:
            raise SealError(
                f"case {case_id} has no current assessment to seal over"
            )
        dictionary_versions = sorted(
            {
                d.dictionary_version
                for d in self.coding.current_decisions(case_id)
            }
        ) if self.coding is not None else []
        items = self.store.list_evidence(case_id)
        inventory = self.build_inventory(case_id)
        seal = CaseSeal(
            seal_id=f"SEAL-{case_id}",
            case_id=case_id,
            sealed_by=sealed_by,
            sealed_on=today,
            pack_version=SEALING_PACK_VERSION,
            review_pack_version=view.pack_version,
            review_state=view.state,
            accepted_acts=accepted_acts,
            review_bindings=bindings,
            assessment_id=current.assessment_id,
            coding_decision_ids=sorted(
                b.artefact_id
                for b in bindings
                if b.artefact_kind == SEAL_KIND_CODING
            ),
            dictionary_versions=dictionary_versions,
            criteria_pack_version=current.criteria_pack_version,
            rsi_version=current.rsi_version,
            clock_pack_version=current.clock_pack_version,
            chain_head_hash=items[-1].item_hash if items else GENESIS_HASH,
            chain_items=len(items),
            inventory=inventory,
            inventory_hash=inventory_hash(inventory),
        )
        self.store.add_seal(seal)
        # The seal is recorded on the case's evidence chain —
        # after the head it binds, as the first entry of the
        # case's closed-record era.
        self.chain.append(
            case_id=case_id,
            kind="case_seal",
            payload=seal.model_dump(mode="json"),
            recorded_on=today,
            item_id=f"EV-{case_id}-SEAL",
        )
        return seal

    # -- export ----------------------------------------------------------
    def build_package(self, seal: CaseSeal) -> dict:
        """The export package for one seal, assembled ONLY from
        the sealed inventory: each inventoried artefact is read
        back from the store and re-hashed — a sealed artefact
        that no longer matches its sealed hash refuses the
        export loudly rather than serving a package that
        disagrees with its seal."""
        case_id = seal.case_id
        payloads = {
            (kind, artefact_id): payload
            for kind, artefact_id, payload in self._artefact_payloads(case_id)
        }
        items = {i.item_id: i for i in self.store.list_evidence(case_id)}
        artefacts: list[dict] = []
        evidence: list[dict] = []
        for line in seal.inventory:
            if line.artefact_kind == SEAL_KIND_EVIDENCE:
                item = items.get(line.artefact_id)
                if item is None or item.item_hash != line.artefact_hash:
                    raise SealError(
                        f"sealed evidence item {line.artefact_id} no"
                        " longer matches the seal — the sealed record"
                        " has changed underneath its seal"
                    )
                evidence.append(
                    {
                        "item": item.model_dump(mode="json"),
                        "payload": self.chain.payload_of(item),
                    }
                )
                continue
            payload = payloads.get((line.artefact_kind, line.artefact_id))
            if payload is None or artefact_hash(payload) != line.artefact_hash:
                raise SealError(
                    f"sealed artefact {line.artefact_kind}"
                    f" {line.artefact_id} no longer matches the seal"
                    " — the sealed record has changed underneath its"
                    " seal"
                )
            artefacts.append(
                {
                    "kind": line.artefact_kind,
                    "id": line.artefact_id,
                    "payload": payload,
                }
            )
        return {
            "package_format": PACKAGE_FORMAT,
            "case_id": case_id,
            "seal": seal.model_dump(mode="json"),
            "artefacts": artefacts,
            "evidence": evidence,
        }

    def package_bytes(self, package: dict) -> bytes:
        """The package's canonical bytes — the bytes its hash
        covers and the bytes an export serves."""
        return canonical_json(package)

    def export_case(
        self, case_id: str, exported_by: str, today: date
    ) -> tuple[CaseExport, dict]:
        """Serve one export of a sealed case and record the
        serving. Refused — naming the seal requirement — when
        the case is not sealed: an unsealed file is still being
        worked and has no export."""
        if self.store.get_case(case_id) is None:
            raise KeyError(f"no such case: {case_id}")
        if not is_named_human(exported_by):
            raise SealError("an export needs a named human exporter")
        seal = self.store.get_seal(case_id)
        if seal is None:
            raise SealError(
                f"case {case_id} is not sealed — export serves a"
                " sealed case file only; seal the case first (a"
                " seal requires a completed dual review)"
            )
        package = self.build_package(seal)
        package_hash = hashlib.sha256(self.package_bytes(package)).hexdigest()
        existing = self.store.list_exports(case_id)
        record = CaseExport(
            export_id=f"EXP-{case_id}-{len(existing) + 1:02d}",
            case_id=case_id,
            seal_id=seal.seal_id,
            exported_by=exported_by,
            exported_on=today,
            package_format=PACKAGE_FORMAT,
            package_hash=package_hash,
        )
        self.store.add_export(record)
        # The serving is written on the case's evidence chain,
        # like every act before it: who exported, when, and the
        # hash of the exact bytes served.
        self.chain.append(
            case_id=case_id,
            kind="case_export",
            payload=record.model_dump(mode="json"),
            recorded_on=today,
            item_id=f"EV-{case_id}-EXP-{len(existing) + 1:02d}",
        )
        return record, package

    # -- the plain-text rendering --------------------------------------
    def render_text(self, package: dict) -> str:
        """A deterministic plain-text rendering of a package —
        the same package renders to the same text, line for line.
        Derived from the package alone, like the verifier."""
        seal = package["seal"]
        by_kind: dict[str, list[str]] = {}
        for entry in package["artefacts"]:
            by_kind.setdefault(entry["kind"], []).append(entry["id"])
        for entry in package["evidence"]:
            by_kind.setdefault(SEAL_KIND_EVIDENCE, []).append(
                entry["item"]["item_id"]
            )
        assessment = next(
            (
                e["payload"]
                for e in package["artefacts"]
                if e["kind"] == SEAL_KIND_ASSESSMENT
                and e["id"] == seal["assessment_id"]
            ),
            {},
        )
        clock = assessment.get("clock", {})
        package_hash = hashlib.sha256(self.package_bytes(package)).hexdigest()
        lines = [
            f"PV CASE EXPORT — {package['package_format']}",
            f"Case: {package['case_id']}",
            f"Seal: {seal['seal_id']} — sealed by {seal['sealed_by']}"
            f" on {seal['sealed_on']} (sealing pack"
            f" {seal['pack_version']})",
            f"Review: {seal['review_state']} under review pack"
            f" {seal['review_pack_version']} — accepted acts:"
            + ", ".join(
                f" {role} {act_id}"
                for role, act_id in sorted(seal["accepted_acts"].items())
            ),
            f"Assessment: {seal['assessment_id']} — seriousness"
            f" {assessment.get('seriousness')}, expectedness"
            f" {assessment.get('expectedness')}, clock"
            f" {clock.get('obligation_class')}, day zero"
            f" {clock.get('day_zero')}, deadline"
            f" {clock.get('deadline') or 'none'}",
            "Inventory:",
        ]
        for kind in sorted(by_kind):
            lines.append(f"  {kind}: {len(by_kind[kind])}")
            for artefact_id in by_kind[kind]:
                lines.append(f"    {artefact_id}")
        lines += [
            f"Evidence chain: {seal['chain_items']} items, head"
            f" {seal['chain_head_hash']}",
            f"Inventory hash: {seal['inventory_hash']}",
            f"Package hash: {package_hash}",
            "This package is an internal case-file export. Nothing"
            " in it has been submitted to a regulator.",
        ]
        return "\n".join(lines) + "\n"
