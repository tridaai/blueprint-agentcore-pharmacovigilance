"""Seed data: the fictional sponsor, its products, and the planted
reference feed.

Everything here is invented for this blueprint — Northbridge
Therapeutics, its three products, and every patient and reporter in
the feed are fictional. The feed is deliberately planted with the
shapes R1 must handle: valid reports, a report with no identifiable
reporter, a report with no identifiable patient, and a pair of
reports that share an exact de-duplication key.
"""

from __future__ import annotations

from datetime import date

from pv_cases.models import (
    AdverseEvent,
    IntakeReport,
    Patient,
    Product,
    Reporter,
    ReportSource,
)

SPONSOR_NAME = "Northbridge Therapeutics"

PRODUCTS: list[Product] = [
    Product(
        code="NBT-101",
        name="Cardivane",
        active_substance="veldorin",
        form="10 mg film-coated tablets",
    ),
    Product(
        code="NBT-204",
        name="Somnirel",
        active_substance="trezapan",
        form="5 mg capsules",
    ),
    Product(
        code="NBT-317",
        name="Dermaclear",
        active_substance="osalide",
        form="2% topical cream",
    ),
]


def products_by_code() -> dict[str, Product]:
    return {p.code: p for p in PRODUCTS}


def reference_feed() -> list[IntakeReport]:
    """The planted intake feed. Stable order, stable ids — tests and
    evals grade against exactly this set."""
    return [
        # A complete, valid clinician report — the demo's main case.
        IntakeReport(
            report_id="RPT-2026-0001",
            source=ReportSource.CLINICIAN,
            received_on=date(2026, 10, 5),
            patient=Patient(initials="M.K.", age=67, sex="F"),
            reporter=Reporter(
                name="Dr Elena Ruiz",
                qualification="cardiologist",
                email="eruiz@example-clinic.org",
                country="ES",
            ),
            suspect_product_code="NBT-101",
            suspect_product_name="Cardivane",
            events=[
                AdverseEvent(
                    verbatim_term="Dizziness and fall at home",
                    onset_date=date(2026, 10, 2),
                    outcome="recovered",
                    description="Patient reported dizziness on standing, "
                    "fell at home, no fracture; dose held by reporter.",
                )
            ],
            narrative="Clinician report via the sponsor's safety mailbox.",
        ),
        # A complete, valid patient report.
        IntakeReport(
            report_id="RPT-2026-0002",
            source=ReportSource.PATIENT,
            received_on=date(2026, 10, 7),
            patient=Patient(age_group="40-49", sex="M", reporter_patient_id="NB-PAT-4471"),
            reporter=Reporter(name="Self report via patient portal", phone="+1-555-0142"),
            suspect_product_code="NBT-204",
            suspect_product_name="Somnirel",
            events=[
                AdverseEvent(
                    verbatim_term="Could not stay awake at work",
                    onset_date=date(2026, 10, 6),
                    outcome="recovering",
                )
            ],
        ),
        # Invalid: nobody identifiable stands behind this report —
        # no reporter name and no contact channel. Becomes a
        # follow-up task, never a case.
        IntakeReport(
            report_id="RPT-2026-0003",
            source=ReportSource.CALL_CENTRE,
            received_on=date(2026, 10, 8),
            patient=Patient(initials="J.T.", age=52, sex="M"),
            reporter=Reporter(qualification="call centre note, caller hung up"),
            suspect_product_code="NBT-101",
            suspect_product_name="Cardivane",
            events=[
                AdverseEvent(
                    verbatim_term="Rash on both arms",
                    onset_date=date(2026, 10, 7),
                    outcome="unknown",
                )
            ],
        ),
        # Invalid: no identifiable patient — an anonymous web form
        # with no patient characteristics at all.
        IntakeReport(
            report_id="RPT-2026-0004",
            source=ReportSource.SPONSOR_SITE,
            received_on=date(2026, 10, 9),
            patient=Patient(),
            reporter=Reporter(name="Anonymous web form", email="anon@example.net"),
            suspect_product_code="NBT-317",
            suspect_product_name="Dermaclear",
            events=[
                AdverseEvent(
                    verbatim_term="Skin burning after use",
                    onset_date=date(2026, 10, 8),
                    outcome="unknown",
                )
            ],
        ),
        # The duplicate-looking pair: the same event reported twice —
        # once by the patient, once by their pharmacist — with the
        # same exact de-duplication key (same patient key fields,
        # product, event term and onset date). R1 stores both as
        # cases and flags the second as a suspected duplicate of the
        # first; merging is R2's job, with a human.
        IntakeReport(
            report_id="RPT-2026-0005",
            source=ReportSource.PATIENT,
            received_on=date(2026, 10, 10),
            patient=Patient(initials="A.B.", date_of_birth=date(1988, 3, 14), sex="F"),
            reporter=Reporter(name="Patient self report", email="ab@example.net"),
            suspect_product_code="NBT-317",
            suspect_product_name="Dermaclear",
            events=[
                AdverseEvent(
                    verbatim_term="Blistering rash on hands",
                    onset_date=date(2026, 10, 9),
                    outcome="recovering",
                )
            ],
        ),
        IntakeReport(
            report_id="RPT-2026-0006",
            source=ReportSource.CLINICIAN,
            received_on=date(2026, 10, 11),
            patient=Patient(initials="A.B.", date_of_birth=date(1988, 3, 14), sex="F"),
            reporter=Reporter(
                name="Pharmacist D. Osei",
                qualification="pharmacist",
                email="dosei@example-pharmacy.org",
            ),
            suspect_product_code="NBT-317",
            suspect_product_name="Dermaclear",
            events=[
                AdverseEvent(
                    verbatim_term="Blistering rash on hands",
                    onset_date=date(2026, 10, 9),
                    outcome="recovering",
                )
            ],
        ),
    ]
