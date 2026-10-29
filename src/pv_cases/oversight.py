"""Safety oversight reporting — Round 11.

Rounds 1–10 built the case record one case at a time: the
validity gate, the dictionary, the seriousness engine and its
clocks, the follow-up workflow, dual-control review, sealing,
the quarantine, the gateway, the access pack. What none of
them gave the safety organisation is the periodic view over
the whole book — the report a head of drug safety reads to
answer "are we inside our clocks, where is work ageing, what
is the queue holding?" This module is that view, built under
the same disciplines as everything it reads:

- **A report is computed only from artefacts.** Every figure
  is an aggregation over stored artefacts of record — cases,
  assessments, coding decisions, follow-up requests and acts,
  quarantine records and acts, review acts, seals. Nothing is
  typed in, estimated or narrated; a figure the artefacts do
  not carry does not appear.
- **Every rate carries its denominator on the figure.** A
  rate figure is `{value, numerator, denominator}`: the
  division is stated with its population, never a bare
  percentage. A class with no cases in the window reads
  `no_cases` — an empty population is undefined, not 0% and
  not 100%, and the report says so rather than flattering
  either way. Compliance the sponsor *discharges* happens in
  the sponsor's regulated process, outside this system; the
  clock figures here measure the internal gate that makes a
  case submittable — dual-control review completed — against
  the deadline the clock pack computed.
- **Reports are insert-only artefacts** (migration 0010). A
  report's id is content-addressed over its window, its
  as-of, the pack version and a fingerprint of the input
  artefacts it was computed from. Recomputing over an
  unmoved record returns the standing artefact; recomputing
  after the record moves mints a NEW report linked by
  `supersedes`. The old figure stands, never edited.
- **Every report is re-performed before it is believed.**
  `verify_report` recomputes every figure from the raw store
  by a second aggregation path and compares figure for
  figure; a report carries its `verified` mark only when the
  re-performance agrees, and `scripts/check_reports.py`
  re-runs the same check over stored reports. One mismatch
  refuses the mark and names the figure.
- **Aggregate-only — no PHI by construction.** The report
  model has no field that could carry a patient or reporter
  identifier, a narrative or a verbatim, and no case ids:
  oversight reads counts. (The generator's own name stands
  on the report — generating is a named act, and staff
  identity is not PHI.) The eval gate scans report payloads
  for forbidden keys, at zero tolerance.
- **Access follows the Round 10 pack.** Reading a report
  takes the `oversight` surface (the case-working roles and
  the quality auditor; not intake, not case admin), and
  generating one is the safety scientist's named act,
  judged by the access pack like every other act — the
  auditor reads reports and generates none. Enforcement
  governs calls that carry a principal; principal-less
  calls remain the trusted local path, as since Round 1.
- **A report is an internal oversight artefact.** It states
  so on its face. It is not a regulatory submission, and
  nothing in this system is ever submitted to a regulator.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field

from pv_cases.access import (
    ACT_GENERATE_OVERSIGHT_REPORT,
    ROLE_MEDICAL_REVIEWER,
    ROLE_QUALITY_AUDITOR,
    ROLE_SAFETY_REVIEWER,
    ROLE_SAFETY_SCIENTIST,
    SURFACE_OVERSIGHT,
    Principal,
    guard_act,
)
from pv_cases.assessment import current_assessment_of
from pv_cases.coding import current_decisions
from pv_cases.evidence import canonical_json
from pv_cases.models import (
    FU_ACT_RESOLUTION,
    FU_STATE_ESCALATED,
    FU_STATE_OPEN,
    FU_STATE_OVERDUE,
    OBLIGATION_15_DAY,
    OBLIGATION_7_DAY,
    OBLIGATION_AWAITING,
    OBLIGATION_PERIODIC,
    REVIEW_ACCEPT_DECISIONS,
    REVIEW_BUCKET_AWAITING_FIRST,
    REVIEW_BUCKET_AWAITING_SECOND,
    REVIEW_BUCKET_HOLD,
    REVIEW_BUCKET_STALE,
    REVIEW_STATE_AWAITING_SECOND,
    REVIEW_STATE_HOLD,
    REVIEW_STATE_REVIEWED,
    CaseStatus,
    CodingReviewStatus,
    CodingStatus,
)

OVERSIGHT_PACK_VERSION = "2026.10.1"

SECTION_INTAKE = "intake"
SECTION_SERIOUSNESS = "seriousness"
SECTION_CLOCKS = "clocks"
SECTION_FOLLOW_UPS = "follow_ups"
SECTION_CODING = "coding"
SECTION_QUARANTINE = "quarantine"
SECTION_REVIEW = "review"
SECTIONS = [
    SECTION_INTAKE,
    SECTION_SERIOUSNESS,
    SECTION_CLOCKS,
    SECTION_FOLLOW_UPS,
    SECTION_CODING,
    SECTION_QUARANTINE,
    SECTION_REVIEW,
]

# The value a rate figure carries when its population is
# empty: undefined, stated — never 0.0000 and never 1.0000.
NO_CASES = "no_cases"

NOTICE = (
    "Internal safety oversight artefact: aggregate counts "
    "computed from this system's case record. It is not a "
    "regulatory submission, and nothing in this system is "
    "ever submitted to a regulator."
)

# Keys that may never appear anywhere in a report payload:
# the PHI and source-text fields of the case record. The
# report model has no place for them; the eval gate scans
# for them anyway, at zero tolerance.
FORBIDDEN_PAYLOAD_KEYS = {
    "patient",
    "patients",
    "patient_id",
    "reporter_patient_id",
    "reporter",
    "reporters",
    "reporter_name",
    "patient_name",
    "initials",
    "date_of_birth",
    "dob",
    "narrative",
    "verbatim",
    "verbatim_term",
    "case_id",
    "case_ids",
}


class OversightError(RuntimeError):
    """A report that cannot be generated as asked — a window
    whose end precedes its start. Named, never guessed
    around."""


# ---------------------------------------------------------------------
# The report pack — the definitions a figure's meaning is
# versioned by. Every report names the pack that produced it.
# ---------------------------------------------------------------------


class OversightSection(BaseModel):
    """One section's definition, as pack data: the population
    it counts, how its window binds, and the measures it
    states."""

    name: str
    title: str
    population_rule: str
    window_rule: str
    measures: list[str]


class OversightPack(BaseModel):
    """The oversight pack: section definitions plus the role
    rule for the surface (mirrored by the access pack's
    grants — this statement is the meaning, that one is the
    enforcement)."""

    version: str
    read_roles: list[str]
    generate_roles: list[str]
    sections: list[OversightSection]

    def section(self, name: str) -> OversightSection:
        for section in self.sections:
            if section.name == name:
                return section
        raise ValueError(f"unknown oversight section: {name!r}")


OVERSIGHT_PACK_V1 = OversightPack(
    version=OVERSIGHT_PACK_VERSION,
    read_roles=[
        ROLE_SAFETY_SCIENTIST,
        ROLE_SAFETY_REVIEWER,
        ROLE_MEDICAL_REVIEWER,
        ROLE_QUALITY_AUDITOR,
    ],
    generate_roles=[ROLE_SAFETY_SCIENTIST],
    sections=[
        OversightSection(
            name=SECTION_INTAKE,
            title="Intake volume",
            population_rule=(
                "Events of record in the window: a report counts "
                "when it founds a case (the case's receipt date) "
                "or when it raises a follow-up task (the task's "
                "creation date). Case states are the states of "
                "record when the report is generated."
            ),
            window_rule="Receipt/creation dates in [start, end], inclusive.",
            measures=[
                "reports_received",
                "cases_founded",
                "follow_up_pending",
                "cases_open",
                "duplicate_linked",
                "closed_as_duplicate",
            ],
        ),
        OversightSection(
            name=SECTION_SERIOUSNESS,
            title="Seriousness mix",
            population_rule=(
                "Cases founded in the window that carry a current "
                "assessment of record. The mix counts the current "
                "assessment's seriousness verdict; the share's "
                "denominator is the assessed population."
            ),
            window_rule="The case's receipt date in [start, end], inclusive.",
            measures=[
                "assessed_cases",
                "serious",
                "non_serious",
                "cannot_assess",
                "serious_share",
            ],
        ),
        OversightSection(
            name=SECTION_CLOCKS,
            title="Clock compliance",
            population_rule=(
                "For each expedited class, the cases whose "
                "current assessment's deadline falls in the "
                "window. A clock counts as met when the case "
                "stands medically reviewed — both current "
                "accepts of record — on or before its deadline; "
                "completed late when review landed after it; "
                "breached when the deadline has passed at the "
                "as-of with no completed review; open within "
                "clock otherwise. Periodic and awaiting-"
                "assessment cases carry no deadline and are "
                "counted as classes of their own, over the "
                "whole book at the as-of."
            ),
            window_rule="Deadline dates in [start, end], inclusive.",
            measures=[
                "expedited_7_day_due",
                "expedited_7_day_met_on_time",
                "expedited_7_day_completed_late",
                "expedited_7_day_breached_open",
                "expedited_7_day_open_within_clock",
                "expedited_7_day_compliance_rate",
                "expedited_15_day_due",
                "expedited_15_day_met_on_time",
                "expedited_15_day_completed_late",
                "expedited_15_day_breached_open",
                "expedited_15_day_open_within_clock",
                "expedited_15_day_compliance_rate",
                "periodic_cases",
                "awaiting_assessment_cases",
            ],
        ),
        OversightSection(
            name=SECTION_FOLLOW_UPS,
            title="Follow-up ageing",
            population_rule=(
                "Follow-up requests raised on or before the "
                "as-of, state and cycle derived from their acts "
                "at the as-of. Resolutions counted are the "
                "resolution acts recorded in the window, over "
                "all requests."
            ),
            window_rule=(
                "Ageing is at the as-of; resolutions bind by "
                "their recorded date in [start, end]."
            ),
            measures=[
                "open_requests",
                "overdue_requests",
                "escalated_requests",
                "open_cycle_1",
                "open_cycle_2",
                "open_cycle_3",
                "resolved_in_window",
            ],
        ),
        OversightSection(
            name=SECTION_CODING,
            title="Dictionary coding coverage",
            population_rule=(
                "The current coding decision per subject (event "
                "or product) over decisions recorded on or "
                "before the as-of: automatic paths (exact, "
                "normalised, synonym) against the manual path a "
                "named coder took, and the decisions standing "
                "uncoded. Open coding-review tasks are the "
                "unresolved queue."
            ),
            window_rule="Cumulative through the as-of.",
            measures=[
                "decisions_of_record",
                "auto_coded",
                "manual_coded",
                "uncoded_standing",
                "auto_share",
                "unresolved_tasks",
            ],
        ),
        OversightSection(
            name=SECTION_QUARANTINE,
            title="Quarantine position",
            population_rule=(
                "Quarantine records raised on or before the "
                "as-of: held fields, those a named reviewer's "
                "release act has freed (acts on or before the "
                "as-of), and the holds still standing. Releases "
                "in the window count release acts by their "
                "recorded date."
            ),
            window_rule=(
                "Position at the as-of; releases in [start, end] "
                "by recorded date."
            ),
            measures=[
                "held_fields",
                "released_fields",
                "standing_holds",
                "released_in_window",
            ],
        ),
        OversightSection(
            name=SECTION_REVIEW,
            title="Review queue position",
            population_rule=(
                "The medical-review queue at the as-of, in the "
                "queue's own buckets (holds, one decision from "
                "done, stale reviews, first reviews), excluding "
                "reviewed cases and cases closed as duplicates; "
                "plus the book's medically reviewed and sealed "
                "counts at the as-of."
            ),
            window_rule="Position at the as-of.",
            measures=[
                "queue_total",
                "review_hold",
                "awaiting_second_review",
                "stale_review",
                "awaiting_first_review",
                "medically_reviewed_cases",
                "sealed_cases",
            ],
        ),
    ],
)


def load_oversight_pack(version: str | None = None) -> OversightPack:
    """The loader, fail-closed like every pack loader in this
    repo: an unknown version is an error, never a silent fall
    back to v1."""
    if version is None or version == OVERSIGHT_PACK_V1.version:
        return OVERSIGHT_PACK_V1
    raise ValueError(f"unknown oversight pack version: {version!r}")


# ---------------------------------------------------------------------
# The report artefact.
# ---------------------------------------------------------------------


class SafetyReport(BaseModel):
    """One periodic safety oversight report: an insert-only
    artefact.

    `sections` maps section name -> figure name -> figure,
    where a figure is `{value, numerator, denominator}`:
    counts carry a null numerator/denominator, and every rate
    carries both, on the figure. `record_fingerprint` hashes
    the sorted ids of the input artefacts the figures derive
    from; `report_id` is content-addressed over the window,
    the as-of, the pack version and that fingerprint, so
    identical inputs mint the identical report and a moved
    input mints a new one linked by `supersedes`. `verified`
    is the re-performance mark: it is set only when
    `verify_report`'s second path agrees with every figure,
    and `verification_problems` names any figure it refused.
    """

    report_id: str
    pack_version: str
    window_start: date
    window_end: date
    as_of: date
    generated_by: str
    generated_on: date
    notice: str = NOTICE
    sections: dict[str, dict[str, dict[str, Any]]] = Field(
        default_factory=dict
    )
    record_fingerprint: str
    supersedes: str | None = None
    verified: bool = False
    verification_problems: list[str] = Field(default_factory=list)


def _count(value: int) -> dict[str, Any]:
    return {"value": value, "numerator": None, "denominator": None}


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    """A rate as a stated figure: four places, HALF_UP, with
    its population on the figure — or `no_cases` when there
    is nothing to divide by. A rate over an empty population
    is not zero and not one; it is undefined, and the figure
    says so."""
    if denominator == 0:
        return {
            "value": NO_CASES,
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


def _in_window(day: date, start: date, end: date) -> bool:
    return start <= day <= end


def _fingerprint(input_ids) -> str:
    blob = canonical_json({"artefact_ids": sorted(input_ids)})
    return hashlib.sha256(blob).hexdigest()


def _report_id(
    window_start: date,
    window_end: date,
    as_of: date,
    pack_version: str,
    record_fingerprint: str,
) -> str:
    blob = canonical_json(
        {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "as_of": as_of.isoformat(),
            "pack_version": pack_version,
            "record_fingerprint": record_fingerprint,
        }
    )
    return "OSR-" + hashlib.sha256(blob).hexdigest()[:16]


def phi_problems(payload, _path: str = "") -> list[str]:
    """Walk a report payload's KEYS (never its prose) and
    name any forbidden key: the PHI / source-text fields the
    aggregate-only discipline excludes by construction."""
    problems: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{_path}.{key}" if _path else str(key)
            if str(key) in FORBIDDEN_PAYLOAD_KEYS:
                problems.append(f"forbidden key at {here}")
            problems.extend(phi_problems(value, here))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            problems.extend(phi_problems(value, f"{_path}[{index}]"))
    return problems


# ---------------------------------------------------------------------
# The builder path: figures over the services' derived views.
# ---------------------------------------------------------------------


def _reviewed_on(review, case_id: str) -> date | None:
    """The date a case's dual-control review completed: the
    later of its two current accept acts, or None when the
    case does not stand medically reviewed."""
    view = review.review_for_case(case_id)
    if view.state != REVIEW_STATE_REVIEWED:
        return None
    accepts = [
        entry.act.recorded_on
        for entry in view.acts
        if not entry.stale and entry.act.decision in REVIEW_ACCEPT_DECISIONS
    ]
    return max(accepts) if accepts else None


def build_figures(store, review, followup, window_start, window_end, as_of):
    """Compute the report's sections over the record. Returns
    (sections, input artefact ids). This is the builder path;
    `verify_report` recomputes by a second path and the two
    must agree figure for figure."""
    input_ids: set[str] = set()
    cases = store.list_cases()
    assessments_by_case: dict[str, list] = {}
    for assessment in store.list_assessments():
        assessments_by_case.setdefault(assessment.case_id, []).append(
            assessment
        )
    current = {
        case_id: current_assessment_of(items)
        for case_id, items in assessments_by_case.items()
    }

    # -- intake -------------------------------------------------
    cases_in = [
        c for c in cases if _in_window(c.received_on, window_start, window_end)
    ]
    tasks_in = [
        t
        for t in store.list_follow_ups()
        if _in_window(t.created_on, window_start, window_end)
    ]
    input_ids.update(c.case_id for c in cases_in)
    input_ids.update(t.task_id for t in tasks_in)
    founded = len(cases_in)
    linked = sum(
        1 for c in cases_in if c.status is CaseStatus.DUPLICATE_LINKED
    )
    closed_dup = sum(
        1 for c in cases_in if c.status is CaseStatus.CLOSED_AS_DUPLICATE
    )
    intake = {
        "reports_received": _count(founded + len(tasks_in)),
        "cases_founded": _count(founded),
        "follow_up_pending": _count(len(tasks_in)),
        "cases_open": _count(founded - linked - closed_dup),
        "duplicate_linked": _count(linked),
        "closed_as_duplicate": _count(closed_dup),
    }

    # -- seriousness ---------------------------------------------
    assessed = [c for c in cases_in if current.get(c.case_id) is not None]
    input_ids.update(current[c.case_id].assessment_id for c in assessed)
    serious = sum(
        1 for c in assessed if current[c.case_id].seriousness.value == "serious"
    )
    non_serious = sum(
        1
        for c in assessed
        if current[c.case_id].seriousness.value == "non_serious"
    )
    cannot = sum(
        1
        for c in assessed
        if current[c.case_id].seriousness.value == "cannot_assess"
    )
    seriousness = {
        "assessed_cases": _count(len(assessed)),
        "serious": _count(serious),
        "non_serious": _count(non_serious),
        "cannot_assess": _count(cannot),
        "serious_share": _rate(serious, len(assessed)),
    }

    # -- clocks ----------------------------------------------------
    clocks: dict[str, dict[str, Any]] = {}
    for obligation, prefix in (
        (OBLIGATION_7_DAY, "expedited_7_day"),
        (OBLIGATION_15_DAY, "expedited_15_day"),
    ):
        due = met = late = breached = open_within = 0
        for case in cases:
            assessment = current.get(case.case_id)
            if assessment is None:
                continue
            clock = assessment.clock
            if clock.obligation_class != obligation or clock.deadline is None:
                continue
            if not _in_window(clock.deadline, window_start, window_end):
                continue
            due += 1
            input_ids.add(assessment.assessment_id)
            reviewed_on = _reviewed_on(review, case.case_id)
            if reviewed_on is not None:
                input_ids.update(
                    a.act.act_id for a in review.review_for_case(case.case_id).acts
                )
                if reviewed_on <= clock.deadline:
                    met += 1
                else:
                    late += 1
            elif as_of > clock.deadline:
                breached += 1
            else:
                open_within += 1
        clocks[f"{prefix}_due"] = _count(due)
        clocks[f"{prefix}_met_on_time"] = _count(met)
        clocks[f"{prefix}_completed_late"] = _count(late)
        clocks[f"{prefix}_breached_open"] = _count(breached)
        clocks[f"{prefix}_open_within_clock"] = _count(open_within)
        clocks[f"{prefix}_compliance_rate"] = _rate(met, due)
    clocks["periodic_cases"] = _count(
        sum(
            1
            for a in current.values()
            if a is not None and a.clock.obligation_class == OBLIGATION_PERIODIC
        )
    )
    clocks["awaiting_assessment_cases"] = _count(
        sum(
            1
            for a in current.values()
            if a is not None and a.clock.obligation_class == OBLIGATION_AWAITING
        )
    )

    # -- follow-up ageing -------------------------------------------
    views = []
    for request in store.list_follow_up_requests():
        if request.raised_on > as_of:
            continue
        input_ids.add(request.task_id)
        views.append(followup.view(request.task_id, as_of))
    open_views = [
        v
        for v in views
        if v.state in (FU_STATE_OPEN, FU_STATE_OVERDUE, FU_STATE_ESCALATED)
    ]
    resolved_in_window = 0
    for act in store.list_follow_up_acts():
        if act.kind == FU_ACT_RESOLUTION and _in_window(
            act.recorded_on, window_start, window_end
        ):
            resolved_in_window += 1
            input_ids.add(act.act_id)
    follow_ups = {
        "open_requests": _count(len(open_views)),
        "overdue_requests": _count(
            sum(1 for v in open_views if v.state == FU_STATE_OVERDUE)
        ),
        "escalated_requests": _count(
            sum(1 for v in open_views if v.state == FU_STATE_ESCALATED)
        ),
        "open_cycle_1": _count(sum(1 for v in open_views if v.cycle == 1)),
        "open_cycle_2": _count(sum(1 for v in open_views if v.cycle == 2)),
        "open_cycle_3": _count(sum(1 for v in open_views if v.cycle == 3)),
        "resolved_in_window": _count(resolved_in_window),
    }

    # -- coding coverage ---------------------------------------------
    decisions = [
        d for d in store.list_coding_decisions() if d.coded_on <= as_of
    ]
    current_dec = current_decisions(decisions)
    input_ids.update(d.decision_id for d in current_dec)
    auto = sum(
        1
        for d in current_dec
        if d.status is CodingStatus.CODED
        and d.match_path.value in ("exact", "normalised", "synonym")
    )
    manual = sum(
        1
        for d in current_dec
        if d.status is CodingStatus.CODED and d.match_path.value == "manual"
    )
    uncoded = sum(
        1 for d in current_dec if d.status is CodingStatus.UNCODED
    )
    open_tasks = [
        t
        for t in store.list_coding_tasks()
        if t.status is CodingReviewStatus.OPEN
    ]
    input_ids.update(t.task_id for t in open_tasks)
    coding = {
        "decisions_of_record": _count(len(current_dec)),
        "auto_coded": _count(auto),
        "manual_coded": _count(manual),
        "uncoded_standing": _count(uncoded),
        "auto_share": _rate(auto, auto + manual),
        "unresolved_tasks": _count(len(open_tasks)),
    }

    # -- quarantine position ------------------------------------------
    records = [
        r for r in store.list_quarantine_records() if r.recorded_on <= as_of
    ]
    release_acts = [
        a for a in store.list_quarantine_acts() if a.kind == "release"
    ]
    released_record_ids = {
        a.record_id for a in release_acts if a.recorded_on <= as_of
    }
    input_ids.update(r.record_id for r in records)
    released = sum(1 for r in records if r.record_id in released_record_ids)
    released_window = sum(
        1
        for a in release_acts
        if _in_window(a.recorded_on, window_start, window_end)
    )
    input_ids.update(
        a.act_id
        for a in release_acts
        if a.recorded_on <= as_of
        or _in_window(a.recorded_on, window_start, window_end)
    )
    quarantine = {
        "held_fields": _count(len(records)),
        "released_fields": _count(released),
        "standing_holds": _count(len(records) - released),
        "released_in_window": _count(released_window),
    }

    # -- review queue position -----------------------------------------
    queue = review.queue(as_of)
    buckets = {
        REVIEW_BUCKET_HOLD: 0,
        REVIEW_BUCKET_AWAITING_SECOND: 0,
        REVIEW_BUCKET_STALE: 0,
        REVIEW_BUCKET_AWAITING_FIRST: 0,
    }
    for entry in queue:
        buckets[entry.bucket] = buckets.get(entry.bucket, 0) + 1
    reviewed_cases = 0
    for case in cases:
        if case.status is CaseStatus.CLOSED_AS_DUPLICATE:
            continue
        view = review.review_for_case(case.case_id)
        if view.state == REVIEW_STATE_REVIEWED:
            reviewed_cases += 1
            input_ids.update(a.act.act_id for a in view.acts)
    seals = store.list_seals()
    input_ids.update(s.seal_id for s in seals)
    review_section = {
        "queue_total": _count(len(queue)),
        "review_hold": _count(buckets[REVIEW_BUCKET_HOLD]),
        "awaiting_second_review": _count(
            buckets[REVIEW_BUCKET_AWAITING_SECOND]
        ),
        "stale_review": _count(buckets[REVIEW_BUCKET_STALE]),
        "awaiting_first_review": _count(
            buckets[REVIEW_BUCKET_AWAITING_FIRST]
        ),
        "medically_reviewed_cases": _count(reviewed_cases),
        "sealed_cases": _count(len(seals)),
    }

    sections = {
        SECTION_INTAKE: intake,
        SECTION_SERIOUSNESS: seriousness,
        SECTION_CLOCKS: clocks,
        SECTION_FOLLOW_UPS: follow_ups,
        SECTION_CODING: coding,
        SECTION_QUARANTINE: quarantine,
        SECTION_REVIEW: review_section,
    }
    return sections, input_ids


# ---------------------------------------------------------------------
# The verifier path: the same figures recomputed from the raw
# store, aggregating independently. The builder above reads
# the services' derived views (the follow-up board views, the
# review queue); this path re-derives its positions from the
# artefacts themselves — per-case review state instead of the
# queue, manual supersession walks instead of the services'
# current-artefact helpers — so a bug in a derived view cannot
# ratify itself.
# ---------------------------------------------------------------------


def _raw_current_assessment(store, case_id: str):
    items = [
        a for a in store.list_assessments(case_id)
    ]
    superseded = {a.supersedes_id for a in items if a.supersedes_id}
    standing = [a for a in items if a.assessment_id not in superseded]
    return standing[-1] if standing else None


def _raw_reviewed_on(review, case_id: str) -> date | None:
    view = review.review_for_case(case_id)
    if view.state != REVIEW_STATE_REVIEWED:
        return None
    dates = [
        entry.act.recorded_on
        for entry in view.acts
        if not entry.stale and entry.act.decision in REVIEW_ACCEPT_DECISIONS
    ]
    return max(dates) if dates else None


def _raw_figures(store, review, followup, window_start, window_end, as_of):
    input_ids: set[str] = set()
    cases = store.list_cases()
    current = {c.case_id: _raw_current_assessment(store, c.case_id) for c in cases}

    cases_in = [
        c for c in cases if window_start <= c.received_on <= window_end
    ]
    tasks_in = [
        t
        for t in store.list_follow_ups()
        if window_start <= t.created_on <= window_end
    ]
    input_ids.update(c.case_id for c in cases_in)
    input_ids.update(t.task_id for t in tasks_in)
    n_linked = sum(
        1 for c in cases_in if c.status == CaseStatus.DUPLICATE_LINKED
    )
    n_closed = sum(
        1 for c in cases_in if c.status == CaseStatus.CLOSED_AS_DUPLICATE
    )
    intake = {
        "reports_received": _count(len(cases_in) + len(tasks_in)),
        "cases_founded": _count(len(cases_in)),
        "follow_up_pending": _count(len(tasks_in)),
        "cases_open": _count(len(cases_in) - n_linked - n_closed),
        "duplicate_linked": _count(n_linked),
        "closed_as_duplicate": _count(n_closed),
    }

    mix = {"serious": 0, "non_serious": 0, "cannot_assess": 0}
    n_assessed = 0
    for case in cases_in:
        assessment = current[case.case_id]
        if assessment is None:
            continue
        n_assessed += 1
        mix[assessment.seriousness.value] += 1
        input_ids.add(assessment.assessment_id)
    seriousness = {
        "assessed_cases": _count(n_assessed),
        "serious": _count(mix["serious"]),
        "non_serious": _count(mix["non_serious"]),
        "cannot_assess": _count(mix["cannot_assess"]),
        "serious_share": _rate(mix["serious"], n_assessed),
    }

    clocks: dict[str, dict[str, Any]] = {}
    for obligation, prefix in (
        (OBLIGATION_7_DAY, "expedited_7_day"),
        (OBLIGATION_15_DAY, "expedited_15_day"),
    ):
        tally = {"due": 0, "met": 0, "late": 0, "breached": 0, "open": 0}
        for case in cases:
            assessment = current[case.case_id]
            if assessment is None or assessment.clock.deadline is None:
                continue
            if assessment.clock.obligation_class != obligation:
                continue
            if not (window_start <= assessment.clock.deadline <= window_end):
                continue
            tally["due"] += 1
            input_ids.add(assessment.assessment_id)
            reviewed_on = _raw_reviewed_on(review, case.case_id)
            if reviewed_on is not None:
                input_ids.update(
                    e.act.act_id for e in review.review_for_case(case.case_id).acts
                )
                if reviewed_on <= assessment.clock.deadline:
                    tally["met"] += 1
                else:
                    tally["late"] += 1
            elif as_of > assessment.clock.deadline:
                tally["breached"] += 1
            else:
                tally["open"] += 1
        clocks[f"{prefix}_due"] = _count(tally["due"])
        clocks[f"{prefix}_met_on_time"] = _count(tally["met"])
        clocks[f"{prefix}_completed_late"] = _count(tally["late"])
        clocks[f"{prefix}_breached_open"] = _count(tally["breached"])
        clocks[f"{prefix}_open_within_clock"] = _count(tally["open"])
        clocks[f"{prefix}_compliance_rate"] = _rate(tally["met"], tally["due"])
    clocks["periodic_cases"] = _count(
        sum(
            1
            for a in current.values()
            if a is not None and a.clock.obligation_class == OBLIGATION_PERIODIC
        )
    )
    clocks["awaiting_assessment_cases"] = _count(
        sum(
            1
            for a in current.values()
            if a is not None and a.clock.obligation_class == OBLIGATION_AWAITING
        )
    )

    open_states = []
    for request in store.list_follow_up_requests():
        if request.raised_on > as_of:
            continue
        input_ids.add(request.task_id)
        view = followup.view(request.task_id, as_of)
        if view.state in (FU_STATE_OPEN, FU_STATE_OVERDUE, FU_STATE_ESCALATED):
            open_states.append(view)
    n_resolved_window = 0
    for act in store.list_follow_up_acts():
        if act.kind == FU_ACT_RESOLUTION and (
            window_start <= act.recorded_on <= window_end
        ):
            n_resolved_window += 1
            input_ids.add(act.act_id)
    follow_ups = {
        "open_requests": _count(len(open_states)),
        "overdue_requests": _count(
            sum(1 for v in open_states if v.state == FU_STATE_OVERDUE)
        ),
        "escalated_requests": _count(
            sum(1 for v in open_states if v.state == FU_STATE_ESCALATED)
        ),
        "open_cycle_1": _count(sum(1 for v in open_states if v.cycle == 1)),
        "open_cycle_2": _count(sum(1 for v in open_states if v.cycle == 2)),
        "open_cycle_3": _count(sum(1 for v in open_states if v.cycle == 3)),
        "resolved_in_window": _count(n_resolved_window),
    }

    all_decisions = [
        d for d in store.list_coding_decisions() if d.coded_on <= as_of
    ]
    superseded_ids = {
        d.supersedes_id for d in all_decisions if d.supersedes_id
    }
    standing = [
        d for d in all_decisions if d.decision_id not in superseded_ids
    ]
    input_ids.update(d.decision_id for d in standing)
    n_auto = sum(
        1
        for d in standing
        if d.status == CodingStatus.CODED
        and d.match_path.value in ("exact", "normalised", "synonym")
    )
    n_manual = sum(
        1
        for d in standing
        if d.status == CodingStatus.CODED and d.match_path.value == "manual"
    )
    n_uncoded = sum(
        1 for d in standing if d.status == CodingStatus.UNCODED
    )
    open_task_list = [
        t for t in store.list_coding_tasks() if t.status == CodingReviewStatus.OPEN
    ]
    input_ids.update(t.task_id for t in open_task_list)
    coding = {
        "decisions_of_record": _count(len(standing)),
        "auto_coded": _count(n_auto),
        "manual_coded": _count(n_manual),
        "uncoded_standing": _count(n_uncoded),
        "auto_share": _rate(n_auto, n_auto + n_manual),
        "unresolved_tasks": _count(len(open_task_list)),
    }

    records = [
        r for r in store.list_quarantine_records() if r.recorded_on <= as_of
    ]
    acts = [a for a in store.list_quarantine_acts() if a.kind == "release"]
    freed = {a.record_id for a in acts if a.recorded_on <= as_of}
    input_ids.update(r.record_id for r in records)
    input_ids.update(
        a.act_id
        for a in acts
        if a.recorded_on <= as_of
        or window_start <= a.recorded_on <= window_end
    )
    quarantine = {
        "held_fields": _count(len(records)),
        "released_fields": _count(
            sum(1 for r in records if r.record_id in freed)
        ),
        "standing_holds": _count(
            sum(1 for r in records if r.record_id not in freed)
        ),
        "released_in_window": _count(
            sum(
                1
                for a in acts
                if window_start <= a.recorded_on <= window_end
            )
        ),
    }

    bucket_counts = {
        REVIEW_BUCKET_HOLD: 0,
        REVIEW_BUCKET_AWAITING_SECOND: 0,
        REVIEW_BUCKET_STALE: 0,
        REVIEW_BUCKET_AWAITING_FIRST: 0,
    }
    n_reviewed = 0
    for case in cases:
        if case.status == CaseStatus.CLOSED_AS_DUPLICATE:
            continue
        view = review.review_for_case(case.case_id)
        if view.state == REVIEW_STATE_REVIEWED:
            n_reviewed += 1
            input_ids.update(e.act.act_id for e in view.acts)
            continue
        if view.state == REVIEW_STATE_HOLD:
            bucket_counts[REVIEW_BUCKET_HOLD] += 1
        elif view.state == REVIEW_STATE_AWAITING_SECOND:
            bucket_counts[REVIEW_BUCKET_AWAITING_SECOND] += 1
        elif view.has_stale_acts:
            bucket_counts[REVIEW_BUCKET_STALE] += 1
        else:
            bucket_counts[REVIEW_BUCKET_AWAITING_FIRST] += 1
    seals = store.list_seals()
    input_ids.update(s.seal_id for s in seals)
    review_section = {
        "queue_total": _count(sum(bucket_counts.values())),
        "review_hold": _count(bucket_counts[REVIEW_BUCKET_HOLD]),
        "awaiting_second_review": _count(
            bucket_counts[REVIEW_BUCKET_AWAITING_SECOND]
        ),
        "stale_review": _count(bucket_counts[REVIEW_BUCKET_STALE]),
        "awaiting_first_review": _count(
            bucket_counts[REVIEW_BUCKET_AWAITING_FIRST]
        ),
        "medically_reviewed_cases": _count(n_reviewed),
        "sealed_cases": _count(len(seals)),
    }

    sections = {
        SECTION_INTAKE: intake,
        SECTION_SERIOUSNESS: seriousness,
        SECTION_CLOCKS: clocks,
        SECTION_FOLLOW_UPS: follow_ups,
        SECTION_CODING: coding,
        SECTION_QUARANTINE: quarantine,
        SECTION_REVIEW: review_section,
    }
    return sections, input_ids


def verify_report(store, review, followup, report: SafetyReport) -> list[str]:
    """Re-perform a report: recompute every figure from the
    raw store by the second path and compare, figure for
    figure, plus the record fingerprint. Returns the list of
    problems — empty only when the report is exactly what the
    record says. A figure the re-performance cannot reproduce
    is named, never summarised away."""
    problems: list[str] = []
    sections, input_ids = _raw_figures(
        store,
        review,
        followup,
        report.window_start,
        report.window_end,
        report.as_of,
    )
    if _fingerprint(input_ids) != report.record_fingerprint:
        problems.append(
            "record_fingerprint: the input artefacts the record "
            "presents do not hash to the report's fingerprint"
        )
    for name in SECTIONS:
        expected = sections.get(name, {})
        stated = report.sections.get(name)
        if stated is None:
            problems.append(f"{name}: section missing from the report")
            continue
        for figure, want in expected.items():
            got = stated.get(figure)
            if got != want:
                problems.append(
                    f"{name}.{figure}: report states {got}, "
                    f"the record re-performs {want}"
                )
        for figure in stated:
            if figure not in expected:
                problems.append(
                    f"{name}.{figure}: stated on the report, "
                    "not derivable from the record"
                )
    for name in report.sections:
        if name not in SECTIONS:
            problems.append(f"{name}: a section the pack does not define")
    return problems


# ---------------------------------------------------------------------
# The service.
# ---------------------------------------------------------------------


class OversightService:
    """Generates and reads safety oversight reports. Holds
    the store and the services whose derived views the
    builder reads; the access control is bound by the wiring,
    like every act-recording service since Round 10."""

    def __init__(self, store, review, followup):
        self.store = store
        self.review = review
        self.followup = followup
        self.access = None  # bound by the wiring (Round 10 pattern)

    def generate(
        self,
        window_start: date,
        window_end: date,
        as_of: date,
        generated_by: str,
        *,
        principal: Principal | None = None,
        today: date | None = None,
    ) -> SafetyReport:
        """Generate the report for a window as of a date, as a
        named human's act. When the call carries a principal,
        the access pack judges it first — the safety
        scientist's act, the principal bound to the named
        generator — and a refusal is recorded in the denial
        log before it propagates. A report whose inputs have
        not moved returns the standing artefact; a moved
        record mints a new report superseding the window's
        standing one. The stored report carries the verified
        mark only when the independent re-performance agrees
        with every figure."""
        if window_end < window_start:
            raise OversightError(
                f"window end {window_end} precedes its start {window_start}"
            )
        guard_act(
            self,
            principal,
            ACT_GENERATE_OVERSIGHT_REPORT,
            surface=SURFACE_OVERSIGHT,
            target=f"{window_start.isoformat()}..{window_end.isoformat()}",
            actor=generated_by,
            on=today or as_of,
        )
        sections, input_ids = build_figures(
            self.store,
            self.review,
            self.followup,
            window_start,
            window_end,
            as_of,
        )
        fingerprint = _fingerprint(input_ids)
        report_id = _report_id(
            window_start,
            window_end,
            as_of,
            OVERSIGHT_PACK_VERSION,
            fingerprint,
        )
        standing = self.store.get_safety_report(report_id)
        if standing is not None:
            return standing
        same_window = [
            r
            for r in self.store.list_safety_reports()
            if r.window_start == window_start and r.window_end == window_end
        ]
        supersedes = None
        if same_window:
            latest = sorted(
                same_window,
                key=lambda r: (r.generated_on, r.as_of, r.report_id),
            )[-1]
            supersedes = latest.report_id
        report = SafetyReport(
            report_id=report_id,
            pack_version=OVERSIGHT_PACK_VERSION,
            window_start=window_start,
            window_end=window_end,
            as_of=as_of,
            generated_by=generated_by,
            generated_on=today or as_of,
            sections=sections,
            record_fingerprint=fingerprint,
            supersedes=supersedes,
        )
        problems = verify_report(
            self.store, self.review, self.followup, report
        )
        report.verified = not problems
        report.verification_problems = problems
        self.store.add_safety_report(report)
        return report

    def get(self, report_id: str) -> SafetyReport | None:
        return self.store.get_safety_report(report_id)

    def reports(self) -> list[SafetyReport]:
        return self.store.list_safety_reports()

    def verify(self, report: SafetyReport) -> list[str]:
        return verify_report(self.store, self.review, self.followup, report)
