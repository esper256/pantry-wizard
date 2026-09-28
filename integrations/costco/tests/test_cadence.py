"""One price batch per run, and a longer wait when Costco has no price."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from costco_sync.cadence import PriceCheck, advance_checks, select_due
from costco_sync.live import CostcoSource
from costco_sync.models import PriceLookupResult, PriceQuote, RetailRow
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
    updated = advance_checks(["sale", "quiet", "gone"], [sale, quiet], {}, NOW, TODAY)
    assert updated["sale"].miss_count == 0
    assert updated["sale"].next_check_at == NOW + timedelta(days=1)
    assert updated["quiet"].next_check_at == NOW + timedelta(days=7)
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


def test_due_rows_prefer_a_sale_and_stop_at_sixty():
    rows = {
        "sale": RetailRow(
            retail_key="costco:121:sale",
            retailer_sku="sale",
            reduction_kind="instant_savings",
        )
    }
    quiet = [f"{index:04d}" for index in range(1000, 1060)]
    checks = {"later": PriceCheck(NOW + timedelta(days=30), 0)}
    due = select_due(
        ["later", "sale", *quiet],
        rows=rows,
        fresh=set(),
        checks=checks,
        now=NOW,
        today=TODAY,
    )
    assert due[0] == "sale"
    assert "later" not in due
    assert due[-1] == "1058"
    assert "1059" not in due
    assert len(due) == 60


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
    retail.append(_row("later"))
    snapshot = {
        "household_timezone": "America/Los_Angeles",
        "preferred_costco_warehouse": "121 Foster City",
        "items": [],
        "retail_memory": retail,
        "known_source_refs": [],
    }
    store = StateStore(tmp_path / "state.db")
    later = NOW + timedelta(days=30)
    store.save_price_checks({"later": PriceCheck(later, 3)})
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

    assert source.asked[0] == "sale"
    assert "later" not in source.asked
    assert "1059" not in source.asked
    assert len(source.asked) == 60
    written = {row["retailer_sku"] for row in result["mutations"]["retail_memory_upserts"]}
    assert written == {"sale"}
    assert checks["sale"].miss_count == 0
    assert checks["sale"].next_check_at == NOW + timedelta(days=1)
    assert checks["1001"].next_check_at == NOW + timedelta(days=7)
    assert checks["1001"].miss_count == 0
    assert checks["1000"].miss_count == 1
    assert checks["1000"].next_check_at == NOW + timedelta(days=2)
    assert checks["later"].miss_count == 3
    assert checks["later"].next_check_at == later
    assert "1059" not in checks
