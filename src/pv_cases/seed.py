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


def coding_fixtures() -> list[IntakeReport]:
    """Round 3's planted set: one report per coding match path, plus
    the traps the lookup must refuse to guess. Each fixture's
    expected coding is named in its comment; tests and evals grade
    exactly these. All are valid reports — every one becomes a
    case."""
    return [
        # Normalised path: the dictionary verbatim in lower case —
        # equal only after case folding. Expected: coded Rash
        # vesicular via `normalised`.
        IntakeReport(
            report_id="RPT-2026-0201",
            source=ReportSource.PATIENT,
            received_on=date(2026, 10, 18),
            patient=Patient(initials="R.D.", age=58, sex="M"),
            reporter=Reporter(name="Patient self report", email="rd@example.net"),
            suspect_product_code="NBT-204",
            suspect_product_name="Somnirel",
            events=[
                AdverseEvent(
                    verbatim_term="blistering rash on hands",
                    onset_date=date(2026, 10, 17),
                    outcome="recovering",
                )
            ],
        ),
        # Synonym path, for the event and the product alike: both
        # wordings are recorded synonyms in the extract. Expected:
        # coded Rash vesicular via `synonym`; product coded
        # NBT-317 via `synonym`.
        IntakeReport(
            report_id="RPT-2026-0202",
            source=ReportSource.CLINICIAN,
            received_on=date(2026, 10, 19),
            patient=Patient(initials="L.M.", age=33, sex="F"),
            reporter=Reporter(
                name="Dr Ines Halvorsen",
                qualification="dermatologist",
                email="ihalvorsen@example-clinic.org",
            ),
            suspect_product_code="NBT-317",
            suspect_product_name="Dermaclear cream",
            events=[
                AdverseEvent(
                    verbatim_term="Blistered rash on hands",
                    onset_date=date(2026, 10, 18),
                    outcome="recovering",
                )
            ],
        ),
        # Near-miss trap: the verbatim resembles the "Blistering
        # rash on hands" entry closely — a string-similarity
        # matcher would code it — but it is not the verbatim, not
        # its normalised form, and not a recorded synonym.
        # Expected: UNCODED (no_dictionary_entry) + a review task.
        # It is the fixture the dictionary-addition flow later
        # settles, as a new dictionary version.
        IntakeReport(
            report_id="RPT-2026-0203",
            source=ReportSource.PATIENT,
            received_on=date(2026, 10, 20),
            patient=Patient(initials="T.H.", age=47, sex="M"),
            reporter=Reporter(name="Patient self report", email="th@example.net"),
            suspect_product_code="NBT-317",
            suspect_product_name="Dermaclear",
            events=[
                AdverseEvent(
                    verbatim_term="Blistering on hands and feet",
                    onset_date=date(2026, 10, 19),
                    outcome="recovering",
                )
            ],
        ),
        # Ambiguity trap: "Feeling drained" is a recorded synonym
        # of two different preferred terms (Fatigue and Asthenia).
        # The lookup must not pick one silently. Expected: UNCODED
        # (ambiguous_match, candidates [Asthenia, Fatigue]) + a
        # review task only a coder's choice can settle.
        IntakeReport(
            report_id="RPT-2026-0204",
            source=ReportSource.CALL_CENTRE,
            received_on=date(2026, 10, 21),
            patient=Patient(initials="W.S.", age=71, sex="F"),
            reporter=Reporter(name="Call centre agent R. Patel", phone="+1-555-0199"),
            suspect_product_code="NBT-101",
            suspect_product_name="Cardivane",
            events=[
                AdverseEvent(
                    verbatim_term="Feeling drained",
                    onset_date=date(2026, 10, 20),
                    outcome="recovering",
                )
            ],
        ),
        # Product near-miss trap: "Cardivane Plus" is not a product
        # name in the extract (and not a synonym of one). The event
        # codes exactly; the product must not be guessed into
        # NBT-101. Expected: event coded Headache via `exact`;
        # product UNCODED (no_dictionary_entry) + a review task.
        IntakeReport(
            report_id="RPT-2026-0205",
            source=ReportSource.PATIENT,
            received_on=date(2026, 10, 22),
            patient=Patient(initials="N.P.", age=29, sex="F"),
            reporter=Reporter(name="Patient self report", email="np@example.net"),
            suspect_product_code=None,
            suspect_product_name="Cardivane Plus",
            events=[
                AdverseEvent(
                    verbatim_term="Severe headache",
                    onset_date=date(2026, 10, 21),
                    outcome="recovered",
                )
            ],
        ),
        # Precedence trap: "Nausea" is one entry's verbatim and
        # another entry's synonym (Vomiting). The stated precedence
        # — exact beats synonym — must settle it. Expected: coded
        # Nausea via `exact`, never Vomiting.
        IntakeReport(
            report_id="RPT-2026-0206",
            source=ReportSource.CLINICIAN,
            received_on=date(2026, 10, 23),
            patient=Patient(initials="G.F.", age=64, sex="M"),
            reporter=Reporter(
                name="Dr Elena Ruiz",
                qualification="cardiologist",
                email="eruiz@example-clinic.org",
                country="ES",
            ),
            suspect_product_code="NBT-204",
            suspect_product_name="Somnirel",
            events=[
                AdverseEvent(
                    verbatim_term="Nausea",
                    onset_date=date(2026, 10, 22),
                    outcome="recovering",
                )
            ],
        ),
    ]


def dedup_fixtures() -> list[IntakeReport]:
    """Round 2's planted set: the near-miss shapes the dedup rule
    pack must grade into their stated bands. Each fixture's expected
    band against the reference feed's A.B. cases (RPT-2026-0005 /
    0006) is named in its comment; tests and evals grade exactly
    these. All are valid reports — every one becomes a case."""
    return [
        # Probable pair (with RPT-2026-0102): the same patient by
        # initials + age (no date of birth on either side, sex
        # missing on one), same product, closely-worded but not
        # identical event terms, onsets two days apart. Expected:
        # probable_duplicate, a link and a human decision.
        IntakeReport(
            report_id="RPT-2026-0101",
            source=ReportSource.PATIENT,
            received_on=date(2026, 10, 6),
            patient=Patient(initials="S.P.", age=45, sex="F"),
            reporter=Reporter(name="Patient self report", email="sp@example.net"),
            suspect_product_code="NBT-204",
            suspect_product_name="Somnirel",
            events=[
                AdverseEvent(
                    verbatim_term="Excessive sleepiness in the morning",
                    onset_date=date(2026, 10, 3),
                    outcome="recovering",
                )
            ],
        ),
        IntakeReport(
            report_id="RPT-2026-0102",
            source=ReportSource.CLINICIAN,
            received_on=date(2026, 10, 8),
            patient=Patient(initials="S.P.", age=45),
            reporter=Reporter(
                name="Dr Mara Lindqvist",
                qualification="GP",
                email="mlindqvist@example-clinic.org",
            ),
            suspect_product_code="NBT-204",
            suspect_product_name="Somnirel",
            events=[
                AdverseEvent(
                    verbatim_term="Excessive sleepiness in the mornings at work",
                    onset_date=date(2026, 10, 5),
                    outcome="recovering",
                )
            ],
        ),
        # Near-miss: same patient and product as the A.B. cases, but
        # a DIFFERENT event with its own onset — a genuine second
        # case. Expected: not_duplicate (event-terms gate).
        IntakeReport(
            report_id="RPT-2026-0103",
            source=ReportSource.CLINICIAN,
            received_on=date(2026, 11, 21),
            patient=Patient(initials="A.B.", date_of_birth=date(1988, 3, 14), sex="F"),
            reporter=Reporter(
                name="Dr Elena Ruiz",
                qualification="cardiologist",
                email="eruiz@example-clinic.org",
                country="ES",
            ),
            suspect_product_code="NBT-317",
            suspect_product_name="Dermaclear",
            events=[
                AdverseEvent(
                    verbatim_term="Severe headache",
                    onset_date=date(2026, 11, 20),
                    outcome="recovered",
                )
            ],
        ),
        # Namesake trap: same initials, same event, same product,
        # same onset as the A.B. cases — but a different date of
        # birth. A different person. Expected: not_duplicate
        # (date-of-birth contradiction gate).
        IntakeReport(
            report_id="RPT-2026-0104",
            source=ReportSource.PATIENT,
            received_on=date(2026, 10, 12),
            patient=Patient(initials="A.B.", date_of_birth=date(1991, 7, 2), sex="F"),
            reporter=Reporter(name="Patient self report", email="a.banerjee@example.net"),
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
        # Recurrence trap: same patient, product and event as the
        # A.B. cases — but onset a full year earlier. A separate
        # occurrence, not a duplicate. Expected: not_duplicate
        # (onset-window gate). Age 38 is consistent with the DOB.
        IntakeReport(
            report_id="RPT-2026-0105",
            source=ReportSource.CLINICIAN,
            received_on=date(2025, 10, 15),
            patient=Patient(initials="A.B.", age=38, date_of_birth=date(1988, 3, 14), sex="F"),
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
                    onset_date=date(2025, 10, 9),
                    outcome="recovered",
                )
            ],
        ),
        # Data-quality fixture: the same A.B. patient again, a new
        # minor event — and an age (52) that contradicts the date of
        # birth on the same patient's other reports (38 in
        # RPT-2026-0105). The history view must show BOTH ages as a
        # conflict, never merge them. Expected dedup band against
        # every A.B. case: not_duplicate (unrelated event terms).
        IntakeReport(
            report_id="RPT-2026-0106",
            source=ReportSource.CALL_CENTRE,
            received_on=date(2026, 10, 16),
            patient=Patient(initials="A.B.", age=52, date_of_birth=date(1988, 3, 14), sex="F"),
            reporter=Reporter(name="Call centre agent R. Patel", phone="+1-555-0199"),
            suspect_product_code="NBT-317",
            suspect_product_name="Dermaclear",
            events=[
                AdverseEvent(
                    verbatim_term="Itchy skin where cream applied",
                    onset_date=date(2026, 10, 15),
                    outcome="recovering",
                )
            ],
        ),
    ]
