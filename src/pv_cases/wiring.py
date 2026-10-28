"""Wiring: build the service graph from settings. One place decides
which store/object-store/model implementations sit behind the ports,
so tests, the API, the Runtime entrypoint and the demo all run the
same graph with different settings."""

from __future__ import annotations

from pv_cases.access import AccessControl
from pv_cases.agent import CaseAssembler, model_for_profile
from pv_cases.assessment import AssessmentService
from pv_cases.coding import CodingService, lookup_event
from pv_cases.config import Settings
from pv_cases.dedup import DedupService
from pv_cases.evidence import EvidenceChain
from pv_cases.followup import FollowUpService
from pv_cases.gateway import (
    TOOL_CASE_RECORD,
    TOOL_DICTIONARY_LOOKUP,
    TOOL_PATIENT_HISTORY,
    GatewayService,
)
from pv_cases.history import HistoryService
from pv_cases.intake import ReferenceFeedAdapter
from pv_cases.memory import MemoryService
from pv_cases.object_store import LocalObjectStore
from pv_cases.quarantine import QuarantineService
from pv_cases.reporting import current_report
from pv_cases.review import ReviewService
from pv_cases.sealing import SealingService
from pv_cases.services import IntakeService
from pv_cases.store import SqlCaseStore


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.objects = LocalObjectStore(settings.object_store_dir)
        if settings.database_url:
            import psycopg

            url = settings.database_url
            self.store = SqlCaseStore(
                "postgres", pg_conn=lambda: psycopg.connect(url)
            )
        else:
            self.store = SqlCaseStore("sqlite", sqlite_path=settings.sqlite_path)
        self.chain = EvidenceChain(self.store, self.objects)
        self.dedup = DedupService(self.store, self.chain)
        self.coding = CodingService(self.store, self.chain)
        self.assessment = AssessmentService(self.store, self.chain)
        self.intake = IntakeService(
            self.store,
            self.objects,
            dedup=self.dedup,
            coding=self.coding,
            assessment=self.assessment,
        )
        # Round 8: the injection quarantine. Screening runs where
        # report text lands — intake binds it here, the follow-up
        # service and the assembler bind below.
        self.quarantine = QuarantineService(self.store, self.chain)
        self.intake.quarantine = self.quarantine
        # Round 5: the follow-up workflow. It founds cases through
        # intake; intake raises its requests — the wiring binds the
        # two after both exist.
        self.followup = FollowUpService(
            self.store,
            self.objects,
            self.chain,
            intake=self.intake,
            coding=self.coding,
            assessment=self.assessment,
            dedup=self.dedup,
        )
        self.intake.followup = self.followup
        self.followup.quarantine = self.quarantine
        # Round 6: the medical-reviewer gate. It reads the coding
        # and assessment layers to bind the record a review
        # decides, and records its acts on the evidence chain.
        self.review = ReviewService(
            self.store,
            self.chain,
            coding=self.coding,
            assessment=self.assessment,
        )
        # Round 7: sealing + export + the offline verifier. The
        # sealing service reads the review gate to seal over it;
        # every writing service then consults the seal before
        # adding to a case — a sealed case is a closed record.
        self.sealing = SealingService(
            self.store,
            self.chain,
            review=self.review,
            coding=self.coding,
            assessment=self.assessment,
        )
        for writer in (
            self.review,
            self.coding,
            self.assessment,
            self.followup,
        ):
            writer.seal_lookup = self.sealing.get_seal
        self.history = HistoryService(self.store, self.chain, self.objects)
        self.feed = ReferenceFeedAdapter()
        self.assembler = CaseAssembler(
            self.store,
            self.chain,
            model_for_profile(settings.model_profile),
            history=self.history,
            coding=self.coding,
            assessment=self.assessment,
        )
        self.assembler.seal_lookup = self.sealing.get_seal
        # Round 8: the Memory port (local double behind it) and
        # the Gateway with the policy pack in front of the three
        # tools it catalogues. Recall writes on the requesting
        # case's chain and a release writes on its case's chain,
        # so both take the seal guard on the same terms as the
        # other writers.
        self.memory = MemoryService(self.store, self.chain)
        self.memory.seal_lookup = self.sealing.get_seal
        self.quarantine.seal_lookup = self.sealing.get_seal
        self.assembler.quarantine = self.quarantine
        # Round 9 (RT-01): the assessment engine reads event
        # descriptions as deciding facts, so it takes the same
        # quarantine the assembler takes — a held description is
        # absent to the verdicts until released.
        self.assessment.quarantine = self.quarantine
        self.gateway = GatewayService(self.store)
        self.gateway.register_tool(
            TOOL_CASE_RECORD,
            lambda case_id: current_report(
                self.store, self.chain, case_id
            ).model_dump(mode="json"),
        )
        from datetime import date as _date

        self.gateway.register_tool(
            TOOL_PATIENT_HISTORY,
            lambda case_id: self.history.patient_history_for_case(
                case_id, _date.today()
            ).model_dump(mode="json"),
        )
        self.gateway.register_tool(
            TOOL_DICTIONARY_LOOKUP, self._dictionary_lookup
        )
        # Round 10: the access control — one guard over the
        # store's denial log, bound onto every service that
        # records a named act, so a principal carried on any of
        # those surfaces is judged by the same pack and every
        # refusal lands in the same log.
        self.access = AccessControl(self.store)
        for guarded in (
            self.review,
            self.sealing,
            self.quarantine,
            self.followup,
            self.dedup,
            self.coding,
            self.memory,
        ):
            guarded.access = self.access

    def _dictionary_lookup(self, verbatim: str) -> dict:
        """The gateway's dictionary tool: the coding layer's own
        tiered lookup over the current dictionary version."""
        dictionary = self.coding.dictionary()
        entry, path, reason, candidates = lookup_event(
            verbatim, dictionary.event_entries
        )
        if entry is None:
            return {
                "verbatim": verbatim,
                "status": "uncoded",
                "reason": reason,
                "candidates": candidates,
                "dictionary_version": dictionary.version,
            }
        return {
            "verbatim": verbatim,
            "status": "coded",
            "preferred_term": entry.preferred_term,
            "soc": entry.soc,
            "match_path": path.value,
            "dictionary_version": dictionary.version,
        }


def build_services(settings: Settings | None = None) -> Services:
    return Services(settings or Settings.from_env())
