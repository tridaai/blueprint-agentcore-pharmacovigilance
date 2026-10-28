"""Seriousness, expectedness and regulatory clocks — Round 4
(Law 2: seriousness is criteria, not vibes; Law 4: regulatory
clocks in code).

Three versioned packs, all data in this module, decide everything:

- The **seriousness criteria pack**: the six ICH criteria (death,
  life-threatening, hospitalisation, disability, congenital
  anomaly, other medically important), each evaluated in code
  against the case's coded facts and recorded outcome/description
  fields. Every criterion lands in exactly one of three states:
  ``fired`` (with the deciding fact recorded), ``not_fired``
  (the deciding facts were present and none was positive), or
  ``cannot_assess`` (the deciding fact is absent — named, never
  assumed negative).
- The **reference safety information (RSI)**: per product, the
  labelled event terms. This extract is a **repo-built stand-in**
  for each product's real label (its company core data sheet) —
  original data written for this blueprint, like the dictionary.
  A coded event is ``expected`` when its term is labelled,
  ``unexpected`` when it is not — including the classic trap: a
  term *more specific* than a labelled term is unexpected even
  though the base term is labelled. An uncoded event, or a case
  whose product is not identified, is ``cannot_assess``, named.
- The **clock pack**: from day zero (the first valid receipt date
  — the case's awareness date, stated in the pack and immovable),
  seriousness and expectedness, the reporting obligation is
  computed in pure date arithmetic: a 7-day expedited clock for
  serious + unexpected cases where death or a life-threatening
  criterion fired, 15-day for other serious + unexpected cases,
  periodic otherwise. A case whose seriousness or expectedness
  cannot be assessed gets no deadline at all — its clock is
  ``awaiting_assessment``, because an obligation cannot be
  computed from unassessed facts.

An assessment is an insert-only artefact pinned to what it read
(the report by fingerprint, the current coding decisions by id,
the three pack versions). When those inputs change — a coder
settles an uncoded term, a recode lands — re-assessment writes a
new artefact superseding the old one by link, with the change
recorded and day zero unchanged. The old verdict stays readable:
a clock that was worked against is itself evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta

from pv_cases.coding import current_decisions as _current_coding
from pv_cases.evidence import EvidenceChain
from pv_cases.reporting import current_report
from pv_cases.models import (
    CRITERION_CONGENITAL,
    CRITERION_DEATH,
    CRITERION_DISABILITY,
    CRITERION_HOSPITALISATION,
    CRITERION_LIFE_THREATENING,
    CRITERION_OTHER_IMPORTANT,
    EXP_EVENT_UNCODED,
    EXP_LABELLED,
    EXP_MORE_SPECIFIC,
    EXP_NOT_IN_LABEL,
    EXP_PRODUCT_UNIDENTIFIED,
    OBLIGATION_15_DAY,
    OBLIGATION_7_DAY,
    OBLIGATION_AWAITING,
    OBLIGATION_PERIODIC,
    SUBJECT_EVENT,
    SUBJECT_PRODUCT,
    Case,
    CaseAssessment,
    ClockBoardEntry,
    CodingDecision,
    CodingStatus,
    CriterionFinding,
    CriterionStatus,
    EventExpectedness,
    Expectedness,
    IntakeReport,
    LabelledTerm,
    ReferenceSafetyInfo,
    ReportingClock,
    SeriousnessOutcome,
    SummaryStatement,
    normalise_text,
    seal_refusal_text,
)
from pv_cases.services import payload_fingerprint

# ---------------------------------------------------------------------
# The seriousness criteria pack — data. Bump the version when any
# wording list or term list changes; assessments record the version
# they ran under. Phrases are matched as substrings of the
# normalised (case/punctuation-folded) outcome or description.
# ---------------------------------------------------------------------

CRITERIA_PACK = {
    "version": "2026.10.1",
    "fatal_outcome_phrases": ["fatal", "died", "death"],
    "life_threatening_outcome_phrases": ["life threatening"],
    "hospitalisation_outcome_phrases": [
        "hospitalised",
        "hospitalized",
        "hospitalisation",
        "hospitalization",
    ],
    "disability_outcome_phrases": [
        "recovered with sequelae",
        "permanent disability",
        "disabling",
    ],
    "life_threatening_description_phrases": [
        "life threatening",
        "intensive care",
        "resuscitated",
    ],
    "hospitalisation_description_phrases": [
        "admitted to hospital",
        "hospitalised",
        "hospitalized",
        "taken to hospital",
        "in hospital",
        "hospital stay",
    ],
    "disability_description_phrases": [
        "permanent loss",
        "permanent disability",
        "sequelae",
        "unable to return to work",
    ],
    "congenital_description_phrases": ["congenital", "born with"],
    # Coded terms that decide a criterion on their own. The
    # life-threatening and congenital lists are empty in this
    # extract — no term the dictionary codes is per-se decisive
    # for those criteria, and the pack says so rather than
    # pretending otherwise. The important-medical-events list is
    # the pack's stand-in for the regulator's IME list.
    "life_threatening_terms": [],
    "congenital_terms": [],
    "important_medical_event_terms": ["Rash vesicular"],
}

CRITERIA_PACK_VERSION = CRITERIA_PACK["version"]

# ---------------------------------------------------------------------
# The clock pack — data.
# ---------------------------------------------------------------------

CLOCK_PACK = {
    "version": "2026.10.1",
    "day_zero_rule": (
        "the date the first valid report was received "
        "(the case awareness date); it never moves"
    ),
    "expedited_7_day_days": 7,
    "expedited_15_day_days": 15,
}

CLOCK_PACK_VERSION = CLOCK_PACK["version"]

# ---------------------------------------------------------------------
# The reference safety information — a repo-built stand-in for the
# products' real labels. Bump the version when the labelled terms
# change; assessments pin the version they checked against.
# ---------------------------------------------------------------------

RSI_VERSION = "2026.10.1"

BASE_RSI_PRODUCTS: dict[str, list[LabelledTerm]] = {
    # Cardivane (veldorin): the label covers the four terms below.
    # Vomiting is notably NOT labelled — nausea is, and the two
    # are different coded terms.
    "NBT-101": [
        LabelledTerm(preferred_term="Dizziness"),
        LabelledTerm(preferred_term="Headache"),
        LabelledTerm(preferred_term="Nausea"),
        LabelledTerm(preferred_term="Fatigue"),
    ],
    # Somnirel (trezapan).
    "NBT-204": [
        LabelledTerm(preferred_term="Somnolence"),
        LabelledTerm(preferred_term="Dizziness"),
        LabelledTerm(preferred_term="Headache"),
    ],
    # Dermaclear (osalide): the label covers "Rash" — and the
    # more specific "Rash vesicular" is stated as beyond the
    # label, the specificity trap the engine must grade as
    # unexpected.
    "NBT-317": [
        LabelledTerm(
            preferred_term="Rash", more_specific_terms=["Rash vesicular"]
        ),
        LabelledTerm(preferred_term="Pruritus"),
        LabelledTerm(preferred_term="Skin burning sensation"),
    ],
}


def base_rsi(created_on: date) -> ReferenceSafetyInfo:
    return ReferenceSafetyInfo(
        version=RSI_VERSION,
        created_on=created_on,
        created_by="repo extract (pv_cases.assessment)",
        note=(
            "Repo-built stand-in for the products' real reference"
            " safety information (their company core data sheets)"
            " — not a real product label."
        ),
        products={k: list(v) for k, v in BASE_RSI_PRODUCTS.items()},
    )


# ---------------------------------------------------------------------
# Fact reading. One event's assessable record: its recorded outcome
# (absent when the report leaves it blank or "unknown"), its
# description, and its current coding decision.
# ---------------------------------------------------------------------


def _outcome_text(event) -> str | None:
    """The event's outcome as normalised text, or None when the
    outcome is absent — an outcome recorded as "unknown" is an
    absence, not a negative."""
    if event.outcome is None:
        return None
    text = normalise_text(event.outcome)
    return text if text and text != "unknown" else None


def _description_text(event) -> str | None:
    if event.description is None:
        return None
    text = normalise_text(event.description)
    return text or None


def _contains(text: str | None, phrases: list[str]) -> str | None:
    """The first pack phrase found in the text, or None."""
    if text is None:
        return None
    for phrase in phrases:
        if phrase in text:
            return phrase
    return None


class _EventFacts:
    def __init__(self, index: int, event, decision: CodingDecision | None):
        self.index = index
        self.event = event
        self.decision = decision
        self.outcome = _outcome_text(event)
        self.description = _description_text(event)

    @property
    def coded(self) -> bool:
        return self.decision is not None and self.decision.status is CodingStatus.CODED

    @property
    def preferred_term(self) -> str | None:
        return self.decision.preferred_term if self.coded else None


def _finding(
    criterion: str,
    fired: list[str],
    silent: list[str],
    quiet_detail: str,
) -> CriterionFinding:
    """The aggregation every criterion shares: a positive deciding
    fact anywhere fires it (the first is recorded); else an event
    whose deciding facts are all absent makes it cannot_assess
    (the first absence is named); else it was assessed and did
    not fire."""
    if fired:
        return CriterionFinding(
            criterion=criterion,
            status=CriterionStatus.FIRED,
            deciding_fact=fired[0],
            detail=f"criterion fired: {fired[0]}",
        )
    if silent:
        return CriterionFinding(
            criterion=criterion,
            status=CriterionStatus.CANNOT_ASSESS,
            missing_fact=silent[0],
            detail=f"cannot assess: {silent[0]} is absent from the record",
        )
    return CriterionFinding(
        criterion=criterion, status=CriterionStatus.NOT_FIRED, detail=quiet_detail
    )


def evaluate_seriousness(
    report: IntakeReport, decisions: list[CodingDecision]
) -> tuple[list[CriterionFinding], SeriousnessOutcome]:
    """The criteria pack over one report and its current coding
    decisions. Pure: builds findings, stores nothing."""
    by_index = {
        d.subject_index: d for d in decisions if d.subject_kind == SUBJECT_EVENT
    }
    facts = [
        _EventFacts(i, event, by_index.get(i))
        for i, event in enumerate(report.events)
    ]
    pack = CRITERIA_PACK
    findings: list[CriterionFinding] = []

    # Death — decided by the outcome field alone: it is where a
    # fatal course is recorded.
    fired, silent = [], []
    for f in facts:
        if _contains(f.outcome, pack["fatal_outcome_phrases"]):
            fired.append(f"events.{f.index}.outcome = {f.event.outcome!r}")
        elif f.outcome is None:
            silent.append(f"events.{f.index}.outcome")
    findings.append(
        _finding(CRITERION_DEATH, fired, silent, "no event outcome records a death")
    )

    # Life-threatening — outcome wording, description wording, or
    # a coded term the pack lists as per-se life-threatening.
    fired, silent = [], []
    for f in facts:
        phrase = _contains(f.outcome, pack["life_threatening_outcome_phrases"]) or _contains(
            f.description, pack["life_threatening_description_phrases"]
        )
        if phrase:
            where = "outcome" if _contains(f.outcome, pack["life_threatening_outcome_phrases"]) else "description"
            fired.append(f"events.{f.index}.{where} records {phrase!r}")
        elif f.preferred_term in pack["life_threatening_terms"]:
            fired.append(
                f"events.{f.index} coded term {f.preferred_term!r}"
                " is listed life-threatening by the pack"
            )
        elif f.outcome is None and f.description is None and not f.coded:
            silent.append(f"events.{f.index}.outcome")
    findings.append(
        _finding(
            CRITERION_LIFE_THREATENING,
            fired,
            silent,
            "no outcome, description or coded term records a life-threatening course",
        )
    )

    # Hospitalisation — outcome or description wording.
    fired, silent = [], []
    for f in facts:
        phrase = _contains(f.outcome, pack["hospitalisation_outcome_phrases"]) or _contains(
            f.description, pack["hospitalisation_description_phrases"]
        )
        if phrase:
            where = "outcome" if _contains(f.outcome, pack["hospitalisation_outcome_phrases"]) else "description"
            fired.append(f"events.{f.index}.{where} records {phrase!r}")
        elif f.outcome is None and f.description is None:
            silent.append(f"events.{f.index}.outcome")
    findings.append(
        _finding(
            CRITERION_HOSPITALISATION,
            fired,
            silent,
            "no outcome or description records a hospitalisation",
        )
    )

    # Disability — outcome or description wording (sequelae,
    # permanent loss).
    fired, silent = [], []
    for f in facts:
        phrase = _contains(f.outcome, pack["disability_outcome_phrases"]) or _contains(
            f.description, pack["disability_description_phrases"]
        )
        if phrase:
            where = "outcome" if _contains(f.outcome, pack["disability_outcome_phrases"]) else "description"
            fired.append(f"events.{f.index}.{where} records {phrase!r}")
        elif f.outcome is None and f.description is None:
            silent.append(f"events.{f.index}.outcome")
    findings.append(
        _finding(
            CRITERION_DISABILITY,
            fired,
            silent,
            "no outcome or description records a disability",
        )
    )

    # Congenital anomaly — a coded term the pack lists, or the
    # description's own wording. An uncoded event with no
    # description is silent: its term is exactly what would
    # decide this.
    fired, silent = [], []
    for f in facts:
        if f.preferred_term in pack["congenital_terms"]:
            fired.append(
                f"events.{f.index} coded term {f.preferred_term!r}"
                " is listed congenital by the pack"
            )
        elif _contains(f.description, pack["congenital_description_phrases"]):
            phrase = _contains(f.description, pack["congenital_description_phrases"])
            fired.append(f"events.{f.index}.description records {phrase!r}")
        elif not f.coded and f.description is None:
            silent.append(f"events.{f.index}.coding")
    findings.append(
        _finding(
            CRITERION_CONGENITAL,
            fired,
            silent,
            "no coded term or description records a congenital anomaly",
        )
    )

    # Other medically important — the pack's important-medical-
    # events list over coded terms. An uncoded event is silent:
    # its term might be on the list.
    fired, silent = [], []
    for f in facts:
        if f.preferred_term in pack["important_medical_event_terms"]:
            fired.append(
                f"events.{f.index} coded term {f.preferred_term!r}"
                " is on the pack's important-medical-events list"
            )
        elif not f.coded:
            silent.append(f"events.{f.index}.coding")
    findings.append(
        _finding(
            CRITERION_OTHER_IMPORTANT,
            fired,
            silent,
            "no coded term is on the important-medical-events list",
        )
    )

    if any(f.status is CriterionStatus.FIRED for f in findings):
        return findings, SeriousnessOutcome.SERIOUS
    if any(f.status is CriterionStatus.CANNOT_ASSESS for f in findings):
        return findings, SeriousnessOutcome.CANNOT_ASSESS
    return findings, SeriousnessOutcome.NON_SERIOUS


def evaluate_expectedness(
    report: IntakeReport,
    decisions: list[CodingDecision],
    rsi: ReferenceSafetyInfo,
) -> tuple[list[EventExpectedness], Expectedness, str | None]:
    """Each event against the RSI for the case's coded product.
    Pure. Returns the per-event verdicts, the case roll-up (any
    unexpected event makes the case unexpected; else any
    unassessable event makes it cannot_assess), and the product
    code assessed against (None when the product is not
    identified by a coded decision)."""
    by_index = {
        d.subject_index: d for d in decisions if d.subject_kind == SUBJECT_EVENT
    }
    product_decision = next(
        (d for d in decisions if d.subject_kind == SUBJECT_PRODUCT), None
    )
    product_code = (
        product_decision.product_code
        if product_decision is not None
        and product_decision.status is CodingStatus.CODED
        else None
    )
    labelled = rsi.products.get(product_code, []) if product_code else []
    entries: list[EventExpectedness] = []
    for i, _event in enumerate(report.events):
        decision = by_index.get(i)
        decision_id = decision.decision_id if decision is not None else ""
        term = (
            decision.preferred_term
            if decision is not None and decision.status is CodingStatus.CODED
            else None
        )
        if product_code is None:
            entries.append(
                EventExpectedness(
                    event_index=i,
                    coding_decision_id=decision_id,
                    preferred_term=term,
                    status=Expectedness.CANNOT_ASSESS,
                    basis=EXP_PRODUCT_UNIDENTIFIED,
                )
            )
            continue
        if term is None:
            entries.append(
                EventExpectedness(
                    event_index=i,
                    coding_decision_id=decision_id,
                    status=Expectedness.CANNOT_ASSESS,
                    basis=EXP_EVENT_UNCODED,
                )
            )
            continue
        hit = next((e for e in labelled if e.preferred_term == term), None)
        if hit is not None:
            entries.append(
                EventExpectedness(
                    event_index=i,
                    coding_decision_id=decision_id,
                    preferred_term=term,
                    status=Expectedness.EXPECTED,
                    basis=EXP_LABELLED,
                    labelled_term=hit.preferred_term,
                )
            )
            continue
        specific_of = next(
            (e for e in labelled if term in e.more_specific_terms), None
        )
        if specific_of is not None:
            entries.append(
                EventExpectedness(
                    event_index=i,
                    coding_decision_id=decision_id,
                    preferred_term=term,
                    status=Expectedness.UNEXPECTED,
                    basis=EXP_MORE_SPECIFIC,
                    labelled_term=specific_of.preferred_term,
                )
            )
        else:
            entries.append(
                EventExpectedness(
                    event_index=i,
                    coding_decision_id=decision_id,
                    preferred_term=term,
                    status=Expectedness.UNEXPECTED,
                    basis=EXP_NOT_IN_LABEL,
                )
            )
    if any(e.status is Expectedness.UNEXPECTED for e in entries):
        return entries, Expectedness.UNEXPECTED, product_code
    if any(e.status is Expectedness.CANNOT_ASSESS for e in entries):
        return entries, Expectedness.CANNOT_ASSESS, product_code
    return entries, Expectedness.EXPECTED, product_code


def compute_clock(
    case: Case,
    seriousness: SeriousnessOutcome,
    expectedness: Expectedness,
    findings: list[CriterionFinding],
) -> ReportingClock:
    """The obligation from the verdicts, in pure date arithmetic.
    Day zero is the case's awareness date under the pack's rule
    and never moves."""
    day_zero = case.awareness_date
    if (
        seriousness is SeriousnessOutcome.CANNOT_ASSESS
        or expectedness is Expectedness.CANNOT_ASSESS
    ):
        blockers = []
        if seriousness is SeriousnessOutcome.CANNOT_ASSESS:
            blockers.append("seriousness cannot be assessed")
        if expectedness is Expectedness.CANNOT_ASSESS:
            blockers.append("expectedness cannot be assessed")
        return ReportingClock(
            obligation_class=OBLIGATION_AWAITING,
            day_zero=day_zero,
            deadline=None,
            clock_pack_version=CLOCK_PACK_VERSION,
            basis=(
                "no deadline is computed while "
                + " and ".join(blockers)
                + " — an obligation cannot be computed from unassessed facts"
            ),
        )
    fired = {f.criterion for f in findings if f.status is CriterionStatus.FIRED}
    if seriousness is SeriousnessOutcome.SERIOUS and expectedness is Expectedness.UNEXPECTED:
        fatal_or_lt = (
            CRITERION_DEATH in fired or CRITERION_LIFE_THREATENING in fired
        )
        days = (
            CLOCK_PACK["expedited_7_day_days"]
            if fatal_or_lt
            else CLOCK_PACK["expedited_15_day_days"]
        )
        return ReportingClock(
            obligation_class=OBLIGATION_7_DAY if fatal_or_lt else OBLIGATION_15_DAY,
            day_zero=day_zero,
            deadline=day_zero + timedelta(days=days),
            clock_pack_version=CLOCK_PACK_VERSION,
            basis=(
                "serious + unexpected"
                + (
                    ", with a fatal or life-threatening criterion fired"
                    if fatal_or_lt
                    else ""
                )
                + f" — {days}-day expedited clock from day zero"
            ),
        )
    basis = (
        "serious but expected — no expedited clock; the case rides the periodic cycle"
        if seriousness is SeriousnessOutcome.SERIOUS
        else "non-serious — no expedited clock; the case rides the periodic cycle"
    )
    return ReportingClock(
        obligation_class=OBLIGATION_PERIODIC,
        day_zero=day_zero,
        deadline=None,
        clock_pack_version=CLOCK_PACK_VERSION,
        basis=basis,
    )


def clock_state(clock: ReportingClock, as_of: date) -> tuple[str, int | None]:
    """The derived state of a stored clock at an explicit date:
    (state, days remaining). A clock with no deadline reports its
    obligation class as its state."""
    if clock.deadline is None:
        return clock.obligation_class, None
    days = (clock.deadline - as_of).days
    if days < 0:
        return "breached", days
    if days == 0:
        return "due_today", days
    return "within_clock", days


# ---------------------------------------------------------------------
# The summary discipline for assessment claims, checked in code —
# the R4 extension of the citation/coding presentation checks.
# ---------------------------------------------------------------------

_SERIOUSNESS_CLAIMS = [
    ("cannot be assessed", SeriousnessOutcome.CANNOT_ASSESS),
    ("non-serious", SeriousnessOutcome.NON_SERIOUS),
    ("serious", SeriousnessOutcome.SERIOUS),
]

_EXPECTEDNESS_CLAIMS = [
    ("cannot be assessed", Expectedness.CANNOT_ASSESS),
    ("unexpected", Expectedness.UNEXPECTED),
    ("expected", Expectedness.EXPECTED),
]

_CLOCK_CLAIMS = [
    ("7-day", OBLIGATION_7_DAY),
    ("15-day", OBLIGATION_15_DAY),
    ("periodic", OBLIGATION_PERIODIC),
    ("awaiting", OBLIGATION_AWAITING),
]

_DEADLINE_RE = re.compile(r"deadline (\d{4}-\d{2}-\d{2})")


def assessment_presentation_problems(
    assessment: CaseAssessment, statements: list[SummaryStatement]
) -> list[str]:
    """A summary may state the case's seriousness, expectedness
    and clock only as the current assessment artefact states
    them. A misstated verdict, a misstated obligation class, or
    a deadline date that is not the clock artefact's deadline
    (including any deadline stated for a case whose clock has
    none) is a problem — and problems refuse the summary
    wholesale, on the same terms as an unresolvable citation."""
    problems: list[str] = []
    for stmt in statements:
        text = stmt.text
        if text.startswith("Seriousness:"):
            claim = text.split(":", 1)[1].strip().lower()
            claimed = next(
                (v for k, v in _SERIOUSNESS_CLAIMS if claim.startswith(k)), None
            )
            if claimed is None:
                problems.append(f"unrecognised seriousness claim: {text[:60]!r}")
            elif claimed is not assessment.seriousness:
                problems.append(
                    f"seriousness misstated: summary claims"
                    f" {claimed.value!r}, the assessment says"
                    f" {assessment.seriousness.value!r}"
                )
        if text.startswith("Expectedness:"):
            claim = text.split(":", 1)[1].strip().lower()
            claimed = next(
                (v for k, v in _EXPECTEDNESS_CLAIMS if claim.startswith(k)), None
            )
            if claimed is None:
                problems.append(f"unrecognised expectedness claim: {text[:60]!r}")
            elif claimed is not assessment.expectedness:
                problems.append(
                    f"expectedness misstated: summary claims"
                    f" {claimed.value!r}, the assessment says"
                    f" {assessment.expectedness.value!r}"
                )
        if text.startswith("Reporting clock:"):
            claimed = next(
                (v for k, v in _CLOCK_CLAIMS if k in text.lower()), None
            )
            if claimed is None:
                problems.append(f"unrecognised clock claim: {text[:60]!r}")
            elif claimed != assessment.clock.obligation_class:
                problems.append(
                    f"clock misstated: summary claims {claimed!r},"
                    f" the clock artefact says"
                    f" {assessment.clock.obligation_class!r}"
                )
        for match in _DEADLINE_RE.finditer(text):
            stated = match.group(1)
            actual = (
                assessment.clock.deadline.isoformat()
                if assessment.clock.deadline
                else None
            )
            if stated != actual:
                problems.append(
                    f"deadline misstated: summary states {stated},"
                    f" the clock artefact says {actual or 'no deadline'}"
                )
    return problems


# ---------------------------------------------------------------------
# The service: run the engines over stored cases, keep the
# artefact chain, read the clock board.
# ---------------------------------------------------------------------


def current_assessment_of(assessments: list[CaseAssessment]) -> CaseAssessment | None:
    """The current artefact: the one nothing supersedes."""
    superseded = {a.supersedes_id for a in assessments if a.supersedes_id}
    current = [a for a in assessments if a.assessment_id not in superseded]
    return current[-1] if current else None


def _change_between(old: CaseAssessment, new: CaseAssessment) -> dict:
    change: dict[str, list] = {}
    pairs = [
        ("seriousness", old.seriousness.value, new.seriousness.value),
        ("expectedness", old.expectedness.value, new.expectedness.value),
        (
            "obligation_class",
            old.clock.obligation_class,
            new.clock.obligation_class,
        ),
        (
            "deadline",
            old.clock.deadline.isoformat() if old.clock.deadline else None,
            new.clock.deadline.isoformat() if new.clock.deadline else None,
        ),
    ]
    for field, before, after in pairs:
        if before != after:
            change[field] = [before, after]
    return change


class AssessmentError(ValueError):
    """An assessment act that cannot be taken — today, one
    refusal: the case is sealed (Round 7), and a sealed case is a
    closed record that takes no new assessments."""


class AssessmentService:
    """Runs the three packs over a case's coded facts and stores
    the verdict as a pinned artefact. Re-assessment over unchanged
    inputs returns the stored artefact; over changed inputs it
    writes a superseding one with the change recorded."""

    def __init__(self, store, chain: EvidenceChain):
        self.store = store
        self.chain = chain
        # Round 7: the sealing guard, bound by wiring — while it
        # is set, a sealed case refuses new assessments.
        self.seal_lookup = None

    # -- inputs ---------------------------------------------------------
    def report_of(self, case: Case) -> IntakeReport:
        # The report of record: the intake payload folded with the
        # case's follow-up reports (pv_cases.reporting, Round 5).
        return current_report(self.store, self.chain, case.case_id)

    # -- the reference safety information -------------------------------
    def ensure_rsi(self, today: date) -> None:
        if self.store.get_rsi_version(RSI_VERSION) is None:
            self.store.add_rsi_version(base_rsi(today))

    def rsi(self, version: str | None = None, today: date | None = None) -> ReferenceSafetyInfo:
        if today is not None:
            self.ensure_rsi(today)
        if version is not None:
            found = self.store.get_rsi_version(version)
            if found is None:
                raise KeyError(f"no such RSI version: {version}")
            return found
        versions = self.store.list_rsi_versions()
        if not versions:
            raise KeyError("no reference safety information stored")
        return max(versions, key=lambda r: r.version)

    # -- assessment -------------------------------------------------------
    def current_assessment(self, case_id: str) -> CaseAssessment | None:
        return current_assessment_of(self.store.list_assessments(case_id))

    def list_assessments(self, case_id: str) -> list[CaseAssessment]:
        return self.store.list_assessments(case_id)

    def assess_case(self, case_id: str, today: date) -> CaseAssessment:
        """Assess one case from its stored facts. Idempotent over
        unchanged inputs: when the fingerprint of what would be
        read equals the current artefact's, the stored artefact
        is returned and nothing is written."""
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        if self.seal_lookup is not None:
            seal = self.seal_lookup(case_id)
            if seal is not None:
                raise AssessmentError(
                    seal_refusal_text(seal, "assessments")
                )
        self.ensure_rsi(today)
        rsi = self.rsi()
        report = self.report_of(case)
        decisions = _current_coding(self.store.list_coding_decisions(case_id))
        material = {
            "case_id": case_id,
            "report_fingerprint": payload_fingerprint(report.model_dump(mode="json")),
            "coding_decision_ids": sorted(d.decision_id for d in decisions),
            "criteria_pack_version": CRITERIA_PACK_VERSION,
            "rsi_version": rsi.version,
            "clock_pack_version": CLOCK_PACK_VERSION,
        }
        fingerprint = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        current = self.current_assessment(case_id)
        if current is not None and current.input_fingerprint == fingerprint:
            return current
        findings, seriousness = evaluate_seriousness(report, decisions)
        event_expectedness, expectedness, product_code = evaluate_expectedness(
            report, decisions, rsi
        )
        clock = compute_clock(case, seriousness, expectedness, findings)
        n = len(self.store.list_assessments(case_id)) + 1
        assessment = CaseAssessment(
            assessment_id=f"ASMT-{case_id}-{n:02d}",
            case_id=case_id,
            criteria_pack_version=CRITERIA_PACK_VERSION,
            rsi_version=rsi.version,
            clock_pack_version=CLOCK_PACK_VERSION,
            product_code=product_code,
            coding_decision_ids=sorted(d.decision_id for d in decisions),
            input_fingerprint=fingerprint,
            seriousness=seriousness,
            criteria=findings,
            expectedness=expectedness,
            event_expectedness=event_expectedness,
            clock=clock,
            change=None,  # set below, once the new artefact exists
            supersedes_id=current.assessment_id if current is not None else None,
            assessed_on=today,
        )
        if current is not None:
            assessment.change = _change_between(current, assessment)
        self.store.add_assessment(assessment)
        return assessment

    # -- the clock board ---------------------------------------------------
    def clock_board(self, as_of: date) -> list[ClockBoardEntry]:
        """Every case with a current assessment, read at ``as_of``:
        deadline-bearing clocks first, soonest first; clocks with
        no deadline after them, by case."""
        entries: list[ClockBoardEntry] = []
        for case in self.store.list_cases():
            current = self.current_assessment(case.case_id)
            if current is None:
                continue
            state, days = clock_state(current.clock, as_of)
            entries.append(
                ClockBoardEntry(
                    case_id=case.case_id,
                    assessment_id=current.assessment_id,
                    seriousness=current.seriousness,
                    expectedness=current.expectedness,
                    obligation_class=current.clock.obligation_class,
                    day_zero=current.clock.day_zero,
                    deadline=current.clock.deadline,
                    days_remaining=days,
                    state=state,
                )
            )
        entries.sort(
            key=lambda e: (
                e.days_remaining is None,
                e.days_remaining if e.days_remaining is not None else 0,
                e.case_id,
            )
        )
        return entries
