"""Round 13 gates: signal counts.

The disproportionality view over the coded book, built under
the same disciplines as the record it reads: every pair's
2x2 computed in code and stated in full (a flagged pair is
recomputable by hand from its own row), the two derived
measures stated honestly where they are undefined (a term
reported with one product only has *no comparator* and can
never meet the criteria, however large its chi-square),
counting by distinct case over a population that excludes
duplicates, attribution only by the standing coded product
decision (an uncoded product is counted on the face as
unattributed, never guessed), tables as insert-only
artefacts that supersede by link when the record moves, an
independent re-performance that must agree before the
verified mark is set, and aggregate-only payloads — no case
ids, no patient or reporter field, by construction and by
scan. Generation is the safety scientist's named act under
the Round 10 access pack; the auditor reads tables and
generates none.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import date

import httpx
import pytest

from pv_cases import api as api_module
from pv_cases.access import AccessDeniedError, Principal
from pv_cases.models import (
    AdverseEvent,
    IntakeReport,
    Patient,
    Reporter,
    ReportSource,
)
from pv_cases.oversight import phi_problems
from pv_cases.signals import (
    NO_COMPARATOR,
    UNDEFINED,
    SignalError,
    load_signal_pack,
)

WINDOW = (date(2026, 10, 1), date(2026, 10, 31))
AS_OF = date(2026, 11, 20)

SCIENTIST = Principal(id="Dr S. Okafor", role="safety_scientist")
REVIEWER_P = Principal(id="Dr B. Lindqvist", role="safety_reviewer")
AUDITOR_P = Principal(id="Ms A. Whitfield", role="quality_auditor")
INTAKE_P = Principal(id="Pat Intake", role="intake_processor")


def _report(report_id, received, verbatim, *, code="NBT-101",
            name="Cardivane", initials="T.P.", age=58, sex="F",
            onset=date(2026, 10, 3), outcome="recovered"):
    return IntakeReport(
        report_id=report_id,
        source=ReportSource.CLINICIAN,
        received_on=received,
        patient=Patient(initials=initials, age=age, sex=sex),
        reporter=Reporter(
            name="Dr Test Reporter",
            qualification="physician",
            email="treporter@example-clinic.org",
            country="GB",
        ),
        suspect_product_code=code,
        suspect_product_name=name,
        events=[
            AdverseEvent(
                verbatim_term=verbatim,
                onset_date=onset,
                outcome=outcome,
            )
        ],
        narrative="Clinician report via the safety mailbox.",
    )


def _ingest(services, report):
    services.intake.ingest(report, report.received_on)


def _book(services):
    """A book whose 2x2s are known exactly: sixteen reports
    over three products. Dermaclear carries the blistering
    rash four times against one report elsewhere (the pair
    that flags); Headache sits five times on Cardivane and
    nowhere else (no comparator, however large its
    chi-square); Somnolence sits four times on Somnirel and
    nowhere else."""
    rows = [
        # Dermaclear: 4x Rash vesicular, 2x Pruritus.
        ("R13-D1", date(2026, 10, 6), "Blistering rash on hands",
         "NBT-317", "Dermaclear", "J.M.", 54, "F", date(2026, 10, 4)),
        ("R13-D2", date(2026, 10, 8), "Blistering rash on hands",
         "NBT-317", "Dermaclear", "K.T.", 67, "M", date(2026, 10, 5)),
        ("R13-D3", date(2026, 10, 10), "Blistering rash on hands",
         "NBT-317", "Dermaclear", "L.A.", 49, "F", date(2026, 10, 8)),
        ("R13-D4", date(2026, 10, 11), "Blistering rash on hands",
         "NBT-317", "Dermaclear", "N.P.", 73, "F", date(2026, 10, 9)),
        ("R13-D5", date(2026, 10, 14), "Itchy skin where cream applied",
         "NBT-317", "Dermaclear", "R.S.", 38, "F", date(2026, 10, 12)),
        ("R13-D6", date(2026, 10, 17), "Itchy skin where cream applied",
         "NBT-317", "Dermaclear", "T.W.", 59, "M", date(2026, 10, 15)),
        # Cardivane: 1x Rash vesicular, 5x Headache.
        ("R13-C1", date(2026, 10, 11), "Blistering rash on hands",
         "NBT-101", "Cardivane", "O.D.", 61, "M", date(2026, 10, 7)),
        ("R13-C2", date(2026, 10, 5), "Severe headache",
         "NBT-101", "Cardivane", "B.F.", 45, "F", date(2026, 10, 3)),
        ("R13-C3", date(2026, 10, 8), "Severe headache",
         "NBT-101", "Cardivane", "C.G.", 52, "M", date(2026, 10, 6)),
        ("R13-C4", date(2026, 10, 12), "Severe headache",
         "NBT-101", "Cardivane", "D.H.", 47, "F", date(2026, 10, 10)),
        ("R13-C5", date(2026, 10, 15), "Severe headache",
         "NBT-101", "Cardivane", "E.J.", 66, "M", date(2026, 10, 13)),
        ("R13-C6", date(2026, 10, 19), "Severe headache",
         "NBT-101", "Cardivane", "F.K.", 58, "F", date(2026, 10, 16)),
        # Somnirel: 4x Somnolence.
        ("R13-S1", date(2026, 10, 4), "Could not stay awake at work",
         "NBT-204", "Somnirel", "G.L.", 44, "M", date(2026, 10, 2)),
        ("R13-S2", date(2026, 10, 11), "Could not stay awake at work",
         "NBT-204", "Somnirel", "H.M.", 51, "F", date(2026, 10, 9)),
        ("R13-S3", date(2026, 10, 16), "Could not stay awake at work",
         "NBT-204", "Somnirel", "I.N.", 63, "M", date(2026, 10, 14)),
        ("R13-S4", date(2026, 10, 20), "Could not stay awake at work",
         "NBT-204", "Somnirel", "J.O.", 57, "F", date(2026, 10, 18)),
    ]
    for rid, received, verbatim, code, name, initials, age, sex, onset in rows:
        _ingest(
            services,
            _report(
                rid, received, verbatim, code=code, name=name,
                initials=initials, age=age, sex=sex, onset=onset,
            ),
        )
    return services


def _pair(table, code, term):
    for pair in table.pairs:
        if pair.product_code == code and pair.preferred_term == term:
            return pair
    raise AssertionError(f"no pair {code} / {term} on the table")


def _generate(services, **kw):
    return services.signals.generate(
        WINDOW[0], WINDOW[1], AS_OF, "Dr S. Okafor", today=AS_OF, **kw
    )


# ---------------------------------------------------------------------
# The figures themselves — hand-computed, exact.
# ---------------------------------------------------------------------


def test_table_figures_are_exact(services):
    _book(services)
    table = _generate(services)
    assert table.verified and table.verification_problems == []
    totals = {k: v["value"] for k, v in table.totals.items()}
    assert totals == {
        "population_cases": 16,
        "attributable_reports": 16,
        "unattributed_reports": 0,
        "uncoded_event_decisions": 0,
        "distinct_products": 3,
        "distinct_terms": 4,
        "pairs_counted": 5,
        "pairs_flagged": 1,
    }

    flagged = _pair(table, "NBT-317", "Rash vesicular")
    assert (flagged.a, flagged.b, flagged.c, flagged.d) == (4, 2, 1, 9)
    assert flagged.prr == "6.6667"
    assert flagged.chi_square == "5.6048"
    assert flagged.meets_criteria is True

    pruritus = _pair(table, "NBT-317", "Pruritus")
    assert (pruritus.a, pruritus.b, pruritus.c, pruritus.d) == (2, 4, 0, 10)
    assert pruritus.prr == NO_COMPARATOR
    assert pruritus.chi_square == "3.8095"
    assert pruritus.meets_criteria is False

    other_side = _pair(table, "NBT-101", "Rash vesicular")
    assert (other_side.a, other_side.b, other_side.c, other_side.d) == (
        1, 5, 4, 6,
    )
    assert other_side.prr == "0.4167"
    assert other_side.chi_square == "0.9503"
    assert other_side.meets_criteria is False


def test_no_comparator_never_flags_however_large_the_chi_square(services):
    """The pack's crisp rule: a term reported with one
    product only has no comparator rate, so the ratio leg
    cannot be evaluated — a chi-square of 12 or 16 does not
    rescue it."""
    _book(services)
    table = _generate(services)
    headache = _pair(table, "NBT-101", "Headache")
    assert (headache.a, headache.b, headache.c, headache.d) == (5, 1, 0, 10)
    assert headache.prr == NO_COMPARATOR
    assert headache.chi_square == "12.1212"
    assert headache.meets_criteria is False
    somnolence = _pair(table, "NBT-204", "Somnolence")
    assert (somnolence.a, somnolence.b, somnolence.c, somnolence.d) == (
        4, 0, 0, 12,
    )
    assert somnolence.prr == NO_COMPARATOR
    assert somnolence.chi_square == "16.0000"
    assert somnolence.meets_criteria is False


def test_soc_rows_carry_their_denominators(services):
    _book(services)
    table = _generate(services)
    rows = {
        (r.product_code, r.soc): r for r in table.soc_rows
    }
    assert len(rows) == 4
    skin_cream = rows[("NBT-317", "Skin and subcutaneous tissue disorders")]
    assert skin_cream.cases == 6 and skin_cream.product_reports == 6
    assert skin_cream.share == {
        "value": "1.0000", "numerator": 6, "denominator": 6,
    }
    skin_card = rows[("NBT-101", "Skin and subcutaneous tissue disorders")]
    assert skin_card.cases == 1 and skin_card.product_reports == 6
    assert skin_card.share == {
        "value": "0.1667", "numerator": 1, "denominator": 6,
    }
    nerv_card = rows[("NBT-101", "Nervous system disorders")]
    assert nerv_card.cases == 5
    assert nerv_card.share["value"] == "0.8333"
    nerv_somn = rows[("NBT-204", "Nervous system disorders")]
    assert nerv_somn.cases == 4 and nerv_somn.share["value"] == "1.0000"


def test_chi_square_undefined_on_an_empty_margin(services):
    """Two reports, one product, one term: every margin but
    one is empty, the statistic's denominator is zero, and
    the row states UNDEFINED rather than a number."""
    for n in range(2):
        _ingest(
            services,
            _report(
                f"R13-U{n}", date(2026, 10, 5 + n), "Severe headache",
                initials=f"U{n}.V.", age=40 + n,
                onset=date(2026, 10, 3 + n),
            ),
        )
    table = _generate(services)
    pair = _pair(table, "NBT-101", "Headache")
    assert (pair.a, pair.b, pair.c, pair.d) == (2, 0, 0, 0)
    assert pair.prr == NO_COMPARATOR
    assert pair.chi_square == UNDEFINED
    assert pair.meets_criteria is False
    assert table.verified


# ---------------------------------------------------------------------
# Population honesty: attribution, the window, the as-of,
# duplicates.
# ---------------------------------------------------------------------


def test_unattributed_and_uncoded_are_counted_on_the_face(services):
    # A product the dictionary does not know: the case is
    # founded, its product stands uncoded, and it enters no
    # pair — counted as unattributed, never guessed.
    _ingest(
        services,
        _report(
            "R13-W1", date(2026, 10, 6), "Severe headache",
            code="NBT-999", name="Wondercream",
            initials="W.A.", age=47, onset=date(2026, 10, 4),
        ),
    )
    # A codable product with an event the dictionary does
    # not know: attributed, its event uncoded and counted.
    _ingest(
        services,
        _report(
            "R13-W2", date(2026, 10, 7), "Feeling peculiar",
            initials="W.B.", age=52, onset=date(2026, 10, 5),
        ),
    )
    # Outside the October window entirely.
    _ingest(
        services,
        _report(
            "R13-W3", date(2026, 11, 2), "Severe headache",
            initials="W.C.", age=60, onset=date(2026, 10, 30),
        ),
    )
    table = _generate(services)
    totals = {k: v["value"] for k, v in table.totals.items()}
    assert totals["population_cases"] == 2
    assert totals["attributable_reports"] == 1
    assert totals["unattributed_reports"] == 1
    assert totals["uncoded_event_decisions"] == 1
    assert table.pairs == []
    assert table.verified


def test_as_of_freezes_the_standing_decisions(services):
    """A case received in the window but coded after the
    as-of has no standing decisions at that as-of: it is in
    the population and unattributed — the table is a view
    of the record as it stood, not as it later became."""
    _ingest(
        services,
        _report(
            "R13-A1", date(2026, 10, 30), "Severe headache",
            initials="A.S.", age=49, onset=date(2026, 10, 28),
        ),
    )
    early = services.signals.generate(
        WINDOW[0], WINDOW[1], date(2026, 10, 15), "Dr S. Okafor",
        today=date(2026, 10, 15),
    )
    totals = {k: v["value"] for k, v in early.totals.items()}
    assert totals["population_cases"] == 1
    assert totals["attributable_reports"] == 0
    assert totals["unattributed_reports"] == 1
    assert early.pairs == []
    assert early.verified


def test_duplicates_are_out_of_the_population(services):
    """The same report twice: the later case links as a
    duplicate at intake and, once a human confirms, closes
    as one. Either way it is not a second report — the
    population counts the primary once."""
    first = _report(
        "R13-P1", date(2026, 10, 6), "Severe headache",
        initials="D.P.", age=60, sex="F", onset=date(2026, 10, 4),
    )
    second = _report(
        "R13-P2", date(2026, 10, 7), "Severe headache",
        initials="D.P.", age=60, sex="F", onset=date(2026, 10, 4),
    )
    third = _report(
        "R13-P3", date(2026, 10, 9), "Severe headache",
        initials="E.Q.", age=44, sex="M", onset=date(2026, 10, 6),
    )
    for report in (first, second, third):
        _ingest(services, report)
    queue = services.dedup.pending_queue()
    assert len(queue) == 1
    services.dedup.confirm_link(
        queue[0].link_id, "Dr A. Rao", date(2026, 10, 12),
        note="same report, submitted twice",
    )
    table = _generate(services)
    totals = {k: v["value"] for k, v in table.totals.items()}
    assert totals["population_cases"] == 2
    assert totals["attributable_reports"] == 2
    pair = _pair(table, "NBT-101", "Headache")
    assert pair.a == 2
    assert table.verified


# ---------------------------------------------------------------------
# The artefact discipline: insert-only, supersession,
# re-performance, tamper evidence, PHI by construction.
# ---------------------------------------------------------------------


def test_same_record_returns_the_standing_table(services):
    _book(services)
    first = _generate(services)
    again = _generate(services)
    assert again.table_id == first.table_id
    assert again.supersedes is None
    assert len(services.signals.tables()) == 1


def test_moved_record_mints_a_superseding_table(services):
    _book(services)
    first = _generate(services)
    _ingest(
        services,
        _report(
            "R13-NEW", date(2026, 10, 21), "Blistering rash on hands",
            code="NBT-317", name="Dermaclear",
            initials="Z.Z.", age=56, onset=date(2026, 10, 19),
        ),
    )
    moved = _generate(services)
    assert moved.table_id != first.table_id
    assert moved.supersedes == first.table_id
    assert len(services.signals.tables()) == 2
    # The old table stands exactly as struck.
    standing = services.signals.get(first.table_id)
    assert standing.totals["attributable_reports"]["value"] == 16
    assert moved.totals["attributable_reports"]["value"] == 17
    assert moved.verified


def test_tampered_cell_is_named_by_the_verifier(services):
    _book(services)
    table = _generate(services)
    tampered_pairs = copy.deepcopy(table.pairs)
    target = next(
        p
        for p in tampered_pairs
        if p.product_code == "NBT-317"
        and p.preferred_term == "Rash vesicular"
    )
    target.a += 1
    tampered = table.model_copy(update={"pairs": tampered_pairs})
    problems = services.signals.verify(tampered)
    assert any(
        "pairs[NBT-317|Rash vesicular|" in p and ".a:" in p
        for p in problems
    )
    tampered_totals = copy.deepcopy(table.totals)
    tampered_totals["pairs_flagged"]["value"] = 99
    tampered2 = table.model_copy(update={"totals": tampered_totals})
    problems2 = services.signals.verify(tampered2)
    assert any("totals.pairs_flagged" in p for p in problems2)


def test_payload_carries_no_phi_keys(services):
    _book(services)
    table = _generate(services)
    assert phi_problems(table.model_dump(mode="json")) == []
    blob = table.model_dump_json()
    assert "R13-D1" not in blob and "CASE-" not in blob


def test_empty_window_is_a_zero_table_not_an_error(services):
    table = services.signals.generate(
        date(2026, 12, 1), date(2026, 12, 31), AS_OF,
        "Dr S. Okafor", today=AS_OF,
    )
    assert table.verified
    assert table.pairs == [] and table.soc_rows == []
    assert table.totals["population_cases"]["value"] == 0


def test_reversed_window_is_refused_by_name(services):
    with pytest.raises(SignalError):
        services.signals.generate(
            WINDOW[1], WINDOW[0], AS_OF, "Dr S. Okafor", today=AS_OF
        )


def test_pack_loader_is_fail_closed():
    pack = load_signal_pack()
    assert pack.version == "2026.10.1"
    assert pack.criteria.min_cases == 3
    assert pack.criteria.prr_threshold == "2.0000"
    assert pack.criteria.chi_square_threshold == "4.0000"
    with pytest.raises(ValueError):
        load_signal_pack("1999.1.1")


# ---------------------------------------------------------------------
# Access: generation is the scientist's act; reads follow
# the pack; every refusal is recorded before it propagates.
# ---------------------------------------------------------------------


def test_generation_roles_and_denial_log(services):
    _book(services)
    denials_before = len(services.store.list_access_denials())
    table = _generate(services, principal=SCIENTIST)
    assert table.verified
    with pytest.raises(AccessDeniedError) as excinfo:
        _generate(services, principal=REVIEWER_P)
    assert excinfo.value.reason == "act_not_permitted"
    with pytest.raises(AccessDeniedError) as excinfo:
        _generate(services, principal=AUDITOR_P)
    assert excinfo.value.reason == "read_only_role"
    with pytest.raises(AccessDeniedError) as excinfo:
        services.signals.generate(
            WINDOW[0], WINDOW[1], AS_OF, "Someone Else",
            principal=SCIENTIST, today=AS_OF,
        )
    assert excinfo.value.reason == "actor_mismatch"
    assert len(services.store.list_access_denials()) == denials_before + 3


# ---------------------------------------------------------------------
# Surfaces: the API.
# ---------------------------------------------------------------------


@pytest.fixture()
def call(services):
    api_module.override_services(services)
    transport = httpx.ASGITransport(app=api_module.app)

    async def _call(method, url, **kw):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as c:
            return await c.request(method, url, **kw)

    return lambda method, url, **kw: asyncio.run(_call(method, url, **kw))


def _headers(principal):
    return {
        "x-principal-id": principal.id,
        "x-principal-role": principal.role,
    }


def test_api_signals_flow(call, services):
    _book(services)
    pack = call("GET", "/signals/pack", headers=_headers(AUDITOR_P))
    assert pack.status_code == 200
    assert pack.json()["criteria"]["min_cases"] == 3
    refused = call("GET", "/signals/pack", headers=_headers(INTAKE_P))
    assert refused.status_code == 403

    made = call(
        "POST", "/signals/tables",
        json={
            "window_start": "2026-10-01",
            "window_end": "2026-10-31",
            "as_of": "2026-11-20",
            "generated_by": "Dr S. Okafor",
        },
        headers=_headers(SCIENTIST),
    )
    assert made.status_code == 200
    table_id = made.json()["table_id"]
    assert made.json()["verified"] is True
    assert made.json()["totals"]["pairs_flagged"]["value"] == 1

    denied = call(
        "POST", "/signals/tables",
        json={
            "window_start": "2026-10-01",
            "window_end": "2026-10-31",
            "as_of": "2026-11-20",
            "generated_by": "Dr B. Lindqvist",
        },
        headers=_headers(REVIEWER_P),
    )
    assert denied.status_code == 403

    listed = call("GET", "/signals/tables", headers=_headers(AUDITOR_P))
    assert [t["table_id"] for t in listed.json()] == [table_id]
    hidden = call("GET", "/signals/tables", headers=_headers(INTAKE_P))
    assert hidden.json() == []

    check = call(
        "GET", f"/signals/tables/{table_id}/verify",
        headers=_headers(AUDITOR_P),
    )
    assert check.json() == {
        "table_id": table_id, "verified": True, "problems": [],
    }
    missing = call("GET", "/signals/tables/SIG-nope")
    assert missing.status_code == 404
