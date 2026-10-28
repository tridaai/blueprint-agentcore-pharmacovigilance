"""Intake: the port reports arrive through, and the reference feed
adapter behind it.

Production intake sources (patient portal, call-centre system,
literature surveillance, partner EDI) each implement
:class:`IntakePort`. The reference feed shipped here delivers the
planted set from :mod:`pv_cases.seed` so the whole pipeline runs
offline with graded, reproducible inputs.
"""

from __future__ import annotations

from typing import Protocol

from pv_cases.models import IntakeReport
from pv_cases.seed import reference_feed


class IntakePort(Protocol):
    def fetch_reports(self) -> list[IntakeReport]:
        """Return the reports waiting at this source, in source order."""
        ...


class ReferenceFeedAdapter:
    """The labelled offline stand-in for a live intake source."""

    name = "reference-feed"

    def fetch_reports(self) -> list[IntakeReport]:
        return reference_feed()
