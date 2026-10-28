"""De-duplication — Round 2.

R1 flagged exact-key suspicion. R2 replaces the suspicion with a
real dedup discipline:

- A **versioned rule pack** (data, below) scores every candidate
  pair of cases on patient identity, product, event terms, onset
  dates and reporter. Scoring is deterministic code; every point is
  a named signal in the recorded breakdown, and three **gates**
  (patient identity unproven, event terms unrelated, onset too far
  apart — plus a hard date-of-birth contradiction) fix the band at
  ``not_duplicate`` regardless of score.
- A definite/probable assessment creates a **link** between the two
  cases and routes it to a named human queue. The later case is
  marked ``duplicate_linked``. Both cases stay fully readable.
- A human confirms (the later case closes as a duplicate, by
  recorded act, its chain intact) or rejects (the link is annotated
  rejected and the case proceeds). 

There is deliberately **no merge and no delete anywhere in this
module or the store port**: duplicate cases are linked and closed,
never combined, because in a safety database each report's
provenance is itself evidence.
"""

from __future__ import annotations

from datetime import date

from pv_cases.evidence import EvidenceChain
from pv_cases.models import (
    Case,
    CaseStatus,
    DedupAssessment,
    DedupBand,
    DedupLink,
    DedupLinkStatus,
    DedupSignal,
    IntakeReport,
    Patient,
    is_named_human,
    normalise_text,
    patient_key,
)

# The rule pack, as data. Bump the version when any weight, threshold
# or gate changes; assessments record the version they ran under.
DEDUP_PACK = {
    "version": "2026.10.1",
    "weights": {
        "patient_exact_key": 40,
        "patient_dob_and_initials": 34,
        "patient_moderate_identity": 22,
        "patient_initials_only": 8,
        "product_same_code": 25,
        "product_same_name": 15,
        "event_term_exact": 20,
        "event_term_overlap": 10,
        "onset_same_day": 10,
        "onset_within_7_days": 6,
        "onset_within_30_days": 3,
        "reporter_same": 5,
    },
    "thresholds": {"definite": 80, "probable": 55},
    "onset_window_days": 90,
    "event_overlap_min": 0.5,
}

PACK_VERSION = DEDUP_PACK["version"]


class DedupDecisionError(ValueError):
    """A link decision that cannot be taken: the link is not pending,
    or the decider is not a named human."""


def _tokens(term: str) -> set[str]:
    return set(normalise_text(term).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _patient_level(a: Patient, b: Patient) -> tuple[str, str]:
    """How strongly the two patient records identify the same person:
    (level name, signal name). The caller applies the DOB
    contradiction gate before trusting any level."""
    init_a, init_b = normalise_text(a.initials or ""), normalise_text(b.initials or "")
    initials_eq = bool(init_a) and init_a == init_b
    initials_clash = bool(init_a) and bool(init_b) and init_a != init_b
    if patient_key(a) == patient_key(b) and not initials_clash:
        return "exact", "patient_exact_key"
    dob_eq = (
        a.date_of_birth is not None
        and b.date_of_birth is not None
        and a.date_of_birth == b.date_of_birth
    )
    if dob_eq and initials_eq:
        return "strong", "patient_dob_and_initials"
    pid_eq = bool(normalise_text(a.reporter_patient_id or "")) and (
        normalise_text(a.reporter_patient_id or "")
        == normalise_text(b.reporter_patient_id or "")
    )
    age_eq = a.age is not None and b.age is not None and a.age == b.age
    if pid_eq or (initials_eq and age_eq):
        return "moderate", "patient_moderate_identity"
    if initials_eq:
        return "weak", "patient_initials_only"
    return "none", ""


def assess_pair(
    case_a: Case, report_a: IntakeReport, case_b: Case, report_b: IntakeReport,
    today: date,
) -> DedupAssessment:
    """Score one pair. ``case_a`` must be the earlier case. Pure:
    builds the assessment, stores nothing."""
    weights = DEDUP_PACK["weights"]
    signals: list[DedupSignal] = []
    score = 0

    def add(name: str, detail: str, gate: bool = False) -> None:
        nonlocal score
        points = 0 if gate else weights[name]
        score += points
        signals.append(DedupSignal(name=name, points=points, detail=detail, gate=gate))

    pa, pb = report_a.patient, report_b.patient
    # Gate: a date-of-birth contradiction defeats any name match —
    # the namesake discipline. Recorded first because it decides
    # before any points are counted.
    dob_contradiction = (
        pa.date_of_birth is not None
        and pb.date_of_birth is not None
        and pa.date_of_birth != pb.date_of_birth
    )
    if dob_contradiction:
        add(
            "patient_dob_contradicts",
            f"dates of birth differ ({pa.date_of_birth} vs {pb.date_of_birth})",
            gate=True,
        )
    level, level_signal = _patient_level(pa, pb)
    if dob_contradiction or level == "none":
        add(
            "patient_identity_unproven",
            "no shared patient identity at any level",
            gate=True,
        )
    else:
        add(level_signal, f"patient identity level: {level}")

    # Product.
    code_a = normalise_text(report_a.suspect_product_code or "")
    code_b = normalise_text(report_b.suspect_product_code or "")
    name_a = normalise_text(report_a.suspect_product_name or "")
    name_b = normalise_text(report_b.suspect_product_name or "")
    if code_a and code_a == code_b:
        add("product_same_code", f"product code {report_a.suspect_product_code}")
    elif name_a and name_a == name_b:
        add("product_same_name", f"product name {report_a.suspect_product_name}")

    # Events: best pair across both reports' event lists.
    terms_a = [normalise_text(e.verbatim_term) for e in report_a.events]
    terms_b = [normalise_text(e.verbatim_term) for e in report_b.events]
    exact_pair = any(t_a == t_b and t_a for t_a in terms_a for t_b in terms_b)
    best_j = 0.0
    best_pair = ("", "")
    for t_a in terms_a:
        for t_b in terms_b:
            j = _jaccard(_tokens(t_a), _tokens(t_b))
            if j > best_j:
                best_j, best_pair = j, (t_a, t_b)
    if exact_pair:
        add("event_term_exact", "an event term matches verbatim (normalised)")
    elif best_j >= DEDUP_PACK["event_overlap_min"]:
        add(
            "event_term_overlap",
            f"closest terms {best_pair[0]!r} / {best_pair[1]!r} overlap {best_j:.2f}",
        )
    else:
        add(
            "event_terms_unrelated",
            f"closest event terms overlap {best_j:.2f} < {DEDUP_PACK['event_overlap_min']}",
            gate=True,
        )

    # Onset of the primary (first) events.
    onset_a = report_a.events[0].onset_date if report_a.events else None
    onset_b = report_b.events[0].onset_date if report_b.events else None
    if onset_a is not None and onset_b is not None:
        gap = abs((onset_a - onset_b).days)
        if gap > DEDUP_PACK["onset_window_days"]:
            add(
                "onset_gap_exceeds_window",
                f"onsets {gap} days apart (window {DEDUP_PACK['onset_window_days']})",
                gate=True,
            )
        elif gap == 0:
            add("onset_same_day", f"onset {onset_a}")
        elif gap <= 7:
            add("onset_within_7_days", f"onsets {gap} days apart")
        elif gap <= 30:
            add("onset_within_30_days", f"onsets {gap} days apart")

    # Reporter: duplicates often arrive through different reporters,
    # so a shared reporter adds a little and a different one nothing.
    rep_a = {
        normalise_text(report_a.reporter.email or ""),
        normalise_text(report_a.reporter.name or ""),
    } - {""}
    rep_b = {
        normalise_text(report_b.reporter.email or ""),
        normalise_text(report_b.reporter.name or ""),
    } - {""}
    if rep_a & rep_b:
        add("reporter_same", "same reporter name or contact")

    gated = any(s.gate for s in signals)
    thresholds = DEDUP_PACK["thresholds"]
    if gated:
        band = DedupBand.NOT_DUPLICATE
    elif score >= thresholds["definite"]:
        band = DedupBand.DEFINITE
    elif score >= thresholds["probable"]:
        band = DedupBand.PROBABLE
    else:
        band = DedupBand.NOT_DUPLICATE

    return DedupAssessment(
        assessment_id=f"DEDUP-{case_a.case_id}-{case_b.case_id}",
        case_a_id=case_a.case_id,
        case_b_id=case_b.case_id,
        pack_version=PACK_VERSION,
        score=score,
        band=band,
        signals=signals,
        assessed_on=today,
    )


def _pair_order(a: Case, b: Case) -> tuple[Case, Case]:
    """Earlier case first: by awareness date, then case id."""
    return (a, b) if (a.received_on, a.case_id) <= (b.received_on, b.case_id) else (b, a)


class DedupService:
    """Runs the pack over stored cases, keeps the links, and takes
    the human decisions. Assessments are stored once per pair per
    pack version; links are created once per pair."""

    def __init__(self, store, chain: EvidenceChain):
        self.store = store
        self.chain = chain

    # -- inputs ---------------------------------------------------------
    def report_of(self, case: Case) -> IntakeReport:
        for item in self.store.list_evidence(case.case_id):
            if item.kind == "intake_report":
                return IntakeReport.model_validate(self.chain.payload_of(item))
        raise KeyError(f"case {case.case_id} has no intake evidence")

    # -- scanning ---------------------------------------------------------
    def _assessed_pair_ids(self) -> set[tuple[str, str]]:
        return {
            (a.case_a_id, a.case_b_id)
            for a in self.store.list_dedup_assessments()
            if a.pack_version == PACK_VERSION
        }

    def scan_case(self, case_id: str, today: date) -> list[DedupLink]:
        """Assess one case against every other stored case; create
        links for new definite/probable findings."""
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        created: list[DedupLink] = []
        for other in self.store.list_cases():
            if other.case_id == case_id:
                continue
            created.extend(self._assess_and_link(case, other, today))
        return created

    def scan_all(self, today: date) -> list[DedupLink]:
        created: list[DedupLink] = []
        cases = self.store.list_cases()
        for case in cases:
            created.extend(self.scan_case(case.case_id, today))
        return created

    def _assess_and_link(self, x: Case, y: Case, today: date) -> list[DedupLink]:
        earlier, later = _pair_order(x, y)
        if (earlier.case_id, later.case_id) in self._assessed_pair_ids():
            return []
        assessment = assess_pair(
            earlier, self.report_of(earlier), later, self.report_of(later), today
        )
        self.store.add_dedup_assessment(assessment)
        if assessment.band is DedupBand.NOT_DUPLICATE:
            return []
        link = DedupLink(
            link_id=f"LINK-{earlier.case_id}-{later.case_id}",
            assessment_id=assessment.assessment_id,
            case_a_id=earlier.case_id,
            case_b_id=later.case_id,
            band=assessment.band,
            created_on=today,
        )
        self.store.add_dedup_link(link)
        if later.status is not CaseStatus.CLOSED_AS_DUPLICATE:
            later.status = CaseStatus.DUPLICATE_LINKED
            self.store.update_case(later)
        return [link]

    # -- the human queue ---------------------------------------------------
    def pending_queue(self) -> list[DedupLink]:
        return self.store.list_dedup_links(status=DedupLinkStatus.PENDING.value)

    def confirm_link(
        self, link_id: str, decided_by: str, today: date, note: str | None = None
    ) -> DedupLink:
        """Confirm: the later case closes as a duplicate of the
        earlier one. The case, its evidence and its chain stay
        exactly where they were — closure is a status and a chain
        record, not a removal."""
        link = self._pending_link(link_id, decided_by)
        link.status = DedupLinkStatus.CONFIRMED
        link.decided_by = decided_by
        link.decided_on = today
        link.decision_note = note
        self.store.update_dedup_link(link)
        case_b = self.store.get_case(link.case_b_id)
        case_b.status = CaseStatus.CLOSED_AS_DUPLICATE
        self.store.update_case(case_b)
        self._record_decision(link, "confirmed_duplicate", today)
        return link

    def reject_link(
        self, link_id: str, decided_by: str, today: date, note: str | None = None
    ) -> DedupLink:
        """Reject: the link is annotated rejected and the later case
        proceeds (its status is restored unless another link still
        holds it)."""
        link = self._pending_link(link_id, decided_by)
        link.status = DedupLinkStatus.REJECTED
        link.decided_by = decided_by
        link.decided_on = today
        link.decision_note = note
        self.store.update_dedup_link(link)
        self._record_decision(link, "rejected", today)
        case_b = self.store.get_case(link.case_b_id)
        if case_b.status is CaseStatus.DUPLICATE_LINKED:
            others = [
                l
                for l in self.store.list_dedup_links()
                if l.case_b_id == case_b.case_id and l.link_id != link.link_id
            ]
            if any(l.status is DedupLinkStatus.CONFIRMED for l in others):
                case_b.status = CaseStatus.CLOSED_AS_DUPLICATE
            elif not any(l.status is DedupLinkStatus.PENDING for l in others):
                case_b.status = (
                    CaseStatus.SUMMARY_ASSEMBLED
                    if self.store.list_summaries(case_b.case_id)
                    else CaseStatus.OPEN
                )
            self.store.update_case(case_b)
        return link

    def _pending_link(self, link_id: str, decided_by: str) -> DedupLink:
        if not is_named_human(decided_by):
            raise DedupDecisionError(
                "a dedup decision needs a named human decider"
            )
        link = self.store.get_dedup_link(link_id)
        if link is None:
            raise KeyError(f"no such dedup link: {link_id}")
        if link.status is not DedupLinkStatus.PENDING:
            raise DedupDecisionError(
                f"link {link_id} is already {link.status.value} — decided links do not move"
            )
        return link

    def _record_decision(self, link: DedupLink, decision: str, today: date) -> None:
        assessment = next(
            (
                a
                for a in self.store.list_dedup_assessments()
                if a.assessment_id == link.assessment_id
            ),
            None,
        )
        self.chain.append(
            case_id=link.case_b_id,
            kind="dedup_decision",
            payload={
                "link_id": link.link_id,
                "decision": decision,
                "decided_by": link.decided_by,
                "decided_on": today.isoformat(),
                "decision_note": link.decision_note,
                "duplicate_of": link.case_a_id,
                "band": link.band.value,
                "score": assessment.score if assessment else None,
                "pack_version": assessment.pack_version if assessment else None,
            },
            recorded_on=today,
            item_id=f"EV-{link.case_b_id}-DEDUP-{link.link_id}",
        )
