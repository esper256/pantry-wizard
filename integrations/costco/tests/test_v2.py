"""What the importer hands a sheet that stores receipt lines and offers.

A trip refresh, the list of numbers Costco answered for, deposits, and the
weekly look back for receipts that posted late.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from costco_sync.cadence import TRIP_PRICE_SKUS, PriceCheck, select_due
from costco_sync.models import PriceLookupResult, PriceQuote
from costco_sync.normalize import parse_receipt_detail
from costco_sync.run import (
    LAST_LONG_LOOKBACK,
    LATE_RECEIPT_LOOKBACK_DAYS,
    import_windows,
    run_window,
)
from costco_sync.store import StateStore

NOW = datetime(2026, 11, 11, 19, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 11)


def _row(sku: str, **fields) -> dict:
    row = {
        "retail_key": f"costco:663:{sku}",
        "store": "Costco",
        "location": "663 Example City",
        "retailer_sku": sku,
        "receipt_name": f"ITEM {sku}",
        "last_paid_at": "2026-10-13",
        "current_price": "5.00",
        "regular_price": "5.00",
        "price_scope": "warehouse",
        "availability": "in_stock",
    }
    row.update(fields)
    return row


def _snapshot(retail: list[dict], **extra) -> dict:
    data = {
        "household_timezone": "America/Los_Angeles",
        "items": [],
        "retail_memory": retail,
        "integrations": [{"integration_key": "costco", "location": "663 Example City"}],
        "known_source_refs": [],
    }
    data.update(extra)
    return data


class _Source:
    """Prices every number at 5.00 except the ones named in ``unpriced``."""

    def __init__(self, receipts: dict[str, dict] | None = None, unpriced: set[str] | None = None) -> None:
        self.receipts = receipts or {}
        self.unpriced = unpriced or set()
        self.asked: list[str] = []
        self.windows: list[tuple[str, str]] = []

    def list_warehouse_receipts(self, start_date: str, end_date: str) -> dict:
        self.windows.append((start_date, end_date))
        return {"receipts": [{"transactionBarcode": barcode} for barcode in self.receipts]}

    def get_receipt_detail(self, barcode: str) -> dict:
        return self.receipts[barcode]

    def lookup_product_names(self, skus, warehouse):
        del skus, warehouse
        return {}

    def lookup_prices(self, skus, warehouse, known=()):
        del warehouse
        self.asked.extend(skus)
        quotes = [
            PriceQuote(item_number=sku, current_price=Decimal("5.00"), regular_price=Decimal("5.00"),
                       price_scope="warehouse", availability="in_stock")
            for sku in skus
            if sku not in self.unpriced
        ]
        return PriceLookupResult(ok=True, quotes=quotes)

    def search_products(self, query, warehouse, limit=5):
        del query, warehouse, limit
        return []


def _import(tmp_path, snapshot: dict, source: _Source, checks: dict | None = None) -> dict:
    store = StateStore(tmp_path / "state.db")
    if checks:
        store.save_price_checks(checks)
    try:
        return import_windows(
            snapshot, source, store,
            windows=[(TODAY - timedelta(days=2), TODAY)], now=NOW, owner="shopping-bot",
        )["mutations"]
    finally:
        store.close()


def test_trip_refresh_prices_only_the_priority_list_in_order(tmp_path):
    retail = [_row(str(sku)) for sku in range(100, 400)]
    not_due = {str(sku): PriceCheck(NOW + timedelta(days=5), 0) for sku in range(100, 400)}
    source = _Source()
    _import(tmp_path, _snapshot(retail, priority_item_numbers=["250", "101", "250", "999"]), source, not_due)
    # 999 is not a product this household knows. A number is asked for once.
    assert source.asked == ["250", "101"]


def test_trip_refresh_is_capped_and_skips_numbers_costco_keeps_missing(tmp_path):
    retail = [_row(str(sku)) for sku in range(100, 400)]
    checks = {"100": PriceCheck(NOW - timedelta(days=1), 3)}
    source = _Source()
    _import(tmp_path, _snapshot(retail, priority_item_numbers=[str(sku) for sku in range(100, 400)]), source, checks)
    assert len(source.asked) == TRIP_PRICE_SKUS
    assert "100" not in source.asked
    assert source.asked[0] == "101"


def test_an_empty_priority_list_prices_nothing(tmp_path):
    source = _Source()
    mutations = _import(tmp_path, _snapshot([_row("100")], priority_item_numbers=[]), source)
    assert source.asked == []
    assert mutations["checked_item_numbers"] == []


def test_no_priority_list_keeps_the_daily_schedule():
    due = select_due(["100"], rows={}, fresh=set(), checks={}, now=NOW, today=TODAY)
    assert due == ["100"]


def test_mutation_file_names_the_numbers_costco_answered_for(tmp_path):
    source = _Source(unpriced={"102"})
    mutations = _import(tmp_path, _snapshot([_row("101"), _row("102")]), source)
    # Neither price changed, so neither row is rewritten. The check is still reported.
    assert mutations["retail_memory_upserts"] == []
    assert mutations["checked_item_numbers"] == ["101"]
    assert mutations["missed_item_numbers"] == ["102"]


def test_a_failed_lookup_reports_no_numbers_as_checked(tmp_path):
    class _Down(_Source):
        def lookup_prices(self, skus, warehouse, known=()):
            del skus, warehouse
            return PriceLookupResult(ok=False, quotes=[], checked=[])

    mutations = _import(tmp_path, _snapshot([_row("101")]), _Down())
    assert mutations["checked_item_numbers"] == []
    assert mutations["missed_item_numbers"] == []


def test_deposits_and_products_marked_not_a_product_are_never_priced(tmp_path):
    retail = [_row("101"), _row("777", receipt_name="CA REDEMP VAL 24PK"), _row("888")]
    source = _Source()
    _import(tmp_path, _snapshot(retail, non_product_item_numbers=["888"]), source)
    assert source.asked == ["101"]


def _receipt(barcode: str, items: list[dict]) -> dict:
    return {
        "transactionBarcode": barcode,
        "warehouseNumber": "663",
        "warehouseName": "Example City",
        "transactionDate": "2026-11-10",
        "itemArray": items,
    }


def test_california_redemption_value_is_a_fee_line():
    receipt = parse_receipt_detail(_receipt("B1", [
        {"itemNumber": "500", "itemDescription01": "SPARKLING WATER", "amount": 9.99, "unit": 1},
        {"itemNumber": "501", "itemDescription01": "CA REDEMP VAL", "itemDescription02": "24PK", "amount": 1.20, "unit": 1},
    ]))
    assert [line.item_number for line in receipt.lines] == ["500"]


def test_new_receipts_are_listed_line_by_line(tmp_path):
    payload = _receipt("B1", [
        {"itemNumber": "500", "itemDescription01": "SPARKLING WATER", "amount": 19.98, "unit": 2},
        {"itemNumber": "500", "itemDescription01": "/500", "amount": -4.00, "unit": 0},
        {"itemNumber": "600", "itemDescription01": "FLOSS PICKS", "amount": -7.49, "unit": -1},
    ])
    mutations = _import(tmp_path, _snapshot([]), _Source({"B1": payload}))
    (receipt,) = mutations["receipts"]
    assert (receipt["barcode"], receipt["warehouse_number"], receipt["date"]) == ("B1", "663", "2026-11-10")
    bought, refunded = receipt["lines"]
    assert bought == {
        "source_ref": "costco:wh:B1:500", "item_number": "500", "description": "SPARKLING WATER",
        "name": "", "kind": "purchase", "quantity": "2", "unit_price": "7.99", "instant_savings": True,
    }
    assert (refunded["kind"], refunded["source_ref"], refunded["unit_price"]) == (
        "refund", "costco:wh:B1:600:refund", "7.49")
    # The event rows for a schema 0.3 sheet say the same thing.
    assert [event["source_ref"] for event in mutations["events"]] == [line["source_ref"] for line in receipt["lines"]]


def test_a_known_receipt_is_not_listed_again(tmp_path):
    payload = _receipt("B1", [{"itemNumber": "500", "itemDescription01": "SPARKLING WATER", "amount": 9.99, "unit": 1}])
    snapshot = _snapshot([], known_source_refs=["costco:wh:B1:500"])
    mutations = _import(tmp_path, snapshot, _Source({"B1": payload}))
    assert mutations["receipts"] == []


def test_the_daily_window_reaches_back_45_days_once_a_week(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        store.set_last_success_through(TODAY - timedelta(days=1))
        start, end, long_lookback = run_window(TODAY, store)
        assert (start, end, long_lookback) == (TODAY - timedelta(days=LATE_RECEIPT_LOOKBACK_DAYS), TODAY, True)

        store.set_meta(LAST_LONG_LOOKBACK, TODAY.isoformat())
        for days in (1, 6):
            start, _end, long_lookback = run_window(TODAY + timedelta(days=days), store)
            assert long_lookback is False
            assert start == TODAY - timedelta(days=2)
        _start, _end, long_lookback = run_window(TODAY + timedelta(days=7), store)
        assert long_lookback is True
    finally:
        store.close()


def test_a_first_run_still_looks_back_90_days(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        start, _end, long_lookback = run_window(TODAY, store)
        assert start == TODAY - timedelta(days=90)
        assert long_lookback is True
    finally:
        store.close()


def test_each_named_account_keeps_its_own_local_state(tmp_path, monkeypatch):
    from pathlib import Path

    from costco_sync.cli import state_path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert state_path(None, None) == tmp_path / ".costco-sync" / "state.db"      # where it has always been
    assert state_path(None, "second") == tmp_path / ".costco-sync" / "second" / "state.db"
    assert state_path(tmp_path / "x.db", "second") == tmp_path / "x.db"


def test_another_accounts_receipts_do_not_make_this_accounts_history_look_done():
    from costco_sync.run import history_windows

    snapshot = _snapshot([], known_source_refs=["costco:wh:B1:500"])
    assert len(history_windows(snapshot, TODAY)) == 1               # a 0.3 sheet: refs mean history exists
    assert len(history_windows(dict(snapshot, history_per_account=True), TODAY)) > 1
    snapshot["integrations"][0]["history_through"] = "2026-11-01"
    assert history_windows(dict(snapshot, history_per_account=True), TODAY) == [(date(2026, 11, 1), TODAY)]


def _printed(barcode: str, **printed) -> dict:
    return {
        "transactionBarcode": barcode, "warehouseNumber": "663", "warehouseName": "Example City",
        "transactionDate": "2026-11-10",
        "itemArray": [{"itemNumber": "100", "itemDescription01": "ITEM 100", "amount": 5.0, "unit": 1}],
        **printed,
    }


def test_a_new_receipt_carries_what_it_printed_below_the_lines(tmp_path):
    source = _Source({"B1": _printed("B1", total=5.46, taxes=0.46, instantSavings=2.0), "B2": _printed("B2")})
    receipts = _import(tmp_path, _snapshot([]), source)["receipts"]
    assert [(r["barcode"], r["total"], r["tax"], r["instant_savings"]) for r in receipts] == [
        ("B1", "5.46", "0.46", "2.00"), ("B2", "", "", "")]


def test_totals_for_older_receipts_come_from_the_saved_copy_then_a_few_downloads(tmp_path, monkeypatch):
    import costco_sync.run as run

    monkeypatch.setattr(run, "TOTALS_FETCH_PER_RUN", 1)
    store = StateStore(tmp_path / "state.db")
    store.put_receipt("OLD1", _printed("OLD1", total=10.0, taxes=1.0))
    store.put_receipt("OLD2", _printed("OLD2"))  # saved, but Costco printed no total
    store.close()

    class Source(_Source):
        fetched: list[str] = []

        def get_receipt_detail(self, barcode: str) -> dict:
            self.fetched.append(barcode)
            return _printed(barcode, total=20.0, taxes=2.0, instantSavings=3.0)

    source = Source()
    source.receipts = {}
    snapshot = _snapshot([], receipts_missing_totals=["OLD1", "OLD2", "OLD3", "OLD4"])
    mutations = _import(tmp_path, snapshot, source)
    assert mutations["receipt_totals"] == [
        {"barcode": "OLD1", "total": "10.00", "tax": "1.00", "instant_savings": ""},
        {"barcode": "OLD3", "total": "20.00", "tax": "2.00", "instant_savings": "3.00"},
    ]
    assert source.fetched == ["OLD3"]
    # OLD3 is saved now, so the next run answers it without Costco and moves on to OLD4.
    assert [t["barcode"] for t in _import(tmp_path, snapshot, source)["receipt_totals"]] == ["OLD1", "OLD3", "OLD4"]


def test_a_failed_totals_download_does_not_fail_the_import(tmp_path):
    class Source(_Source):
        def get_receipt_detail(self, barcode: str) -> dict:
            if barcode.startswith("OLD"):
                raise RuntimeError("Costco said no")
            return super().get_receipt_detail(barcode)

    source = Source({"B1": _printed("B1", total=5.46)})
    mutations = _import(tmp_path, _snapshot([], receipts_missing_totals=["OLD1", "OLD2"]), source)
    assert mutations["receipt_totals"] == []
    assert [r["barcode"] for r in mutations["receipts"]] == ["B1"]
