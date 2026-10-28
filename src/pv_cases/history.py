"""Patient/product history views — Round 2.

The safety team's second question about any case is "what else do
we know about this patient / this product?" These views answer it
from **stored artefacts only**: intake evidence payloads, case
records and follow-up artefacts. Nothing is hand-entered, and
nothing is fetched from anywhere else.

Discipline, carried from the AML blueprint's profile views:

- A view is **derived in code** and **content-addressed**: the same
  artefacts rebuild the same ``view_id``, and rebuilding returns the
  stored view rather than storing a copy.
- Identity facts keep the case they were read from. When two cases
  record different values for the same fact, **both stand** in
  ``identity_conflicts`` — the view never picks a winner.
- Follow-up tasks (reports that never became cases) are part of a
  product's and a patient's history too: an unreportable report is
  still a signal the team chased.
- Round 3: the views consume the cases' current coding decisions —
  events are counted by preferred term where coded, and uncoded
  verbatims are counted separately and named. A coding change is a
  content change, so it produces a new view version under the same
  content-addressing discipline.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from pv_cases.coding import current_decisions as _current_coding
from pv_cases.evidence import EvidenceChain
from pv_cases.models import (
    SUBJECT_EVENT,
    SUBJECT_PRODUCT,
    CodingStatus,
    HistoryEvent,
    IdentityFact,
    IntakeReport,
    PatientHistoryView,
    ProductHistoryView,
    normalise_text,
    patient_key,
)
from pv_cases.seed import products_by_code

_IDENTITY_FIELDS = ["initials", "age", "age_group", "sex", "date_of_birth", "reporter_patient_id"]


def _content_id(prefix: str, body: dict) -> str:
    material = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class HistoryService:
    def __init__(self, store, chain: EvidenceChain, objects):
        self.store = store
        self.chain = chain
        self.objects = objects

    # -- artefact reads ---------------------------------------------------
    def _report_of(self, case_id: str) -> IntakeReport | None:
        for item in self.store.list_evidence(case_id):
            if item.kind == "intake_report":
                return IntakeReport.model_validate(self.chain.payload_of(item))
        return None

    def _case_reports(self) -> list[tuple[object, IntakeReport]]:
        pairs = []
        for case in self.store.list_cases():
            report = self._report_of(case.case_id)
            if report is not None:
                pairs.append((case, report))
        return pairs

    def _follow_up_reports(self) -> list[tuple[str, IntakeReport]]:
        out = []
        for task in self.store.list_follow_ups():
            raw = self.objects.get_object(f"follow-ups/{task.task_id}.json")
            out.append((task.task_id, IntakeReport.model_validate_json(raw)))
        return out

    def _coding_by_case(self) -> dict[str, dict[tuple[str, int], object]]:
        """Current coding decisions per case, keyed by (subject
        kind, subject index) — the Round 3 layer the views count
        from. Cases never coded simply have no entry."""
        by_case: dict[str, dict[tuple[str, int], object]] = {}
        for decision in _current_coding(self.store.list_coding_decisions()):
            by_case.setdefault(decision.case_id, {})[
                (decision.subject_kind, decision.subject_index)
            ] = decision
        return by_case

    @staticmethod
    def _count_coding(events_with_index, coding_for_case):
        """Fold one case's event coding into (coded term counts,
        uncoded verbatims, uncoded count). An event with no coding
        decision at all is counted nowhere — absence of coding is
        not uncodedness."""
        coded: dict[str, int] = {}
        uncoded_terms: list[str] = []
        uncoded_count = 0
        for index, event in events_with_index:
            decision = coding_for_case.get((SUBJECT_EVENT, index))
            if decision is None:
                continue
            if decision.status is CodingStatus.CODED:
                coded[decision.preferred_term] = coded.get(decision.preferred_term, 0) + 1
            else:
                uncoded_count += 1
                if event.verbatim_term not in uncoded_terms:
                    uncoded_terms.append(event.verbatim_term)
        return coded, uncoded_terms, uncoded_count

    # -- patient views ------------------------------------------------------
    def build_patient_history(self, key: str, today: date) -> PatientHistoryView:
        pairs = [(c, r) for c, r in self._case_reports() if patient_key(r.patient) == key]
        pairs.sort(key=lambda cr: cr[0].case_id)
        coding_by_case = self._coding_by_case()
        events: list[HistoryEvent] = []
        facts: list[IdentityFact] = []
        term_counts: dict[str, int] = {}
        coded_counts: dict[str, int] = {}
        uncoded_terms: list[str] = []
        uncoded_count = 0
        products: set[str] = set()
        statuses: dict[str, str] = {}
        for case, report in pairs:
            statuses[case.case_id] = case.status.value
            if report.suspect_product_code:
                products.add(report.suspect_product_code)
            for field in _IDENTITY_FIELDS:
                value = getattr(report.patient, field)
                if value is not None and str(value).strip() != "":
                    facts.append(
                        IdentityFact(
                            field=field,
                            value=str(value),
                            case_id=case.case_id,
                            report_id=report.report_id,
                        )
                    )
            for index, event in enumerate(report.events):
                events.append(
                    HistoryEvent(
                        case_id=case.case_id,
                        report_id=report.report_id,
                        product_code=report.suspect_product_code,
                        verbatim_term=event.verbatim_term,
                        onset_date=event.onset_date,
                        outcome=event.outcome,
                    )
                )
                term = normalise_text(event.verbatim_term)
                term_counts[term] = term_counts.get(term, 0) + 1
            coded, terms, count = self._count_coding(
                list(enumerate(report.events)),
                coding_by_case.get(case.case_id, {}),
            )
            for term, n in coded.items():
                coded_counts[term] = coded_counts.get(term, 0) + n
            for term in terms:
                if term not in uncoded_terms:
                    uncoded_terms.append(term)
            uncoded_count += count
        follow_ups = [
            task_id
            for task_id, report in self._follow_up_reports()
            if patient_key(report.patient) == key
        ]
        conflicts: dict[str, list[str]] = {}
        for field in _IDENTITY_FIELDS:
            values = sorted({f.value for f in facts if f.field == field})
            if len(values) > 1:
                conflicts[field] = values

        body = {
            "patient_key": key,
            "case_ids": [c.case_id for c, _ in pairs],
            "events": [e.model_dump(mode="json") for e in events],
            "identity_facts": [f.model_dump(mode="json") for f in facts],
            "identity_conflicts": conflicts,
            "follow_up_task_ids": follow_ups,
            "case_statuses": statuses,
            "coded_term_counts": coded_counts,
            "uncoded_terms": sorted(uncoded_terms),
            "uncoded_event_count": uncoded_count,
        }
        view_id = _content_id("PHV", body)
        existing = self.store.get_history_view(view_id)
        if existing is not None:
            return existing
        view = PatientHistoryView(
            view_id=view_id,
            patient_key=key,
            version=len(self.store.list_history_views("patient", key)) + 1,
            built_on=today,
            case_ids=body["case_ids"],
            case_count=len(pairs),
            event_count=len(events),
            events=events,
            event_term_counts=term_counts,
            coded_term_counts=coded_counts,
            uncoded_terms=sorted(uncoded_terms),
            uncoded_event_count=uncoded_count,
            product_codes=sorted(products),
            identity_facts=facts,
            identity_conflicts=conflicts,
            follow_up_task_ids=follow_ups,
            case_statuses=statuses,
        )
        self.store.add_history_view("patient", key, view)
        return view

    def patient_history_for_case(self, case_id: str, today: date) -> PatientHistoryView:
        case = self.store.get_case(case_id)
        if case is None:
            raise KeyError(f"no such case: {case_id}")
        report = self._report_of(case_id)
        if report is None:
            raise KeyError(f"case {case_id} has no intake evidence")
        return self.build_patient_history(patient_key(report.patient), today)

    def latest_patient_view(self, key: str) -> PatientHistoryView | None:
        views = self.store.list_history_views("patient", key)
        if not views:
            return None
        return max(views, key=lambda v: v.version)

    # -- product views ------------------------------------------------------
    def build_product_history(self, product_code: str, today: date) -> ProductHistoryView:
        pairs = [
            (c, r)
            for c, r in self._case_reports()
            if (r.suspect_product_code or "") == product_code
        ]
        pairs.sort(key=lambda cr: cr[0].case_id)
        coding_by_case = self._coding_by_case()
        events: list[HistoryEvent] = []
        term_counts: dict[str, int] = {}
        coded_counts: dict[str, int] = {}
        uncoded_terms: list[str] = []
        uncoded_count = 0
        uncoded_products: list[str] = []
        patient_keys: set[str] = set()
        statuses: dict[str, str] = {}
        for case, report in pairs:
            statuses[case.case_id] = case.status.value
            patient_keys.add(patient_key(report.patient))
            for event in report.events:
                events.append(
                    HistoryEvent(
                        case_id=case.case_id,
                        report_id=report.report_id,
                        product_code=report.suspect_product_code,
                        verbatim_term=event.verbatim_term,
                        onset_date=event.onset_date,
                        outcome=event.outcome,
                    )
                )
                term = normalise_text(event.verbatim_term)
                term_counts[term] = term_counts.get(term, 0) + 1
            case_coding = coding_by_case.get(case.case_id, {})
            coded, terms, count = self._count_coding(
                list(enumerate(report.events)), case_coding
            )
            for term, n in coded.items():
                coded_counts[term] = coded_counts.get(term, 0) + n
            for term in terms:
                if term not in uncoded_terms:
                    uncoded_terms.append(term)
            uncoded_count += count
            product_decision = case_coding.get((SUBJECT_PRODUCT, 0))
            if (
                product_decision is not None
                and product_decision.status is CodingStatus.UNCODED
                and product_decision.verbatim not in uncoded_products
            ):
                uncoded_products.append(product_decision.verbatim)
        follow_ups = [
            task_id
            for task_id, report in self._follow_up_reports()
            if (report.suspect_product_code or "") == product_code
        ]
        product = products_by_code().get(product_code)
        body = {
            "product_code": product_code,
            "case_ids": [c.case_id for c, _ in pairs],
            "events": [e.model_dump(mode="json") for e in events],
            "patient_keys": sorted(patient_keys),
            "follow_up_task_ids": follow_ups,
            "case_statuses": statuses,
            "coded_term_counts": coded_counts,
            "uncoded_terms": sorted(uncoded_terms),
            "uncoded_event_count": uncoded_count,
            "uncoded_product_names": sorted(uncoded_products),
        }
        view_id = _content_id("PRHV", body)
        existing = self.store.get_history_view(view_id)
        if existing is not None:
            return existing
        view = ProductHistoryView(
            view_id=view_id,
            product_code=product_code,
            product_name=product.name if product else None,
            version=len(self.store.list_history_views("product", product_code)) + 1,
            built_on=today,
            case_ids=body["case_ids"],
            case_count=len(pairs),
            event_count=len(events),
            events=events,
            event_term_counts=term_counts,
            coded_term_counts=coded_counts,
            uncoded_terms=sorted(uncoded_terms),
            uncoded_event_count=uncoded_count,
            uncoded_product_names=sorted(uncoded_products),
            patient_keys=sorted(patient_keys),
            follow_up_task_ids=follow_ups,
            case_statuses=statuses,
        )
        self.store.add_history_view("product", product_code, view)
        return view
