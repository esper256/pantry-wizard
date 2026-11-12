"""The importer bridge, against mutation files written by hand.

`test_importer.py` runs the real costco-sync through the same code.
"""

import json
import unittest
from datetime import timedelta

from helpers import NOW, book, household, receipt, rows, say
from pantry_wizard import costco_bridge
from pantry_wizard.store import PwError


def setup_store(be):
    costco_bridge.lease(book(be), "costco", "test-bot", location="000 Example City", status="active")


EGGS = {"retailer_sku": "1025795", "retailer_name": "Kirkland Eggs 5 dozen", "receipt_name": "KS 5DZ EGGS",
        "current_price": "11.49", "regular_price": "12.99", "baseline_unit_price": "12.99",
        "reduction_kind": "instant_savings,below_baseline", "reduction_ends_at": "2026-11-22",
        "price_scope": "warehouse", "observed_at": "2026-11-11T08:20:00-08:00",
        "availability": "in_stock", "sale_scope": "both", "purchase_count": "2"}

MUTATION = {
    "generated_at": "2026-11-11T08:20:00-08:00",
    "receipts": [{"barcode": "BARCODE1", "warehouse_number": "000", "date": "2026-11-11", "lines": [
        {"source_ref": "costco:wh:BARCODE1:1025795", "item_number": "1025795", "description": "KS 5DZ EGGS",
         "name": "Kirkland Eggs 5 dozen", "kind": "purchase", "quantity": "1", "unit_price": "12.99"},
        {"source_ref": "costco:wh:BARCODE1:777:refund", "item_number": "777", "description": "FLOSS PICKS",
         "name": "", "kind": "refund", "quantity": "1", "unit_price": "7.49"},
    ]}],
    # The same lines in their schema 0.3 form. The bridge does not read them.
    "events": [{"source_ref": "costco:wh:BARCODE1:1025795"}, {"source_ref": "costco:wh:BARCODE1:777:refund"}],
    "retail_memory_upserts": [EGGS],
    "checked_item_numbers": ["1025795"],
    "missed_item_numbers": [],
    "item_alias_updates": [],
    "integration_upsert": {"status": "active", "history_through": "2026-11-11", "owner": "someone-else",
                           "lease_until": "2099-01-01T00:00:00-08:00"},
    "summary": {"text": "Imported 1 Costco receipts."},
}


class BridgeTests(unittest.TestCase):
    def test_late_totals_fill_only_an_imported_receipt_that_has_none(self):
        be = household()
        setup_store(be)
        receipt(be, "costco", "2026-10-13", [{"sku": "1", "text": "A"}], rid="OLD")
        receipt(be, "costco", "2026-10-14", [{"sku": "1", "text": "A"}], rid="HAS", total="9.00")
        receipt(be, "costco", "2026-10-15", [{"sku": "1", "text": "A"}], rid="PIC", source="photo")
        snap = costco_bridge.snapshot(book(be), "costco")
        self.assertEqual(snap["receipts_missing_totals"], ["OLD"])
        totals = [{"barcode": b, "total": "5.46", "tax": "0.46", "instant_savings": "1.00"}
                  for b in ("OLD", "HAS", "PIC", "NEVER-SEEN")]
        report = costco_bridge.apply(book(be), {"receipts": [], "receipt_totals": totals})
        self.assertEqual(report["totals_filled"], 1)
        got = {r["receipt"]: (r["total"], r["tax"], r["savings"]) for r in rows(be, "Receipts")}
        self.assertEqual(got, {"costco:OLD": ("5.46", "0.46", "1.00"), "costco:HAS": ("9.00", "", ""),
                               "photo:costco:PIC": ("", "", "")})

    def test_apply_writes_each_fact_to_its_own_tab(self):
        be = household()
        setup_store(be)
        say(be, [{"op": "add", "item": "eggs"}], by="sam")
        receipt(be, "costco", "2026-10-13", [{"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99"}],
                rid="OLD")
        say(be, [{"op": "link", "product": "costco:1025795", "item": "eggs"}])

        report = costco_bridge.apply(book(be), MUTATION)
        self.assertEqual((report["receipts"], report["lines"]), (1, 2))
        self.assertEqual(report["closed"], ["Eggs"])           # the open request closed by itself
        self.assertEqual(report["summary"], "Imported 1 Costco receipts.")

        products = {p["product"]: p for p in rows(be, "Products")}
        self.assertEqual(products["costco:1025795"]["item"], "eggs")      # the person's link survives
        self.assertEqual(products["costco:1025795"]["receipt_name"], "KS 5DZ EGGS")

        offer = {o["product"]: o for o in rows(be, "Offers")}["costco:1025795"]
        self.assertEqual((offer["sale"], offer["sale_scope"], offer["price"]), ("instant_savings", "both", "11.49"))
        self.assertEqual(offer["checked_at"], "2026-11-11T08:20:00-08:00")

        lines = {l["ref"]: l for l in rows(be, "Lines")}
        refund = lines["costco:wh:BARCODE1:777:refund"]
        self.assertEqual((refund["kind"], refund["receipt"], refund["product"], refund["text"]),
                         ("refund", "costco:BARCODE1", "costco:777", "FLOSS PICKS"))

        sync = rows(be, "Sync")[0]
        self.assertEqual(sync["history_through"], "2026-11-11")
        self.assertEqual(sync["owner"], "test-bot")            # the lease is not the importer's to set
        self.assertNotIn("2099", sync["lease_until"])
        eggs = [v for v in rows(be, "v_Items") if v["item"] == "eggs"][0]
        self.assertEqual(eggs["last_bought"], "2026-11-11")    # the readable copy is refreshed

    def test_lease_is_renewed_by_its_owner_and_refused_to_others(self):
        be = household()
        costco_bridge.lease(book(be), "costco", "bot-a", location="000 Example City", status="active")
        row = costco_bridge.lease(book(be), "costco", "bot-a")
        self.assertEqual((row["owner"], row["location"]), ("bot-a", "000 Example City"))
        with self.assertRaises(PwError):
            costco_bridge.lease(book(be), "costco", "bot-b")
        later = book(be, NOW + timedelta(hours=40))
        self.assertEqual(costco_bridge.lease(later, "costco", "bot-b")["owner"], "bot-b")

    def test_applying_twice_changes_nothing(self):
        be = household()
        setup_store(be)
        costco_bridge.apply(book(be), MUTATION)
        kept = ("Products", "Receipts", "Lines", "Offers", "Log")
        before = json.dumps({t: be.tabs[t] for t in kept})
        again = costco_bridge.apply(book(be), MUTATION)
        self.assertEqual((again["receipts"], again["offers_written"]), (0, 0))
        self.assertEqual(json.dumps({t: be.tabs[t] for t in kept}), before)

    def test_a_row_resent_without_a_change_keeps_its_changed_at(self):
        """The importer resends a row when only the purchase count moved."""
        be = household()
        setup_store(be)
        costco_bridge.apply(book(be), MUTATION)
        resent = dict(EGGS, purchase_count="3", observed_at="2026-11-12T08:00:00-08:00")
        costco_bridge.apply(book(be), {"generated_at": "2026-11-12T08:00:00-08:00",
                                       "retail_memory_upserts": [resent]})
        offer = rows(be, "Offers")[0]
        self.assertEqual(offer["changed_at"], "2026-11-11T08:20:00-08:00")
        self.assertEqual(offer["checked_at"], "2026-11-11T08:20:00-08:00")   # it was not asked about either

    def test_checked_and_missed_lists_record_freshness(self):
        be = household()
        setup_store(be)
        costco_bridge.apply(book(be), MUTATION)
        costco_bridge.apply(book(be), {"generated_at": "2026-11-12T08:00:00-08:00",
                                       "checked_item_numbers": ["1025795"], "missed_item_numbers": ["777"]})
        offers = {o["product"]: o for o in rows(be, "Offers")}
        self.assertEqual(offers["costco:1025795"]["checked_at"], "2026-11-12T08:00:00-08:00")
        self.assertEqual(offers["costco:1025795"]["changed_at"], "2026-11-11T08:20:00-08:00")
        self.assertEqual(offers["costco:777"]["misses"], "1")

    def test_a_name_match_fills_an_empty_link_and_never_replaces_one(self):
        be = household()
        setup_store(be)
        costco_bridge.apply(book(be), MUTATION)
        update = {"item_alias_updates": [{"item_id": "eggs", "aliases": "large eggs; costco:1025795"}]}
        self.assertEqual(costco_bridge.apply(book(be), update)["links_filled"], 1)
        audit = rows(be, "Log")[-1]
        self.assertEqual((audit["by"], audit["value"]), ("costco-sync", "costco:1025795 -> eggs"))
        say(be, [{"op": "link", "product": "costco:1025795", "item": "ketchup"}])
        self.assertEqual(costco_bridge.apply(book(be), update)["links_filled"], 0)
        self.assertEqual(rows(be, "Products")[0]["item"], "ketchup")

    def test_a_second_account_has_its_own_row_lease_and_membership(self):
        be = household()
        setup_store(be)
        first = dict(MUTATION, integration_upsert=dict(MUTATION["integration_upsert"], membership_fingerprint="aaa"))
        costco_bridge.apply(book(be), first)
        # Another agent runs the second membership. Neither lease is in the other's way.
        added = costco_bridge.lease(book(be), "costco", "bot-b", location="000 Example City", account="Second")
        self.assertEqual((added["sync"], added["prices"], added["owner"]), ("costco:second", "", "bot-b"))
        self.assertEqual(costco_bridge.lease(book(be), "costco", "test-bot")["sync"], "costco:primary")
        with self.assertRaises(PwError):
            costco_bridge.lease(book(be), "costco", "test-bot", account="second")

        second = {
            "generated_at": "2026-11-12T09:00:00-08:00",
            "receipts": [{"barcode": "BARCODE2", "date": "2026-11-12", "lines": [
                {"source_ref": "costco:wh:BARCODE2:1025795", "item_number": "1025795",
                 "description": "KS 5DZ EGGS", "kind": "purchase", "quantity": "1", "unit_price": "12.49"}]}],
            # It bought eggs, so the importer resends the row. Prices are not this account's to write.
            "retail_memory_upserts": [dict(EGGS, current_price="1.00", baseline_unit_price="12.49")],
            "checked_item_numbers": ["1025795"],
            "integration_upsert": {"status": "active", "membership_fingerprint": "bbb",
                                   "history_from": "2026-11-12", "history_through": "2026-11-12"},
            "summary": {"text": "Imported 1 Costco receipts."},
        }
        with self.assertRaises(PwError) as wrong:      # forgot to say which account
            costco_bridge.apply(book(be), second)
        self.assertIn("different membership", str(wrong.exception))
        report = costco_bridge.apply(book(be), second, account="second")
        self.assertEqual((report["account"], report["receipts"], report["offers_written"]), ("second", 1, 0))

        sync = {r["sync"]: r for r in rows(be, "Sync")}
        self.assertEqual([(r["membership_fingerprint"], r["owner"], r["history_through"], r["prices"])
                          for r in (sync["costco:primary"], sync["costco:second"])],
                         [("aaa", "test-bot", "2026-11-11", "yes"), ("bbb", "bot-b", "2026-11-12", "")])
        receipts = {r["receipt"]: r["account"] for r in rows(be, "Receipts")}
        self.assertEqual(receipts, {"costco:BARCODE1": "primary", "costco:BARCODE2": "second"})
        offer = rows(be, "Offers")[0]
        self.assertEqual((offer["price"], offer["usual_price"], offer["checked_at"]),
                         ("11.49", "12.99", "2026-11-11T08:20:00-08:00"))
        # One product, one history.
        self.assertEqual([p["product"] for p in rows(be, "Products") if p["sku"] == "1025795"], ["costco:1025795"])
        from pantry_wizard import stats
        paid = stats.product_stats(book(be), NOW.date())["costco:1025795"]
        self.assertEqual((len(paid["dates"]), paid["last_paid"]), (2, "12.49"))

        # The second account is told every receipt line either account imported, and to price nothing.
        snap = costco_bridge.snapshot(book(be), "costco", priority=["1025795"], account="second")
        self.assertEqual(snap["priority_item_numbers"], [])
        self.assertIn("costco:wh:BARCODE1:1025795", snap["known_source_refs"])
        self.assertEqual((snap["integrations"][0]["membership_fingerprint"], snap["integrations"][0]["owner"]),
                         ("bbb", "bot-b"))
        # With no account named, a command means the one that refreshes prices.
        self.assertEqual(costco_bridge.snapshot(book(be), "costco", priority=["1025795"])["priority_item_numbers"],
                         ["1025795"])
        with self.assertRaises(PwError):
            costco_bridge.snapshot(book(be), "costco", account="third")

    def test_the_price_refresh_can_move_to_another_account(self):
        be = household()
        setup_store(be)
        costco_bridge.lease(book(be), "costco", "test-bot", account="second")
        costco_bridge.lease(book(be), "costco", "test-bot", account="second", prices=True)
        self.assertEqual({r["account"]: r["prices"] for r in rows(be, "Sync")}, {"primary": "", "second": "yes"})

    def test_a_file_from_an_older_importer_is_refused(self):
        be = household()
        setup_store(be)
        with self.assertRaises(PwError):
            costco_bridge.apply(book(be), {"events": [{"source_ref": "costco:wh:B:1"}]})
        self.assertEqual(len(rows(be, "Lines")), 0)

    def test_snapshot_gives_the_importer_what_it_already_expects(self):
        be = household()
        setup_store(be)
        costco_bridge.apply(book(be), MUTATION)
        say(be, [{"op": "link", "product": "costco:1025795", "item": "eggs"},
                 {"op": "classify", "product": "costco:777", "kind": "not_product"},
                 {"op": "add", "item": "eggs"}])
        # A photo of a later trip. Its lines are not the importer's.
        receipt(be, "costco", "2026-11-10", [{"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99",
                                              "ref": "costco:wh:P:1025795"}],
                source="photo", rid="P")
        snap = costco_bridge.snapshot(book(be), "costco", priority=["1025795"])
        self.assertEqual(snap["household_timezone"], "America/Los_Angeles")
        self.assertEqual(snap["known_source_refs"],
                         ["costco:wh:BARCODE1:1025795", "costco:wh:BARCODE1:777:refund"])
        self.assertEqual([r["retailer_sku"] for r in snap["retail_memory"]], ["1025795"])
        self.assertEqual(snap["non_product_item_numbers"], ["777"])
        row = snap["retail_memory"][0]
        self.assertEqual((row["retail_key"], row["store"]), ("costco:000:1025795", "Costco"))
        self.assertEqual((row["item_id"], row["last_paid_unit_price"], row["purchase_count"]),
                         ("eggs", "12.99", "2"))
        eggs = [i for i in snap["items"] if i["item_id"] == "eggs"][0]
        self.assertIn("costco:1025795", eggs["aliases"])
        self.assertEqual((eggs["purchase_intent"], eggs["preferred_stores"]), ("buy", "costco"))
        integration = snap["integrations"][0]
        self.assertEqual((integration["integration_key"], integration["owner"]), ("costco", "test-bot"))
        self.assertEqual(snap["priority_item_numbers"], ["1025795"])


if __name__ == "__main__":
    unittest.main()
