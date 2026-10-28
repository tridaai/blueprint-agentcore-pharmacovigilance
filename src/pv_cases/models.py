"""Domain models for the pharmacovigilance case agent.

The centre of the domain is a distinction drug safety runs on and this
code refuses to blur: a *report* is whatever arrived; a *case* exists
only when the four minimum reporting criteria hold. Everything in this
module is a plain record — the decisions live in
:mod:`pv_cases.validity` and :mod:`pv_cases.services`.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class ReportSource(str, Enum):
    PATIENT = "patient"
    CLINICIAN = "clinician"
    CALL_CENTRE = "call_centre"
    LITERATURE = "literature"
    SPONSOR_SITE = "sponsor_site"


class CaseStatus(str, Enum):
    OPEN = "open"
    SUMMARY_ASSEMBLED = "summary_assembled"
    # R2: a dedup link (definite/probable) is pending or confirmed
    # against this case. The case stays fully readable in both.
    DUPLICATE_LINKED = "duplicate_linked"
    # R2: a named human confirmed the link; the case is closed as a
    # duplicate of the linked case — closed, never deleted or merged.
    CLOSED_AS_DUPLICATE = "closed_as_duplicate"


class FollowUpStatus(str, Enum):
    OPEN = "open"


class Product(BaseModel):
    """A fictional Northbridge Therapeutics product (seed data)."""

    code: str
    name: str
    active_substance: str
    form: str


class Patient(BaseModel):
    """Patient facts as reported. ``identifiable`` is never stored — it
    is derived by the validity gate from what is present here."""

    initials: str | None = None
    age: int | None = None
    age_group: str | None = None
    sex: str | None = None
    date_of_birth: date | None = None
    reporter_patient_id: str | None = None


class Reporter(BaseModel):
    name: str | None = None
    qualification: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None


class AdverseEvent(BaseModel):
    verbatim_term: str
    onset_date: date | None = None
    outcome: str | None = None
    description: str | None = None


class IntakeReport(BaseModel):
    """One adverse-event report as it arrived at intake, before any
    validity judgement. The raw payload is evidence; this model is the
    structured reading of it."""

    report_id: str
    source: ReportSource
    received_on: date
    patient: Patient = Field(default_factory=Patient)
    reporter: Reporter = Field(default_factory=Reporter)
    suspect_product_code: str | None = None
    suspect_product_name: str | None = None
    events: list[AdverseEvent] = Field(default_factory=list)
    narrative: str | None = None


class ValidityAssessment(BaseModel):
    """The validity gate's verdict on one report, criterion by
    criterion. ``missing`` names every unmet criterion in the gate's
    fixed order; a report is valid iff ``missing`` is empty."""

    valid: bool
    missing: list[str] = Field(default_factory=list)


# The four minimum criteria for a valid individual case safety report,
# in the order the gate checks and reports them.
CRITERION_PATIENT = "identifiable_patient"
CRITERION_REPORTER = "identifiable_reporter"
CRITERION_PRODUCT = "suspect_product"
CRITERION_EVENT = "adverse_event"
ALL_CRITERIA = [
    CRITERION_PATIENT,
    CRITERION_REPORTER,
    CRITERION_PRODUCT,
    CRITERION_EVENT,
]

_PLACEHOLDERS = {"", "unknown", "n/a", "na", "none", "-", "?"}


def _present(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _PLACEHOLDERS


def patient_is_identifiable(patient: Patient) -> bool:
    """Identifiable means at least one recorded characteristic beyond
    a bare assertion that a patient exists: initials, an age or age
    group, a sex, a date of birth, or a reporter-held patient id."""
    return any(
        [
            _present(patient.initials),
            patient.age is not None,
            _present(patient.age_group),
            _present(patient.sex),
            patient.date_of_birth is not None,
            _present(patient.reporter_patient_id),
        ]
    )


def reporter_is_identifiable(reporter: Reporter) -> bool:
    """Identifiable means the safety team could, in principle, go back
    to this person: a name, or a direct contact channel."""
    return any(
        [
            _present(reporter.name),
            _present(reporter.email),
            _present(reporter.phone),
        ]
    )


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalise_text(text: str) -> str:
    """The package's one text normalisation (dedup, history)."""
    return _normalise(text)


def is_named_human(value: str | None) -> bool:
    """A decision act needs a name a reviewer could be held to —
    non-empty and not a placeholder."""
    return _present(value)


def patient_key(patient: Patient) -> str:
    """A stable mechanical key for the patient as reported — used only
    for exact-key duplicate suspicion in R1 (real de-duplication,
    with fuzzy matching and human review, is R2)."""
    if patient.date_of_birth is not None:
        basis = f"dob:{patient.date_of_birth.isoformat()}"
    elif _present(patient.reporter_patient_id):
        basis = f"pid:{_normalise(patient.reporter_patient_id or '')}"
    else:
        basis = f"init:{_normalise(patient.initials or '')}"
    sex = _normalise(patient.sex or "")
    return f"{basis}|{sex}"


def dedup_key(report: IntakeReport) -> str:
    """Exact mechanical key: same patient + same product + same first
    event term + same onset date. Two valid reports sharing a key are
    *suspected* duplicates — flagged, never merged, in R1."""
    first = report.events[0] if report.events else None
    parts = [
        patient_key(report.patient),
        _normalise(report.suspect_product_code or report.suspect_product_name or ""),
        _normalise(first.verbatim_term if first else ""),
        first.onset_date.isoformat() if first and first.onset_date else "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class Case(BaseModel):
    case_id: str
    report_id: str
    source: ReportSource
    received_on: date
    awareness_date: date
    product_code: str | None = None
    status: CaseStatus = CaseStatus.OPEN
    dedup_key: str
    suspected_duplicate_of: str | None = None


class FollowUpTask(BaseModel):
    """What an invalid report becomes: tracked work to chase the
    missing criteria — never a case."""

    task_id: str
    report_id: str
    missing_criteria: list[str]
    status: FollowUpStatus = FollowUpStatus.OPEN
    created_on: date


class EvidenceItem(BaseModel):
    item_id: str
    case_id: str
    kind: str
    object_key: str
    payload_sha256: str
    prev_hash: str
    item_hash: str
    recorded_on: date


class Citation(BaseModel):
    """A pointer from one summary statement to the evidence that
    supports it: an evidence item plus a field path inside its
    payload. A citation that does not resolve is refused in code."""

    evidence_item_id: str
    field: str


class SummaryStatement(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)


class CaseSummary(BaseModel):
    """The R1 agent step's output: a case summary record whose every
    statement carries citations into the case's evidence."""

    summary_id: str
    case_id: str
    model_profile: str
    statements: list[SummaryStatement]
    assembled_on: date


# ---------------------------------------------------------------------
# Round 2: de-duplication.
#
# A dedup *assessment* is the rule pack's scored verdict on one pair
# of cases, with every signal that produced the score recorded. A
# *link* is what a definite/probable assessment creates: a pending
# human decision. Nothing in this module merges anything — there is
# no merge artefact because there is no merge.
# ---------------------------------------------------------------------


class DedupBand(str, Enum):
    DEFINITE = "definite_duplicate"
    PROBABLE = "probable_duplicate"
    NOT_DUPLICATE = "not_duplicate"


class DedupSignal(BaseModel):
    """One scored (or gating) observation behind an assessment.
    ``gate`` signals carry no points: when one fires it fixes the
    band regardless of the score, and it says why."""

    name: str
    points: int = 0
    detail: str = ""
    gate: bool = False


class DedupAssessment(BaseModel):
    assessment_id: str
    case_a_id: str  # the earlier case of the pair
    case_b_id: str  # the later case of the pair
    pack_version: str
    score: int
    band: DedupBand
    signals: list[DedupSignal] = Field(default_factory=list)
    assessed_on: date


class DedupLinkStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class DedupLink(BaseModel):
    """A pending (then decided) duplicate link between two cases.
    Both cases stay readable forever; confirmation closes the later
    case as a duplicate by recorded human act, rejection annotates
    the link and lets the later case proceed."""

    link_id: str
    assessment_id: str
    case_a_id: str
    case_b_id: str
    band: DedupBand
    status: DedupLinkStatus = DedupLinkStatus.PENDING
    created_on: date
    decided_by: str | None = None
    decided_on: date | None = None
    decision_note: str | None = None


# ---------------------------------------------------------------------
# Round 2: history views.
#
# Derived-in-code views over stored artefacts only. A view is
# content-addressed: rebuilding it over unchanged inputs returns the
# stored view. Facts keep the case they were read from, and identity
# conflicts are shown — never merged into a single "true" value.
# ---------------------------------------------------------------------


class IdentityFact(BaseModel):
    field: str
    value: str
    case_id: str
    report_id: str


class HistoryEvent(BaseModel):
    case_id: str
    report_id: str
    product_code: str | None = None
    verbatim_term: str
    onset_date: date | None = None
    outcome: str | None = None


class PatientHistoryView(BaseModel):
    view_id: str
    patient_key: str
    version: int
    built_on: date
    case_ids: list[str] = Field(default_factory=list)
    case_count: int = 0
    event_count: int = 0
    events: list[HistoryEvent] = Field(default_factory=list)
    event_term_counts: dict[str, int] = Field(default_factory=dict)
    # Round 3: dictionary coding of this view's events — preferred
    # terms counted where coded, uncoded verbatims counted
    # separately and named, never folded into a coded count.
    coded_term_counts: dict[str, int] = Field(default_factory=dict)
    uncoded_terms: list[str] = Field(default_factory=list)
    uncoded_event_count: int = 0
    product_codes: list[str] = Field(default_factory=list)
    identity_facts: list[IdentityFact] = Field(default_factory=list)
    identity_conflicts: dict[str, list[str]] = Field(default_factory=dict)
    follow_up_task_ids: list[str] = Field(default_factory=list)
    case_statuses: dict[str, str] = Field(default_factory=dict)


class ProductHistoryView(BaseModel):
    view_id: str
    product_code: str
    product_name: str | None = None
    version: int
    built_on: date
    case_ids: list[str] = Field(default_factory=list)
    case_count: int = 0
    event_count: int = 0
    events: list[HistoryEvent] = Field(default_factory=list)
    event_term_counts: dict[str, int] = Field(default_factory=dict)
    patient_keys: list[str] = Field(default_factory=list)
    follow_up_task_ids: list[str] = Field(default_factory=list)
    case_statuses: dict[str, str] = Field(default_factory=dict)
    # Round 3: the dictionary coding of this view's events and of
    # the product itself — preferred terms counted where coded,
    # uncoded verbatims counted separately and named, never folded
    # into a coded count.
    coded_term_counts: dict[str, int] = Field(default_factory=dict)
    uncoded_terms: list[str] = Field(default_factory=list)
    uncoded_event_count: int = 0
    uncoded_product_names: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Round 3: dictionary coding.
#
# Coding is a deterministic lookup against a versioned terminology
# EXTRACT built for this repo — original data, not licensed MedDRA.
# A *dictionary version* is an immutable artefact: additions take
# effect only as a new version, never as an edit. A *coding
# decision* pins (case, subject, dictionary version, verbatim as
# reported) and records the match path taken. An uncoded decision is
# a first-class outcome with a named reason and a review task — it
# is never a guess at a nearby term.
# ---------------------------------------------------------------------

# Subject kinds a coding decision can be about.
SUBJECT_EVENT = "event"
SUBJECT_PRODUCT = "product"

# Uncoded reasons (CodingDecision.uncoded_reason).
UNCODED_NO_ENTRY = "no_dictionary_entry"
UNCODED_AMBIGUOUS = "ambiguous_match"


class CodingMatchPath(str, Enum):
    EXACT = "exact"  # verbatim as reported == dictionary verbatim
    NORMALISED = "normalised"  # equal after case/punctuation folding
    SYNONYM = "synonym"  # matches a recorded synonym of the entry
    MANUAL = "manual"  # resolved by a named human coder's act
    NONE = "none"  # uncoded — no path taken


class CodingStatus(str, Enum):
    CODED = "coded"
    UNCODED = "uncoded"


class DictionaryEntry(BaseModel):
    """One event entry of the extract: a reporter verbatim, the
    preferred term it codes to, its system organ class, and the
    other reporter wordings recorded as synonyms of the verbatim."""

    verbatim: str
    preferred_term: str
    soc: str
    synonyms: list[str] = Field(default_factory=list)


class ProductDictionaryEntry(BaseModel):
    """One product entry of the extract: a product name as reported,
    and the coded product (code, name, active substance) it maps to."""

    verbatim_name: str
    product_code: str
    product_name: str
    active_substance: str
    synonyms: list[str] = Field(default_factory=list)


class DictionaryVersion(BaseModel):
    """One immutable version of the terminology extract. The store
    port has no update or delete for these — a change is a new
    version that supersedes the old one, which stays readable."""

    version: str
    created_on: date
    created_by: str
    note: str = ""
    supersedes_version: str | None = None
    event_entries: list[DictionaryEntry] = Field(default_factory=list)
    product_entries: list[ProductDictionaryEntry] = Field(default_factory=list)


class CodingDecision(BaseModel):
    """The coding of one subject (one event, or the suspect product)
    of one case. Immutable once stored; a later coding of the same
    subject is a new decision linked by ``supersedes_id``, and the
    *current* decision for a subject is the one nothing supersedes."""

    decision_id: str
    case_id: str
    subject_kind: str  # SUBJECT_EVENT | SUBJECT_PRODUCT
    subject_index: int  # the event's index in the report; 0 for the product
    verbatim: str  # as reported — preserved exactly, coded or not
    status: CodingStatus
    match_path: CodingMatchPath
    dictionary_version: str
    # Coded event fields.
    preferred_term: str | None = None
    soc: str | None = None
    # Coded product fields.
    product_code: str | None = None
    product_name: str | None = None
    active_substance: str | None = None
    # Uncoded fields: why, and (for an ambiguous match) the labels
    # the verbatim genuinely maps to — the coder's choice is from
    # these, recorded, never silent.
    uncoded_reason: str | None = None
    candidates: list[str] = Field(default_factory=list)
    coded_by: str | None = None  # the named coder, for MANUAL decisions
    supersedes_id: str | None = None
    coded_on: date


# ---------------------------------------------------------------------
# Round 4: seriousness, expectedness, regulatory clocks.
#
# A *case assessment* is the seriousness + expectedness + clock
# verdict on one case, computed in code from the case's coded facts
# under three versioned packs (the seriousness criteria pack, the
# reference safety information, the clock pack). Like a coding
# decision it is an insert-only artefact: when the facts it read
# change, a new assessment supersedes the old one by link, with the
# change recorded — the old verdict stays readable, because a clock
# that was reported against is itself evidence.
# ---------------------------------------------------------------------

# The six ICH seriousness criteria, in the order the engine
# evaluates and reports them.
CRITERION_DEATH = "death"
CRITERION_LIFE_THREATENING = "life_threatening"
CRITERION_HOSPITALISATION = "hospitalisation"
CRITERION_DISABILITY = "disability"
CRITERION_CONGENITAL = "congenital_anomaly"
CRITERION_OTHER_IMPORTANT = "other_medically_important"
SERIOUSNESS_CRITERIA = [
    CRITERION_DEATH,
    CRITERION_LIFE_THREATENING,
    CRITERION_HOSPITALISATION,
    CRITERION_DISABILITY,
    CRITERION_CONGENITAL,
    CRITERION_OTHER_IMPORTANT,
]


class CriterionStatus(str, Enum):
    FIRED = "fired"
    NOT_FIRED = "not_fired"
    # The fact that would decide this criterion is absent from the
    # record. Named, never assumed negative.
    CANNOT_ASSESS = "cannot_assess"


class CriterionFinding(BaseModel):
    """One criterion's verdict for one case. Exactly one of
    ``deciding_fact`` (what fired it) / ``missing_fact`` (what
    absence blocks it) is set when the status is fired /
    cannot_assess; a not-fired criterion read real facts and found
    none of them positive."""

    criterion: str
    status: CriterionStatus
    deciding_fact: str | None = None
    missing_fact: str | None = None
    detail: str = ""


class SeriousnessOutcome(str, Enum):
    SERIOUS = "serious"
    NON_SERIOUS = "non_serious"
    CANNOT_ASSESS = "cannot_assess"


class Expectedness(str, Enum):
    EXPECTED = "expected"
    UNEXPECTED = "unexpected"
    CANNOT_ASSESS = "cannot_assess"


# Expectedness bases (EventExpectedness.basis).
EXP_LABELLED = "labelled_term"
EXP_MORE_SPECIFIC = "more_specific_than_labelled"
EXP_NOT_IN_LABEL = "not_in_label"
EXP_EVENT_UNCODED = "event_uncoded"
EXP_PRODUCT_UNIDENTIFIED = "product_not_identified"


class EventExpectedness(BaseModel):
    """One event's expectedness against the reference safety
    information for the case's product."""

    event_index: int
    coding_decision_id: str
    preferred_term: str | None = None
    status: Expectedness
    basis: str  # one of the EXP_* constants above
    labelled_term: str | None = None


# Reporting obligation classes (ReportingClock.obligation_class).
OBLIGATION_7_DAY = "expedited_7_day"
OBLIGATION_15_DAY = "expedited_15_day"
OBLIGATION_PERIODIC = "periodic"
OBLIGATION_AWAITING = "awaiting_assessment"


class ReportingClock(BaseModel):
    """The case's reporting obligation, computed in code. Day zero
    is stated by the clock pack's rule (the first valid receipt
    date — the case's awareness date) and never moves, even when a
    later assessment supersedes this one."""

    obligation_class: str
    day_zero: date
    deadline: date | None = None
    clock_pack_version: str
    basis: str = ""


class CaseAssessment(BaseModel):
    """The seriousness + expectedness + clock verdict on one case,
    pinned to exactly what it read: the report (by fingerprint),
    the current coding decisions (by id), and the three pack
    versions. Immutable once stored; a re-assessment over changed
    inputs is a new artefact superseding this one by link."""

    assessment_id: str
    case_id: str
    criteria_pack_version: str
    rsi_version: str
    clock_pack_version: str
    product_code: str | None = None
    coding_decision_ids: list[str] = Field(default_factory=list)
    input_fingerprint: str
    seriousness: SeriousnessOutcome
    criteria: list[CriterionFinding] = Field(default_factory=list)
    expectedness: Expectedness
    event_expectedness: list[EventExpectedness] = Field(default_factory=list)
    clock: ReportingClock
    # What changed versus the superseded assessment:
    # {field: [old, new]} over seriousness / expectedness /
    # obligation_class / deadline. None for a first assessment;
    # an empty dict means the inputs moved but the verdicts did not.
    change: dict | None = None
    supersedes_id: str | None = None
    assessed_on: date


class ClockBoardEntry(BaseModel):
    """One case's clock as read on a board at an explicit ``as_of``:
    the state and the days remaining are derived at read time from
    the stored clock artefact — never stored themselves."""

    case_id: str
    assessment_id: str
    seriousness: SeriousnessOutcome
    expectedness: Expectedness
    obligation_class: str
    day_zero: date
    deadline: date | None = None
    days_remaining: int | None = None
    state: str  # within_clock | due_today | breached | the class itself when no deadline


class LabelledTerm(BaseModel):
    """One labelled event term in the reference safety information:
    the term the product's label covers, and the coded terms that
    are MORE specific/severe than the label covers — an event coded
    to one of those is unexpected even though its base term is
    labelled."""

    preferred_term: str
    more_specific_terms: list[str] = Field(default_factory=list)


class ReferenceSafetyInfo(BaseModel):
    """The versioned reference safety information extract — a
    repo-built stand-in for each product's real label (the
    company core data sheet). Immutable once stored, like the
    dictionary it parallels."""

    version: str
    created_on: date
    created_by: str
    note: str = ""
    products: dict[str, list[LabelledTerm]] = Field(default_factory=dict)


class CodingReviewStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"  # a recode replaced the uncoded decision


class CodingReviewTask(BaseModel):
    """The coding-review queue item an uncoded decision raises. A
    task closes by a named coder's resolution act, or as superseded
    when a recode under a newer dictionary replaces the decision."""

    task_id: str
    case_id: str
    decision_id: str  # the uncoded decision awaiting resolution
    subject_kind: str
    subject_index: int
    verbatim: str
    reason: str  # UNCODED_NO_ENTRY | UNCODED_AMBIGUOUS
    candidates: list[str] = Field(default_factory=list)
    status: CodingReviewStatus = CodingReviewStatus.OPEN
    created_on: date
    resolved_by: str | None = None
    resolved_on: date | None = None
    resolution_decision_id: str | None = None
