"""Wiring: build the service graph from settings. One place decides
which store/object-store/model implementations sit behind the ports,
so tests, the API, the Runtime entrypoint and the demo all run the
same graph with different settings."""

from __future__ import annotations

from pv_cases.agent import CaseAssembler, model_for_profile
from pv_cases.assessment import AssessmentService
from pv_cases.coding import CodingService
from pv_cases.config import Settings
from pv_cases.dedup import DedupService
from pv_cases.evidence import EvidenceChain
from pv_cases.followup import FollowUpService
from pv_cases.history import HistoryService
from pv_cases.intake import ReferenceFeedAdapter
from pv_cases.object_store import LocalObjectStore
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


def build_services(settings: Settings | None = None) -> Services:
    return Services(settings or Settings.from_env())
