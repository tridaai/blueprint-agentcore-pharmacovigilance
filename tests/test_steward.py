"""Round 14 gates: dictionary stewardship.

The aggregate layer over the coding book, built under the
same disciplines as the record it reads: the worklist folds
standing uncoded decisions in code under a versioned pack
(every row recomputable by hand from the book), ranked by
the pack's stated order, as an insert-only artefact that
supersedes by link when the record moves; an independent
re-performance that must agree before the verified mark is
set; aggregate-only payloads — reported wordings, counts,
dates and coded product labels, no case ids — graded by
the Round 11 key scan. Candidates are proposed against a
worklist row and decided under dual control: the proposer
may not decide, decided work does not move, a confirmation
enacts the mapping as a new dictionary version through the
coding layer's own addition, and no case already coded is
touched — history stands. An ambiguous wording takes no
candidate and is pointed at the per-case coder path.
Stewardship acts are judged by the Round 10 access pack;
the auditor reads everything and performs none of it.
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
from pv_cases.steward import (
    CANDIDATE_CONFIRMED,
    CANDIDATE_PROPOSED,
    CANDIDATE_REJECTED,
    STATE_CODES_NOW,
    STATE_STILL_UNCODED,
    StewardError,
    load_steward_pack,
)

AS_OF = date(2026, 11, 20)
DECIDE_DAY = date(2026, 11, 21)

SCIENTIST = Principal(id="Dr S. Okafor", role="safety_scientist")
ADMIN = Principal(id="Mr K. Dev", role="case_admin")
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
    """A book whose uncoded page is known exactly: three
    reports of one tingling wording over two products, one
    ambiguous wording (a recorded synonym of two terms),
    one report naming an unknown product, one rash wording
    on the cream, and one fully coded control. Patients and
    onsets differ per report — same-patient repeats of one
    wording would be duplicate suspicion, a different
    discipline."""
    rows = [
        ("R14-A1", date(2026, 10, 5), "Tingling in the fingers",
         "NBT-101", "Cardivane", "J.M.", 54, "F", date(2026, 10, 4)),
        ("R14-A2", date(2026, 10, 9), "Tingling in the fingers",
         "NBT-101", "Cardivane", "K.T.", 67, "M", date(2026, 10, 5)),
        ("R14-A3", date(2026, 10, 14), "Tingling in the fingers",
         "NBT-204", "Somnirel", "L.A.", 49, "F", date(2026, 10, 8)),
        ("R14-B1", date(2026, 10, 7), "Feeling drained",
         "NBT-101", "Cardivane", "B.F.", 45, "F", date(2026, 10, 3)),
        ("R14-C1", date(2026, 10, 11), "Severe headache",
         None, "Velocard", "C.G.", 52, "M", date(2026, 10, 6)),
        ("R14-E1", date(2026, 10, 13), "Rash over hands and wrists",
         "NBT-317", "Dermaclear", "R.S.", 38, "F", date(2026, 10, 12)),
        ("R14-D1", date(2026, 10, 12), "Severe headache",
         "NBT-101", "Cardivane", "D.H.", 47, "F", date(2026, 10, 10)),
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


def _generate(services, **kw):
    return services.steward.generate(
        AS_OF, "Dr S. Okafor", today=AS_OF, **kw
    )


def _row(worklist, wording):
    for row in worklist.rows:
        if row.reported_wording == wording:
            return row
    raise AssertionError(f"no row for {wording!r}")


# ---------------------------------------------------------------------
# The worklist: figures, ranking, re-performance.
# ---------------------------------------------------------------------


def test_worklist_figures_and_ranking(services):
    _book(services)
    worklist = _generate(services)
    assert worklist.verified
    assert worklist.verification_problems == []
    assert services.steward.verify(worklist) == []
    assert worklist.base_dictionary_version == "2026.10.1"
    assert {
        k: v["value"] for k, v in worklist.totals.items()
    } == {
        "distinct_wordings": 4,
        "uncoded_decisions": 6,
        "uncoded_cases": 6,
        "open_coding_tasks": 6,
    }
    assert [r.reported_wording for r in worklist.rows] == [
        "Tingling in the fingers",
        "Feeling drained",
        "Velocard",
        "Rash over hands and wrists",
    ]
    tingling = _row(worklist, "Tingling in the fingers")
    assert tingling.subject_kind == "event"
    assert tingling.reason == "no_dictionary_entry"
    assert tingling.reports == 3
    assert tingling.decisions == 3
    assert tingling.first_seen == date(2026, 10, 5)
    assert tingling.last_seen == date(2026, 10, 14)
    assert [
        (p.product_code, p.product_name, p.reports)
        for p in tingling.products
    ] == [
        ("NBT-101", "Cardivane", 2),
        ("NBT-204", "Somnirel", 1),
    ]
    assert tingling.unattributed_reports == 0
    assert tingling.dictionary_state == STATE_STILL_UNCODED
    assert tingling.current_label is None

    drained = _row(worklist, "Feeling drained")
    assert drained.reason == "ambiguous_match"
    assert drained.candidates == ["Asthenia", "Fatigue"]
    assert drained.reports == 1

    velocard = _row(worklist, "Velocard")
    assert velocard.subject_kind == "product"
    assert velocard.reason == "no_dictionary_entry"
    assert velocard.products == []
    assert velocard.reports == 1


def test_worklist_deterministic_and_supersedes(services):
    _book(services)
    first = _generate(services)
    again = _generate(services)
    assert again.worklist_id == first.worklist_id
    assert len(services.steward.worklists()) == 1
    before = services.store.get_steward_worklist(
        first.worklist_id
    ).model_dump_json()
    _ingest(
        services,
        _report(
            "R14-A4", date(2026, 10, 16), "Tingling in the fingers",
            initials="Z.Z.", age=56, sex="F",
            onset=date(2026, 10, 15),
        ),
    )
    moved = _generate(services)
    assert moved.worklist_id != first.worklist_id
    assert moved.supersedes == first.worklist_id
    assert _row(moved, "Tingling in the fingers").reports == 4
    assert moved.verified
    standing = services.store.get_steward_worklist(first.worklist_id)
    assert standing.model_dump_json() == before


def test_tampered_worklist_is_named(services):
    _book(services)
    worklist = _generate(services)
    rows = copy.deepcopy(worklist.rows)
    rows[0].reports += 1
    problems = services.steward.verify(
        worklist.model_copy(update={"rows": rows})
    )
    assert any(
        "rows[event|tingling in the fingers].reports" in p
        for p in problems
    )
    dropped = worklist.model_copy(
        update={"rows": worklist.rows[1:]}
    )
    problems = services.steward.verify(dropped)
    assert any(
        "rows[event|tingling in the fingers]: missing" in p
        for p in problems
    )


def test_phi_scan_clean(services):
    _book(services)
    worklist = _generate(services)
    assert phi_problems(worklist.model_dump(mode="json")) == []
    candidate = services.steward.propose(
        worklist.worklist_id,
        "Tingling in the fingers",
        "event",
        "Dr S. Okafor",
        preferred_term="Paraesthesia",
        soc="Nervous system disorders",
        today=DECIDE_DAY,
    )
    assert phi_problems(candidate.model_dump(mode="json")) == []
    record = services.steward.decide(
        candidate.candidate_id,
        "confirm",
        "Ms R. Chen",
        today=DECIDE_DAY,
    )
    assert phi_problems(record.model_dump(mode="json")) == []


def test_pack_loader_fails_closed():
    assert load_steward_pack().version == "2026.10.1"
    with pytest.raises(ValueError):
        load_steward_pack("1900.01.1")


# ---------------------------------------------------------------------
# The candidate route: proposal, dual control, enactment.
# ---------------------------------------------------------------------


def _propose_tingling(services, worklist):
    return services.steward.propose(
        worklist.worklist_id,
        "Tingling in the fingers",
        "event",
        "Dr S. Okafor",
        preferred_term="Paraesthesia",
        soc="Nervous system disorders",
        today=DECIDE_DAY,
    )


def test_propose_confirm_mints_new_version(services):
    _book(services)
    worklist = _generate(services)
    candidate = _propose_tingling(services, worklist)
    assert candidate.dictionary_base_version == "2026.10.1"
    assert (
        services.steward.candidate_state(candidate.candidate_id)
        == CANDIDATE_PROPOSED
    )
    # A proposal enacts nothing.
    assert [
        v.version for v in services.coding.list_versions()
    ] == ["2026.10.1"]
    record = services.steward.decide(
        candidate.candidate_id,
        "confirm",
        "Ms R. Chen",
        "Recurring wording, clean mapping.",
        today=DECIDE_DAY,
    )
    assert record.minted_dictionary_version == "2026.10.2"
    assert (
        services.steward.candidate_state(candidate.candidate_id)
        == CANDIDATE_CONFIRMED
    )
    versions = services.coding.list_versions()
    assert [v.version for v in versions] == ["2026.10.1", "2026.10.2"]
    minted = versions[-1]
    assert minted.created_by == "Ms R. Chen"
    assert candidate.candidate_id in minted.note
    entry = next(
        e
        for e in minted.event_entries
        if e.verbatim == "Tingling in the fingers"
    )
    assert entry.preferred_term == "Paraesthesia"
    assert entry.soc == "Nervous system disorders"


def test_dual_control_and_decided_work(services):
    _book(services)
    worklist = _generate(services)
    candidate = _propose_tingling(services, worklist)
    with pytest.raises(StewardError):
        services.steward.decide(
            candidate.candidate_id,
            "confirm",
            "Dr S. Okafor",
            today=DECIDE_DAY,
        )
    # A name variant of the proposer is the same person
    # under the repo's folding — dual control still refuses.
    with pytest.raises(StewardError):
        services.steward.decide(
            candidate.candidate_id,
            "reject",
            "Dr S Okafor",
            today=DECIDE_DAY,
        )
    services.steward.decide(
        candidate.candidate_id,
        "confirm",
        "Ms R. Chen",
        today=DECIDE_DAY,
    )
    with pytest.raises(StewardError):
        services.steward.decide(
            candidate.candidate_id,
            "reject",
            "Mr K. Dev",
            today=DECIDE_DAY,
        )


def test_rejection_mints_nothing(services):
    _book(services)
    worklist = _generate(services)
    candidate = services.steward.propose(
        worklist.worklist_id,
        "Rash over hands and wrists",
        "event",
        "Dr S. Okafor",
        preferred_term="Rash",
        soc="Skin and subcutaneous tissue disorders",
        today=DECIDE_DAY,
    )
    record = services.steward.decide(
        candidate.candidate_id,
        "reject",
        "Ms R. Chen",
        "Too close to two existing wordings; settle per case.",
        today=DECIDE_DAY,
    )
    assert record.minted_dictionary_version is None
    assert (
        services.steward.candidate_state(candidate.candidate_id)
        == CANDIDATE_REJECTED
    )
    assert [
        v.version for v in services.coding.list_versions()
    ] == ["2026.10.1"]


def test_history_stands_and_later_cases_code(services):
    _book(services)
    worklist = _generate(services)
    case_id = "CASE-R14-A1"
    before = [
        d.model_dump_json()
        for d in services.store.list_coding_decisions(case_id)
    ]
    candidate = _propose_tingling(services, worklist)
    services.steward.decide(
        candidate.candidate_id,
        "confirm",
        "Ms R. Chen",
        today=DECIDE_DAY,
    )
    # The confirmation wrote nothing on the earlier case:
    # its decisions are byte-identical, and its standing
    # event decision is still uncoded under the old version.
    after = [
        d.model_dump_json()
        for d in services.store.list_coding_decisions(case_id)
    ]
    assert after == before
    standing = services.coding.current_decisions(case_id)
    event = next(d for d in standing if d.subject_kind == "event")
    assert event.status.value == "uncoded"
    assert event.dictionary_version == "2026.10.1"
    # A case coded after the decision date codes under the
    # minted version.
    _ingest(
        services,
        _report(
            "R14-A5", date(2026, 11, 22), "Tingling in the fingers",
            initials="Y.Y.", age=61, sex="M",
            onset=date(2026, 11, 20),
        ),
    )
    standing = services.coding.current_decisions("CASE-R14-A5")
    event = next(d for d in standing if d.subject_kind == "event")
    assert event.status.value == "coded"
    assert event.preferred_term == "Paraesthesia"
    assert event.dictionary_version == "2026.10.2"


def test_codes_now_state_after_dictionary_learns(services):
    _book(services)
    worklist = _generate(services)
    candidate = _propose_tingling(services, worklist)
    services.steward.decide(
        candidate.candidate_id,
        "confirm",
        "Ms R. Chen",
        today=DECIDE_DAY,
    )
    struck = _generate(services)
    assert struck.base_dictionary_version == "2026.10.2"
    row = _row(struck, "Tingling in the fingers")
    # The old cases still stand uncoded — the row remains,
    # now stating that the dictionary has learned the
    # wording since they were coded.
    assert row.reports == 3
    assert row.dictionary_state == STATE_CODES_NOW
    assert row.current_label == "Paraesthesia"
    assert struck.verified


def test_proposal_refusals(services):
    _book(services)
    worklist = _generate(services)
    with pytest.raises(StewardError):
        services.steward.propose(
            worklist.worklist_id,
            "Not on the page",
            "event",
            "Dr S. Okafor",
            preferred_term="Rash",
            soc="Skin and subcutaneous tissue disorders",
            today=DECIDE_DAY,
        )
    with pytest.raises(StewardError):
        services.steward.propose(
            worklist.worklist_id,
            "Tingling in the fingers",
            "event",
            "Dr S. Okafor",
            preferred_term="Paraesthesia",
            today=DECIDE_DAY,
        )
    with pytest.raises(StewardError):
        services.steward.propose(
            "SWL-nope",
            "Tingling in the fingers",
            "event",
            "Dr S. Okafor",
            preferred_term="Paraesthesia",
            soc="Nervous system disorders",
            today=DECIDE_DAY,
        )


def test_ambiguous_wording_takes_no_candidate(services):
    _book(services)
    worklist = _generate(services)
    with pytest.raises(StewardError) as excinfo:
        services.steward.propose(
            worklist.worklist_id,
            "Feeling drained",
            "event",
            "Dr S. Okafor",
            preferred_term="Exhaustion",
            soc="General disorders and administration site conditions",
            today=DECIDE_DAY,
        )
    assert "manual resolution" in str(excinfo.value)


def test_product_candidate_maps_new_name(services):
    _book(services)
    worklist = _generate(services)
    candidate = services.steward.propose(
        worklist.worklist_id,
        "Velocard",
        "product",
        "Mr K. Dev",
        product_code="NBT-101",
        today=DECIDE_DAY,
    )
    with pytest.raises(StewardError):
        services.steward.propose(
            worklist.worklist_id,
            "Velocard",
            "product",
            "Mr K. Dev",
            product_code="NBT-999",
            today=DECIDE_DAY,
        )
    record = services.steward.decide(
        candidate.candidate_id,
        "confirm",
        "Dr S. Okafor",
        today=DECIDE_DAY,
    )
    assert record.minted_dictionary_version == "2026.10.2"
    _ingest(
        services,
        _report(
            "R14-C2", date(2026, 11, 22), "Severe headache",
            code=None, name="Velocard",
            initials="W.W.", age=59, sex="F",
            onset=date(2026, 11, 19),
        ),
    )
    standing = services.coding.current_decisions("CASE-R14-C2")
    product = next(
        d for d in standing if d.subject_kind == "product"
    )
    assert product.status.value == "coded"
    assert product.product_code == "NBT-101"
    assert product.dictionary_version == "2026.10.2"


# ---------------------------------------------------------------------
# Access: the pack judges the stewardship acts.
# ---------------------------------------------------------------------


def test_access_pack_judges_steward_acts(services):
    _book(services)
    denials_before = len(services.store.list_access_denials())
    worklist = services.steward.generate(
        AS_OF, "Mr K. Dev", principal=ADMIN, today=AS_OF
    )
    assert worklist.verified
    with pytest.raises(AccessDeniedError) as excinfo:
        _generate(services, principal=AUDITOR_P)
    assert excinfo.value.reason == "read_only_role"
    with pytest.raises(AccessDeniedError) as excinfo:
        _generate(services, principal=REVIEWER_P)
    assert excinfo.value.reason == "act_not_permitted"
    with pytest.raises(AccessDeniedError) as excinfo:
        services.steward.generate(
            AS_OF, "Someone Else", principal=SCIENTIST, today=AS_OF
        )
    assert excinfo.value.reason == "actor_mismatch"
    with pytest.raises(AccessDeniedError):
        services.steward.propose(
            worklist.worklist_id,
            "Tingling in the fingers",
            "event",
            "Pat Intake",
            preferred_term="Paraesthesia",
            soc="Nervous system disorders",
            principal=INTAKE_P,
            today=DECIDE_DAY,
        )
    candidate = services.steward.propose(
        worklist.worklist_id,
        "Tingling in the fingers",
        "event",
        "Dr S. Okafor",
        preferred_term="Paraesthesia",
        soc="Nervous system disorders",
        principal=SCIENTIST,
        today=DECIDE_DAY,
    )
    with pytest.raises(AccessDeniedError):
        services.steward.decide(
            candidate.candidate_id,
            "confirm",
            "Dr B. Lindqvist",
            principal=REVIEWER_P,
            today=DECIDE_DAY,
        )
    assert (
        len(services.store.list_access_denials())
        == denials_before + 5
    )


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


def test_api_steward_flow(call, services):
    _book(services)
    pack = call("GET", "/steward/pack", headers=_headers(AUDITOR_P))
    assert pack.status_code == 200
    assert pack.json()["version"] == "2026.10.1"
    refused = call("GET", "/steward/pack", headers=_headers(INTAKE_P))
    assert refused.status_code == 403

    made = call(
        "POST", "/steward/worklists",
        json={"as_of": "2026-11-20", "generated_by": "Dr S. Okafor"},
        headers=_headers(SCIENTIST),
    )
    assert made.status_code == 200
    worklist_id = made.json()["worklist_id"]
    assert made.json()["verified"] is True
    assert made.json()["totals"]["distinct_wordings"]["value"] == 4

    listed = call(
        "GET", "/steward/worklists", headers=_headers(AUDITOR_P)
    )
    assert [w["worklist_id"] for w in listed.json()] == [worklist_id]
    hidden = call(
        "GET", "/steward/worklists", headers=_headers(INTAKE_P)
    )
    assert hidden.json() == []

    check = call(
        "GET", f"/steward/worklists/{worklist_id}/verify",
        headers=_headers(AUDITOR_P),
    )
    assert check.json() == {
        "worklist_id": worklist_id, "verified": True, "problems": [],
    }
    missing = call("GET", "/steward/worklists/SWL-nope")
    assert missing.status_code == 404

    proposed = call(
        "POST", "/steward/candidates",
        json={
            "worklist_id": worklist_id,
            "reported_wording": "Tingling in the fingers",
            "subject_kind": "event",
            "proposed_by": "Dr S. Okafor",
            "preferred_term": "Paraesthesia",
            "soc": "Nervous system disorders",
        },
        headers=_headers(SCIENTIST),
    )
    assert proposed.status_code == 200
    candidate_id = proposed.json()["candidate_id"]
    assert proposed.json()["state"] == "proposed"

    self_decide = call(
        "POST", f"/steward/candidates/{candidate_id}/decisions",
        json={"decision": "confirm", "decided_by": "Dr S. Okafor"},
        headers=_headers(SCIENTIST),
    )
    assert self_decide.status_code == 422

    decided = call(
        "POST", f"/steward/candidates/{candidate_id}/decisions",
        json={
            "decision": "confirm",
            "decided_by": "Mr K. Dev",
            "rationale": "Clean mapping.",
        },
        headers=_headers(ADMIN),
    )
    assert decided.status_code == 200
    assert decided.json()["minted_dictionary_version"] == "2026.10.2"

    view = call(
        "GET", f"/steward/candidates/{candidate_id}",
        headers=_headers(AUDITOR_P),
    )
    assert view.json()["state"] == "confirmed"
    assert view.json()["decision"]["decided_by"] == "Mr K. Dev"
