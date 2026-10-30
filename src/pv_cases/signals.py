"""Signal counts — Round 13.

Rounds 1–12 built the case record and the periodic oversight
view over it. What neither gave the safety organisation is
the signal-detection view: over the coded book, which
product–event pairs are reported disproportionately often
against the rest of the book? That is the question every
pharmacovigilance operation asks of its data between
periodic reports, and until this round nothing in the
system could answer it — the arithmetic existed nowhere.
This module is that view, built under the same disciplines
as everything it reads:

- **The counts are computed in code, never judged by a
  model.** For every product–event pair the table states
  the full 2×2 — reports with the product and the term
  (a), with the product and not the term (b), with the
  term and not the product (c), with neither (d) — and the
  two derived measures the stated criteria use: the
  proportional reporting ratio and the chi-square of the
  2×2. A pair is flagged when it meets the pack's stated
  criteria (a >= 3, PRR >= 2, chi-square >= 4 — the
  long-published screening criteria for spontaneous-report
  disproportionality). A flag is a prompt for human signal
  evaluation, never a finding: disproportionality in
  spontaneous reports is not incidence and not causation,
  and the table says so on its face.
- **Every measure carries its counts.** No ratio appears
  without the four cells it was computed from, and no
  share without its numerator and denominator on the
  figure. Where a measure is undefined the table states
  why instead of inventing a number: a term reported only
  with one product has *no comparator* (the ratio's second
  term divides by zero), and a 2×2 with an empty margin
  has an *undefined* chi-square.
- **Counting is by distinct case.** A case carrying the
  same term twice still counts once in its cell —
  disproportionality counts reports, not mentions. Cases
  closed as duplicates and duplicate-linked cases are out
  of the population: the primary case carries the report,
  and counting both would count one report twice.
- **Attribution is by the standing coded decision.** A
  case enters a product's row only when its standing
  suspect-product decision is coded; a case whose product
  stands uncoded is counted on the table's face as
  unattributed — never silently dropped, never guessed
  into a product. Event terms come from standing coded
  event decisions only; an uncoded event enters no pair
  and is counted on the face as uncoded.
- **Tables are insert-only artefacts** (migration 0012).
  A table's id is content-addressed over its window, its
  as-of, the pack version and a fingerprint of the input
  artefacts it was computed from. Recomputing over an
  unmoved record returns the standing artefact;
  recomputing after the record moves mints a NEW table
  linked by `supersedes`. The old table stands, never
  edited.
- **Every table is re-performed before it is believed.**
  `verify_table` recomputes every cell and measure from
  the raw store by a second aggregation path and compares
  figure for figure; a table carries its `verified` mark
  only when the re-performance agrees, and
  `scripts/check_signals.py` re-runs the same check over
  stored tables. One mismatch refuses the mark and names
  the cell.
- **Aggregate-only — no PHI by construction.** The table
  model has no field that could carry a patient or
  reporter identifier, a narrative, a verbatim or a case
  id: it states product codes, coded terms and counts.
  (The generator's own name stands on the table —
  generating is a named act, and staff identity is not
  PHI.) The eval gate scans table payloads for forbidden
  keys, at zero tolerance.
- **Access follows the Round 10 pack.** Reading a table
  takes the `signal_counts` surface (the case-working
  roles and the quality auditor; not intake, not case
  admin), and generating one is the safety scientist's
  named act, judged by the access pack like every other
  act — the auditor reads tables and generates none.
- **A table is an internal signal-detection artefact.**
  It states so on its face. It is not a regulatory
  submission, and nothing in this system is ever
  submitted to a regulator.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field

from pv_cases.access import (
    ACT_GENERATE_SIGNAL_COUNTS,
    ROLE_MEDICAL_REVIEWER,
    ROLE_QUALITY_AUDITOR,
    ROLE_SAFETY_REVIEWER,
    ROLE_SAFETY_SCIENTIST,
    SURFACE_SIGNAL_COUNTS,
    Principal,
    guard_act,
)
from pv_cases.coding import current_decisions
from pv_cases.evidence import canonical_json
from pv_cases.models import (
    SUBJECT_EVENT,
    SUBJECT_PRODUCT,
    CaseStatus,
    CodingStatus,
)
from pv_cases.oversight import phi_problems

SIGNAL_PACK_VERSION = "2026.10.1"

# The value a pair's PRR carries when the term was reported
# with no other product: the ratio's comparator term is
# zero, so the ratio is undefined — stated, never faked as
# a large number. A pair with no comparator can never meet
# the stated criteria: the ratio leg cannot be evaluated.
NO_COMPARATOR = "no_comparator"

# The value a pair's chi-square carries when a margin of
# the 2x2 is empty and the statistic's denominator is zero.
UNDEFINED = "undefined"

NOTICE = (
    "Internal signal-detection artefact: disproportionality "
    "counts computed in code from this system's coded case "
    "record. A flagged pair is a prompt for human signal "
    "evaluation — disproportionality in spontaneous reports "
    "is not incidence and not evidence of causation. It is "
    "not a regulatory submission, and nothing in this system "
    "is ever submitted to a regulator."
)


class SignalError(RuntimeError):
    """A table that cannot be generated as asked — a window
    whose end precedes its start. Named, never guessed
    around."""


# ---------------------------------------------------------------------
# The signal pack — the definitions a figure's meaning is
# versioned by. Every table names the pack that produced it.
# ---------------------------------------------------------------------


class SignalCriteria(BaseModel):
    """The stated screening criteria a pair is flagged
    against: the minimum report count, the PRR floor and
    the chi-square floor, as pack data — a table's flags
    are reproducible from its pack alone."""

    min_cases: int
    prr_threshold: str
    chi_square_threshold: str


class SignalPack(BaseModel):
    """The signal pack: the population, attribution and
    counting rules, the measures a pair and a system-organ
    row state, the flagging criteria, and the role rule for
    the surface (mirrored by the access pack's grants — this
    statement is the meaning, that one is the enforcement)."""

    version: str
    read_roles: list[str]
    generate_roles: list[str]
    population_rule: str
    attribution_rule: str
    counting_rule: str
    pair_measures: list[str]
    soc_measures: list[str]
    criteria: SignalCriteria


SIGNAL_PACK_V1 = SignalPack(
    version=SIGNAL_PACK_VERSION,
    read_roles=[
        ROLE_SAFETY_SCIENTIST,
        ROLE_SAFETY_REVIEWER,
        ROLE_MEDICAL_REVIEWER,
        ROLE_QUALITY_AUDITOR,
    ],
    generate_roles=[ROLE_SAFETY_SCIENTIST],
    population_rule=(
        "Cases received inside the window, excluding cases "
        "closed as duplicates and duplicate-linked cases — "
        "the primary case carries the report. Standing "
        "decisions are those of record at the as-of (coded "
        "on or before it, current under supersession)."
    ),
    attribution_rule=(
        "A case is attributed to the product its standing "
        "suspect-product decision codes. A case whose "
        "standing product decision is uncoded is counted as "
        "unattributed on the table's face and enters no pair."
    ),
    counting_rule=(
        "Distinct cases per cell: a case carrying a term "
        "more than once counts once. Event terms are the "
        "preferred terms of standing coded event decisions; "
        "an uncoded event decision enters no pair and is "
        "counted on the face as uncoded."
    ),
    pair_measures=[
        "a (product and term)",
        "b (product, not term)",
        "c (term, not product)",
        "d (neither)",
        "prr",
        "chi_square",
        "meets_criteria",
    ],
    soc_measures=["cases", "product_reports", "share"],
    criteria=SignalCriteria(
        min_cases=3,
        prr_threshold="2.0000",
        chi_square_threshold="4.0000",
    ),
)


def load_signal_pack(version: str | None = None) -> SignalPack:
    """The loader, fail-closed like every pack loader in this
    repo: an unknown version is an error, never a silent fall
    back to v1."""
    if version is None or version == SIGNAL_PACK_V1.version:
        return SIGNAL_PACK_V1
    raise ValueError(f"unknown signal pack version: {version!r}")


# ---------------------------------------------------------------------
# The table artefact.
# ---------------------------------------------------------------------


class SignalPair(BaseModel):
    """One product–event pair's 2x2 over the book, with the
    two derived measures and the criteria verdict. `prr` is
    a four-place string or NO_COMPARATOR; `chi_square` is a
    four-place string or UNDEFINED. The cells are always
    stated, so a reader can recompute either measure by
    hand from the row alone."""

    product_code: str
    product_name: str
    preferred_term: str
    soc: str
    a: int
    b: int
    c: int
    d: int
    prr: str
    chi_square: str
    meets_criteria: bool


class SignalSocRow(BaseModel):
    """One product's reporting within one system organ
    class: the distinct attributed cases carrying any coded
    term of the class, against that product's attributed
    reports — the share carries its denominator on the
    figure, as every rate in this system does."""

    product_code: str
    product_name: str
    soc: str
    cases: int
    product_reports: int
    share: dict[str, Any]


class SignalTable(BaseModel):
    """One signal table over a window: an insert-only
    artefact.

    `totals` states the population honestly — how many
    cases the window held, how many were attributable to a
    coded product, how many stood unattributed, how many
    standing event decisions were uncoded — before any pair
    is read. `record_fingerprint` hashes the sorted ids of
    the input artefacts the figures derive from;
    `table_id` is content-addressed over the window, the
    as-of, the pack version and that fingerprint, so
    identical inputs mint the identical table and a moved
    input mints a new one linked by `supersedes`.
    `verified` is the re-performance mark: it is set only
    when `verify_table`'s second path agrees with every
    figure, and `verification_problems` names any figure it
    refused."""

    table_id: str
    pack_version: str
    window_start: date
    window_end: date
    as_of: date
    generated_by: str
    generated_on: date
    notice: str = NOTICE
    totals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pairs: list[SignalPair] = Field(default_factory=list)
    soc_rows: list[SignalSocRow] = Field(default_factory=list)
    record_fingerprint: str
    supersedes: str | None = None
    verified: bool = False
    verification_problems: list[str] = Field(default_factory=list)


def _count(value: int) -> dict[str, Any]:
    return {"value": value, "numerator": None, "denominator": None}


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    """A rate as a stated figure: four places, HALF_UP, with
    its population on the figure — or `no_cases` when there
    is nothing to divide by, the oversight pack's stated
    empty-population value."""
    if denominator == 0:
        return {
            "value": "no_cases",
            "numerator": numerator,
            "denominator": denominator,
        }
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return {
        "value": format(value, ".4f"),
        "numerator": numerator,
        "denominator": denominator,
    }


def _four_places(numerator: int, denominator: int) -> str:
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return format(value, ".4f")


def _measures(a: int, b: int, c: int, d: int, criteria: SignalCriteria):
    """The two derived measures of a 2x2 and the criteria
    verdict, computed in code from the four cells alone.

    PRR = [a/(a+b)] / [c/(c+d)]. When c is 0 the comparator
    rate is zero and the ratio is undefined: the row states
    NO_COMPARATOR and can never meet the criteria — the
    ratio leg cannot be evaluated, however large a is.
    Chi-square is the Pearson statistic of the 2x2,
    N(ad-bc)^2 / ((a+b)(c+d)(a+c)(b+d)); an empty margin
    zeroes the denominator and the row states UNDEFINED.
    """
    if c == 0:
        prr = NO_COMPARATOR
    else:
        prr = _four_places(a * (c + d), c * (a + b))
    n = a + b + c + d
    margins = (a + b) * (c + d) * (a + c) * (b + d)
    if margins == 0:
        chi_square = UNDEFINED
    else:
        chi_square = _four_places(
            n * (a * d - b * c) ** 2, margins
        )
    meets = (
        a >= criteria.min_cases
        and prr != NO_COMPARATOR
        and Decimal(prr) >= Decimal(criteria.prr_threshold)
        and chi_square != UNDEFINED
        and Decimal(chi_square) >= Decimal(criteria.chi_square_threshold)
    )
    return prr, chi_square, meets


def _fingerprint(input_ids) -> str:
    blob = canonical_json({"artefact_ids": sorted(input_ids)})
    return hashlib.sha256(blob).hexdigest()


def _table_id(
    window_start: date,
    window_end: date,
    as_of: date,
    pack_version: str,
    record_fingerprint: str,
) -> str:
    blob = canonical_json(
        {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "as_of": as_of.isoformat(),
            "pack_version": pack_version,
            "record_fingerprint": record_fingerprint,
        }
    )
    return "SIG-" + hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------
# The builder path: a per-case fold. Each population case is
# visited once; its standing decisions attribute it to a
# product and name its terms; the cells accumulate from
# those per-case facts.
# ---------------------------------------------------------------------


def _standing_by_case(store, as_of: date):
    """case_id -> the case's standing decisions at the
    as-of, grouped from its own decision list by the coding
    layer's current-decision walk."""
    grouped: dict[str, list] = {}
    for case in store.list_cases():
        decisions = [
            d
            for d in store.list_coding_decisions(case.case_id)
            if d.coded_on <= as_of
        ]
        grouped[case.case_id] = current_decisions(decisions)
    return grouped


def build_table_figures(store, window_start, window_end, as_of):
    """Compute a table's totals, pairs and SOC rows over the
    record. Returns (totals, pairs, soc_rows, input artefact
    ids). This is the builder path; `verify_table`
    recomputes by a second path and the two must agree
    figure for figure."""
    pack = SIGNAL_PACK_V1
    input_ids: set[str] = set()
    cases = [
        c
        for c in store.list_cases()
        if window_start <= c.received_on <= window_end
        and c.status
        not in (CaseStatus.DUPLICATE_LINKED, CaseStatus.CLOSED_AS_DUPLICATE)
    ]
    standing = _standing_by_case(store, as_of)

    attributed: dict[str, tuple[str, str]] = {}  # case_id -> (code, name)
    case_terms: dict[str, set[tuple[str, str]]] = {}
    unattributed = 0
    uncoded_events = 0
    for case in cases:
        input_ids.add(case.case_id)
        decisions = standing.get(case.case_id, [])
        input_ids.update(d.decision_id for d in decisions)
        product = next(
            (d for d in decisions if d.subject_kind == SUBJECT_PRODUCT),
            None,
        )
        events = [
            d for d in decisions if d.subject_kind == SUBJECT_EVENT
        ]
        uncoded_events += sum(
            1 for d in events if d.status is CodingStatus.UNCODED
        )
        if product is None or product.status is not CodingStatus.CODED:
            unattributed += 1
            continue
        attributed[case.case_id] = (product.product_code, product.product_name)
        case_terms[case.case_id] = {
            (d.preferred_term, d.soc)
            for d in events
            if d.status is CodingStatus.CODED and d.preferred_term
        }

    total = len(attributed)
    by_product: dict[str, list[str]] = {}
    for case_id, (code, _name) in attributed.items():
        by_product.setdefault(code, []).append(case_id)
    product_names = {
        code: attributed[ids[0]][1] for code, ids in by_product.items()
    }
    term_cases: dict[tuple[str, str], set[str]] = {}
    for case_id, terms in case_terms.items():
        for term in terms:
            term_cases.setdefault(term, set()).add(case_id)

    pairs: list[SignalPair] = []
    for code in sorted(by_product):
        members = by_product[code]
        member_set = set(members)
        terms_here = set()
        for case_id in members:
            terms_here.update(case_terms[case_id])
        for term, soc in sorted(terms_here):
            a = len(member_set & term_cases[(term, soc)])
            b = len(members) - a
            c = len(term_cases[(term, soc)]) - a
            d = total - a - b - c
            prr, chi_square, meets = _measures(
                a, b, c, d, pack.criteria
            )
            pairs.append(
                SignalPair(
                    product_code=code,
                    product_name=product_names[code],
                    preferred_term=term,
                    soc=soc,
                    a=a,
                    b=b,
                    c=c,
                    d=d,
                    prr=prr,
                    chi_square=chi_square,
                    meets_criteria=meets,
                )
            )

    soc_rows: list[SignalSocRow] = []
    for code in sorted(by_product):
        members = by_product[code]
        socs = set()
        for case_id in members:
            socs.update(soc for _term, soc in case_terms[case_id])
        for soc in sorted(socs):
            count = sum(
                1
                for case_id in members
                if any(s == soc for _t, s in case_terms[case_id])
            )
            soc_rows.append(
                SignalSocRow(
                    product_code=code,
                    product_name=product_names[code],
                    soc=soc,
                    cases=count,
                    product_reports=len(members),
                    share=_rate(count, len(members)),
                )
            )

    totals = {
        "population_cases": _count(len(cases)),
        "attributable_reports": _count(total),
        "unattributed_reports": _count(unattributed),
        "uncoded_event_decisions": _count(uncoded_events),
        "distinct_products": _count(len(by_product)),
        "distinct_terms": _count(len(term_cases)),
        "pairs_counted": _count(len(pairs)),
        "pairs_flagged": _count(
            sum(1 for p in pairs if p.meets_criteria)
        ),
    }
    return totals, pairs, soc_rows, input_ids


# ---------------------------------------------------------------------
# The verifier path: the same figures recomputed from the raw
# store by a global fold — every decision in the store is
# grouped by case and walked by hand over the superseded
# set, instead of the coding layer's per-case current walk —
# so a bug in the builder's fold cannot ratify itself.
# ---------------------------------------------------------------------


def _raw_table_figures(store, window_start, window_end, as_of):
    pack = SIGNAL_PACK_V1
    input_ids: set[str] = set()
    population = []
    for case in store.list_cases():
        if not (window_start <= case.received_on <= window_end):
            continue
        if case.status in (
            CaseStatus.DUPLICATE_LINKED,
            CaseStatus.CLOSED_AS_DUPLICATE,
        ):
            continue
        population.append(case)

    all_decisions = [
        d for d in store.list_coding_decisions() if d.coded_on <= as_of
    ]
    superseded = {d.supersedes_id for d in all_decisions if d.supersedes_id}
    standing_by_case: dict[str, list] = {}
    for decision in all_decisions:
        if decision.decision_id in superseded:
            continue
        standing_by_case.setdefault(decision.case_id, []).append(decision)

    product_of: dict[str, tuple[str, str]] = {}
    terms_of: dict[str, set[tuple[str, str]]] = {}
    n_unattributed = 0
    n_uncoded_events = 0
    for case in population:
        input_ids.add(case.case_id)
        decisions = standing_by_case.get(case.case_id, [])
        input_ids.update(d.decision_id for d in decisions)
        products = [
            d for d in decisions if d.subject_kind == SUBJECT_PRODUCT
        ]
        events = [d for d in decisions if d.subject_kind == SUBJECT_EVENT]
        n_uncoded_events += sum(
            1 for d in events if d.status is CodingStatus.UNCODED
        )
        coded_products = [
            d for d in products if d.status is CodingStatus.CODED
        ]
        if not coded_products:
            n_unattributed += 1
            continue
        product = coded_products[0]
        product_of[case.case_id] = (
            product.product_code,
            product.product_name,
        )
        terms_of[case.case_id] = {
            (d.preferred_term, d.soc)
            for d in events
            if d.status is CodingStatus.CODED and d.preferred_term
        }

    n_attributed = len(product_of)
    members_of: dict[str, set[str]] = {}
    for case_id, (code, _name) in product_of.items():
        members_of.setdefault(code, set()).add(case_id)
    names = {}
    for code, ids in members_of.items():
        names[code] = product_of[sorted(ids)[0]][1]
    carriers: dict[tuple[str, str], set[str]] = {}
    for case_id, terms in terms_of.items():
        for term in terms:
            carriers.setdefault(term, set()).add(case_id)

    pairs: list[SignalPair] = []
    for code in sorted(members_of):
        members = members_of[code]
        terms_here = set()
        for case_id in members:
            terms_here.update(terms_of[case_id])
        for term, soc in sorted(terms_here):
            a = len(members & carriers[(term, soc)])
            b = len(members) - a
            c = len(carriers[(term, soc)]) - a
            d = n_attributed - a - b - c
            prr, chi_square, meets = _measures(a, b, c, d, pack.criteria)
            pairs.append(
                SignalPair(
                    product_code=code,
                    product_name=names[code],
                    preferred_term=term,
                    soc=soc,
                    a=a,
                    b=b,
                    c=c,
                    d=d,
                    prr=prr,
                    chi_square=chi_square,
                    meets_criteria=meets,
                )
            )

    soc_rows: list[SignalSocRow] = []
    for code in sorted(members_of):
        members = members_of[code]
        socs = set()
        for case_id in members:
            socs.update(soc for _t, soc in terms_of[case_id])
        for soc in sorted(socs):
            count = sum(
                1
                for case_id in members
                if any(s == soc for _t, s in terms_of[case_id])
            )
            soc_rows.append(
                SignalSocRow(
                    product_code=code,
                    product_name=names[code],
                    soc=soc,
                    cases=count,
                    product_reports=len(members),
                    share=_rate(count, len(members)),
                )
            )

    totals = {
        "population_cases": _count(len(population)),
        "attributable_reports": _count(n_attributed),
        "unattributed_reports": _count(n_unattributed),
        "uncoded_event_decisions": _count(n_uncoded_events),
        "distinct_products": _count(len(members_of)),
        "distinct_terms": _count(len(carriers)),
        "pairs_counted": _count(len(pairs)),
        "pairs_flagged": _count(sum(1 for p in pairs if p.meets_criteria)),
    }
    return totals, pairs, soc_rows, input_ids


def _pair_key(pair: SignalPair) -> str:
    return f"{pair.product_code}|{pair.preferred_term}|{pair.soc}"


def _soc_key(row: SignalSocRow) -> str:
    return f"{row.product_code}|{row.soc}"


def verify_table(store, table: SignalTable) -> list[str]:
    """Re-perform a table: recompute every figure from the
    raw store by the second path and compare, figure for
    figure, plus the record fingerprint. Returns the list
    of problems — empty only when the table is exactly what
    the record says. A figure the re-performance cannot
    reproduce is named, never summarised away."""
    problems: list[str] = []
    totals, pairs, soc_rows, input_ids = _raw_table_figures(
        store, table.window_start, table.window_end, table.as_of
    )
    if _fingerprint(input_ids) != table.record_fingerprint:
        problems.append(
            "record_fingerprint: the input artefacts the record "
            "presents do not hash to the table's fingerprint"
        )
    for figure, want in totals.items():
        got = table.totals.get(figure)
        if got != want:
            problems.append(
                f"totals.{figure}: table states {got}, "
                f"the record re-performs {want}"
            )
    for figure in table.totals:
        if figure not in totals:
            problems.append(
                f"totals.{figure}: stated on the table, "
                "not derivable from the record"
            )
    expected_pairs = {_pair_key(p): p for p in pairs}
    stated_pairs = {_pair_key(p): p for p in table.pairs}
    for key, want in expected_pairs.items():
        got = stated_pairs.get(key)
        if got is None:
            problems.append(f"pairs[{key}]: missing from the table")
            continue
        for field in (
            "product_name",
            "a",
            "b",
            "c",
            "d",
            "prr",
            "chi_square",
            "meets_criteria",
        ):
            if getattr(got, field) != getattr(want, field):
                problems.append(
                    f"pairs[{key}].{field}: table states "
                    f"{getattr(got, field)}, the record "
                    f"re-performs {getattr(want, field)}"
                )
    for key in stated_pairs:
        if key not in expected_pairs:
            problems.append(
                f"pairs[{key}]: stated on the table, "
                "not derivable from the record"
            )
    expected_socs = {_soc_key(r): r for r in soc_rows}
    stated_socs = {_soc_key(r): r for r in table.soc_rows}
    for key, want in expected_socs.items():
        got = stated_socs.get(key)
        if got is None:
            problems.append(f"soc_rows[{key}]: missing from the table")
            continue
        for field in ("product_name", "cases", "product_reports", "share"):
            if getattr(got, field) != getattr(want, field):
                problems.append(
                    f"soc_rows[{key}].{field}: table states "
                    f"{getattr(got, field)}, the record "
                    f"re-performs {getattr(want, field)}"
                )
    for key in stated_socs:
        if key not in expected_socs:
            problems.append(
                f"soc_rows[{key}]: stated on the table, "
                "not derivable from the record"
            )
    return problems


# ---------------------------------------------------------------------
# The service.
# ---------------------------------------------------------------------


class SignalService:
    """Generates and reads signal tables. Holds only the
    store — the figures fold from artefacts of record — and
    the access control is bound by the wiring, like every
    act-recording service since Round 10."""

    def __init__(self, store):
        self.store = store
        self.access = None  # bound by the wiring (Round 10 pattern)

    def generate(
        self,
        window_start: date,
        window_end: date,
        as_of: date,
        generated_by: str,
        *,
        principal: Principal | None = None,
        today: date | None = None,
    ) -> SignalTable:
        """Generate the table for a window as of a date, as a
        named human's act. When the call carries a principal,
        the access pack judges it first — the safety
        scientist's act, the principal bound to the named
        generator — and a refusal is recorded in the denial
        log before it propagates. A table whose inputs have
        not moved returns the standing artefact; a moved
        record mints a new table superseding the window's
        standing one. The stored table carries the verified
        mark only when the independent re-performance agrees
        with every figure."""
        if window_end < window_start:
            raise SignalError(
                f"window end {window_end} precedes its start {window_start}"
            )
        guard_act(
            self,
            principal,
            ACT_GENERATE_SIGNAL_COUNTS,
            surface=SURFACE_SIGNAL_COUNTS,
            target=f"{window_start.isoformat()}..{window_end.isoformat()}",
            actor=generated_by,
            on=today or as_of,
        )
        totals, pairs, soc_rows, input_ids = build_table_figures(
            self.store, window_start, window_end, as_of
        )
        fingerprint = _fingerprint(input_ids)
        table_id = _table_id(
            window_start,
            window_end,
            as_of,
            SIGNAL_PACK_VERSION,
            fingerprint,
        )
        standing = self.store.get_signal_table(table_id)
        if standing is not None:
            return standing
        same_window = [
            t
            for t in self.store.list_signal_tables()
            if t.window_start == window_start and t.window_end == window_end
        ]
        supersedes = None
        if same_window:
            latest = sorted(
                same_window,
                key=lambda t: (t.generated_on, t.as_of, t.table_id),
            )[-1]
            supersedes = latest.table_id
        table = SignalTable(
            table_id=table_id,
            pack_version=SIGNAL_PACK_VERSION,
            window_start=window_start,
            window_end=window_end,
            as_of=as_of,
            generated_by=generated_by,
            generated_on=today or as_of,
            totals=totals,
            pairs=pairs,
            soc_rows=soc_rows,
            record_fingerprint=fingerprint,
            supersedes=supersedes,
        )
        problems = verify_table(self.store, table)
        table.verified = not problems
        table.verification_problems = problems
        self.store.add_signal_table(table)
        return table

    def get(self, table_id: str) -> SignalTable | None:
        return self.store.get_signal_table(table_id)

    def tables(self) -> list[SignalTable]:
        return self.store.list_signal_tables()

    def verify(self, table: SignalTable) -> list[str]:
        return verify_table(self.store, table)
