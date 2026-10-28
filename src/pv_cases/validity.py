"""The validity gate — Law 1 of this blueprint, in code.

A case exists only when all four minimum criteria hold: an
identifiable patient, an identifiable reporter, a suspect product,
and at least one adverse event. The gate is a pure function of the
report: deterministic, total, and the only route by which a report
can become a case (see :mod:`pv_cases.services`).
"""

from __future__ import annotations

from pv_cases.models import (
    CRITERION_EVENT,
    CRITERION_PATIENT,
    CRITERION_PRODUCT,
    CRITERION_REPORTER,
    IntakeReport,
    ValidityAssessment,
    patient_is_identifiable,
    reporter_is_identifiable,
)


def assess_validity(report: IntakeReport) -> ValidityAssessment:
    missing: list[str] = []
    if not patient_is_identifiable(report.patient):
        missing.append(CRITERION_PATIENT)
    if not reporter_is_identifiable(report.reporter):
        missing.append(CRITERION_REPORTER)
    product_named = bool(
        (report.suspect_product_code and report.suspect_product_code.strip())
        or (report.suspect_product_name and report.suspect_product_name.strip())
    )
    if not product_named:
        missing.append(CRITERION_PRODUCT)
    has_event = any(e.verbatim_term and e.verbatim_term.strip() for e in report.events)
    if not has_event:
        missing.append(CRITERION_EVENT)
    return ValidityAssessment(valid=not missing, missing=missing)
