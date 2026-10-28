from datetime import date

import pytest

from pv_cases.config import Settings
from pv_cases.wiring import Services

DAY = date(2026, 10, 12)


@pytest.fixture()
def services(tmp_path) -> Services:
    return Services(
        Settings(
            sqlite_path=str(tmp_path / "pv.db"),
            object_store_dir=str(tmp_path / "objects"),
        )
    )


@pytest.fixture()
def fed(services) -> Services:
    services.intake.ingest_feed(services.feed.fetch_reports(), DAY)
    return services


@pytest.fixture()
def full(services) -> Services:
    """The R1 reference feed plus Round 2's dedup fixtures — the
    full planted book the dedup and history gates grade against."""
    from pv_cases.seed import dedup_fixtures

    services.intake.ingest_feed(services.feed.fetch_reports(), DAY)
    for report in dedup_fixtures():
        services.intake.ingest(report, DAY)
    return services


@pytest.fixture()
def coded(full) -> Services:
    """The full book plus Round 3's coding fixtures — one case per
    match path and per trap the dictionary lookup must handle."""
    from pv_cases.seed import coding_fixtures

    for report in coding_fixtures():
        full.intake.ingest(report, DAY)
    return full


@pytest.fixture()
def assessed(coded) -> Services:
    """The coded book plus Round 4's assessment fixtures — one
    case per seriousness / expectedness / clock shape the three
    packs must grade."""
    from pv_cases.seed import assessment_fixtures

    for report in assessment_fixtures():
        coded.intake.ingest(report, DAY)
    return coded


@pytest.fixture()
def followed(assessed) -> Services:
    """The assessed book plus Round 5's follow-up originals — one
    report per follow-up shape: an assessment request, a coding
    request that resolves on its second cycle, a coding request
    that escalates, and a validity request."""
    from pv_cases.seed import followup_fixtures

    for report in followup_fixtures():
        assessed.intake.ingest(report, DAY)
    return assessed
