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


# ---------------------------------------------------------------------
# Round 5: the follow-up workflow.
#
# A *follow-up request* is the workflow task the system raises when
# a report or a case is missing a fact only a follow-up report can
# supply: a validity criterion (the R1 task's missing list, made
# specific), a codable wording for an uncoded event, or the fact an
# assessment names as absent. It is a distinct artefact from the
# R1 ``FollowUpTask`` — that one is the intake record of an invalid
# report; this one is the tracked work, with a due date from a
# versioned pack and a lifecycle.
#
# Requests and their acts are both insert-only artefacts. A request
# never changes in place: a follow-up report answering it is
# recorded as an *act* (an attempt that rolls the due date forward,
# a resolution, or an escalation at the pack's cycle cap), and the
# request's current state is derived from its acts in code.
# ---------------------------------------------------------------------

# Follow-up request sources.
FU_SOURCE_VALIDITY = "validity"
FU_SOURCE_CODING = "coding"
FU_SOURCE_ASSESSMENT = "assessment"

# Missing-item classes (MissingItem.item_class). The follow-up pack
# prices a due date per class; a request's governing days are the
# slowest class among its items. The validity classes reuse the
# gate's criterion names.
FU_CLASS_UNCODED_EVENT = "uncoded_event"
FU_CLASS_MISSING_FACT = "missing_case_fact"

# Follow-up act kinds.
FU_ACT_ATTEMPT = "attempt"
FU_ACT_RESOLUTION = "resolution"
FU_ACT_ESCALATION = "escalation"

# Derived task states. ``overdue`` is an open task read after its
# current due date; ``escalated`` is the named state a task lands
# in when its last allowed cycle fails — handed to a human queue,
# never silently dropped.
FU_STATE_OPEN = "open"
FU_STATE_OVERDUE = "overdue"
FU_STATE_RESOLVED = "resolved"
FU_STATE_ESCALATED = "escalated"


class MissingItem(BaseModel):
    """One fact a follow-up report must supply, named exactly: the
    field it lands in, the class the pack prices it under, and a
    detail sentence stating what is needed and why. A follow-up
    request is a list of these — never a generic "more info
    needed"."""

    item_class: str
    field: str
    detail: str
    # What this absence blocks: criterion names for assessment
    # items, ["coding"] for an uncoded event, the criterion itself
    # for a validity item.
    blocks: list[str] = Field(default_factory=list)
    event_index: int | None = None
    verbatim: str | None = None  # coding items: the term as reported


class FollowUpRequest(BaseModel):
    """One follow-up workflow task. ``case_id`` is None only for a
    validity request, whose report has no case yet; the case it
    creates is named by its resolution act. ``due_on`` is the
    cycle-1 due date — later cycles' due dates live on the
    attempt acts, and the current due date is derived."""

    task_id: str
    source: str  # FU_SOURCE_*
    report_id: str  # the report whose deficiency raised this task
    case_id: str | None = None
    subject_index: int | None = None  # coding requests: the event index
    decision_id: str | None = None  # coding requests: the raising decision
    items: list[MissingItem] = Field(default_factory=list)
    pack_version: str
    class_days: int  # the governing (slowest) class's days
    raised_on: date
    due_on: date


class FollowUpAct(BaseModel):
    """One recorded answer to a follow-up request: the follow-up
    report that arrived, the cycle it answered, and what it did —
    resolved the request, rolled it into its next cycle with a new
    due date, or escalated it at the cycle cap. A resolution of a
    validity request names the case the follow-up created."""

    act_id: str
    task_id: str
    kind: str  # FU_ACT_*
    follow_up_report_id: str
    case_id: str | None = None
    cycle: int
    new_due_on: date | None = None
    recorded_on: date
    detail: str = ""


class FollowUpTaskView(BaseModel):
    """A request read at an explicit ``as_of``: the stored artefact
    plus the state, cycle and due date derived from its acts.
    ``days_overdue`` is 0 unless the task stands open past its
    current due date — ageing is a read-time derivation, like the
    clock board's."""

    task: FollowUpRequest
    state: str
    cycle: int
    due_on: date
    days_overdue: int = 0
    # The case the task belongs to: its own case, or (for a
    # resolved validity request) the case its follow-up created.
    case_id: str | None = None
    acts: list[FollowUpAct] = Field(default_factory=list)


class FollowUpBoardEntry(BaseModel):
    """One row of the follow-up board at an ``as_of``: unresolved
    work first (escalated, then most-overdue), each entry naming
    the exact fields still missing."""

    task_id: str
    source: str
    case_id: str | None = None
    report_id: str
    state: str
    cycle: int
    due_on: date
    days_overdue: int = 0
    missing_fields: list[str] = Field(default_factory=list)


class FollowUpSubmission(BaseModel):
    """The recorded outcome of one follow-up report submission."""

    task_id: str
    follow_up_report_id: str
    act_id: str
    outcome: str  # the act kind recorded: attempt | resolution | escalation
    state: str  # the task's state after the act
    cycle: int  # the task's cycle after the act
    due_on: date  # the task's current due date after the act
    case_id: str | None = None
    new_case_id: str | None = None  # set when a validity follow-up created the case
    links_created: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Round 6: medical review — dual control.
#
# A *medical review act* is one named reviewer's recorded decision
# on one case's current record: the assessment, the coding
# decisions and the assembled summary, each bound by id and hash at
# the moment of the act. Acts are insert-only artefacts, stored in
# the case store and recorded on the case's evidence chain; a
# case's review state is derived from its acts in code, never
# written back. Dual control (design Law 5) is mechanical: the
# gate completes only on current accept acts from both reviewer
# roles, recorded by two distinct people.
# ---------------------------------------------------------------------

# Reviewer roles (the review pack's role list).
REVIEW_ROLE_MEDICAL = "medical_reviewer"
REVIEW_ROLE_SAFETY = "safety_reviewer"
REVIEW_ROLES = [REVIEW_ROLE_MEDICAL, REVIEW_ROLE_SAFETY]

# Review decisions. A reject must carry its reason and an
# accept-with-comment its comment — the rationale is part of the
# act, not an afterthought.
REVIEW_ACCEPT = "accept"
REVIEW_ACCEPT_WITH_COMMENT = "accept_with_comment"
REVIEW_REJECT = "reject_with_reason"
REVIEW_DECISIONS = [REVIEW_ACCEPT, REVIEW_ACCEPT_WITH_COMMENT, REVIEW_REJECT]
REVIEW_ACCEPT_DECISIONS = [REVIEW_ACCEPT, REVIEW_ACCEPT_WITH_COMMENT]

# Derived case review states.
REVIEW_STATE_AWAITING_FIRST = "awaiting_first_review"
REVIEW_STATE_AWAITING_SECOND = "awaiting_second_review"
REVIEW_STATE_REVIEWED = "medically_reviewed"
REVIEW_STATE_HOLD = "review_hold"

# Review queue buckets: the four kinds of review work. A stale
# case is one whose recorded acts no longer bind the current
# record and which holds no current accept — it needs a fresh
# review, and the queue says so separately from never-reviewed
# work.
REVIEW_BUCKET_HOLD = "review_hold"
REVIEW_BUCKET_AWAITING_SECOND = "awaiting_second_review"
REVIEW_BUCKET_STALE = "stale_review"
REVIEW_BUCKET_AWAITING_FIRST = "awaiting_first_review"


class ReviewBinding(BaseModel):
    """One artefact a review act binds: its kind, its id, and a
    hash of the exact version reviewed. For the summary the hash
    covers the narrative content (statements + model profile) —
    summary ids are per-assembly sequence numbers, not versions,
    so a re-assembly of identical text is the same narrative,
    while any change to the text is a new one."""

    artefact_kind: str  # assessment | coding_decision | summary
    artefact_id: str
    artefact_hash: str


class MedicalReviewAct(BaseModel):
    """One reviewer's decision on one case, recorded once and
    never edited. The act binds the record it reviewed; when any
    bound artefact is superseded the act goes stale — it stays on
    the trail, labelled, and stops counting toward the gate."""

    act_id: str
    case_id: str
    reviewer: str
    role: str  # REVIEW_ROLE_*
    decision: str  # REVIEW_DECISIONS
    rationale: str = ""
    pack_version: str
    bindings: list[ReviewBinding] = Field(default_factory=list)
    recorded_on: date


class ReviewActView(BaseModel):
    """One act read against the case's current record: whether it
    still binds that record, and — when it does not — the ids of
    the artefacts that superseded the ones it bound."""

    act: MedicalReviewAct
    stale: bool
    superseded_by: list[str] = Field(default_factory=list)


class CaseReviewView(BaseModel):
    """A case's review position, derived from its acts: the state,
    which roles hold a current accept (and by whom), which roles
    are still missing, and — on a hold — the rejecting act and its
    reason, on the case's face."""

    case_id: str
    state: str  # REVIEW_STATE_*
    pack_version: str
    accepted_roles: dict[str, str] = Field(default_factory=dict)
    missing_roles: list[str] = Field(default_factory=list)
    hold_act_id: str | None = None
    hold_by: str | None = None
    hold_role: str | None = None
    hold_reason: str | None = None
    has_stale_acts: bool = False
    acts: list[ReviewActView] = Field(default_factory=list)


class ReviewQueueEntry(BaseModel):
    """One row of the medical-review queue. PHI minimisation is
    structural: the entry carries the case id, its seriousness,
    expectedness and clock position, and its review state —
    nothing that identifies the patient or the reporter."""

    case_id: str
    bucket: str  # REVIEW_BUCKET_*
    review_state: str
    seriousness: str | None = None
    expectedness: str | None = None
    obligation_class: str | None = None
    clock_state: str | None = None
    deadline: date | None = None
    days_remaining: int | None = None
    has_stale_acts: bool = False


# ---------------------------------------------------------------------
# Round 7: sealing, export, offline verification.
#
# A *case seal* is the named act that closes a medically reviewed
# case file: it binds the case's full artefact inventory (every
# artefact by kind, id and content hash), the evidence chain's
# head at seal time, the review bindings the gate was satisfied
# over, and the pack versions in force. A sealed case is a closed
# record — the service layer refuses new follow-ups, recodes,
# assessments, review acts and summaries for it, and there is no
# unseal: a correction is a new case. A *case export* is one
# recorded serving of the sealed inventory as a deterministic
# package; the export record (who, when, the package's hash) is
# stored and written on the case's evidence chain. Neither a seal
# nor an export submits anything anywhere — they produce a
# reviewable package, and what a sponsor does with it is the
# sponsor's regulated process.
# ---------------------------------------------------------------------

# The export package format this round defines. A verifier that
# meets any other format string refuses it by name.
PACKAGE_FORMAT = "pv-case-export/1"

# Inventory artefact kinds a seal binds.
SEAL_KIND_CASE = "case"
SEAL_KIND_CODING = "coding_decision"
SEAL_KIND_ASSESSMENT = "assessment"
SEAL_KIND_SUMMARY = "summary"
SEAL_KIND_REVIEW = "review_act"
SEAL_KIND_FU_REQUEST = "follow_up_request"
SEAL_KIND_FU_ACT = "follow_up_act"
SEAL_KIND_EVIDENCE = "evidence_item"


class SealedArtefact(BaseModel):
    """One line of a seal's inventory: an artefact the seal binds,
    by kind, id and the hash of its exact stored form. For an
    evidence item the hash is the item's chain hash — it covers
    the payload digest and the item's predecessor."""

    artefact_kind: str  # SEAL_KIND_*
    artefact_id: str
    artefact_hash: str


class CaseSeal(BaseModel):
    """The seal over one case's file, recorded once and never
    edited. ``chain_head_hash`` is the head of the case's evidence
    chain at seal time — the seal's own chain item, and any later
    export items, stand after it and are not part of the sealed
    record. ``inventory_hash`` is a hash of the whole inventory,
    so exactness can be checked without walking every line."""

    seal_id: str
    case_id: str
    sealed_by: str
    sealed_on: date
    pack_version: str  # the sealing pack the seal was taken under
    review_pack_version: str
    # The review position the seal was taken from, and the acts
    # that satisfied the gate (role -> act id), over exactly the
    # bindings below.
    review_state: str  # always REVIEW_STATE_REVIEWED — checked at seal time
    accepted_acts: dict[str, str] = Field(default_factory=dict)
    review_bindings: list[ReviewBinding] = Field(default_factory=list)
    # The current record the gate decided, named for the verifier.
    assessment_id: str
    coding_decision_ids: list[str] = Field(default_factory=list)
    dictionary_versions: list[str] = Field(default_factory=list)
    criteria_pack_version: str
    rsi_version: str
    clock_pack_version: str
    chain_head_hash: str
    chain_items: int
    inventory: list[SealedArtefact] = Field(default_factory=list)
    inventory_hash: str


class CaseExport(BaseModel):
    """One recorded serving of a sealed case's export package.
    The package bytes are a deterministic function of the sealed
    inventory, so two exports of one seal carry the same
    ``package_hash``; the records differ (exporter, date, id) and
    each is written on the case's evidence chain."""

    export_id: str
    case_id: str
    seal_id: str
    exported_by: str
    exported_on: date
    package_format: str  # PACKAGE_FORMAT
    package_hash: str


def seal_refusal_text(seal: "CaseSeal", artefact: str) -> str:
    """The refusal every service layer raises when work is
    offered to a sealed case. One wording, everywhere: the seal
    is named, and so is the only road left — a new case."""
    return (
        f"case {seal.case_id} is sealed ({seal.seal_id}, sealed by"
        f" {seal.sealed_by} on {seal.sealed_on}) — a sealed case is"
        f" a closed record and takes no new {artefact}; a"
        " correction is a new case"
    )


class PackageCheck(BaseModel):
    """One named check of the offline verifier, with what it
    found. A check the package gives the verifier no way to run
    (a missing section, an unparseable artefact) fails — absence
    of evidence is not a pass."""

    name: str
    passed: bool
    detail: str = ""


class VerificationReport(BaseModel):
    """The offline verifier's verdict on one export package: the
    package's identity as computed (never as claimed), every
    named check, and the overall verdict — ``pass`` only when
    every check passes."""

    package_format: str | None = None
    case_id: str | None = None
    seal_id: str | None = None
    package_hash: str = ""
    checks: list[PackageCheck] = Field(default_factory=list)
    verdict: str = "fail"  # pass | fail


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


# ---------------------------------------------------------------------
# Round 8: injection quarantine, the Memory port, the Gateway +
# Policy ports.
#
# Three regulated-case disciplines the earlier rounds left as
# ports on the architecture page, made real:
#
# - A *quarantine record* is the screening verdict on one free-text
#   field of one report on a case's chain (the report narrative, an
#   event description): the field is held — it reaches no drafting
#   or summary surface and no model prompt — until a named
#   reviewer's *quarantine act* releases it. The source text itself
#   is never altered: it stands in the evidence payload exactly as
#   reported, and the record names the field, the text's hash and
#   the pack rules that fired. Design Law 7 made mechanical:
#   instructions inside source documents are quarantined, never
#   obeyed.
# - A *memory retrieval* is one recorded act of cross-case recall
#   through the Memory port: the strategy, the requesting case,
#   and the matches — each match a reference (case id, artefact
#   ids, the coded facts that matched), never a copy of another
#   case's facts and never anything that identifies a patient or
#   a reporter. Retrievals are insert-only, expire under the
#   memory pack's retention rule, and expire as tombstones: the
#   record stands, the recall stops serving it.
# - A *gateway call* is one tool invocation through the Gateway
#   port, judged by the policy pack before the tool runs: the
#   principal, their role, the tool, the PHI family it touches,
#   the decision and the pack rule that decided. A denial is the
#   AgentCore shape — HTTP 200 carrying a JSON-RPC error with
#   code -32002 — and the tool behind it never executes.
#
# All three artefact families are insert-only, like everything
# since Round 3: there is no update or delete for any of them.
# ---------------------------------------------------------------------

# Quarantine finding kinds (the screening pack's classes).
QUARANTINE_KIND_INSTRUCTION = "instruction_directive"
QUARANTINE_KIND_ROLE_PLAY = "role_impersonation"
QUARANTINE_KIND_VERDICT = "verdict_directive"
QUARANTINE_KIND_AUTHORITY = "authority_impersonation"
QUARANTINE_KIND_ENCODED = "encoded_payload"

# Quarantine act kinds. Release is the only act: a hold is the
# record's existence, and it ends only by a named release.
QUARANTINE_ACT_RELEASE = "release"


class QuarantineFinding(BaseModel):
    """One screening-pack rule that fired on one held field: the
    rule, its class, and the matched span (truncated) — enough for
    a reviewer to judge the hold without the drafting surfaces
    ever seeing the text."""

    rule_id: str
    kind: str  # QUARANTINE_KIND_*
    excerpt: str


class QuarantineRecord(BaseModel):
    """The screening verdict on one free-text field of one report
    on one case's chain. The record is content-addressed over
    (case, evidence item, field, text hash): re-screening the
    same text is the same record, and a follow-up that changes
    the text is a new field version with its own verdict."""

    record_id: str
    case_id: str
    evidence_item_id: str
    source_kind: str  # intake_report | follow_up_report | original_report
    field: str  # narrative | events.<i>.description
    text_sha256: str
    findings: list[QuarantineFinding] = Field(default_factory=list)
    pack_version: str
    recorded_on: date


class QuarantineAct(BaseModel):
    """A named reviewer's release of one held field. The act
    names the reviewer and carries their rationale; the field is
    usable again from the act onward, as data — the act never
    edits the record or the source text."""

    act_id: str
    record_id: str
    case_id: str
    kind: str  # QUARANTINE_ACT_RELEASE
    released_by: str
    rationale: str = ""
    pack_version: str
    recorded_on: date


class QuarantineFieldView(BaseModel):
    """One held (or released) field read against the case's acts:
    the record, whether a release stands over it, and the act
    that released it where one exists."""

    record: QuarantineRecord
    released: bool
    release_act_id: str | None = None
    released_by: str | None = None


# Memory strategies (the memory pack's recall shapes).
MEMORY_STRATEGY_SAME_PATIENT = "same_patient"
MEMORY_STRATEGY_SAME_PRODUCT_EVENT = "same_product_event"
MEMORY_STRATEGY_CODING_PRECEDENT = "coding_precedent"
MEMORY_STRATEGIES = [
    MEMORY_STRATEGY_SAME_PATIENT,
    MEMORY_STRATEGY_SAME_PRODUCT_EVENT,
    MEMORY_STRATEGY_CODING_PRECEDENT,
]


class MemoryMatch(BaseModel):
    """One prior case a retrieval matched, by reference only: the
    case id, the artefacts that evidence the match, and the coded
    facts the match rests on. No patient or reporter field, no
    narrative text — a match points at a case file, it does not
    copy one, and it never merges the two cases' facts."""

    case_id: str
    basis: str  # same_patient_key | same_product_and_coded_term | same_verbatim_term
    artefact_ids: list[str] = Field(default_factory=list)
    coded_term: str | None = None
    seriousness: str | None = None


class MemoryRetrieval(BaseModel):
    """One recorded recall through the Memory port. The subject
    is carried as a sha256 fingerprint, never as the key itself:
    a patient key is built from identifying characteristics, and
    the retrieval record does not need it to prove what it did.
    ``expires_on`` is the retention pack's date; past it the
    retrieval is a tombstone — kept, counted, never served."""

    retrieval_id: str
    case_id: str | None = None  # the requesting case; None = desk recall
    requested_by: str
    requested_on: date
    strategy: str  # MEMORY_STRATEGY_*
    subject_fingerprint: str
    subject_label: str  # a non-identifying description of the subject
    matches: list[MemoryMatch] = Field(default_factory=list)
    pack_version: str
    expires_on: date


# Gateway decisions and the denial's wire shape (design: Policy
# denials arrive as HTTP 200 + JSON-RPC -32002, never as a 403).
GATEWAY_DECISION_ALLOW = "allow"
GATEWAY_DECISION_DENY = "deny"
GATEWAY_DENIAL_CODE = -32002
GATEWAY_DENIAL_HTTP_STATUS = 200

# PHI families a gateway tool touches (the policy pack's caps are
# stated per family).
GATEWAY_PHI_NONE = "none"
GATEWAY_PHI_CASE_RECORD = "case_record"
GATEWAY_PHI_PATIENT_HISTORY = "patient_history"


class GatewayCall(BaseModel):
    """One tool invocation through the Gateway port, judged by
    the policy pack before the tool ran: who called, in what
    role, which tool, the PHI family it touches, the decision and
    the rule that decided. Denied calls are recorded on exactly
    the same terms as allowed ones — the record is how a denial
    is surfaced, since the wire answer is a 200."""

    call_id: str
    tool: str
    principal: str
    role: str
    case_id: str | None = None
    phi_family: str  # GATEWAY_PHI_*
    decision: str  # GATEWAY_DECISION_*
    rule_id: str
    pack_version: str
    recorded_on: date
