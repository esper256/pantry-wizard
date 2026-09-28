"""Price the household list first, then catch up, then recheck on a schedule."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from costco_sync.cadence import (
    CATCHUP_PRICE_SKUS,
    STEADY_PRICE_SKUS,
    PriceCheck,
    advance_checks,
    latest_paid_on,
    select_due,
)
from costco_sync.live import CostcoSource
from costco_sync.models import NormalizedLine, PriceLookupResult, PriceQuote, Receipt, RetailRow
from costco_sync.run import import_windows
from costco_sync.store import StateStore

NOW = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 28)


def test_schedule_waits_a_day_on_sale_a_week_when_quiet_and_doubles_a_miss():
    sale = PriceQuote(
        item_number="sale",
        current_price=Decimal("10.49"),
        regular_price=Decimal("13.99"),
        reduction_ends_at="2026-10-25",
        explicit_instant_savings=True,
        price_scope="warehouse",
    )
    quiet = PriceQuote(
        item_number="quiet",
        current_price=Decimal("11.79"),
        regular_price=Decimal("11.79"),
        price_scope="warehouse",
    )
    updated = advance_checks(
        ["sale", "quiet", "old", "nodate", "gone"],
        [sale, quiet, _quiet_quote("old"), _quiet_quote("nodate")],
        {},
        NOW,
        TODAY,
        paid_on={"sale": "2020-01-01", "quiet": "2026-09-01", "old": "2024-01-01"},
    )
    assert updated["sale"].miss_count == 0
    assert updated["sale"].next_check_at == NOW + timedelta(days=1)
    assert updated["quiet"].next_check_at == NOW + timedelta(days=7)
    assert updated["old"].next_check_at == NOW + timedelta(days=30)
    assert updated["nodate"].next_check_at == NOW + timedelta(days=30)
    boundary = advance_checks(
        ["edge", "older"],
        [_quiet_quote("edge"), _quiet_quote("older")],
        {},
        NOW,
        TODAY,
        paid_on={"edge": "2025-09-28", "older": "2025-09-27"},
    )
    assert boundary["edge"].next_check_at == NOW + timedelta(days=7)
    assert boundary["older"].next_check_at == NOW + timedelta(days=30)
    assert updated["gone"].miss_count == 1
    assert updated["gone"].next_check_at == NOW + timedelta(days=2)

    again = advance_checks(["gone"], [], updated, NOW, TODAY)
    assert again["gone"].miss_count == 2
    assert again["gone"].next_check_at == NOW + timedelta(days=4)

    capped = advance_checks(
        ["gone"],
        [],
        {"gone": PriceCheck(NOW, 7)},
        NOW,
        TODAY,
    )
    assert capped["gone"].miss_count == 8
    assert capped["gone"].next_check_at == NOW + timedelta(days=90)


def _quiet_quote(sku: str) -> PriceQuote:
    return PriceQuote(
        item_number=sku,
        current_price=Decimal("11.79"),
        regular_price=Decimal("11.79"),
        price_scope="warehouse",
    )


def _memory(sku: str, **fields) -> RetailRow:
    row = RetailRow(retail_key=f"costco:121:{sku}", retailer_sku=sku)
    for name, value in fields.items():
        setattr(row, name, value)
    return row


def test_buy_list_and_promotions_come_before_unchecked_history():
    rows = {
        "1": _memory("1", last_paid_at="2026-09-20"),
        "2": _memory("2", item_id="paper", last_paid_at="2026-09-20"),
        "993449": _memory("993449", item_id="milk", last_paid_at="2024-01-01"),
        "promo": _memory("promo", reduction_kind="instant_savings", reduction_ends_at="2026-10-25"),
        "base": _memory("base", reduction_kind="below_baseline", last_paid_at="2026-09-27"),
    }
    due = select_due(
        ["1", "base", "promo", "993449", "2"],
        rows=rows,
        fresh=set(),
        checks={"base": PriceCheck(NOW - timedelta(days=1), 0)},
        now=NOW,
        today=TODAY,
        buy_ids={"milk"},
    )
    assert due == ["993449", "2", "promo", "1"]
    assert "base" not in due


def test_unchecked_history_is_newest_purchase_first_and_stops_at_the_catchup_cap():
    rows = {str(index): _memory(str(index), last_paid_at="2020-01-01") for index in range(1, 301)}
    rows["300"] = _memory("300", last_paid_at="2026-09-01")
    rows["2"] = _memory("2", last_paid_at="2026-08-01")
    rows["0"] = _memory("0", reduction_kind="below_baseline")
    due = select_due(
        ["0", *[str(index) for index in range(1, 301)]],
        rows=rows,
        fresh=set(),
        checks={"0": PriceCheck(NOW - timedelta(days=1), 0)},
        now=NOW,
        today=TODAY,
    )
    assert due[0] == "300"
    assert due[1] == "2"
    assert "0" not in due
    assert len(due) == CATCHUP_PRICE_SKUS
    assert due[2] == "1"
    assert "239" in due
    assert "240" not in due


def test_quiet_rechecks_wait_for_the_oldest_due_and_stop_at_eighty():
    skus = [str(index) for index in range(1, 82)]
    rows = {sku: _memory(sku, last_paid_at="2026-09-28" if sku == "81" else "2020-01-01") for sku in skus}
    rows["50"] = _memory("50", reduction_kind="instant_savings,below_baseline")
    checks = {
        sku: PriceCheck(NOW - timedelta(days=100 - index), 0)
        for index, sku in enumerate(skus, start=1)
    }
    due = select_due(
        list(reversed(skus)),
        rows=rows,
        fresh=set(),
        checks=checks,
        now=NOW,
        today=TODAY,
    )
    assert due[0] == "50"
    assert due[1] == "1"
    assert "81" not in due
    assert len(due) == STEADY_PRICE_SKUS


def test_a_new_receipt_date_does_not_reorder_due_rechecks():
    rows = {
        "old": _memory("old", last_paid_at="2020-01-01"),
        "freshpay": _memory("freshpay", last_paid_at="2026-09-28"),
    }
    due = select_due(
        ["freshpay", "old"],
        rows=rows,
        fresh=set(),
        checks={
            "old": PriceCheck(NOW - timedelta(days=2), 0),
            "freshpay": PriceCheck(NOW - timedelta(days=1), 0),
        },
        now=NOW,
        today=TODAY,
    )
    assert due == ["old", "freshpay"]


def test_latest_paid_on_uses_a_receipt_imported_in_this_run():
    rows = {"42": _memory("42", last_paid_at="2020-01-01")}
    receipt = Receipt(
        barcode="R1",
        warehouse_number="121",
        warehouse_name="Foster City",
        occurred_on="2026-09-28",
        lines=[
            NormalizedLine(
                kind="purchase",
                item_number="42",
                description="Milk",
                quantity=Decimal("1"),
                unit_price_paid=Decimal("9.99"),
                pre_savings_unit_price=Decimal("9.99"),
                had_instant_savings=False,
                source_ref="new",
            )
        ],
    )
    assert latest_paid_on(rows, [receipt], set())["42"] == "2026-09-28"
    assert latest_paid_on(rows, [receipt], {"new"})["42"] == "2020-01-01"


def test_a_failed_batch_does_not_advance_those_item_numbers(tmp_path):
    class _Partial(_Source):
        def lookup_prices(self, skus, warehouse):
            del warehouse
            self.asked.extend(skus)
            return PriceLookupResult(
                ok=True,
                quotes=[
                    PriceQuote(
                        item_number="1",
                        current_price=Decimal("1.00"),
                        regular_price=Decimal("1.00"),
                        price_scope="warehouse",
                    )
                ],
                checked=["1"],
            )

    snapshot = {
        "household_timezone": "America/Los_Angeles",
        "preferred_costco_warehouse": "121 Foster City",
        "items": [],
        "retail_memory": [_row("1"), _row("2")],
        "known_source_refs": [],
    }
    store = StateStore(tmp_path / "state.db")
    source = _Partial()
    try:
        import_windows(
            snapshot,
            source,
            store,
            windows=[(date(2026, 9, 1), date(2026, 9, 28))],
            now=NOW,
            owner="shopping-bot",
        )
        checks = store.price_checks()
    finally:
        store.close()
    assert source.asked == ["1", "2"]
    assert "1" in checks
    assert "2" not in checks


def test_a_new_receipt_line_is_due_even_when_its_check_is_in_the_future():
    due = select_due(
        ["42"],
        rows={},
        fresh={"42"},
        checks={"42": PriceCheck(NOW + timedelta(days=6), 0)},
        now=NOW,
        today=TODAY,
    )
    assert due == ["42"]


def test_price_batches_pause_between_requests(monkeypatch):
    source = CostcoSource.__new__(CostcoSource)
    pauses: list[float] = []

    class Response:
        status_code = 200

        def json(self):
            return {"productData": []}

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    source._price_summaries(
        [str(index) for index in range(60)],
        "663",
        pause_seconds=20,
        sleep=pauses.append,
    )
    assert pauses == [20, 20]


class _Source:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def list_warehouse_receipts(self, start_date: str, end_date: str) -> dict:
        del start_date, end_date
        return {"receipts": []}

    def get_receipt_detail(self, barcode: str) -> dict:
        raise AssertionError(barcode)

    def lookup_product_names(self, skus, warehouse):
        del skus, warehouse
        return {}

    def lookup_prices(self, skus, warehouse):
        del warehouse
        self.asked.extend(skus)
        quotes = []
        for sku in skus:
            if sku == "1000":
                continue
            if sku == "sale":
                quotes.append(
                    PriceQuote(
                        item_number=sku,
                        current_price=Decimal("8.49"),
                        regular_price=Decimal("9.99"),
                        reduction_ends_at="2026-10-25",
                        explicit_instant_savings=True,
                        price_scope="warehouse",
                    )
                )
            else:
                quotes.append(
                    PriceQuote(
                        item_number=sku,
                        current_price=Decimal("1.00"),
                        regular_price=Decimal("1.00"),
                        price_scope="warehouse",
                    )
                )
        return PriceLookupResult(ok=True, quotes=quotes)

    def search_products(self, query, warehouse, limit=5):
        del query, warehouse, limit
        return []


def _row(sku: str, **fields) -> dict:
    row = {
        "retail_key": f"costco:121:{sku}",
        "store": "Costco",
        "location": "121 Foster City",
        "retailer_sku": sku,
        "last_paid_at": "2026-09-01",
        "current_price": "1.00",
        "regular_price": "1.00",
        "reduction_kind": "",
        "reduction_ends_at": "",
        "price_scope": "warehouse",
        "observed_at": "2026-09-01T10:00:00-07:00",
    }
    row.update(fields)
    return row


def test_one_run_prices_one_batch_and_leaves_a_miss_on_the_sheet(tmp_path):
    quiet = [f"{index:04d}" for index in range(1000, 1060)]
    retail = [_row(sku) for sku in quiet]
    retail.append(
        _row(
            "sale",
            current_price="9.99",
            regular_price="9.99",
            reduction_kind="instant_savings",
            reduction_ends_at="2026-10-25",
        )
    )
    retail.append(_row("993449", item_id="milk", last_paid_at="2024-01-01"))
    retail.append(_row("2", last_paid_at="2020-01-01"))
    retail.append(_row("later"))
    retail.append(_row("base", reduction_kind="below_baseline"))
    snapshot = {
        "household_timezone": "America/Los_Angeles",
        "preferred_costco_warehouse": "121 Foster City",
        "items": [{"item_id": "milk", "purchase_intent": "buy"}],
        "retail_memory": retail,
        "known_source_refs": [],
    }
    store = StateStore(tmp_path / "state.db")
    later = NOW + timedelta(days=30)
    store.save_price_checks(
        {
            "later": PriceCheck(later, 3),
            "base": PriceCheck(NOW - timedelta(days=1), 0),
        }
    )
    source = _Source()
    try:
        result = import_windows(
            snapshot,
            source,
            store,
            windows=[(date(2026, 9, 1), date(2026, 9, 28))],
            now=NOW,
            owner="shopping-bot",
        )
    finally:
        checks = store.price_checks()
        store.close()

    assert source.asked[0] == "993449"
    assert source.asked[1] == "sale"
    assert "later" not in source.asked
    assert "base" not in source.asked
    assert "1059" in source.asked
    assert "2" in source.asked
    written = {row["retailer_sku"] for row in result["mutations"]["retail_memory_upserts"]}
    assert written == {"sale"}
    assert checks["sale"].miss_count == 0
    assert checks["sale"].next_check_at == NOW + timedelta(days=1)
    assert checks["1001"].next_check_at == NOW + timedelta(days=7)
    assert checks["1001"].miss_count == 0
    assert checks["2"].next_check_at == NOW + timedelta(days=30)
    assert checks["1000"].miss_count == 1
    assert checks["1000"].next_check_at == NOW + timedelta(days=2)
    assert checks["later"].miss_count == 3
    assert checks["later"].next_check_at == later
    assert checks["base"].next_check_at == NOW - timedelta(days=1)
