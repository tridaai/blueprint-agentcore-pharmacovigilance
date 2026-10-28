"""The AgentCore Memory port — Round 8.

The architecture page has promised this port since Round 1:
short-term memory per session, long-term recall of patient and
product history "behind a port, with a local double for offline
tests", the repo stating plainly that AgentCore Memory is the
production path. Round 2's history views covered part of the
need — derived, content-addressed views over stored artefacts —
but a view is not a memory: nothing recorded *that a recall
happened*, what it rested on, or when it stops being served.
This module is that port and that record.

- **The port**: :class:`MemoryPort` is the seam AgentCore Memory
  sits behind in production (semantic + episodic strategies over
  the case book). :class:`LocalMemoryDouble` is the labelled
  offline implementation, backed by the case store.
- **Recall is by reference, never by copy.** A retrieval's
  matches name prior cases and the artefacts that evidence the
  match (the assessment, the coding decisions) plus the coded
  facts the match rests on — a preferred term, a seriousness
  verdict. No patient or reporter field, no narrative text: the
  subject itself is carried as a sha256 fingerprint, because a
  patient key is built from identifying characteristics and the
  retrieval record does not need the key to prove what it did.
- **Recall never merges.** Reading another case changes nothing
  in either case: no fact moves, no artefact is written to the
  source case. The only writes are the retrieval record itself
  and its note on the *requesting* case's chain — which is why a
  sealed requesting case refuses recall: its file is closed.
- **Retention is a pack rule, and expiry is a tombstone.** A
  retrieval is served until the pack's retention days run out;
  past that date it stands in the record, counted as a tombstone,
  and no read serves it. Nothing is deleted.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Protocol

from pv_cases.evidence import EvidenceChain, canonical_json
from pv_cases.models import (
    MEMORY_STRATEGIES,
    MEMORY_STRATEGY_CODING_PRECEDENT,
    MEMORY_STRATEGY_SAME_PATIENT,
    MEMORY_STRATEGY_SAME_PRODUCT_EVENT,
    MemoryMatch,
    MemoryRetrieval,
    is_named_human,
    normalise_text,
    patient_key,
    seal_refusal_text,
)
from pv_cases.reporting import current_report

# ---------------------------------------------------------------------
# The memory pack — data. Bump the version when a strategy, the
# retention rule or the minimisation rule changes; retrievals
# carry the version they were made under.
# ---------------------------------------------------------------------

MEMORY_PACK = {
    "version": "2026.10.1",
    "strategies": list(MEMORY_STRATEGIES),
    # A retrieval is served for this many days from the day it
    # was made; after that it is a tombstone — kept, counted,
    # never served. Retention is expiry, never deletion.
    "retention_days": 365,
    # What a match may carry: case and artefact references and
    # coded facts. Never a patient or reporter field, never
    # narrative text, never the subject key itself — the subject
    # is fingerprinted.
    "minimisation_rule": (
        "matches carry case ids, artefact ids and coded facts"
        " only; the subject is a sha256 fingerprint of its key"
    ),
    # Recall points at other cases; it does not move their
    # facts. The requesting case gains a record of the recall
    # and nothing else changes anywhere.
    "merge_rule": "recall references, never merges",
}

MEMORY_PACK_VERSION = MEMORY_PACK["version"]


class MemoryError(ValueError):
    """A recall that cannot be made: an unnamed requester, an
    unknown strategy, a case that does not exist, or a sealed
    requesting case."""


class MemoryPort(Protocol):
    """The seam the production AgentCore Memory service sits
    behind. The port records retrievals; the recall *computation*
    over the case book is the service's, so the double and the
    production service grade identically."""

    def record(self, retrieval: MemoryRetrieval) -> MemoryRetrieval: ...
    def get(self, retrieval_id: str) -> MemoryRetrieval | None: ...
    def list_all(self) -> list[MemoryRetrieval]: ...


class LocalMemoryDouble:
    """The labelled offline Memory implementation: retrievals
    persist through the case store port, insert-only."""

    name = "local-memory-double"

    def __init__(self, store):
        self.store = store

    def record(self, retrieval: MemoryRetrieval) -> MemoryRetrieval:
        self.store.add_memory_retrieval(retrieval)
        return retrieval

    def get(self, retrieval_id: str) -> MemoryRetrieval | None:
        return self.store.get_memory_retrieval(retrieval_id)

    def list_all(self) -> list[MemoryRetrieval]:
        return self.store.list_memory_retrievals()


def _fingerprint(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


class MemoryService:
    """Cross-case recall over the case book, recorded through
    the Memory port."""

    def __init__(self, store, chain: EvidenceChain, memory: MemoryPort | None = None):
        self.store = store
        self.chain = chain
        self.memory = memory or LocalMemoryDouble(store)
        # Round 7's guard, bound by the wiring: recall writes its
        # record on the requesting case's chain, so a sealed
        # requesting case refuses it — the file is closed.
        self.seal_lookup = None

    # -- internals -----------------------------------------------------
    def _report(self, case_id: str):
        return current_report(self.store, self.chain, case_id)

    def _current_assessment(self, case_id: str):
        assessments = self.store.list_assessments(case_id)
        return assessments[-1] if assessments else None

    def _current_decisions(self, case_id: str):
        from pv_cases.coding import current_decisions

        return current_decisions(self.store.list_coding_decisions(case_id))

    def _match(self, case_id: str, basis: str, coded_term: str | None) -> MemoryMatch:
        artefact_ids: list[str] = []
        assessment = self._current_assessment(case_id)
        if assessment is not None:
            artefact_ids.append(assessment.assessment_id)
        artefact_ids += [
            d.decision_id for d in self._current_decisions(case_id)
        ]
        return MemoryMatch(
            case_id=case_id,
            basis=basis,
            artefact_ids=artefact_ids,
            coded_term=coded_term,
            seriousness=(
                assessment.seriousness.value if assessment is not None else None
            ),
        )

    def _record(
        self,
        case_id: str | None,
        strategy: str,
        subject: str,
        subject_label: str,
        matches: list[MemoryMatch],
        requested_by: str,
        today: date,
    ) -> MemoryRetrieval:
        existing = self.memory.list_all()
        scope = case_id if case_id is not None else "DESK"
        retrieval = MemoryRetrieval(
            retrieval_id=f"MEM-{scope}-{len(existing) + 1:03d}",
            case_id=case_id,
            requested_by=requested_by,
            requested_on=today,
            strategy=strategy,
            subject_fingerprint=_fingerprint(subject),
            subject_label=subject_label,
            matches=matches,
            pack_version=MEMORY_PACK_VERSION,
            expires_on=today + timedelta(days=MEMORY_PACK["retention_days"]),
        )
        self.memory.record(retrieval)
        if case_id is not None:
            self.chain.append(
                case_id=case_id,
                kind="memory_retrieval",
                payload={"memory": retrieval.model_dump(mode="json")},
                recorded_on=today,
                item_id=f"EV-{retrieval.retrieval_id}",
            )
        return retrieval

    # -- recall ----------------------------------------------------------
    def recall_for_case(
        self, case_id: str, strategy: str, requested_by: str, today: date
    ) -> MemoryRetrieval:
        """Recall prior cases relevant to one case, under one
        strategy. The requesting case is never its own match."""
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        if not is_named_human(requested_by):
            raise MemoryError("a memory recall needs a named requester")
        if strategy not in MEMORY_PACK["strategies"]:
            raise MemoryError(f"unknown memory strategy: {strategy!r}")
        if self.seal_lookup is not None:
            seal = self.seal_lookup(case_id)
            if seal is not None:
                raise MemoryError(seal_refusal_text(seal, "memory retrievals"))
        report = self._report(case_id)
        others = [c for c in self.store.list_cases() if c.case_id != case_id]
        if strategy == MEMORY_STRATEGY_SAME_PATIENT:
            key = patient_key(report.patient)
            matches = [
                self._match(c.case_id, "same_patient_key", None)
                for c in others
                if patient_key(self._report(c.case_id).patient) == key
            ]
            return self._record(
                case_id,
                strategy,
                f"patient:{key}",
                "the requesting case's patient",
                matches,
                requested_by,
                today,
            )
        if strategy == MEMORY_STRATEGY_SAME_PRODUCT_EVENT:
            terms = {
                d.preferred_term
                for d in self._current_decisions(case_id)
                if d.subject_kind == "event" and d.preferred_term
            }
            matches = []
            for c in others:
                if c.product_code != case.product_code:
                    continue
                shared = terms & {
                    d.preferred_term
                    for d in self._current_decisions(c.case_id)
                    if d.subject_kind == "event" and d.preferred_term
                }
                for term in sorted(shared):
                    matches.append(
                        self._match(c.case_id, "same_product_and_coded_term", term)
                    )
            return self._record(
                case_id,
                strategy,
                f"product:{case.product_code}",
                f"product {case.product_code}, the case's coded terms",
                matches,
                requested_by,
                today,
            )
        # coding_precedent: this case's verbatim terms, and the
        # decisions other cases reached on the same words.
        matches = []
        verbatims = {
            normalise_text(d.verbatim)
            for d in self._current_decisions(case_id)
            if d.subject_kind == "event"
        }
        for c in others:
            for d in self._current_decisions(c.case_id):
                if (
                    d.subject_kind == "event"
                    and normalise_text(d.verbatim) in verbatims
                ):
                    matches.append(
                        self._match(c.case_id, "same_verbatim_term", d.preferred_term)
                    )
                    break
        return self._record(
            case_id,
            strategy,
            f"case:{case_id}:verbatims",
            "the requesting case's event verbatim terms",
            matches,
            requested_by,
            today,
        )

    def recall_coding_precedents(
        self, verbatim: str, requested_by: str, today: date
    ) -> MemoryRetrieval:
        """A desk recall (no requesting case): every stored
        decision on one verbatim term, across the book, by
        reference. Nothing is written to any case's chain."""
        if not is_named_human(requested_by):
            raise MemoryError("a memory recall needs a named requester")
        target = normalise_text(verbatim)
        matches: list[MemoryMatch] = []
        seen: set[str] = set()
        for decision in self.store.list_coding_decisions():
            if decision.subject_kind != "event":
                continue
            if normalise_text(decision.verbatim) != target:
                continue
            if decision.case_id in seen:
                continue
            seen.add(decision.case_id)
            matches.append(
                self._match(
                    decision.case_id, "same_verbatim_term", decision.preferred_term
                )
            )
        return self._record(
            None,
            MEMORY_STRATEGY_CODING_PRECEDENT,
            f"verbatim:{target}",
            f"the verbatim term {verbatim!r}",
            matches,
            requested_by,
            today,
        )

    # -- retention -------------------------------------------------------
    def active_retrievals(
        self, as_of: date, case_id: str | None = None
    ) -> list[MemoryRetrieval]:
        """The retrievals still served at ``as_of``: made, and
        not past their retention date."""
        return [
            r
            for r in self.memory.list_all()
            if (case_id is None or r.case_id == case_id) and r.expires_on >= as_of
        ]

    def retention_view(self, as_of: date) -> dict:
        """The retention position at ``as_of``: how many
        retrievals are active, and how many stand expired as
        tombstones — kept and counted, never deleted, never
        served."""
        all_retrievals = self.memory.list_all()
        expired = [r for r in all_retrievals if r.expires_on < as_of]
        return {
            "as_of": as_of.isoformat(),
            "pack_version": MEMORY_PACK_VERSION,
            "retention_days": MEMORY_PACK["retention_days"],
            "active": len(all_retrievals) - len(expired),
            "expired_tombstones": len(expired),
            "expired_ids": [r.retrieval_id for r in expired],
        }
