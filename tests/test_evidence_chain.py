"""Evidence chain: append-only order, verification, and tamper
evidence — altering a stored payload breaks verification."""

from tests.conftest import DAY


def test_chain_verifies_after_intake(fed):
    for case in fed.store.list_cases():
        assert fed.chain.verify_chain(case.case_id)


def test_chain_links_to_genesis_and_grows(fed):
    case = fed.store.get_case("CASE-RPT-2026-0001")
    item = fed.chain.append(
        case.case_id,
        "follow_up_note",
        {"note": "Reporter confirmed onset date by phone."},
        recorded_on=DAY,
        item_id="EV-CASE-RPT-2026-0001-002",
    )
    items = fed.store.list_evidence(case.case_id)
    assert items[0].prev_hash == "GENESIS"
    assert items[1].prev_hash == items[0].item_hash
    assert item.item_hash == items[1].item_hash
    assert fed.chain.verify_chain(case.case_id)


def test_tampered_payload_breaks_verification(fed):
    case_id = "CASE-RPT-2026-0001"
    item = fed.store.list_evidence(case_id)[0]
    path = fed.objects._path(item.object_key)
    original = path.read_bytes()
    path.write_bytes(original.replace(b"Dizziness", b"Dizzinesx"))
    assert not fed.chain.verify_chain(case_id)


def test_empty_chain_verifies(fed):
    assert fed.chain.verify_chain("CASE-DOES-NOT-EXIST")
