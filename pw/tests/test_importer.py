"""The real costco-sync, run through the bridge against a fake Costco.

This is the check that the two packages agree on the snapshot and the
mutation file. It is skipped when costco-sync is not beside this package.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from helpers import book, household, receipt, rows, say
from pantry_wizard import brief, costco_bridge, stats

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "integrations", "costco", "src"))
try:
    from costco_sync.models import LeaseError, PriceLookupResult, PriceQuote
    from costco_sync.run import history_windows, import_windows
    from costco_sync.sheetapply import validate_snapshot
    from costco_sync.store import StateStore
except ImportError:  # pragma: no cover
    import_windows = None

TZ = timezone(timedelta(hours=-8))
MORNING = datetime(2026, 11, 11, 8, 15, tzinfo=TZ)


def payload(barcode, day, items, member="SECRET-MEMBER"):
    return {"transactionBarcode": barcode, "warehouseNumber": "663", "warehouseName": "Example City",
            "transactionDate": day, "membershipNumber": member, "itemArray": items}


RECEIPTS = {
    "B1013": payload("B1013", "2026-10-13", [
        {"itemNumber": "1025795", "itemDescription01": "KS 5DZ EGGS", "amount": 12.99, "unit": 1},
        {"itemNumber": "500", "itemDescription01": "SPINDRIFT", "amount": 19.98, "unit": 2},
        {"itemNumber": "501", "itemDescription01": "CA REDEMP VAL", "amount": 2.40, "unit": 2},
    ]),
    "B1027": payload("B1027", "2026-10-27", [
        {"itemNumber": "1025795", "itemDescription01": "KS 5DZ EGGS", "amount": 12.99, "unit": 1},
        {"itemNumber": "500", "itemDescription01": "SPINDRIFT", "amount": 9.99, "unit": 1},
        {"itemNumber": "900", "itemDescription01": "TOWER HEATER", "amount": 44.99, "unit": 1},
        {"itemNumber": "600", "itemDescription01": "FLOSS PICKS", "amount": -7.49, "unit": -1},
    ]),
}


class FakeCostco:
    def __init__(self):
        self.asked = []
        self.sale = {}
        self.receipts = RECEIPTS

    def list_warehouse_receipts(self, start, end):
        return {"receipts": [{"transactionBarcode": b} for b in self.receipts]}

    def get_receipt_detail(self, barcode):
        return self.receipts[barcode]

    def lookup_product_names(self, skus, warehouse):
        return {"1025795": "Kirkland Signature Eggs, 5 dozen"}

    def lookup_prices(self, skus, warehouse):
        self.asked.append(list(skus))
        quotes = []
        for sku in skus:
            if sku == "900":
                continue  # Costco no longer prices the heater
            price = self.sale.get(sku)
            quotes.append(PriceQuote(
                item_number=sku, current_price=Decimal(price or "9.99"), regular_price=Decimal("9.99"),
                reduction_ends_at="2026-11-22" if price else "", price_scope="warehouse",
                availability="in_stock", warehouse_checked=True, warehouse_on_sale=bool(price)))
        return PriceLookupResult(ok=True, quotes=quotes)

    def search_products(self, query, warehouse, limit=5):
        return []


@unittest.skipIf(import_windows is None, "costco-sync is not importable")
class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.be = household()
        self.state = StateStore(Path(tempfile.mkdtemp()) / "state.db")
        self.addCleanup(self.state.close)
        self.costco = FakeCostco()
        costco_bridge.lease(book(self.be, MORNING), "costco", "test-bot",
                            location="663 Example City", status="setup")

    def sync(self, now, trip=False, owner="test-bot", account="", costco=None, state=None):
        wb = book(self.be, now)
        tier = brief.trip_tier(wb, "costco") if trip else None
        snap = costco_bridge.snapshot(wb, "costco", (tier["tier0"] + tier["tier1"]) if tier else None,
                                      account=account)
        snap = json.loads(json.dumps(snap))                    # it travels as a file
        self.assertEqual(validate_snapshot(snap), [])
        result = import_windows(snap, costco or self.costco, state or self.state,
                                windows=[(date(2026, 8, 1), now.date())], now=now, owner=owner)
        mutations = json.loads(json.dumps(result["mutations"]))
        return costco_bridge.apply(book(self.be, now), mutations, account=account)

    def test_two_memberships_feed_one_store(self):
        self.sync(MORNING)
        say(self.be, [{"op": "link", "product": "costco:1025795", "item": "eggs"}])

        # Sam's card. Its receipts never show on the first login. Sam photographed one of them.
        photo = receipt(self.be, "costco", "2026-11-05", [
            {"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99"},
            {"sku": "700", "text": "BUTTER", "unit_price": "11.99"}],
            source="photo", total="24.98", by="sam", now=MORNING - timedelta(days=5))
        sams = FakeCostco()
        sams.receipts = {"S1105": payload("S1105", "2026-11-05", [
            {"itemNumber": "1025795", "itemDescription01": "KS 5DZ EGGS", "amount": 12.99, "unit": 1},
            {"itemNumber": "700", "itemDescription01": "BUTTER", "amount": 11.99, "unit": 1},
        ], member="SAMS-MEMBER")}
        sams_state = StateStore(Path(tempfile.mkdtemp()) / "state.db")
        self.addCleanup(sams_state.close)
        costco_bridge.lease(book(self.be, MORNING), "costco", "test-bot", location="663 Example City",
                            account="sam")

        # A first download for this account is the whole history, whatever the other one has imported.
        first = json.loads(json.dumps(costco_bridge.snapshot(book(self.be, MORNING), "costco", account="sam")))
        self.assertGreater(len(history_windows(first, MORNING.date())), 1)
        again = json.loads(json.dumps(costco_bridge.snapshot(book(self.be, MORNING), "costco")))
        self.assertEqual(len(history_windows(again, MORNING.date())), 1)

        report = self.sync(MORNING, account="sam", costco=sams, state=sams_state)
        self.assertEqual((report["account"], report["receipts"]), ("sam", 1))
        self.assertEqual(report["superseded"], [photo["receipt"]])   # official copy, from either account
        self.assertEqual(sams.asked, [])                             # prices are the first account's job

        sync = {r["account"]: r for r in rows(self.be, "Sync")}
        self.assertNotEqual(sync["primary"]["membership_fingerprint"], sync["sam"]["membership_fingerprint"])
        self.assertEqual((sync["primary"]["history_through"], sync["sam"]["history_through"]),
                         ("2026-10-27", "2026-11-05"))
        eggs = stats.item_stats(book(self.be, MORNING), MORNING.date())["eggs"]
        self.assertEqual((eggs["last_bought"], eggs["buys_12mo"]), ("2026-11-05", 3))   # both cards, once each

        # The next morning each account runs again. Neither refuses the other, and
        # the first one prices the butter only Sam has bought.
        tomorrow = MORNING + timedelta(days=1)
        self.assertEqual(self.sync(tomorrow, account="sam", costco=sams, state=sams_state)["receipts"], 0)
        self.assertEqual(self.sync(tomorrow)["receipts"], 0)
        self.assertIn("700", self.costco.asked[-1])
        self.assertEqual({o["product"]: o["price"] for o in rows(self.be, "Offers")}["costco:700"], "9.99")

    def test_first_import_then_a_quiet_day_then_a_trip(self):
        # Sam photographed the Oct 27 receipt before Costco posted it, and
        # misread one digit of the barcode.
        say(self.be, [{"op": "add", "item": "eggs"}], by="sam", now=MORNING - timedelta(days=20))
        photo = receipt(self.be, "costco", "2026-10-27", [
            {"sku": "1025795", "text": "KS 5DZ EGGS", "item": "eggs", "unit_price": "12.99"},
            {"sku": "500", "text": "SPINDRIFT", "unit_price": "9.99"}],
            source="photo", rid="B1O27", by="sam", now=MORNING - timedelta(days=15))
        self.assertEqual(photo["closed"], ["Eggs"])
        self.assertEqual(photo["linked_products"], ["costco:1025795"])

        self.costco.sale = {"500": "7.99"}
        report = self.sync(MORNING)
        self.assertEqual((report["receipts"], report["lines"]), (2, 6))
        self.assertEqual(report["superseded"], ["photo:costco:B1O27"])
        self.assertIn("Imported 2 Costco receipts from 663 Example City", report["summary"])

        receipts = {r["receipt"]: r for r in rows(self.be, "Receipts")}
        self.assertEqual(receipts["costco:B1027"]["status"], "recorded")
        self.assertEqual(receipts["photo:costco:B1O27"]["status"], "superseded")
        lines = {l["ref"]: l for l in rows(self.be, "Lines") if l["ref"]}
        self.assertEqual(lines["costco:wh:B1027:600:refund"]["kind"], "refund")
        self.assertNotIn("SECRET-MEMBER", json.dumps(self.be.tabs))
        self.assertNotIn("costco:501", {p["product"] for p in rows(self.be, "Products")})  # deposit dropped

        products = {p["product"]: p for p in rows(self.be, "Products")}
        self.assertEqual(products["costco:1025795"]["item"], "eggs")          # the link from the photo holds
        self.assertEqual(products["costco:1025795"]["name"], "KS 5DZ EGGS")   # and the row is not rewritten
        eggs = stats.item_stats(book(self.be, MORNING), MORNING.date())["eggs"]
        self.assertEqual((eggs["last_bought"], eggs["buys_12mo"]), ("2026-10-27", 2))  # counted once

        offers = {o["product"]: o for o in rows(self.be, "Offers")}
        self.assertEqual((offers["costco:500"]["sale"], offers["costco:500"]["sale_scope"],
                          offers["costco:500"]["price"]), ("instant_savings", "both", "7.99"))
        self.assertEqual(offers["costco:900"]["misses"], "1")
        stamp = offers["costco:500"]["checked_at"]
        self.assertEqual(stamp, "2026-11-11T08:15:00-08:00")

        sync = rows(self.be, "Sync")[0]
        self.assertEqual((sync["status"], sync["history_from"], sync["history_through"], sync["owner"]),
                         ("active", "2026-10-13", "2026-10-27", "test-bot"))
        self.assertTrue(sync["membership_fingerprint"])

        # The same day again. The refunded floss is a product now, so it gets
        # its first price. After that nothing is due and nothing is written.
        again = self.sync(MORNING + timedelta(hours=1))
        self.assertEqual((again["receipts"], self.costco.asked[-1]), (0, ["600"]))
        asked = len(self.costco.asked)
        third = self.sync(MORNING + timedelta(hours=2))
        self.assertEqual((third["receipts"], third["offers_written"]), (0, 0))
        self.assertEqual(len(self.costco.asked), asked)

        # A trip refresh asks only about what this household rebuys.
        noon = MORNING + timedelta(hours=3)
        self.costco.sale = {"500": "7.99", "1025795": "10.49"}
        self.sync(noon, trip=True)
        self.assertEqual(self.costco.asked[-1], ["1025795", "500"])
        offers = {o["product"]: o for o in rows(self.be, "Offers")}
        self.assertEqual(offers["costco:1025795"]["price"], "10.49")
        self.assertEqual(offers["costco:500"]["checked_at"], "2026-11-11T11:15:00-08:00")
        self.assertEqual(offers["costco:500"]["changed_at"], stamp)        # asked again, nothing moved
        self.assertEqual(offers["costco:900"]["checked_at"], stamp)        # the heater was not asked about

        text = brief.render(brief.build(book(self.be, noon), "costco"))
        self.assertIn("Eggs", text)
        self.assertIn("SPINDRIFT", text)
        self.assertNotIn("HEATER", text)

    def test_the_importer_refuses_another_agents_lease(self):
        with self.assertRaises(LeaseError):
            self.sync(MORNING, owner="other-bot")
        self.assertEqual(len(rows(self.be, "Receipts")), 0)


if __name__ == "__main__":
    unittest.main()
