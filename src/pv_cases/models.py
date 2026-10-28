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
