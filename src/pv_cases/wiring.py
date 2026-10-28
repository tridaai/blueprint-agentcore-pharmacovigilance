"""Wiring: build the service graph from settings. One place decides
which store/object-store/model implementations sit behind the ports,
so tests, the API, the Runtime entrypoint and the demo all run the
same graph with different settings."""

from __future__ import annotations

from pv_cases.agent import CaseAssembler, model_for_profile
from pv_cases.config import Settings
from pv_cases.dedup import DedupService
from pv_cases.evidence import EvidenceChain
from pv_cases.history import HistoryService
from pv_cases.intake import ReferenceFeedAdapter
from pv_cases.object_store import LocalObjectStore
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
        self.intake = IntakeService(self.store, self.objects, dedup=self.dedup)
        self.history = HistoryService(self.store, self.chain, self.objects)
        self.feed = ReferenceFeedAdapter()
        self.assembler = CaseAssembler(
            self.store,
            self.chain,
            model_for_profile(settings.model_profile),
            history=self.history,
        )


def build_services(settings: Settings | None = None) -> Services:
    return Services(settings or Settings.from_env())
