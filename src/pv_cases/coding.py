"""Dictionary coding — Round 3 (Law 3: the model never codes alone).

Coding is a **deterministic lookup in code** against a versioned
terminology extract built for this repo. The extract is original
data written for this blueprint — it is **not licensed MedDRA**,
and nothing here connects to the real dictionary.

The discipline:

- Three match paths, in stated precedence: **exact** (the verbatim
  as reported is the dictionary verbatim), **normalised** (equal
  after the package's case/punctuation folding), **synonym** (the
  verbatim is a recorded synonym of the entry). At the best tier
  that matches, one label wins or the term is ambiguous — there is
  no string-similarity or fuzzy path anywhere in this module, by
  design: a verbatim that merely *resembles* a preferred term is
  not coded to it.
- A verbatim with no entry is **uncoded**: the decision records the
  verbatim exactly as reported, names the reason, and raises a
  coding-review task. Uncoded is a first-class outcome, never a
  guess.
- A named human coder resolves a task by choosing a term that
  exists in the dictionary (for an ambiguous task, one of the
  recorded candidates). The resolution is a new decision,
  ``match_path = manual``, superseding the uncoded one by link and
  recorded on the case's evidence chain.
- A coder may also propose a dictionary **addition**. It takes
  effect only as a new dictionary version — dictionary artefacts
  are insert-only in the store port, so no version, and no decision
  pinned to it, can ever be edited in place.
- Automatic coding never supersedes a human's manual decision.
"""

from __future__ import annotations

from datetime import date

from pv_cases.evidence import EvidenceChain
from pv_cases.models import (
    SUBJECT_EVENT,
    SUBJECT_PRODUCT,
    UNCODED_AMBIGUOUS,
    UNCODED_NO_ENTRY,
    Case,
    CodingDecision,
    CodingMatchPath,
    CodingReviewStatus,
    CodingReviewTask,
    CodingStatus,
    DictionaryEntry,
    DictionaryVersion,
    IntakeReport,
    ProductDictionaryEntry,
    SummaryStatement,
    is_named_human,
    normalise_text,
)

BASE_VERSION = "2026.10.1"

# The shipped extract — original data, written for this blueprint.
# Preferred terms are grouped under system organ classes in the
# MedDRA style, but this is a reduced repo-built extract, not the
# licensed dictionary. Two entries are deliberate teaching shapes:
# "Feeling drained" is a synonym of two different preferred terms
# (an ambiguity only a coder may settle), and "Nausea" is one
# entry's verbatim and another's synonym (the exact path must win
# by the stated precedence).
BASE_EVENT_ENTRIES: list[DictionaryEntry] = [
    DictionaryEntry(
        verbatim="Blistering rash on hands",
        preferred_term="Rash vesicular",
        soc="Skin and subcutaneous tissue disorders",
        synonyms=["Blistered rash on hands", "Hand blistering rash"],
    ),
    DictionaryEntry(
        verbatim="Dizziness and fall at home",
        preferred_term="Dizziness",
        soc="Nervous system disorders",
        synonyms=["Felt dizzy and fell", "Dizzy spell and fall"],
    ),
    DictionaryEntry(
        verbatim="Could not stay awake at work",
        preferred_term="Somnolence",
        soc="Nervous system disorders",
        synonyms=["Unable to stay awake", "Kept falling asleep at work"],
    ),
    DictionaryEntry(
        verbatim="Excessive sleepiness in the morning",
        preferred_term="Somnolence",
        soc="Nervous system disorders",
        synonyms=["Morning sleepiness", "Excessive morning sleepiness"],
    ),
    DictionaryEntry(
        verbatim="Severe headache",
        preferred_term="Headache",
        soc="Nervous system disorders",
        synonyms=["Bad headache", "Head pain"],
    ),
    DictionaryEntry(
        verbatim="Itchy skin where cream applied",
        preferred_term="Pruritus",
        soc="Skin and subcutaneous tissue disorders",
        synonyms=["Itching at application site", "Skin itching where applied"],
    ),
    DictionaryEntry(
        verbatim="Rash on both arms",
        preferred_term="Rash",
        soc="Skin and subcutaneous tissue disorders",
        synonyms=["Arm rash", "Rash both arms"],
    ),
    DictionaryEntry(
        verbatim="Skin burning after use",
        preferred_term="Skin burning sensation",
        soc="Skin and subcutaneous tissue disorders",
        synonyms=["Burning skin after application"],
    ),
    DictionaryEntry(
        verbatim="Tiredness all day",
        preferred_term="Fatigue",
        soc="General disorders and administration site conditions",
        synonyms=["Feeling drained"],
    ),
    DictionaryEntry(
        verbatim="Worn out feeling",
        preferred_term="Asthenia",
        soc="General disorders and administration site conditions",
        synonyms=["Feeling drained"],
    ),
    DictionaryEntry(
        verbatim="Nausea",
        preferred_term="Nausea",
        soc="Gastrointestinal disorders",
        synonyms=["Feeling sick"],
    ),
    DictionaryEntry(
        verbatim="Vomiting",
        preferred_term="Vomiting",
        soc="Gastrointestinal disorders",
        synonyms=["Nausea", "Being sick"],
    ),
]

BASE_PRODUCT_ENTRIES: list[ProductDictionaryEntry] = [
    ProductDictionaryEntry(
        verbatim_name="Cardivane",
        product_code="NBT-101",
        product_name="Cardivane",
        active_substance="veldorin",
        synonyms=["Cardivane tablets"],
    ),
    ProductDictionaryEntry(
        verbatim_name="Somnirel",
        product_code="NBT-204",
        product_name="Somnirel",
        active_substance="trezapan",
        synonyms=["Somnirel capsules"],
    ),
    ProductDictionaryEntry(
        verbatim_name="Dermaclear",
        product_code="NBT-317",
        product_name="Dermaclear",
        active_substance="osalide",
        synonyms=["Dermaclear cream"],
    ),
]


def base_dictionary(created_on: date) -> DictionaryVersion:
    return DictionaryVersion(
        version=BASE_VERSION,
        created_on=created_on,
        created_by="repo extract (pv_cases.coding)",
        note="The blueprint's shipped terminology extract — repo-built, not licensed MedDRA.",
        event_entries=list(BASE_EVENT_ENTRIES),
        product_entries=list(BASE_PRODUCT_ENTRIES),
    )


class CodingError(ValueError):
    """A coder act that cannot be taken: an unnamed coder, a term
    that is not in the dictionary, a colliding addition."""


class CodingTaskClosed(CodingError):
    """The review task is not open — decided work does not move."""


# ---------------------------------------------------------------------
# The lookup. Pure functions over a dictionary version's entries.
# ---------------------------------------------------------------------


def _lookup(verbatim, entries, exact_pred, norm_pred, syn_pred, label_of):
    """The tiered lookup shared by events and products.

    Returns ``(entry, path, uncoded_reason, candidates)``: on a hit,
    the entry and the path taken; on a miss, ``None`` with the
    reason — ``ambiguous_match`` (with the competing labels) when
    the best matching tier maps the verbatim to more than one
    label, else ``no_dictionary_entry``.
    """
    v = verbatim.strip()
    vn = normalise_text(verbatim)
    if not vn:
        return None, CodingMatchPath.NONE, UNCODED_NO_ENTRY, []
    for path, pred in (
        (CodingMatchPath.EXACT, exact_pred),
        (CodingMatchPath.NORMALISED, norm_pred),
        (CodingMatchPath.SYNONYM, syn_pred),
    ):
        matches = [e for e in entries if pred(e, v, vn)]
        labels = sorted({label_of(e) for e in matches})
        if len(labels) == 1:
            winner = next(e for e in matches if label_of(e) == labels[0])
            return winner, path, None, []
        if len(labels) > 1:
            return None, CodingMatchPath.NONE, UNCODED_AMBIGUOUS, labels
    return None, CodingMatchPath.NONE, UNCODED_NO_ENTRY, []


def lookup_event(verbatim: str, entries: list[DictionaryEntry]):
    return _lookup(
        verbatim,
        entries,
        exact_pred=lambda e, v, vn: e.verbatim == v,
        norm_pred=lambda e, v, vn: normalise_text(e.verbatim) == vn,
        syn_pred=lambda e, v, vn: vn in {normalise_text(s) for s in e.synonyms},
        label_of=lambda e: e.preferred_term,
    )


def lookup_product(verbatim: str, entries: list[ProductDictionaryEntry]):
    # A report that names only the product code codes by the code,
    # on the exact path.
    return _lookup(
        verbatim,
        entries,
        exact_pred=lambda e, v, vn: e.verbatim_name == v or e.product_code == v,
        norm_pred=lambda e, v, vn: normalise_text(e.verbatim_name) == vn,
        syn_pred=lambda e, v, vn: vn in {normalise_text(s) for s in e.synonyms},
        label_of=lambda e: e.product_code,
    )


def current_decisions(decisions: list[CodingDecision]) -> list[CodingDecision]:
    """The current decision per subject: the ones nothing
    supersedes. Stored decisions are never edited, so currency is
    derived from the supersession links alone."""
    superseded = {d.supersedes_id for d in decisions if d.supersedes_id}
    return [d for d in decisions if d.decision_id not in superseded]


def coding_presentation_problems(
    decisions: list[CodingDecision],
    statements: list[SummaryStatement],
    dictionary: DictionaryVersion,
) -> list[str]:
    """The summary discipline for coded terms, checked in code:
    a statement may not present an uncoded term as coded (no coded
    claim, no arrow, no dictionary label attached to it), may not
    present a coded term as uncoded, and an arrow claim must name
    the decision's actual label. The intake statement that simply
    reports the verbatim ("Reported event: …") presents it as
    reported, which is always allowed."""
    problems: list[str] = []
    labels = {normalise_text(e.preferred_term) for e in dictionary.event_entries}
    labels |= {normalise_text(e.product_name) for e in dictionary.product_entries}
    for decision in decisions:
        vnorm = normalise_text(decision.verbatim)
        if not vnorm:
            continue
        for stmt in statements:
            tnorm = normalise_text(stmt.text)
            if vnorm not in tnorm:
                continue
            if decision.status is CodingStatus.UNCODED:
                if "uncoded" in tnorm:
                    continue
                # Judge only what the statement adds around the
                # verbatim: the verbatim itself may contain a
                # dictionary label as a substring ("Cardivane Plus"
                # contains "Cardivane") without the statement
                # claiming any coding for it.
                remainder = tnorm.replace(vnorm, " ")
                if (
                    "→" in stmt.text
                    or "coded" in remainder
                    or any(label in remainder for label in labels)
                ):
                    problems.append(
                        f"uncoded term presented as coded: {decision.verbatim!r}"
                        f" in {stmt.text[:60]!r}"
                    )
            else:
                if "uncoded" in tnorm:
                    problems.append(
                        f"coded term presented as uncoded: {decision.verbatim!r}"
                        f" in {stmt.text[:60]!r}"
                    )
                elif "→" in stmt.text:
                    claimed = stmt.text.split("→", 1)[1].strip()
                    head = claimed.split(" (")[0].strip().rstrip(".")
                    label = (
                        decision.preferred_term
                        if decision.subject_kind == SUBJECT_EVENT
                        else decision.product_name
                    )
                    if head != label:
                        problems.append(
                            f"coded term misstated: {decision.verbatim!r} claimed"
                            f" as {head!r}, decision says {label!r}"
                        )
    return problems


def _bump(version: str) -> str:
    head, _, tail = version.rpartition(".")
    return f"{head}.{int(tail) + 1}"


def _version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))


class CodingService:
    """Runs the lookup over stored cases, keeps the review queue,
    and takes the coder acts (resolutions and dictionary
    additions)."""

    def __init__(self, store, chain: EvidenceChain):
        self.store = store
        self.chain = chain

    # -- inputs ---------------------------------------------------------
    def report_of(self, case: Case) -> IntakeReport:
        for item in self.store.list_evidence(case.case_id):
            if item.kind == "intake_report":
                return IntakeReport.model_validate(self.chain.payload_of(item))
        raise KeyError(f"case {case.case_id} has no intake evidence")

    # -- the dictionary ---------------------------------------------------
    def ensure_base(self, today: date) -> None:
        if self.store.get_dictionary_version(BASE_VERSION) is None:
            self.store.add_dictionary_version(base_dictionary(today))

    def dictionary(self, version: str | None = None, today: date | None = None) -> DictionaryVersion:
        if today is not None:
            self.ensure_base(today)
        if version is not None:
            found = self.store.get_dictionary_version(version)
            if found is None:
                raise KeyError(f"no such dictionary version: {version}")
            return found
        versions = self.store.list_dictionary_versions()
        if not versions:
            raise KeyError("no dictionary version stored")
        return max(versions, key=lambda d: _version_key(d.version))

    def list_versions(self, today: date | None = None) -> list[DictionaryVersion]:
        if today is not None:
            self.ensure_base(today)
        return sorted(
            self.store.list_dictionary_versions(), key=lambda d: _version_key(d.version)
        )

    # -- coding ---------------------------------------------------------
    def current_decisions(self, case_id: str) -> list[CodingDecision]:
        decisions = current_decisions(self.store.list_coding_decisions(case_id))
        return sorted(
            decisions, key=lambda d: (d.subject_kind != SUBJECT_EVENT, d.subject_index)
        )

    def code_case(
        self, case_id: str, today: date, version: str | None = None
    ) -> list[CodingDecision]:
        """Code every subject of the case under one dictionary
        version. Idempotent per version: a subject already coded
        under that version is left alone, and a manual (human)
        decision is never auto-superseded at any version. Returns
        the decisions written by this run."""
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        dv = self.dictionary(version, today)
        report = self.report_of(case)
        subjects: list[tuple[str, int, str]] = [
            (SUBJECT_EVENT, i, event.verbatim_term)
            for i, event in enumerate(report.events)
        ]
        product_verbatim = report.suspect_product_name or report.suspect_product_code
        if product_verbatim:
            subjects.append((SUBJECT_PRODUCT, 0, product_verbatim))

        existing = self.store.list_coding_decisions(case_id)
        current = {
            (d.subject_kind, d.subject_index): d for d in current_decisions(existing)
        }
        written: list[CodingDecision] = []
        for kind, index, verbatim in subjects:
            cur = current.get((kind, index))
            if cur is not None and cur.match_path is CodingMatchPath.MANUAL:
                continue
            if cur is not None and cur.dictionary_version == dv.version:
                continue
            if kind == SUBJECT_EVENT:
                entry, path, reason, candidates = lookup_event(verbatim, dv.event_entries)
                coded_fields = (
                    {"preferred_term": entry.preferred_term, "soc": entry.soc}
                    if entry is not None
                    else {}
                )
            else:
                entry, path, reason, candidates = lookup_product(
                    verbatim, dv.product_entries
                )
                coded_fields = (
                    {
                        "product_code": entry.product_code,
                        "product_name": entry.product_name,
                        "active_substance": entry.active_substance,
                    }
                    if entry is not None
                    else {}
                )
            n = (
                len(
                    [
                        d
                        for d in existing
                        if (d.subject_kind, d.subject_index) == (kind, index)
                    ]
                )
                + 1
            )
            tag = "E" if kind == SUBJECT_EVENT else "P"
            decision = CodingDecision(
                decision_id=f"CD-{case_id}-{tag}{index}-{n:02d}",
                case_id=case_id,
                subject_kind=kind,
                subject_index=index,
                verbatim=verbatim,
                status=CodingStatus.CODED if entry is not None else CodingStatus.UNCODED,
                match_path=path,
                dictionary_version=dv.version,
                uncoded_reason=None if entry is not None else reason,
                candidates=[] if entry is not None else candidates,
                supersedes_id=cur.decision_id if cur is not None else None,
                coded_on=today,
                **coded_fields,
            )
            self.store.add_coding_decision(decision)
            existing.append(decision)
            written.append(decision)
            self._sync_task(cur, decision, today)
        return written

    def _sync_task(
        self,
        cur: CodingDecision | None,
        decision: CodingDecision,
        today: date,
    ) -> None:
        """Keep the review queue honest across a (re)coding: the
        superseded uncoded decision's open task closes as
        superseded; a coded outcome resolves it by the dictionary
        version that settled it; a still-uncoded outcome opens a
        fresh task for the new decision."""
        old_task = None
        if cur is not None and cur.status is CodingStatus.UNCODED:
            for task in self.store.list_coding_tasks(status=CodingReviewStatus.OPEN.value):
                if task.decision_id == cur.decision_id:
                    old_task = task
                    break
        if decision.status is CodingStatus.UNCODED:
            if old_task is not None:
                old_task.status = CodingReviewStatus.SUPERSEDED
                self.store.update_coding_task(old_task)
            siblings = [
                t
                for t in self.store.list_coding_tasks()
                if t.case_id == decision.case_id
                and t.subject_kind == decision.subject_kind
                and t.subject_index == decision.subject_index
            ]
            tag = "E" if decision.subject_kind == SUBJECT_EVENT else "P"
            base = f"CT-{decision.case_id}-{tag}{decision.subject_index}"
            task_id = base if not siblings else f"{base}-{len(siblings) + 1}"
            self.store.add_coding_task(
                CodingReviewTask(
                    task_id=task_id,
                    case_id=decision.case_id,
                    decision_id=decision.decision_id,
                    subject_kind=decision.subject_kind,
                    subject_index=decision.subject_index,
                    verbatim=decision.verbatim,
                    reason=decision.uncoded_reason or UNCODED_NO_ENTRY,
                    candidates=decision.candidates,
                    created_on=today,
                )
            )
        elif old_task is not None:
            old_task.status = CodingReviewStatus.RESOLVED
            old_task.resolved_by = f"dictionary:{decision.dictionary_version}"
            old_task.resolved_on = today
            old_task.resolution_decision_id = decision.decision_id
            self.store.update_coding_task(old_task)

    # -- the coder acts ---------------------------------------------------
    def uncoded_queue(self) -> list[CodingReviewTask]:
        return self.store.list_coding_tasks(status=CodingReviewStatus.OPEN.value)

    def resolve(
        self,
        task_id: str,
        coder: str,
        today: date,
        preferred_term: str | None = None,
        product_code: str | None = None,
    ) -> CodingDecision:
        """The resolution act: a named coder chooses a term that
        exists in the current dictionary (for an ambiguous task,
        one of the recorded candidates). The new decision is
        manual, supersedes the uncoded one by link, and the act is
        recorded on the case's evidence chain."""
        if not is_named_human(coder):
            raise CodingError("a coding resolution needs a named human coder")
        task = self.store.get_coding_task(task_id)
        if task is None:
            raise KeyError(f"no such coding task: {task_id}")
        if task.status is not CodingReviewStatus.OPEN:
            raise CodingTaskClosed(
                f"coding task {task_id} is {task.status.value} — decided work does not move"
            )
        dv = self.dictionary(today=today)
        uncoded = next(
            (
                d
                for d in self.store.list_coding_decisions(task.case_id)
                if d.decision_id == task.decision_id
            ),
            None,
        )
        if uncoded is None:
            raise CodingError(f"task {task_id} names a missing decision")
        if task.subject_kind == SUBJECT_EVENT:
            if not preferred_term:
                raise CodingError("an event resolution names a preferred term")
            entry = next(
                (e for e in dv.event_entries if e.preferred_term == preferred_term),
                None,
            )
            if entry is None:
                raise CodingError(
                    f"{preferred_term!r} is not a preferred term in dictionary {dv.version}"
                )
            if task.reason == UNCODED_AMBIGUOUS and preferred_term not in task.candidates:
                raise CodingError(
                    f"an ambiguous term resolves to one of its candidates"
                    f" {task.candidates}, not {preferred_term!r}"
                )
            coded_fields = {"preferred_term": entry.preferred_term, "soc": entry.soc}
            chosen = preferred_term
        else:
            if not product_code:
                raise CodingError("a product resolution names a product code")
            entry = next(
                (e for e in dv.product_entries if e.product_code == product_code),
                None,
            )
            if entry is None:
                raise CodingError(
                    f"{product_code!r} is not a coded product in dictionary {dv.version}"
                )
            if task.reason == UNCODED_AMBIGUOUS and product_code not in task.candidates:
                raise CodingError(
                    f"an ambiguous product resolves to one of its candidates"
                    f" {task.candidates}, not {product_code!r}"
                )
            coded_fields = {
                "product_code": entry.product_code,
                "product_name": entry.product_name,
                "active_substance": entry.active_substance,
            }
            chosen = product_code
        n = (
            len(
                [
                    d
                    for d in self.store.list_coding_decisions(task.case_id)
                    if (d.subject_kind, d.subject_index)
                    == (task.subject_kind, task.subject_index)
                ]
            )
            + 1
        )
        tag = "E" if task.subject_kind == SUBJECT_EVENT else "P"
        decision = CodingDecision(
            decision_id=f"CD-{task.case_id}-{tag}{task.subject_index}-{n:02d}",
            case_id=task.case_id,
            subject_kind=task.subject_kind,
            subject_index=task.subject_index,
            verbatim=task.verbatim,
            status=CodingStatus.CODED,
            match_path=CodingMatchPath.MANUAL,
            dictionary_version=dv.version,
            coded_by=coder,
            supersedes_id=uncoded.decision_id,
            coded_on=today,
            **coded_fields,
        )
        self.store.add_coding_decision(decision)
        task.status = CodingReviewStatus.RESOLVED
        task.resolved_by = coder
        task.resolved_on = today
        task.resolution_decision_id = decision.decision_id
        self.store.update_coding_task(task)
        self.chain.append(
            case_id=task.case_id,
            kind="coding_decision",
            payload={
                "task_id": task.task_id,
                "decision_id": decision.decision_id,
                "supersedes_id": uncoded.decision_id,
                "subject_kind": task.subject_kind,
                "subject_index": task.subject_index,
                "verbatim": task.verbatim,
                "uncoded_reason": task.reason,
                "chosen": chosen,
                "dictionary_version": dv.version,
                "coded_by": coder,
                "coded_on": today.isoformat(),
            },
            recorded_on=today,
            item_id=f"EV-{task.case_id}-CODING-{task.task_id}",
        )
        return decision

    def propose_addition(
        self,
        proposed_by: str,
        today: date,
        kind: str,
        entry,
        note: str = "",
    ) -> DictionaryVersion:
        """Propose a dictionary addition. It takes effect only as
        a NEW dictionary version: the current version's artefacts
        are never touched, and decisions already taken keep the
        version they pinned. Collisions that would manufacture an
        ambiguity or rewrite an existing mapping are refused."""
        if not is_named_human(proposed_by):
            raise CodingError("a dictionary addition needs a named human proposer")
        current = self.dictionary(today=today)
        if kind == SUBJECT_EVENT:
            if not (
                entry.verbatim.strip()
                and entry.preferred_term.strip()
                and entry.soc.strip()
            ):
                raise CodingError(
                    "an event entry needs a verbatim, a preferred term and an SOC"
                )
            vn = normalise_text(entry.verbatim)
            for existing in current.event_entries:
                if normalise_text(existing.verbatim) == vn:
                    raise CodingError(
                        f"{entry.verbatim!r} already codes to"
                        f" {existing.preferred_term!r} in dictionary {current.version}"
                        " — there is nothing to add"
                    )
                if vn in {normalise_text(s) for s in existing.synonyms}:
                    raise CodingError(
                        f"{entry.verbatim!r} is already a synonym of"
                        f" {existing.preferred_term!r} in dictionary {current.version}"
                    )
            for synonym in entry.synonyms:
                sn = normalise_text(synonym)
                for existing in current.event_entries:
                    maps_elsewhere = normalise_text(
                        existing.verbatim
                    ) == sn or sn in {normalise_text(s) for s in existing.synonyms}
                    if maps_elsewhere and existing.preferred_term != entry.preferred_term:
                        raise CodingError(
                            f"synonym {synonym!r} already maps to"
                            f" {existing.preferred_term!r} — the addition would"
                            " manufacture an ambiguity"
                        )
            event_entries = current.event_entries + [entry]
            product_entries = current.product_entries
        elif kind == SUBJECT_PRODUCT:
            canonical = next(
                (e for e in current.product_entries if e.product_code == entry.product_code),
                None,
            )
            if canonical is None:
                raise CodingError(
                    f"{entry.product_code!r} is not a coded product in dictionary"
                    f" {current.version} — an addition maps a new reported name"
                    " to a product the dictionary already codes"
                )
            vn = normalise_text(entry.verbatim_name)
            for existing in current.product_entries:
                same_wording = normalise_text(existing.verbatim_name) == vn or vn in {
                    normalise_text(s) for s in existing.synonyms
                }
                if same_wording:
                    raise CodingError(
                        f"{entry.verbatim_name!r} already maps to"
                        f" {existing.product_code!r} in dictionary {current.version}"
                        " — there is nothing to add"
                    )
            stored = ProductDictionaryEntry(
                verbatim_name=entry.verbatim_name,
                product_code=canonical.product_code,
                product_name=canonical.product_name,
                active_substance=canonical.active_substance,
                synonyms=list(entry.synonyms),
            )
            event_entries = current.event_entries
            product_entries = current.product_entries + [stored]
        else:
            raise CodingError(f"unknown addition kind: {kind!r}")
        new_version = DictionaryVersion(
            version=_bump(current.version),
            created_on=today,
            created_by=proposed_by,
            note=note,
            supersedes_version=current.version,
            event_entries=event_entries,
            product_entries=product_entries,
        )
        self.store.add_dictionary_version(new_version)
        return new_version
