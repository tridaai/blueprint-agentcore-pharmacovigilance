"""Application services: intake and case assembly.

:class:`IntakeService` is the only route from a report to a case, and
it consults the validity gate before anything is stored as one. The
raw report payload is preserved as the first evidence item on the
case's hash chain; an invalid report's payload is kept as a follow-up
artefact (never a case evidence chain, because there is no case).
"""

from __future__ import annotations

import hashlib
from datetime import date

from pv_cases.evidence import EvidenceChain
from pv_cases.models import (
    Case,
    CaseStatus,
    FollowUpTask,
    IntakeReport,
    dedup_key,
)
from pv_cases.object_store import ObjectStore
from pv_cases.validity import assess_validity


def _case_id_for(report_id: str) -> str:
    return "CASE-" + report_id


class IntakeOutcome:
    def __init__(self, report_id: str, case: Case | None, follow_up: FollowUpTask | None):
        self.report_id = report_id
        self.case = case
        self.follow_up = follow_up

    @property
    def became_case(self) -> bool:
        return self.case is not None


class IntakeService:
    def __init__(self, store, objects: ObjectStore, dedup=None, coding=None, assessment=None):
        self.store = store
        self.objects = objects
        self.chain = EvidenceChain(store, objects)
        # Round 5: the follow-up service, bound by the wiring after
        # both exist (the follow-up service founds cases through
        # this intake, so neither can own the other). When bound,
        # every invalid report also raises a workflow follow-up
        # request naming the missing fields, and every new case is
        # scanned for deficiencies a follow-up report can cure.
        self.followup = None
        # Round 8: the injection quarantine, bound by the wiring.
        # When bound, the report's free-text fields are screened
        # the moment its payload lands on the new case's chain —
        # before any consumer can draft from them.
        self.quarantine = None
        # Round 2: when a dedup service is wired, every new case is
        # assessed against the stored book before ingest returns —
        # a duplicate finding is a link + a human queue item, never
        # a merge.
        self.dedup = dedup
        # Round 3: when a coding service is wired, every new case is
        # coded against the current dictionary version before
        # ingest returns — the workflow order of the design brief:
        # validity, de-duplication, then coding.
        self.coding = coding
        # Round 4: when an assessment service is wired, every new
        # case is assessed (seriousness, expectedness, clock)
        # against its coded facts before ingest returns — the
        # brief's workflow order: coding, then the seriousness
        # engine, then expectedness and the clocks.
        self.assessment = assessment

    def ingest(self, report: IntakeReport, today: date) -> IntakeOutcome:
        assessment = assess_validity(report)
        payload = report.model_dump(mode="json")
        if not assessment.valid:
            # Not a case. The payload is preserved as a follow-up
            # artefact and the missing criteria become tracked work.
            task = FollowUpTask(
                task_id=f"FU-{report.report_id}",
                report_id=report.report_id,
                missing_criteria=assessment.missing,
                created_on=today,
            )
            self.objects.put_object(f"follow-ups/{task.task_id}.json", _canonical(payload))
            self.store.add_follow_up(task)
            if self.followup is not None:
                self.followup.raise_for_invalid(report, today)
            return IntakeOutcome(report.report_id, None, task)

        case_id = _case_id_for(report.report_id)
        key = dedup_key(report)
        existing = self.store.find_case_by_dedup_key(key)
        case = Case(
            case_id=case_id,
            report_id=report.report_id,
            source=report.source,
            received_on=report.received_on,
            awareness_date=report.received_on,
            product_code=report.suspect_product_code,
            dedup_key=key,
            suspected_duplicate_of=existing.case_id if existing else None,
        )
        self.store.add_case(case)
        intake_item = self.chain.append(
            case_id=case_id,
            kind="intake_report",
            payload=payload,
            recorded_on=today,
            item_id=f"EV-{case_id}-001",
        )
        if self.quarantine is not None:
            self.quarantine.screen_item(case_id, intake_item, today)
        if self.dedup is not None:
            self.dedup.scan_case(case_id, today)
            # The scan may have marked this case duplicate_linked.
            case = self.store.get_case(case_id)
        if self.coding is not None:
            self.coding.code_case(case_id, today)
        if self.assessment is not None:
            self.assessment.assess_case(case_id, today)
        if self.followup is not None:
            self.followup.raise_for_case(case_id, today)
        return IntakeOutcome(report.report_id, case, None)

    def ingest_feed(self, reports: list[IntakeReport], today: date) -> list[IntakeOutcome]:
        return [self.ingest(r, today) for r in reports]


def _canonical(payload: dict) -> bytes:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def summary_id_for(case_id: str, existing_count: int) -> str:
    return f"SUM-{case_id}-{existing_count + 1:03d}"


def payload_fingerprint(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()
