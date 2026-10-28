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
