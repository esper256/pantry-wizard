"""Load the anonymized Costco API fixtures through the existing parsers."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from costco_sync.normalize import barcodes_from_list, membership_number, parse_receipt_detail

API = Path(__file__).parent / "fixtures" / "api"
FAKE_MEMBER = "111111111111"
FAKE_CARD = "XXXX"
DETAIL_FILES = (
    "receipt_detail_instant_savings_spaces.json",
    "receipt_detail_refund.json",
    "receipt_detail_multi_tender.json",
)


def _load(name: str) -> dict:
    return json.loads((API / name).read_text())


def _receipt(payload: dict) -> dict:
    return payload["data"]["receiptsWithCounts"]["receipts"][0]


def test_detail_fixtures_parse_as_receipts():
    for name in DETAIL_FILES:
        payload = _load(name)
        receipt = parse_receipt_detail(payload)
        raw = _receipt(payload)
        assert receipt.barcode == raw["transactionBarcode"]
        assert len(receipt.barcode) == 23
        assert receipt.warehouse_number == str(raw["warehouseNumber"])
        assert receipt.occurred_on == raw["transactionDate"]
        assert receipt.lines
        assert receipt.total is not None
        assert membership_number(payload) == FAKE_MEMBER
        for tender in raw.get("tenderArray") or []:
            account = tender.get("displayAccountNumber")
            if account not in (None, ""):
                assert account == FAKE_CARD
                assert len(account) == 4


def test_instant_savings_fixture_keeps_spaced_slash_children():
    """Issue #50: live discount text is `/` plus optional spaces plus the parent SKU."""
    payload = _load("receipt_detail_instant_savings_spaces.json")
    descriptions = [
        str(item.get("itemDescription01") or "")
        for item in _receipt(payload)["itemArray"]
    ]
    assert any(re.fullmatch(r"/\s{2,}\d+", text) for text in descriptions)
    assert any(re.fullmatch(r"/\s\d+", text) for text in descriptions)
    assert any(re.fullmatch(r"/\d+", text) for text in descriptions)
    receipt = parse_receipt_detail(payload)
    assert receipt.instant_savings == Decimal("7.5")
    assert any(line.kind == "purchase" for line in receipt.lines)


def test_refund_fixture_parses_refund_lines():
    payload = _load("receipt_detail_refund.json")
    raw = _receipt(payload)
    assert raw["transactionType"] == "Refund"
    assert raw["total"] < 0
    receipt = parse_receipt_detail(payload)
    assert any(line.kind == "refund" for line in receipt.lines)
    assert any(line.item_number == "1951495" for line in receipt.lines)


def test_multi_tender_fixture_keeps_both_tenders():
    payload = _load("receipt_detail_multi_tender.json")
    raw = _receipt(payload)
    tenders = raw["tenderArray"]
    assert len(tenders) == 2
    names = {row.get("tenderTypeName") or row.get("tenderDescription") for row in tenders}
    assert "CITI Rebate" in names
    assert "Cash" in names
    descriptions = [
        str(item.get("itemDescription01") or "")
        for item in raw["itemArray"]
    ]
    assert any(re.fullmatch(r"/\s{4}\d+", text) for text in descriptions)
    receipt = parse_receipt_detail(payload)
    assert receipt.instant_savings == Decimal("38.1")
    assert any(line.kind == "purchase" for line in receipt.lines)


def test_list_fixture_barcodes_match_detail_fixtures():
    listing = _load("receipts_list.json")
    counts = listing["data"]["receiptsWithCounts"]
    barcodes = barcodes_from_list(listing)
    expected = [
        _receipt(_load(name))["transactionBarcode"] for name in DETAIL_FILES
    ]
    assert barcodes == expected
    assert counts["inWarehouse"] == len(counts["receipts"]) == 3
    assert counts["gasStation"] == 0
    assert counts["carWash"] == 0
    assert counts["gasAndCarWash"] == 0
    for raw in counts["receipts"]:
        assert "membershipNumber" not in raw
        assert raw["documentType"]
        for tender in raw.get("tenderArray") or []:
            assert "displayAccountNumber" not in tender
            assert "approvalNumber" not in tender
