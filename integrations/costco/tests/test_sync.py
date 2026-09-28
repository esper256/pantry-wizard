"""Receipt pairing, idempotent mutations, and the fields the sheet must not receive."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from costco_sync.decide import build_mutations, snapshot_from_json
from costco_sync.live import CostcoSource
from costco_sync.models import AuthError, PriceLookupResult, PriceQuote, SearchHit
from costco_sync.normalize import (
    barcodes_from_list,
    parse_catalog_prices,
    parse_receipt_detail,
    parse_summary_prices,
)
from costco_sync.run import write_import
from costco_sync.store import StateStore

FIXTURE = Path(__file__).parent / "fixtures" / "guac_receipt.json"
NOW = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)
SECRETS = ("SECRET-MEMBER-42", "XXXX1234", "APPROVAL999")


def _guac() -> dict:
    return json.loads(FIXTURE.read_text())


def _snapshot(**overrides) -> object:
    data = {
        "household_timezone": "America/Los_Angeles",
        "preferred_costco_warehouse": "121 Foster City",
        "items": [],
        "retail_memory": [],
        "known_source_refs": [],
    }
    data.update(overrides)
    return snapshot_from_json(data)


def _no_prices(skus, warehouse):
    del skus, warehouse
    return PriceLookupResult(ok=True, quotes=[])


def _no_search(query, warehouse):
    del query, warehouse
    return []


def test_instant_savings_child_reduces_the_unit_price_paid():
    receipt = parse_receipt_detail(_guac())
    assert len(receipt.lines) == 1
    line = receipt.lines[0]
    assert line.item_number == "1553261"
    assert line.kind == "purchase"
    assert line.unit_price_paid == Decimal("9.99")
    assert line.had_instant_savings is True
    assert line.source_ref == "costco:wh:21134300501862509051323:1553261"
    assert receipt.occurred_on == "2026-09-01"


def test_deposit_discount_sku_and_payment_fields_never_enter_the_mutation():
    receipt = parse_receipt_detail(_guac())
    mutations, _, _ = build_mutations(
        _snapshot(),
        [receipt],
        names={},
        price_lookup=_no_prices,
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    encoded = json.dumps(mutations)
    for secret in SECRETS:
        assert secret not in encoded
    skus = {row["retailer_sku"] for row in mutations["retail_memory_upserts"]}
    assert skus == {"1553261"}
    assert "363064" not in encoded
    assert "CRV" not in encoded
    purchased = [event for event in mutations["events"] if event["event_type"] == "purchased"]
    assert len(purchased) == 1
    assert purchased[0]["price_paid"] == "9.99"
    assert purchased[0]["actor"] == "membership import"
    assert purchased[0]["occurred_at"] == "2026-09-01"


def test_reimport_skips_known_source_refs_and_does_not_double_count():
    receipt = parse_receipt_detail(_guac())
    quote = PriceQuote(
        item_number="1553261",
        current_price=Decimal("9.99"),
        regular_price=Decimal("13.99"),
        price_scope="warehouse",
        product_name="Guacamole Bowl",
        reduction_ends_at="2026-10-12",
    )

    def prices(skus, warehouse):
        assert warehouse == "121"
        assert skus == ["1553261"]
        return PriceLookupResult(ok=True, quotes=[quote])

    first, samples, sampled = build_mutations(
        _snapshot(),
        [receipt],
        names={"1553261": "Guacamole Bowl"},
        price_lookup=prices,
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert first["retail_memory_upserts"][0]["purchase_count"] == "1"
    assert first["retail_memory_upserts"][0]["baseline_unit_price"] == ""
    assert first["retail_memory_upserts"][0]["reduction_kind"] == "instant_savings"
    assert not any(event["event_type"] == "deal_observed" for event in first["events"])

    second_snapshot = _snapshot(
        items=first["new_items"],
        retail_memory=first["retail_memory_upserts"],
        known_source_refs=[event["source_ref"] for event in first["events"]],
    )
    second, _, _ = build_mutations(
        second_snapshot,
        [receipt],
        names={"1553261": "Guacamole Bowl"},
        price_lookup=prices,
        search=_no_search,
        baseline_samples=samples,
        already_sampled=sampled,
        now=NOW,
    )
    assert second["events"] == []
    assert second["new_items"] == []
    assert second["retail_memory_upserts"][0]["purchase_count"] == "1"


def test_existing_inventory_is_not_rewritten_and_one_purchase_is_not_a_preference():
    receipt = parse_receipt_detail(_guac())
    snapshot = _snapshot(
        items=[
            {
                "item_id": "item-guac",
                "canonical_name": "Guac Bowl",
                "aliases": "",
                "inventory_state": "plenty",
                "purchase_intent": "",
                "preferred_stores": "",
            }
        ]
    )
    mutations, _, _ = build_mutations(
        snapshot,
        [receipt],
        names={},
        price_lookup=_no_prices,
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert mutations["new_items"] == []
    assert mutations["item_alias_updates"] == [
        {"item_id": "item-guac", "aliases": "costco:1553261"}
    ]
    patch = mutations["item_alias_updates"][0]
    assert "inventory_state" not in patch
    assert "preferred_stores" not in patch
    assert "purchase_intent" not in patch
    created_keys = set()
    for item in mutations["new_items"]:
        created_keys.update(item)
    assert "plenty" not in json.dumps(mutations["item_alias_updates"])


def test_a_receipt_does_not_create_a_household_item():
    receipt = parse_receipt_detail(_guac())
    mutations, _, _ = build_mutations(
        _snapshot(),
        [receipt],
        names={},
        price_lookup=_no_prices,
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert mutations["new_items"] == []
    purchased = [event for event in mutations["events"] if event["event_type"] == "purchased"]
    assert len(purchased) == 1
    assert purchased[0]["item_id"] == ""
    assert mutations["retail_memory_upserts"][0]["retailer_sku"] == "1553261"
    assert mutations["retail_memory_upserts"][0]["item_id"] == ""


def test_price_check_stays_on_the_interest_set():
    receipt = parse_receipt_detail(_guac())
    asked: list[str] = []

    def prices(skus, warehouse):
        del warehouse
        asked.extend(skus)
        return PriceLookupResult(ok=True, quotes=[])

    def search(query, warehouse):
        del warehouse
        if query == "Oat Milk":
            return [SearchHit("424242", "Kirkland Oat Milk")]
        if query == "Spindrift":
            return [
                SearchHit("111", "Spindrift Lemon"),
                SearchHit("222", "Spindrift Raspberry"),
            ]
        return []

    snapshot = _snapshot(
        items=[
            {
                "item_id": "oat",
                "canonical_name": "Oat Milk",
                "inventory_state": "out",
                "purchase_intent": "",
                "preferred_stores": "",
                "aliases": "",
            },
            {
                "item_id": "spin",
                "canonical_name": "Spindrift",
                "inventory_state": "adequate",
                "purchase_intent": "buy",
                "preferred_stores": "",
                "aliases": "",
            },
            {
                "item_id": "vitamin",
                "canonical_name": "Vitamin D",
                "inventory_state": "very_low",
                "purchase_intent": "",
                "preferred_stores": "",
                "aliases": "",
            },
            {
                "item_id": "paper",
                "canonical_name": "Paper Towels",
                "inventory_state": "plenty",
                "purchase_intent": "",
                "preferred_stores": "FoodMaxx",
                "aliases": "",
            },
        ]
    )
    mutations, _, _ = build_mutations(
        snapshot,
        [receipt],
        names={},
        price_lookup=prices,
        search=search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert asked == ["1553261", "424242"]
    assert "999001" not in asked
    aliases = {row["item_id"]: row["aliases"] for row in mutations["item_alias_updates"]}
    assert aliases["oat"] == "costco:424242"
    assert "spin" not in aliases
    assert "vitamin" not in aliases
    assert "paper" not in aliases

    def only_oat(query, warehouse):
        del warehouse
        if query == "Milk":
            return [SearchHit("777", "Oat Milk")]
        return []

    milk = _snapshot(
        items=[
            {
                "item_id": "milk",
                "canonical_name": "Milk",
                "inventory_state": "out",
                "purchase_intent": "",
                "preferred_stores": "",
                "aliases": "",
            }
        ]
    )
    milk_mutations, _, _ = build_mutations(
        milk,
        [],
        names={},
        price_lookup=prices,
        search=only_oat,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert milk_mutations["item_alias_updates"] == []


def test_few_cents_are_not_a_price_reduction_and_a_real_drop_is():
    receipt = parse_receipt_detail(
        {
            "transactionBarcode": "barcode-1",
            "warehouseNumber": "121",
            "warehouseName": "Foster City",
            "transactionDate": "2026-08-01",
            "itemArray": [
                {
                    "itemNumber": "42",
                    "itemDescription01": "SPINDRIFT",
                    "amount": 10.00,
                    "unit": 1,
                }
            ],
        }
    )

    def prices_for(current: str):
        def prices(skus, warehouse):
            del skus, warehouse
            return PriceLookupResult(
                ok=True,
                quotes=[
                    PriceQuote(
                        item_number="42",
                        current_price=Decimal(current),
                        regular_price=Decimal(current),
                        price_scope="online",
                        product_name="Spindrift",
                    )
                ],
            )

        return prices

    noisy, _, _ = build_mutations(
        _snapshot(),
        [receipt],
        names={},
        price_lookup=prices_for("9.95"),
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    row = noisy["retail_memory_upserts"][0]
    assert row["baseline_unit_price"] == "10.00"
    assert row["reduction_kind"] == ""
    assert row["price_scope"] == "online"
    assert not any(event["event_type"] == "deal_observed" for event in noisy["events"])

    real, _, _ = build_mutations(
        _snapshot(),
        [receipt],
        names={},
        price_lookup=prices_for("8.49"),
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert real["retail_memory_upserts"][0]["reduction_kind"] == "below_baseline"
    assert real["retail_memory_upserts"][0]["current_price"] == "8.49"
    assert not any(event["event_type"] == "deal_observed" for event in real["events"])


def test_failed_price_lookup_does_not_clear_a_known_price():
    receipt = parse_receipt_detail(_guac())
    snapshot = _snapshot(
        retail_memory=[
            {
                "retail_key": "costco:121:1553261",
                "item_id": "item-guac",
                "store": "Costco",
                "location": "121 Foster City",
                "retailer_sku": "1553261",
                "retailer_name": "Guac Bowl",
                "receipt_name": "GUAC BOWL",
                "last_paid_unit_price": "9.99",
                "last_paid_at": "2026-08-01",
                "baseline_unit_price": "13.99",
                "purchase_count": "2",
                "current_price": "11.99",
                "regular_price": "13.99",
                "reduction_kind": "instant_savings",
                "reduction_ends_at": "2026-10-01",
                "price_scope": "warehouse",
                "observed_at": "2026-09-20T10:00:00-07:00",
            }
        ],
        items=[
            {
                "item_id": "item-guac",
                "canonical_name": "Guac Bowl",
                "aliases": "costco:1553261",
                "inventory_state": "plenty",
            }
        ],
    )

    def failed(skus, warehouse):
        del skus, warehouse
        return PriceLookupResult(ok=False, quotes=[])

    mutations, _, _ = build_mutations(
        snapshot,
        [receipt],
        names={},
        price_lookup=failed,
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    row = mutations["retail_memory_upserts"][0]
    assert row["current_price"] == "11.99"
    assert row["reduction_kind"] == "instant_savings"
    assert row["purchase_count"] == "3"
    assert "inventory_state" not in row
    assert mutations["price_lookup"] == "failed"


def test_refund_is_evidence_and_not_part_of_the_price_baseline():
    receipt = parse_receipt_detail(
        {
            "transactionBarcode": "barcode-2",
            "warehouseNumber": "121",
            "warehouseName": "Foster City",
            "transactionDate": "2026-08-02",
            "itemArray": [
                {"itemNumber": "42", "itemDescription01": "SPINDRIFT", "amount": 10, "unit": 1},
                {"itemNumber": "42", "itemDescription01": "SPINDRIFT", "amount": -10, "unit": -1},
            ],
        }
    )
    assert [line.kind for line in receipt.lines] == ["purchase", "refund"]
    mutations, _, _ = build_mutations(
        _snapshot(),
        [receipt],
        names={},
        price_lookup=_no_prices,
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    row = mutations["retail_memory_upserts"][0]
    assert row["purchase_count"] == "1"
    assert row["baseline_unit_price"] == "10.00"
    kinds = [event["event_type"] for event in mutations["events"]]
    assert kinds.count("purchased") == 1
    assert kinds.count("refunded") == 1


def test_warehouse_summary_prices_record_the_sale_and_leave_an_unsale_price():
    """Recorded from the product summary API for warehouse 663 on 2026-09-28.

    Item 1553261 is $3.50 off through 2026-10-25 at that warehouse. Item 1542070
    has a warehouse price and no promotion. A 847 price on the same payload is
    not the 663 price.
    """
    payload = json.loads((Path(__file__).parent / "fixtures" / "price_summary_663.json").read_text())
    quotes = {
        quote.item_number: quote
        for quote in parse_summary_prices(payload["productData"], "663")
    }
    guac = quotes["1553261"]
    assert guac.current_price == Decimal("10.49")
    assert guac.regular_price == Decimal("13.99")
    assert guac.price_scope == "warehouse"
    assert guac.explicit_instant_savings
    assert guac.reduction_ends_at == "2026-10-25"
    assert guac.product_name.startswith("Wholly Guacamole")
    crackers = quotes["1542070"]
    assert crackers.current_price == Decimal("11.79")
    assert crackers.regular_price == Decimal("11.79")
    assert crackers.price_scope == "warehouse"
    assert not crackers.explicit_instant_savings
    assert crackers.reduction_ends_at == ""

    def prices(skus, warehouse):
        assert warehouse == "663"
        assert skus == ["1542070", "1553261"]
        return PriceLookupResult(ok=True, quotes=list(quotes.values()))

    snapshot = _snapshot(
        preferred_costco_warehouse="663 Concord",
        retail_memory=[
            {
                "retail_key": "costco:663:1553261",
                "store": "Costco",
                "location": "663 Concord",
                "retailer_sku": "1553261",
                "receipt_name": "GUAC",
                "current_price": "",
            },
            {
                "retail_key": "costco:663:1542070",
                "store": "Costco",
                "location": "663 Concord",
                "retailer_sku": "1542070",
                "receipt_name": "GOLDFISH",
                "current_price": "",
            },
        ],
    )
    mutations, _, _ = build_mutations(
        snapshot,
        [],
        names={},
        price_lookup=prices,
        search=_no_search,
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert mutations["price_lookup"] == "ok"
    by_key = {row["retail_key"]: row for row in mutations["retail_memory_upserts"]}
    assert by_key["costco:663:1553261"]["current_price"] == "10.49"
    assert by_key["costco:663:1553261"]["regular_price"] == "13.99"
    assert by_key["costco:663:1553261"]["reduction_kind"] == "instant_savings"
    assert by_key["costco:663:1553261"]["reduction_ends_at"] == "2026-10-25"
    assert by_key["costco:663:1553261"]["price_scope"] == "warehouse"
    assert by_key["costco:663:1542070"]["current_price"] == "11.79"
    assert by_key["costco:663:1542070"]["reduction_kind"] == ""
    assert not any(event["event_type"] == "deal_observed" for event in mutations["events"])


def test_price_summary_http_400_does_not_become_an_auth_failure():
    source = CostcoSource.__new__(CostcoSource)
    source._require_auth = lambda: None

    def rejected(item_numbers, warehouse):
        del item_numbers, warehouse
        raise RuntimeError("Costco price summary returned HTTP 400")

    source._price_summaries = rejected
    result = source.lookup_prices(["1553261"], "663")
    assert result.ok is False
    assert result.quotes == []


def test_catalog_price_scope():
    warehouse = parse_catalog_prices(
        [
            {
                "itemNumber": "1",
                "warehousePrice": "11.99",
                "listPrice": "14.99",
                "description": {"shortDescription": "Spindrift"},
            }
        ]
    )
    assert warehouse[0].price_scope == "warehouse"
    assert warehouse[0].current_price == Decimal("11.99")
    online = parse_catalog_prices([{"itemNumber": "1", "price": 11.99, "regularPrice": 14.99}])
    assert online[0].price_scope == "online"
    assert online[0].regular_price == Decimal("14.99")


def test_gas_receipts_are_not_selected():
    barcodes = barcodes_from_list(
        {
            "data": {
                "receiptsWithCounts": {
                    "receipts": [
                        {"transactionBarcode": "warehouse-1", "documentType": "warehouse"},
                        {"transactionBarcode": "gas-1", "documentType": "gas"},
                        {"transactionBarcode": "wash-1", "documentType": "carwash"},
                    ]
                }
            }
        }
    )
    assert barcodes == ["warehouse-1"]


def test_auth_failure_does_not_write_a_mutation_file(tmp_path: Path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "household_timezone": "America/Los_Angeles",
                "preferred_costco_warehouse": "121 Foster City",
                "items": [],
                "retail_memory": [],
                "known_source_refs": [],
            }
        )
    )
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")

    class Boom:
        def list_warehouse_receipts(self, start_date: str, end_date: str) -> dict:
            del start_date, end_date
            raise AuthError("nope")

    with pytest.raises(AuthError):
        write_import(
            snapshot,
            out,
            store,
            Boom(),
            start=date(2026, 9, 1),
            end=date(2026, 9, 28),
            now=NOW,
        )
    assert not out.exists()
    store.close()


def test_cli_auth_failure_returns_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"preferred_costco_warehouse": "121 Foster City"}) + "\n"
    )
    out = tmp_path / "mutations.json"

    class Boom:
        def list_warehouse_receipts(self, start_date: str, end_date: str) -> dict:
            del start_date, end_date
            raise AuthError("token expired")

    monkeypatch.setattr("costco_sync.cli._source", lambda account: Boom())
    from costco_sync.cli import main

    code = main(
        [
            "run",
            "--snapshot",
            str(snapshot),
            "--out",
            str(out),
            "--state",
            str(tmp_path / "state.db"),
            "--now",
            "2026-09-28T17:00:00+00:00",
        ]
    )
    assert code == 2
    assert not out.exists()


def test_auth_without_a_token_points_at_chrome_devtools(monkeypatch: pytest.MonkeyPatch, capsys):
    class Status:
        account = "personal"

        def status(self):
            return {"account": "personal", "has_refresh_token": False}

    monkeypatch.setattr("costco_sync.live.CostcoSource", lambda account, policy=None: Status())
    from costco_sync.cli import main

    code = main(["auth", "--account", "personal"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "Developer Tools" in printed
    assert "Orders & Purchases" in printed
    assert "www.costco.com" in printed
    assert "refreshtoken" in printed
    assert "--refresh-token-stdin" in printed
    assert "costco-auth-browser" not in printed
