"""Eval gate — Rounds 1–7 (pharmacovigilance case agent).

Runs the planted feed through the real service graph on a throwaway
SQLite store and grades the guarantees. Every counter is
zero-tolerance unless marked otherwise:

Round 1:
  invalid_reports_became_cases   a report failing the validity gate
                                 must never exist as a case
  valid_cases_missing            every valid planted report is a case
  follow_ups_missing             every invalid planted report is
                                 tracked follow-up work
  duplicate_suspicion_missed     the planted duplicate pair shares a
                                 key and the second case is flagged
  uncited_statements             summary statements without citations
  citation_resolution_failures   citations not resolving to evidence
  chain_verification_failures    any case whose evidence chain fails
  summaries_missing              the graded case has no summary

Round 2 (de-duplication + history views), graded over the reference
feed plus the dedup fixture set:
  dedup_missed_definite          the planted pair is not assessed
                                 definite, or its link is missing
  dedup_wrongful_definite        any other pair assessed definite
  dedup_probable_band_wrong      the probable fixture pair lands
                                 outside probable_duplicate
  dedup_trap_band_wrong          a near-miss trap pair lands outside
                                 not_duplicate
  dedup_links_missing            pending links are not exactly the
                                 definite + probable findings
  dedup_auto_merges              cases lost, or a confirmed-closed
                                 case unreadable / not closed — the
                                 structural guarantee is behavioural
                                 here: confirm a link, lose nothing
  dedup_decision_chain_failures  the confirmed case's chain fails
                                 after the decision is recorded
  history_patient_figures_wrong  patient view figures vs hand truth
  history_product_figures_wrong  product view figures vs hand truth
  history_conflicts_merged       an identity conflict collapsed to
                                 one value instead of showing both
  history_rebuild_not_idempotent rebuilding a view changed its id
  history_citation_failures      assembler history statements
                                 missing, or their citations fail

Round 3 (dictionary coding), graded over the full book — the
reference feed, the dedup fixtures and the coding fixtures:
  coding_wrongful_codings        any current coding decision that
                                 differs from the hand-derived truth
                                 (status, match path, label, uncoded
                                 reason, pinned dictionary version,
                                 verbatim as reported)
  coding_review_tasks_missing    an uncoded current decision with
                                 no open review task, or a task
                                 pointing anywhere else
  uncoded_presented_as_coded     a summary statement presenting an
                                 uncoded term as coded (or misstating
                                 a coded one), checked in code over
                                 every assembled case
  history_coded_figures_wrong    history views' coded-term counts
                                 and named uncoded terms vs truth
  dictionary_in_place_edits      the store port growing an update
                                 or delete for dictionary versions
                                 or coding decisions, a stored
                                 version changing after an addition,
                                 or a decision pinned to the old
                                 version moving

Round 4 (seriousness + expectedness + regulatory clocks), graded
over the full book plus the assessment fixtures:
  seriousness_misclassifications a case's stored seriousness
                                 verdict, or any criterion's
                                 fired / not-fired status, differing
                                 from the planted truth
  assumed_negative_criteria      a criterion recorded not-fired
                                 whose planted truth is
                                 cannot-assess (silence read as
                                 safety), or a non-serious verdict
                                 standing over an unassessable
                                 criterion
  expectedness_misclassifications a case's stored expectedness
                                 verdict, or a graded event's
                                 status / basis, differing from the
                                 planted truth — the specificity
                                 trap and the uncoded cases included
  deadline_errors                an obligation class, day zero or
                                 deadline date differing from the
                                 planted truth (day zero is always
                                 the first valid receipt date), a
                                 clock-board state disagreeing with
                                 its artefact at a graded as_of, or
                                 a superseding assessment moving
                                 day zero
  stale_clock_presentations_accepted a stored summary whose
                                 seriousness / expectedness / clock
                                 statements disagree with the
                                 current assessment artefact, or
                                 that lacks the assessment
                                 statements entirely

Round 5 (the follow-up workflow), graded over the book plus the
follow-up fixtures:
  followup_tasks_missing_or_wrong a request the planted
                                 deficiencies must raise is absent,
                                 or names the wrong fields/classes,
                                 or a complete case carries one
  followup_closure_without_qualifying_report a request resolved
                                 by a follow-up that did not land
                                 its fact, or a decided request
                                 accepting another report
  followup_escalation_missing    the escalation fixture's request
                                 not escalated at the cycle cap,
                                 or duplicated by the raising scan
  followup_day_zero_moved        a follow-up moving a case's day
                                 zero, or the day-20 case's new
                                 deadline differing from day zero + 7
  followup_breach_hidden         the day-20 case's clock not
                                 stated breached at the follow-up
                                 date, or a summary misstating it
  unresolved_task_ageing_wrong   the board's derived states /
                                 days-overdue differing from hand
                                 truth at a graded as_of
  followup_merges_or_losses      cases lost or chains broken by
                                 follow-ups, the founded case not
                                 linked as a suspected duplicate,
                                 or in-place edits of requests/acts

Round 6 (medical review — dual control), graded over the book:
  review_dual_control_refusals   the same person in the second
                                 role, an unnamed reviewer, or a
                                 reject without its reason being
                                 recorded instead of refused
  review_single_reviewer_completed a case reaching
                                 medically_reviewed on one role's
                                 act, or failing to reach it on two
                                 distinct reviewers' acts
  review_stale_acts_counted      acts bound to a superseded
                                 record still counting toward the
                                 gate, or not labelled stale with
                                 the case back at awaiting review
  review_hold_wrong              a current reject not holding its
                                 case, an accept over the same
                                 record clearing the hold, the hold
                                 surviving the record's supersession,
                                 or a fresh dual review failing to
                                 complete the gate afterwards
  review_queue_phi_leaks         a planted patient or reporter
                                 identity anywhere in the review
                                 queue's serialised form, a reviewed
                                 case standing as queue work, or
                                 the stale case outside its bucket
  review_acts_edited_in_place    the store port growing an update
                                 or delete for review acts, or a
                                 case's evidence chain failing with
                                 review acts recorded on it

Round 7 (sealing, export, the offline verifier), graded over the
book the review round leaves behind:
  seal_unreviewed_case_sealed    a seal taken over the stale
                                 case, or under an unnamed sealer
  seal_post_seal_artefact_accepted a sealed case taking a new
                                 review act, recode, assessment,
                                 summary or follow-up request
  export_nondeterministic        two exports of one sealed case
                                 differing in bytes or in hash
  export_tampered_package_verified the verifier passing a
                                 package with an altered figure, a
                                 dropped artefact, swapped review
                                 acts or a relinked chain — or
                                 failing the untampered package
  export_unsealed_case_served    an export served for a case with
                                 no seal standing

Run: uv run python evals/run_evals.py  (exit 0 = GATE: PASS)
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pv_cases.assessment import assessment_presentation_problems  # noqa: E402
from pv_cases.coding import coding_presentation_problems  # noqa: E402
from pv_cases.config import Settings  # noqa: E402
from pv_cases.models import (  # noqa: E402
    DictionaryEntry,
    CaseStatus,
    DedupBand,
)
from pv_cases.followup import FollowUpError  # noqa: E402
from pv_cases.seed import (  # noqa: E402
    assessment_fixtures,
    coding_fixtures,
    dedup_fixtures,
    followup_fixtures,
    followup_reports,
    reference_feed,
)
from pv_cases.validity import assess_validity  # noqa: E402
from pv_cases.wiring import Services  # noqa: E402

DAY = date(2026, 10, 12)
AB_KEY = "dob:1988-03-14|f"
C = "CASE-RPT-2026-"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pv-evals-"))
    svc = Services(
        Settings(sqlite_path=str(tmp / "pv.db"), object_store_dir=str(tmp / "objects"))
    )
    counters: dict[str, int] = {}

    # ---------------- Round 1 ----------------
    reports = reference_feed()
    expected_valid = {r.report_id for r in reports if assess_validity(r).valid}
    expected_invalid = {r.report_id for r in reports if not assess_validity(r).valid}

    svc.intake.ingest_feed(reports, DAY)
    cases = svc.store.list_cases()
    case_reports = {c.report_id for c in cases}
    follow_ups = svc.store.list_follow_ups()
    follow_up_reports = {t.report_id for t in follow_ups}

    counters["invalid_reports_became_cases"] = len(case_reports & expected_invalid)
    counters["valid_cases_missing"] = len(expected_valid - case_reports)
    counters["follow_ups_missing"] = len(expected_invalid - follow_up_reports)
    counters["follow_up_for_valid_reports"] = len(follow_up_reports & expected_valid)

    second = svc.store.get_case("CASE-RPT-2026-0006")
    counters["duplicate_suspicion_missed"] = (
        0
        if second and second.suspected_duplicate_of == "CASE-RPT-2026-0005"
        else 1
    )

    uncited = 0
    unresolved = 0
    chain_failures = 0
    summaries_missing = 0
    for case in cases:
        summary = svc.assembler.assemble(case.case_id, DAY)
        if not summary.statements:
            summaries_missing += 1
        problems = svc.assembler.resolver.problems(case.case_id, summary.statements)
        unresolved += len(problems)
        uncited += sum(1 for s in summary.statements if not s.citations)
        if not svc.chain.verify_chain(case.case_id):
            chain_failures += 1
    counters["uncited_statements"] = uncited
    counters["citation_resolution_failures"] = unresolved
    counters["chain_verification_failures"] = chain_failures
    counters["summaries_missing"] = summaries_missing

    # ---------------- Round 2: dedup ----------------
    for report in dedup_fixtures():
        svc.intake.ingest(report, DAY)
    svc.dedup.scan_all(DAY)

    by_pair = {
        frozenset((a.case_a_id, a.case_b_id)): a
        for a in svc.store.list_dedup_assessments()
    }

    def band_of(x: str, y: str):
        item = by_pair.get(frozenset((x, y)))
        return item.band if item else None

    definite_pair = (f"{C}0005", f"{C}0006")
    probable_pair = (f"{C}0101", f"{C}0102")
    trap_pairs = [
        (f"{C}0005", f"{C}0103"),  # different event — genuine 2nd case
        (f"{C}0005", f"{C}0104"),  # namesake — DOB contradicts
        (f"{C}0005", f"{C}0105"),  # recurrence — onset a year apart
        (f"{C}0005", f"{C}0106"),  # same patient, unrelated event
        (f"{C}0006", f"{C}0104"),  # namesake against the pair's twin
    ]
    counters["dedup_missed_definite"] = (
        0 if band_of(*definite_pair) is DedupBand.DEFINITE else 1
    )
    counters["dedup_wrongful_definite"] = sum(
        1
        for pair, item in by_pair.items()
        if item.band is DedupBand.DEFINITE and pair != frozenset(definite_pair)
    )
    counters["dedup_probable_band_wrong"] = (
        0 if band_of(*probable_pair) is DedupBand.PROBABLE else 1
    )
    counters["dedup_trap_band_wrong"] = sum(
        1 for pair in trap_pairs if band_of(*pair) is not DedupBand.NOT_DUPLICATE
    )
    pending_ids = {l.link_id for l in svc.dedup.pending_queue()}
    expected_links = {
        f"LINK-{definite_pair[0]}-{definite_pair[1]}",
        f"LINK-{probable_pair[0]}-{probable_pair[1]}",
    }
    counters["dedup_links_missing"] = len(expected_links - pending_ids) + len(
        pending_ids - expected_links
    )

    # Confirming the definite link must lose nothing: the case
    # closes, stays readable, and its chain still verifies.
    total_before = len(svc.store.list_cases())
    svc.dedup.confirm_link(
        f"LINK-{definite_pair[0]}-{definite_pair[1]}", "Eval Reviewer", DAY
    )
    closed = svc.store.get_case(definite_pair[1])
    counters["dedup_auto_merges"] = (total_before - len(svc.store.list_cases())) + (
        0
        if closed is not None and closed.status is CaseStatus.CLOSED_AS_DUPLICATE
        else 1
    )
    counters["dedup_decision_chain_failures"] = (
        0 if svc.chain.verify_chain(definite_pair[1]) else 1
    )

    # ---------------- Round 2: history views ----------------
    view = svc.history.build_patient_history(AB_KEY, DAY)
    patient_wrong = 0
    patient_wrong += view.case_count != 5
    patient_wrong += view.event_count != 5
    patient_wrong += view.event_term_counts.get("blistering rash on hands") != 3
    patient_wrong += set(view.case_ids) != {
        f"{C}0005", f"{C}0006", f"{C}0103", f"{C}0105", f"{C}0106",
    }
    counters["history_patient_figures_wrong"] = patient_wrong
    counters["history_conflicts_merged"] = (
        0 if view.identity_conflicts.get("age") == ["38", "52"] else 1
    )
    rebuilt = svc.history.build_patient_history(AB_KEY, DAY)
    counters["history_rebuild_not_idempotent"] = (
        0 if rebuilt.view_id == view.view_id else 1
    )

    product = svc.history.build_product_history("NBT-317", DAY)
    product_wrong = 0
    product_wrong += product.case_count != 6
    product_wrong += product.event_count != 6
    product_wrong += product.event_term_counts.get("blistering rash on hands") != 4
    product_wrong += product.follow_up_task_ids != ["FU-RPT-2026-0004"]
    counters["history_product_figures_wrong"] = product_wrong

    # The assembler consumes the built view; its history statements
    # cite the view artefact and must resolve.
    summary = svc.assembler.assemble(f"{C}0005", DAY)
    history_statements = [
        s for s in summary.statements if s.text.startswith("Patient history:")
    ]
    problems = svc.assembler.resolver.problems(f"{C}0005", summary.statements)
    counters["history_citation_failures"] = len(problems) + (
        0 if len(history_statements) == 2 else 1
    )

    # ---------------- Round 3: coding ----------------
    # History views consume the coding layer (checked here, over
    # the feed + dedup book, before the coding fixtures arrive).
    coded_wrong = 0
    coded_wrong += view.coded_term_counts != {
        "Rash vesicular": 3,
        "Headache": 1,
        "Pruritus": 1,
    }
    coded_wrong += view.uncoded_event_count != 0
    coded_wrong += product.coded_term_counts != {
        "Rash vesicular": 4,
        "Headache": 1,
        "Pruritus": 1,
    }
    sp_view = svc.history.build_patient_history("init:s p|", DAY)
    coded_wrong += sp_view.coded_term_counts != {}
    coded_wrong += sp_view.uncoded_event_count != 1
    coded_wrong += sp_view.uncoded_terms != [
        "Excessive sleepiness in the mornings at work"
    ]
    counters["history_coded_figures_wrong"] = coded_wrong

    for report in coding_fixtures():
        svc.intake.ingest(report, DAY)
    reports_by_case = {}
    for report in reference_feed() + dedup_fixtures() + coding_fixtures():
        reports_by_case[f"CASE-{report.report_id}"] = report

    # Hand-derived truth for every subject of every case in the
    # book: (status, match path, label) with the label a preferred
    # term for events and a product code for products.
    E, P = "event", "product"
    CODED, UNCODED = "coded", "uncoded"
    EXPECTED = {
        f"{C}0001": {E: (CODED, "exact", "Dizziness"), P: (CODED, "exact", "NBT-101")},
        f"{C}0002": {E: (CODED, "exact", "Somnolence"), P: (CODED, "exact", "NBT-204")},
        f"{C}0005": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0006": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0101": {E: (CODED, "exact", "Somnolence"), P: (CODED, "exact", "NBT-204")},
        f"{C}0102": {E: (UNCODED, "none", None), P: (CODED, "exact", "NBT-204")},
        f"{C}0103": {E: (CODED, "exact", "Headache"), P: (CODED, "exact", "NBT-317")},
        f"{C}0104": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0105": {E: (CODED, "exact", "Rash vesicular"), P: (CODED, "exact", "NBT-317")},
        f"{C}0106": {E: (CODED, "exact", "Pruritus"), P: (CODED, "exact", "NBT-317")},
        f"{C}0201": {E: (CODED, "normalised", "Rash vesicular"), P: (CODED, "exact", "NBT-204")},
        f"{C}0202": {E: (CODED, "synonym", "Rash vesicular"), P: (CODED, "synonym", "NBT-317")},
        f"{C}0203": {E: (UNCODED, "none", None), P: (CODED, "exact", "NBT-317")},
        f"{C}0204": {E: (UNCODED, "none", None), P: (CODED, "exact", "NBT-101")},
        f"{C}0205": {E: (CODED, "exact", "Headache"), P: (UNCODED, "none", None)},
        f"{C}0206": {E: (CODED, "exact", "Nausea"), P: (CODED, "exact", "NBT-204")},
    }
    UNCODED_REASONS = {
        (f"{C}0102", E): "no_dictionary_entry",
        (f"{C}0203", E): "no_dictionary_entry",
        (f"{C}0204", E): "ambiguous_match",
        (f"{C}0205", P): "no_dictionary_entry",
    }
    wrongful = 0
    for case_id, subjects in EXPECTED.items():
        report = reports_by_case[case_id]
        current = {
            (d.subject_kind, d.subject_index): d
            for d in svc.coding.current_decisions(case_id)
        }
        for kind, (status, path, label) in subjects.items():
            decision = current.get((kind, 0))
            if decision is None:
                wrongful += 1
                continue
            verbatim = (
                report.events[0].verbatim_term
                if kind == E
                else (report.suspect_product_name or report.suspect_product_code)
            )
            wrongful += decision.status.value != status
            wrongful += decision.match_path.value != path
            wrongful += decision.dictionary_version != "2026.10.1"
            wrongful += decision.verbatim != verbatim
            if status == CODED:
                actual = (
                    decision.preferred_term if kind == E else decision.product_code
                )
                wrongful += actual != label
            else:
                wrongful += decision.uncoded_reason != UNCODED_REASONS[(case_id, kind)]
    ambiguous = next(
        d
        for d in svc.coding.current_decisions(f"{C}0204")
        if d.subject_kind == E
    )
    wrongful += ambiguous.candidates != ["Asthenia", "Fatigue"]
    counters["coding_wrongful_codings"] = wrongful

    # Every uncoded current decision has exactly one open task,
    # and every open task points at an uncoded current decision.
    open_tasks = svc.coding.uncoded_queue()
    uncoded_ids = {
        d.decision_id
        for case in svc.store.list_cases()
        for d in svc.coding.current_decisions(case.case_id)
        if d.status.value == UNCODED
    }
    counters["coding_review_tasks_missing"] = len(
        {t.decision_id for t in open_tasks} ^ uncoded_ids
    ) + (len(open_tasks) - len({t.decision_id for t in open_tasks}))

    # Assemble every case: no summary may present an uncoded term
    # as coded, and the uncoded cases must say so out loud.
    dictionary = svc.coding.dictionary(today=DAY)
    presented = 0
    for case in svc.store.list_cases():
        summary = svc.assembler.assemble(case.case_id, DAY)
        presented += len(
            coding_presentation_problems(
                svc.coding.current_decisions(case.case_id),
                summary.statements,
                dictionary,
            )
        )
    for case_id, marker in (
        (f"{C}0203", "Event uncoded: Blistering on hands and feet"),
        (f"{C}0204", "Event uncoded: Feeling drained"),
        (f"{C}0205", "Product uncoded: Cardivane Plus"),
    ):
        texts = [
            s.text for s in svc.store.list_summaries(case_id)[-1].statements
        ]
        presented += 0 if any(t.startswith(marker) for t in texts) else 1
    counters["uncoded_presented_as_coded"] = presented

    # ---------------- Round 4: assessment ----------------
    # Seriousness, expectedness and the regulatory clocks, graded
    # over the full book plus the assessment fixtures against
    # hand-derived truth. This section runs BEFORE the dictionary
    # addition below on purpose: that block recodes CASE-0203,
    # and the verdicts graded here are the ones standing over the
    # dictionary version the cases were born under.
    for report in assessment_fixtures():
        svc.intake.ingest(report, DAY)
        reports_by_case[f"CASE-{report.report_id}"] = report

    CRITERIA = (
        "death",
        "life_threatening",
        "hospitalisation",
        "disability",
        "congenital_anomaly",
        "other_medically_important",
    )
    F, N, CA = "fired", "not_fired", "cannot_assess"

    def crit(**overrides):
        base = {name: N for name in CRITERIA}
        base.update(overrides)
        return base

    OMI = "other_medically_important"
    # case -> (seriousness, criteria, expectedness, obligation)
    ASSESS_TRUTH = {
        f"{C}0001": ("non_serious", crit(), "expected", "periodic"),
        f"{C}0002": ("non_serious", crit(), "expected", "periodic"),
        f"{C}0005": ("serious", crit(**{OMI: F}), "unexpected", "expedited_15_day"),
        f"{C}0006": ("serious", crit(**{OMI: F}), "unexpected", "expedited_15_day"),
        f"{C}0101": ("non_serious", crit(), "expected", "periodic"),
        f"{C}0102": (
            "cannot_assess",
            crit(**{OMI: CA, "congenital_anomaly": CA}),
            "cannot_assess",
            "awaiting_assessment",
        ),
        f"{C}0103": ("non_serious", crit(), "unexpected", "periodic"),
        f"{C}0104": ("serious", crit(**{OMI: F}), "unexpected", "expedited_15_day"),
        f"{C}0105": ("serious", crit(**{OMI: F}), "unexpected", "expedited_15_day"),
        f"{C}0106": ("non_serious", crit(), "expected", "periodic"),
        f"{C}0201": ("serious", crit(**{OMI: F}), "unexpected", "expedited_15_day"),
        f"{C}0202": ("serious", crit(**{OMI: F}), "unexpected", "expedited_15_day"),
        f"{C}0203": (
            "cannot_assess",
            crit(**{OMI: CA, "congenital_anomaly": CA}),
            "cannot_assess",
            "awaiting_assessment",
        ),
        f"{C}0204": (
            "cannot_assess",
            crit(**{OMI: CA, "congenital_anomaly": CA}),
            "cannot_assess",
            "awaiting_assessment",
        ),
        f"{C}0205": ("non_serious", crit(), "cannot_assess", "awaiting_assessment"),
        f"{C}0206": ("non_serious", crit(), "unexpected", "periodic"),
        f"{C}0301": ("serious", crit(death=F), "unexpected", "expedited_7_day"),
        f"{C}0302": (
            "serious",
            crit(hospitalisation=F, **{OMI: F}),
            "unexpected",
            "expedited_15_day",
        ),
        f"{C}0303": ("serious", crit(hospitalisation=F), "expected", "periodic"),
        f"{C}0304": ("non_serious", crit(), "expected", "periodic"),
        f"{C}0305": (
            "cannot_assess",
            crit(death=CA, hospitalisation=CA, disability=CA),
            "expected",
            "awaiting_assessment",
        ),
        f"{C}0306": (
            "cannot_assess",
            crit(**{OMI: CA}),
            "cannot_assess",
            "awaiting_assessment",
        ),
        f"{C}0307": (
            "serious",
            crit(life_threatening=F),
            "unexpected",
            "expedited_7_day",
        ),
        f"{C}0308": ("serious", crit(disability=F), "expected", "periodic"),
        f"{C}0309": (
            "serious",
            crit(congenital_anomaly=F),
            "expected",
            "periodic",
        ),
    }
    # Event-level expectedness truth for the traps: (status, basis).
    EVENT_TRUTH = {
        f"{C}0005": ("unexpected", "more_specific_than_labelled"),
        f"{C}0102": ("cannot_assess", "event_uncoded"),
        f"{C}0103": ("unexpected", "not_in_label"),
        f"{C}0201": ("unexpected", "not_in_label"),
        f"{C}0205": ("cannot_assess", "product_not_identified"),
        f"{C}0206": ("unexpected", "not_in_label"),
        f"{C}0301": ("unexpected", "not_in_label"),
        f"{C}0302": ("unexpected", "more_specific_than_labelled"),
        f"{C}0304": ("expected", "labelled_term"),
        f"{C}0306": ("cannot_assess", "event_uncoded"),
    }
    # Explicit deadline pins (day zero + 7 / + 15, to the day).
    DEADLINE_PINS = {
        f"{C}0005": "2026-10-25",
        f"{C}0301": "2026-10-21",
        f"{C}0302": "2026-10-31",
        f"{C}0307": "2026-10-27",
    }

    seriousness_wrong = 0
    assumed_negative = 0
    expectedness_wrong = 0
    deadline_wrong = 0
    for case_id, (seriousness, criteria, expectedness, obligation) in (
        ASSESS_TRUTH.items()
    ):
        artefact = svc.assessment.current_assessment(case_id)
        if artefact is None:
            seriousness_wrong += 1
            expectedness_wrong += 1
            deadline_wrong += 1
            continue
        seriousness_wrong += artefact.seriousness.value != seriousness
        stored = {f.criterion: f.status.value for f in artefact.criteria}
        for name, status in criteria.items():
            if stored.get(name) != status:
                if status == CA and stored.get(name) == N:
                    assumed_negative += 1
                else:
                    seriousness_wrong += 1
        if seriousness == "non_serious" and any(
            f.status.value == CA for f in artefact.criteria
        ):
            assumed_negative += 1
        expectedness_wrong += artefact.expectedness.value != expectedness
        if case_id in EVENT_TRUTH:
            (entry,) = artefact.event_expectedness
            expectedness_wrong += (entry.status.value, entry.basis) != EVENT_TRUTH[
                case_id
            ]
        # The clock: class, day zero (always the first valid
        # receipt date) and the deadline arithmetic to the day.
        case = svc.store.get_case(case_id)
        clock = artefact.clock
        deadline_wrong += clock.obligation_class != obligation
        deadline_wrong += clock.day_zero != case.received_on
        deadline_wrong += clock.day_zero != case.awareness_date
        if obligation == "expedited_7_day":
            from datetime import timedelta

            deadline_wrong += clock.deadline != clock.day_zero + timedelta(days=7)
        elif obligation == "expedited_15_day":
            from datetime import timedelta

            deadline_wrong += clock.deadline != clock.day_zero + timedelta(days=15)
        else:
            deadline_wrong += clock.deadline is not None
        if case_id in DEADLINE_PINS:
            deadline_wrong += (
                clock.deadline is None
                or clock.deadline.isoformat() != DEADLINE_PINS[case_id]
            )
    counters["seriousness_misclassifications"] = seriousness_wrong
    counters["assumed_negative_criteria"] = assumed_negative
    counters["expectedness_misclassifications"] = expectedness_wrong

    # The clock board read at a graded date: the fatal case has
    # breached, the hospital case is mid-clock, the unassessable
    # case waits — and every entry agrees with its artefact.
    board = {e.case_id: e for e in svc.assessment.clock_board(date(2026, 10, 22))}
    deadline_wrong += (board[f"{C}0301"].state, board[f"{C}0301"].days_remaining) != (
        "breached",
        -1,
    )
    deadline_wrong += (board[f"{C}0302"].state, board[f"{C}0302"].days_remaining) != (
        "within_clock",
        9,
    )
    deadline_wrong += board[f"{C}0305"].state != "awaiting_assessment"
    deadline_wrong += board[f"{C}0305"].days_remaining is not None
    counters["deadline_errors"] = deadline_wrong

    # Every stored summary must present the assessment exactly as
    # the current artefact states it — verdicts, class, deadline.
    stale = 0
    for case in svc.store.list_cases():
        summary = svc.assembler.assemble(case.case_id, DAY)
        current = svc.assessment.current_assessment(case.case_id)
        stale += len(assessment_presentation_problems(current, summary.statements))
        prefixes = ("Seriousness:", "Expectedness:", "Reporting clock:")
        texts = [s.text for s in summary.statements]
        stale += sum(
            0 if any(t.startswith(p) for t in texts) else 1 for p in prefixes
        )
    counters["stale_clock_presentations_accepted"] = stale

    # A case that becomes assessable supersedes: the coder settles
    # CASE-0204's ambiguous term, the new artefact records the
    # change, day zero does not move, and a fresh summary speaks
    # the new verdict.
    case_id = f"{C}0204"
    first = svc.assessment.current_assessment(case_id)
    task = next(t for t in svc.coding.uncoded_queue() if t.case_id == case_id)
    svc.coding.resolve(task.task_id, "Eval Coder", DAY, preferred_term="Fatigue")
    second = svc.assessment.assess_case(case_id, DAY)
    counters["seriousness_misclassifications"] += (
        second.supersedes_id != first.assessment_id
        or second.change
        != {
            "seriousness": ["cannot_assess", "non_serious"],
            "expectedness": ["cannot_assess", "expected"],
            "obligation_class": ["awaiting_assessment", "periodic"],
        }
    )
    counters["deadline_errors"] += second.clock.day_zero != date(2026, 10, 21)
    fresh = svc.assembler.assemble(case_id, DAY)
    counters["stale_clock_presentations_accepted"] += len(
        assessment_presentation_problems(second, fresh.statements)
    )

    # Dictionary immutability: the port grows no in-place edit for
    # versions or decisions, an addition leaves the stored base
    # version and its pinned decisions untouched, and a recode
    # under the new version settles the near-miss term.
    edits = 0
    for verb in (
        "update_dictionary_version",
        "delete_dictionary_version",
        "update_coding_decision",
        "delete_coding_decision",
    ):
        edits += 1 if hasattr(svc.store, verb) else 0
    v1_before = svc.coding.dictionary("2026.10.1").model_dump()
    pinned_before = next(
        d
        for d in svc.store.list_coding_decisions(f"{C}0203")
        if d.subject_kind == E
    ).model_dump()
    svc.coding.propose_addition(
        "Eval Coder",
        DAY,
        "event",
        DictionaryEntry(
            verbatim="Blistering on hands and feet",
            preferred_term="Rash vesicular",
            soc="Skin and subcutaneous tissue disorders",
        ),
    )
    edits += svc.coding.dictionary("2026.10.1").model_dump() != v1_before
    edits += (
        next(
            d
            for d in svc.store.list_coding_decisions(f"{C}0203")
            if d.decision_id == pinned_before["decision_id"]
        ).model_dump()
        != pinned_before
    )
    svc.coding.code_case(f"{C}0203", DAY)
    settled = next(
        d
        for d in svc.coding.current_decisions(f"{C}0203")
        if d.subject_kind == E
    )
    edits += settled.status.value != CODED or settled.dictionary_version != "2026.10.2"
    edits += settled.supersedes_id != pinned_before["decision_id"]
    counters["dictionary_in_place_edits"] = edits

    # ---------------- Round 5: the follow-up workflow ----------------
    # Requests are raised in code from the gate's, the dictionary's
    # and the assessment engine's outputs, and answered only by
    # follow-up reports. Graded over the book plus the planted
    # follow-up fixtures, against hand-derived truth.
    cases_before_r5 = len(svc.store.list_cases())
    for report in followup_fixtures():
        svc.intake.ingest(report, DAY)
    answers = followup_reports()

    tasks_wrong = 0
    f1_task = svc.store.get_follow_up_request(f"FUP-{C}0401-A")
    tasks_wrong += f1_task is None
    if f1_task is not None:
        tasks_wrong += f1_task.source != "assessment"
        tasks_wrong += [
            (i.item_class, i.field) for i in f1_task.items
        ] != [("missing_case_fact", "events.0.outcome")]
        tasks_wrong += f1_task.items[0].blocks != [
            "death",
            "hospitalisation",
            "disability",
        ]
        tasks_wrong += f1_task.due_on != date(2026, 10, 26)  # DAY + 14
    f2_task = svc.store.get_follow_up_request(f"FUP-{C}0411-E0")
    tasks_wrong += f2_task is None
    if f2_task is not None:
        tasks_wrong += f2_task.source != "coding"
        tasks_wrong += f2_task.items[0].verbatim != "Rash across both hands"
        tasks_wrong += f2_task.due_on != date(2026, 11, 11)  # DAY + 30
    f3_task = svc.store.get_follow_up_request("FUP-RPT-2026-0431")
    tasks_wrong += f3_task is None
    if f3_task is not None:
        tasks_wrong += [
            (i.item_class, i.field) for i in f3_task.items
        ] != [("identifiable_reporter", "reporter")]
    # The book's split, exactly: 0305's absent outcome and 0205's
    # unidentified product raise assessment requests; the uncoded
    # cases raise coding requests and no assessment request; a
    # complete case raises nothing.
    tasks_wrong += svc.store.get_follow_up_request(f"FUP-{C}0305-A") is None
    tasks_wrong += svc.store.get_follow_up_request(f"FUP-{C}0306-A") is not None
    tasks_wrong += len(svc.followup.tasks_for_case(f"{C}0304", DAY)) != 0
    tasks_wrong += len(svc.followup.tasks_for_case(f"{C}0001", DAY)) != 0
    counters["followup_tasks_missing_or_wrong"] = tasks_wrong

    # Closure discipline: a follow-up that does not land the fact
    # records an attempt, never a resolution; the one that lands
    # it resolves, and a decided request takes no more reports.
    closure_wrong = 0
    f2_before = svc.store.get_follow_up_request(f"FUP-{C}0411-E0").model_dump()
    sub = svc.followup.submit_follow_up(
        f"FUP-{C}0411-E0", answers["RPT-2026-0412"], date(2026, 10, 16)
    )
    closure_wrong += sub.outcome != "attempt" or sub.cycle != 2
    closure_wrong += sub.due_on != date(2026, 11, 15)
    closure_wrong += (
        svc.followup.view(f"FUP-{C}0411-E0", date(2026, 10, 16)).state != "open"
    )
    sub = svc.followup.submit_follow_up(
        f"FUP-{C}0411-E0", answers["RPT-2026-0413"], date(2026, 10, 28)
    )
    closure_wrong += sub.outcome != "resolution"
    try:
        svc.followup.submit_follow_up(
            f"FUP-{C}0411-E0", answers["RPT-2026-0413"], date(2026, 10, 29)
        )
        closure_wrong += 1  # a resolved request accepted another report
    except FollowUpError:
        pass
    counters["followup_closure_without_qualifying_report"] = closure_wrong

    # Requests and acts are insert-only artefacts: the attempt
    # above left the request artefact byte-identical, and the
    # store port grows no update or delete for either table.
    edits = sum(
        1
        for verb in (
            "update_follow_up_request",
            "delete_follow_up_request",
            "update_follow_up_act",
            "delete_follow_up_act",
        )
        if hasattr(svc.store, verb)
    )
    edits += (
        svc.store.get_follow_up_request(f"FUP-{C}0411-E0").model_dump()
        != f2_before
    )

    # The day-20 story: the fact lands after the clock it creates
    # has already run out. Day zero does not move, the deadline is
    # day zero + 7, and the breach is stated — on the board and in
    # the summary.
    day_zero_wrong = 0
    breach_hidden = 0
    svc.followup.submit_follow_up(
        f"FUP-{C}0401-A", answers["RPT-2026-0402"], date(2026, 10, 25)
    )
    artefact = svc.assessment.current_assessment(f"{C}0401")
    day_zero_wrong += artefact.clock.day_zero != date(2026, 10, 5)
    from datetime import timedelta as _td

    day_zero_wrong += artefact.clock.deadline != date(2026, 10, 5) + _td(days=7)
    day_zero_wrong += artefact.change != {
        "seriousness": ["cannot_assess", "serious"],
        "obligation_class": ["awaiting_assessment", "expedited_7_day"],
        "deadline": [None, "2026-10-12"],
    }
    entry = next(
        e
        for e in svc.assessment.clock_board(date(2026, 10, 25))
        if e.case_id == f"{C}0401"
    )
    breach_hidden += (entry.state, entry.days_remaining) != ("breached", -13)
    summary = svc.assembler.assemble(f"{C}0401", date(2026, 10, 25))
    breach_hidden += len(
        assessment_presentation_problems(artefact, summary.statements)
    )
    breach_hidden += (
        0
        if any(
            "deadline 2026-10-12" in s.text for s in summary.statements
        )
        else 1
    )
    counters["followup_day_zero_moved"] = day_zero_wrong
    counters["followup_breach_hidden"] = breach_hidden

    # Escalation: three failed attempts land the request in the
    # named escalated state at the pack's cap; the raising scan
    # does not duplicate it and it takes no further reports.
    escalation_wrong = 0
    outcomes = [
        svc.followup.submit_follow_up(
            f"FUP-{C}0421-E0", answers[rid], day
        ).outcome
        for rid, day in (
            ("RPT-2026-0422", date(2026, 10, 17)),
            ("RPT-2026-0423", date(2026, 10, 27)),
            ("RPT-2026-0424", date(2026, 11, 6)),
        )
    ]
    escalation_wrong += outcomes != ["attempt", "attempt", "escalation"]
    escalation_wrong += (
        svc.followup.view(f"FUP-{C}0421-E0", date(2026, 11, 6)).state
        != "escalated"
    )
    escalation_wrong += len(
        svc.followup.raise_for_case(f"{C}0421", date(2026, 11, 20))
    )
    try:
        svc.followup.submit_follow_up(
            f"FUP-{C}0421-E0", answers["RPT-2026-0424"], date(2026, 11, 7)
        )
        escalation_wrong += 1  # an escalated request accepted a report
    except FollowUpError:
        pass
    counters["followup_escalation_missing"] = escalation_wrong

    # A validity follow-up founds its case — flagged and linked as
    # a suspected duplicate, never merged; no case is lost and
    # every chain still verifies.
    losses = 0
    sub = svc.followup.submit_follow_up(
        "FUP-RPT-2026-0431", answers["RPT-2026-0432"], date(2026, 10, 15)
    )
    losses += sub.new_case_id != f"{C}0432"
    founded = svc.store.get_case(f"{C}0432")
    losses += founded is None
    if founded is not None:
        losses += founded.suspected_duplicate_of != f"{C}0005"
        losses += founded.awareness_date != date(2026, 10, 15)
    losses += (
        svc.store.get_dedup_link(f"LINK-{C}0005-{C}0432") is None
    )
    # +3 valid originals (0401, 0411, 0421) and +1 founded case.
    losses += len(svc.store.list_cases()) != cases_before_r5 + 4
    losses += sum(
        1 for case in svc.store.list_cases() if not svc.chain.verify_chain(case.case_id)
    )
    counters["followup_merges_or_losses"] = losses + edits

    # Ageing is derived, exactly: the feed's reporter request is
    # 25 days overdue at the graded date, the escalated request
    # does not age, the resolved ones stand resolved.
    board = {e.task_id: e for e in svc.followup.board(date(2026, 11, 20))}
    ageing_wrong = 0
    ageing_wrong += (board["FUP-RPT-2026-0003"].state, board["FUP-RPT-2026-0003"].days_overdue) != (
        "overdue",
        25,
    )
    ageing_wrong += (board[f"FUP-{C}0421-E0"].state, board[f"FUP-{C}0421-E0"].days_overdue) != (
        "escalated",
        0,
    )
    ageing_wrong += board[f"FUP-{C}0401-A"].state != "resolved"
    ageing_wrong += board["FUP-RPT-2026-0431"].state != "resolved"
    counters["unresolved_task_ageing_wrong"] = ageing_wrong

    # ---------------- Round 6: medical review — dual control ----------------
    # The gate completes only on two distinct reviewers, one per
    # role, over the same bound record. A reject holds until the
    # record it bound is superseded; supersession stales every
    # act bound to the old record.
    from pv_cases.models import (
        AdverseEvent as _AE,
        IntakeReport as _IR,
        Patient as _Pat,
        ReportSource as _Src,
        Reporter as _Rep,
    )
    from pv_cases.review import ReviewError

    refusals_wrong = 0
    svc.review.record_review(
        f"{C}0304", "Dr A. Rao", "medical_reviewer", "accept", "", DAY
    )
    for reviewer, role, decision, rationale in (
        ("Dr A. Rao", "safety_reviewer", "accept", ""),  # same person, second role
        ("unknown", "safety_reviewer", "accept", ""),  # unnamed reviewer
        ("Dr B. Lindqvist", "safety_reviewer", "reject_with_reason", ""),  # no reason
        ("Dr B. Lindqvist", "chief", "accept", ""),  # unknown role
    ):
        try:
            svc.review.record_review(
                f"{C}0304", reviewer, role, decision, rationale, DAY
            )
            refusals_wrong += 1  # recorded instead of refused
        except ReviewError:
            pass
    refusals_wrong += len(svc.store.list_review_acts(f"{C}0304")) != 1
    counters["review_dual_control_refusals"] = refusals_wrong

    single_wrong = 0
    single_wrong += (
        svc.review.review_for_case(f"{C}0304").state != "awaiting_second_review"
    )
    svc.review.record_review(
        f"{C}0304", "Dr B. Lindqvist", "safety_reviewer", "accept", "", DAY
    )
    single_wrong += (
        svc.review.review_for_case(f"{C}0304").state != "medically_reviewed"
    )
    counters["review_single_reviewer_completed"] = single_wrong

    # Staleness: 0307 is fully reviewed, then a dictionary
    # addition + recode rewrites its coding and its assessment —
    # both acts go stale and the case falls back to awaiting a
    # first review.
    stale_wrong = 0
    svc.review.record_review(
        f"{C}0307", "Dr A. Rao", "medical_reviewer", "accept", "", DAY
    )
    svc.review.record_review(
        f"{C}0307", "Dr B. Lindqvist", "safety_reviewer", "accept", "", DAY
    )
    stale_wrong += (
        svc.review.review_for_case(f"{C}0307").state != "medically_reviewed"
    )
    svc.coding.propose_addition(
        "Dr C. Mensah",
        DAY,
        "event",
        DictionaryEntry(
            verbatim="Queasiness after dose",
            preferred_term="Nausea",
            soc="Gastrointestinal disorders",
        ),
    )
    svc.coding.code_case(f"{C}0307", DAY)
    svc.assessment.assess_case(f"{C}0307", DAY)
    view = svc.review.review_for_case(f"{C}0307")
    stale_wrong += view.state != "awaiting_first_review"
    stale_wrong += not view.has_stale_acts
    stale_wrong += not all(a.stale for a in view.acts)
    stale_wrong += not all(a.superseded_by for a in view.acts)
    counters["review_stale_acts_counted"] = stale_wrong

    # The hold: a reject on 0305 stands over an accept on the
    # same record; the follow-up that lands the outcome
    # supersedes the record, the hold clears with it, and a fresh
    # dual review completes the gate.
    hold_wrong = 0
    svc.review.record_review(
        f"{C}0305",
        "Dr B. Lindqvist",
        "safety_reviewer",
        "reject_with_reason",
        "Seriousness cannot be confirmed until the outcome is on record.",
        DAY,
    )
    svc.review.record_review(
        f"{C}0305", "Dr A. Rao", "medical_reviewer", "accept", "", DAY
    )
    view = svc.review.review_for_case(f"{C}0305")
    hold_wrong += view.state != "review_hold"
    hold_wrong += "outcome is on record" not in (view.hold_reason or "")
    answer = _IR(
        report_id="RPT-2026-0305-FU1",
        source=_Src.CALL_CENTRE,
        received_on=date(2026, 10, 25),
        patient=_Pat(initials="H.B.", age=62, sex="M"),
        reporter=_Rep(name="Call centre agent R. Patel", phone="+1-555-0199"),
        suspect_product_code="NBT-101",
        suspect_product_name="Cardivane",
        events=[
            _AE(
                verbatim_term="Severe headache",
                onset_date=date(2026, 10, 19),
                outcome="recovered",
                description="Recovered fully within two days;"
                " no hospital care was needed.",
            )
        ],
    )
    svc.followup.submit_follow_up(f"FUP-{C}0305-A", answer, date(2026, 10, 25))
    view = svc.review.review_for_case(f"{C}0305")
    hold_wrong += view.state != "awaiting_first_review"
    hold_wrong += view.hold_act_id is not None
    svc.review.record_review(
        f"{C}0305", "Dr A. Rao", "medical_reviewer", "accept", "",
        date(2026, 10, 25),
    )
    svc.review.record_review(
        f"{C}0305", "Dr B. Lindqvist", "safety_reviewer", "accept", "",
        date(2026, 10, 25),
    )
    hold_wrong += (
        svc.review.review_for_case(f"{C}0305").state != "medically_reviewed"
    )
    counters["review_hold_wrong"] = hold_wrong

    # The queue: minimised by construction, reviewed cases are
    # not work, and the staled case stands in its own bucket.
    import json as _json

    queue = svc.review.queue(date(2026, 11, 20))
    blob = _json.dumps([e.model_dump(mode="json") for e in queue])
    leaks = sum(
        1
        for phi in (
            "H.B.",
            "D.K.",
            "1988-03-14",
            "dk@example.net",
            "R. Patel",
            "+1-555-0199",
            "initials",
            "date_of_birth",
        )
        if phi in blob
    )
    by_id = {e.case_id: e for e in queue}
    leaks += f"{C}0304" in by_id or f"{C}0305" in by_id
    leaks += by_id[f"{C}0307"].bucket != "stale_review"
    counters["review_queue_phi_leaks"] = leaks

    # Acts are insert-only artefacts, and the trails they leave
    # on the evidence chains verify like everything else.
    edits = sum(
        1
        for verb in ("update_review_act", "delete_review_act")
        if hasattr(svc.store, verb)
    )
    edits += sum(
        1
        for case in svc.store.list_cases()
        if not svc.chain.verify_chain(case.case_id)
    )
    counters["review_acts_edited_in_place"] = edits

    # ---------------- Round 7: sealing, export, verifier ----------------
    # The seal is taken only from medically_reviewed, by a named
    # sealer; a sealed case refuses every later artefact; the
    # export is byte-deterministic and recorded; and the offline
    # verifier re-performs the package's guarantees, failing
    # tampered packages with the broken check named.
    import copy as _copy
    import hashlib as _hashlib

    from pv_cases.agent import CitationRefusal as _Refusal
    from pv_cases.assessment import AssessmentError as _AssessErr
    from pv_cases.coding import CodingError as _CodingErr
    from pv_cases.evidence import canonical_json as _canon
    from pv_cases.evidence import item_hash as _item_hash
    from pv_cases.followup import FollowUpError as _FUErr
    from pv_cases.sealing import SealError
    from pv_cases.verify import verify_package

    seal_wrong = 0
    # 0307's review is stale (Round 6) — sealing it must refuse.
    try:
        svc.sealing.seal_case(f"{C}0307", "PV Operations Lead", DAY)
        seal_wrong += 1
    except SealError:
        pass
    # An unnamed sealer is refused even over a reviewed case.
    try:
        svc.sealing.seal_case(f"{C}0304", " ", DAY)
        seal_wrong += 1
    except SealError:
        pass
    counters["seal_unreviewed_case_sealed"] = seal_wrong

    svc.sealing.seal_case(f"{C}0304", "PV Operations Lead", DAY)

    post_seal_wrong = 0
    for attempt in (
        lambda: svc.review.record_review(
            f"{C}0304", "Dr A. Rao", "medical_reviewer", "accept", "", DAY
        ),
        lambda: svc.coding.code_case(f"{C}0304", DAY),
        lambda: svc.assessment.assess_case(f"{C}0304", DAY),
        lambda: svc.assembler.assemble(f"{C}0304", DAY),
        lambda: svc.followup.raise_for_case(f"{C}0304", DAY),
    ):
        try:
            attempt()
            post_seal_wrong += 1  # a sealed case took a new artefact
        except (
            ReviewError,
            _CodingErr,
            _AssessErr,
            _Refusal,
            _FUErr,
        ):
            pass
    counters["seal_post_seal_artefact_accepted"] = post_seal_wrong

    det_wrong = 0
    record1, package1 = svc.sealing.export_case(
        f"{C}0304", "PV Operations Lead", DAY
    )
    record2, package2 = svc.sealing.export_case(
        f"{C}0304", "Q. Auditor", date(2026, 10, 26)
    )
    det_wrong += (
        svc.sealing.package_bytes(package1)
        != svc.sealing.package_bytes(package2)
    )
    det_wrong += record1.package_hash != record2.package_hash
    det_wrong += len(svc.sealing.list_exports(f"{C}0304")) != 2
    counters["export_nondeterministic"] = det_wrong

    tamper_wrong = 0
    clean = verify_package(package1)
    tamper_wrong += clean.verdict != "pass"
    tamper_wrong += clean.package_hash != record1.package_hash

    def _failed_names(pkg) -> set[str]:
        return {c.name for c in verify_package(pkg).checks if not c.passed}

    altered = _copy.deepcopy(package1)
    for entry in altered["artefacts"]:
        if entry["kind"] == "assessment":
            entry["payload"]["clock"]["deadline"] = "2026-12-31"
    tamper_wrong += "artefact_hashes" not in _failed_names(altered)

    dropped = _copy.deepcopy(package1)
    dropped["artefacts"] = [
        e for e in dropped["artefacts"] if e["kind"] != "coding_decision"
    ]
    tamper_wrong += "seal_inventory_exact" not in _failed_names(dropped)

    swapped = _copy.deepcopy(package1)
    acts = [e for e in swapped["artefacts"] if e["kind"] == "review_act"]
    acts[0]["payload"], acts[1]["payload"] = (
        acts[1]["payload"],
        acts[0]["payload"],
    )
    tamper_wrong += "review_gate" not in _failed_names(swapped)

    relinked = _copy.deepcopy(package1)
    first = relinked["evidence"][0]
    first["payload"]["report_id"] = "RPT-FORGED"
    first["item"]["payload_sha256"] = _hashlib.sha256(
        _canon(first["payload"])
    ).hexdigest()
    first["item"]["item_hash"] = _item_hash(
        first["item"]["prev_hash"],
        first["item"]["payload_sha256"],
        first["item"]["kind"],
        first["item"]["object_key"],
    )
    tamper_wrong += "chain_linkage" not in _failed_names(relinked)
    counters["export_tampered_package_verified"] = tamper_wrong

    unsealed_wrong = 0
    try:
        svc.sealing.export_case(f"{C}0305", "PV Operations Lead", DAY)
        unsealed_wrong += 1  # served without a seal
    except SealError:
        pass
    counters["export_unsealed_case_served"] = unsealed_wrong

    print("Rounds 1–7 evals — pharmacovigilance case agent")
    for name, value in counters.items():
        print(f"  {name}: {value} (required 0)")
    failed = [n for n, v in counters.items() if v != 0]
    if failed:
        print("GATE: FAIL")
        for name in failed:
            print(f"  - {name} = {counters[name]}")
        return 1
    print("GATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
