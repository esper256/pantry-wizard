import unittest

from helpers import NOW, book, rows, say
from pantry_wizard import costco_bridge, migrate
from pantry_wizard.backend import MemoryBackend


def v1_workbook():
    def tab(header, *rows):
        return [header] + [[r.get(h, "") for h in header] for r in rows]

    items_h = ["item_id", "canonical_name", "aliases", "inventory_state", "purchase_intent",
               "requested_quantity", "intent_expires_at", "preferred_stores", "item_policy",
               "state_summary", "updated_at"]
    retail_h = ["retail_key", "item_id", "store", "location", "retailer_sku", "retailer_name",
                "receipt_name", "baseline_unit_price", "current_price", "regular_price",
                "reduction_kind", "reduction_ends_at", "price_scope", "observed_at",
                "availability", "sale_scope"]
    events_h = ["event_id", "recorded_at", "occurred_at", "actor", "written_by", "item_id",
                "event_type", "quantity", "unit", "store", "price_paid", "raw_message",
                "interpretation", "source_type", "source_ref", "supersedes_event_id"]
    return {
        "Config": tab(["key", "value", "description"],
                      {"key": "household_timezone", "value": "America/Los_Angeles"},
                      {"key": "currency", "value": "USD"}, {"key": "schema_version", "value": "0.3"}),
        "Items": tab(items_h,
                     {"item_id": "u1", "canonical_name": "Eggs", "aliases": "large eggs; costco:100",
                      "purchase_intent": "buy", "requested_quantity": "2", "preferred_stores": "Costco"},
                     {"item_id": "u2", "canonical_name": "Honey cereal", "item_policy": "Buy only if on sale."},
                     {"item_id": "u3", "canonical_name": "Bananas", "purchase_intent": "do_not_buy",
                      "intent_expires_at": "2026-11-18"},
                     {"item_id": "u4", "canonical_name": "Cheese", "preferred_stores": "Trader Joe's; Costco"},
                     {"item_id": "u5", "canonical_name": "Smash burgers",
                      "item_policy": "Load up whenever in stock."}),
        "RetailMemory": tab(retail_h,
                            {"retail_key": "costco:000:100", "store": "Costco", "retailer_sku": "100",
                             "retailer_name": "Eggs 5 dozen", "receipt_name": "KS 5DZ EGGS",
                             "baseline_unit_price": "12.99", "current_price": "11.49",
                             "reduction_kind": "instant_savings", "sale_scope": "both",
                             "availability": "in_stock", "observed_at": "2026-10-27T08:00:00-07:00"},
                            {"retail_key": "costco:000:200", "item_id": "u2", "store": "Costco",
                             "retailer_sku": "200", "receipt_name": "HBO W/ALMNDS"},
                            {"retail_key": "costco:000:999", "store": "Costco", "retailer_sku": "999",
                             "receipt_name": "CA REDEMP VAL"}),
        "Integrations": tab(["integration_key", "store", "status", "location", "owner", "lease_until"],
                            {"integration_key": "costco", "store": "Costco", "status": "active",
                             "location": "000 Example City", "owner": "bot"}),
        "Events": tab(events_h,
                      {"event_id": "e1", "recorded_at": "2026-09-28T10:00:00-07:00", "occurred_at": "2026-09-20",
                       "actor": "costco-sync", "written_by": "costco-sync", "event_type": "purchased",
                       "quantity": "1", "store": "Costco", "price_paid": "12.99",
                       "source_type": "receipt_import", "source_ref": "costco:wh:B1:100"},
                      {"event_id": "e2", "recorded_at": "2026-09-28T10:00:00-07:00", "occurred_at": "2026-09-20",
                       "actor": "costco-sync", "written_by": "costco-sync", "event_type": "purchased",
                       "store": "Costco", "price_paid": "1.75", "source_type": "receipt_import",
                       "source_ref": "costco:wh:B1:999"},
                      {"event_id": "e3", "recorded_at": "2026-10-27T18:00:00-07:00", "occurred_at": "2026-10-27",
                       "actor": "Sam", "written_by": "other-bot", "item_id": "u1", "event_type": "purchased",
                       "store": "Costco", "price_paid": "12.99", "interpretation": "KS 5DZ EGGS",
                       "source_type": "receipt_photo", "source_ref": "photo:costco:B2:1"},
                      {"event_id": "e4", "recorded_at": "2026-10-01T18:00:00-07:00", "occurred_at": "2026-10-01",
                       "actor": "sam", "written_by": "other-bot", "item_id": "u4", "event_type": "purchased",
                       "store": "Trader Joe's", "price_paid": "5.99", "interpretation": "BRIE",
                       "source_type": "receipt_photo", "source_ref": "tj receipt photo 2026-10-01"},
                      {"event_id": "e5", "recorded_at": "2026-11-10T09:00:00-08:00", "actor": "Alex",
                       "written_by": "bot", "item_id": "u1", "event_type": "requested", "quantity": "2",
                       "raw_message": "get two eggs", "source_type": "human_chat"},
                      {"event_id": "e6", "recorded_at": "2026-11-03T09:00:00-08:00", "actor": "alex",
                       "written_by": "bot", "item_id": "u2", "event_type": "observed_out",
                       "raw_message": "we're out of cereal", "source_type": "human_chat"},
                      # A refund line, and a photo logged under the importer's own key format.
                      {"event_id": "e7", "recorded_at": "2026-09-28T10:00:00-07:00", "occurred_at": "2026-09-20",
                       "actor": "membership import", "written_by": "costco-sync", "event_type": "refunded",
                       "quantity": "1", "store": "Costco", "price_paid": "7.49",
                       "source_type": "receipt_import", "source_ref": "costco:wh:B1:300:refund"},
                      {"event_id": "e8", "recorded_at": "2026-10-27T18:00:00-07:00", "occurred_at": "2026-10-27",
                       "actor": "partner (whiteboard)", "written_by": "other-bot", "event_type": "purchased",
                       "store": "Costco", "price_paid": "11.49", "source_type": "receipt_photo",
                       "source_ref": "costco:wh:B2:100#2"},
                      # A photo keyed by receipt and SKU, the way the member-card receipt was logged.
                      {"event_id": "e10", "recorded_at": "2026-10-27T18:00:00-07:00", "occurred_at": "2026-10-27",
                       "actor": "Sam", "written_by": "other-bot", "event_type": "purchased",
                       "store": "Costco", "price_paid": "14.99", "source_type": "receipt_photo",
                       "source_ref": "photo:costco:B2:200#2"},
                      # Said in chat, with no receipt behind it.
                      {"event_id": "e9", "recorded_at": "2026-11-02T12:00:00-08:00", "occurred_at": "2026-11-02",
                       "actor": "user", "written_by": "bot", "item_id": "u3", "event_type": "purchased",
                       "store": "Corner Market", "price_paid": "1.29", "raw_message": "I picked up bananas",
                       "interpretation": "Bought bananas at Corner Market", "source_type": "human_chat"}),
    }


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.be = MemoryBackend()
        self.report = migrate.migrate(v1_workbook(), self.be, once=["Cheese"],
                                      people=["partner=Sam", "user=Alex"])

    def test_every_check_passes(self):
        self.assertEqual(self.report["checks"], {k: True for k in self.report["checks"]})
        self.assertEqual(self.report["new"]["people"], ["sam", "alex"])  # one spelling each
        self.assertEqual(sorted(self.report["new"]["stores"]), ["corner-market", "costco", "trader-joes"])
        names = {r["source"]: r["name"] for r in rows(self.be, "Sources")}
        self.assertEqual((names["person:sam"], names["store:trader-joes"]), ("Sam", "Trader Joe's"))

    def test_an_unmapped_label_is_left_for_a_person_to_see(self):
        be = MemoryBackend()
        report = migrate.migrate(v1_workbook(), be)
        self.assertEqual(report["new"]["people"], ["sam", "alex", "partner", "user"])

    def test_items_get_readable_keys_rules_and_links(self):
        items = {i["item"]: i for i in rows(self.be, "Items")}
        self.assertEqual(items["eggs"]["aliases"], "large eggs")
        self.assertEqual(items["honey-cereal"]["rule"], "on_sale")
        self.assertEqual((items["smash-burgers"]["rule"], items["smash-burgers"]["keep"]),
                         ("when_in_stock", "Load up whenever in stock."))
        self.assertEqual(items["cheese"]["kind"], "once")
        self.assertEqual(items["cheese"]["stores"], "trader-joes; costco")
        products = {p["product"]: p for p in rows(self.be, "Products")}
        self.assertEqual(products["costco:100"]["item"], "eggs")        # from the costco:100 alias
        self.assertEqual(products["costco:200"]["item"], "honey-cereal")  # from item_id
        self.assertEqual(products["costco:999"]["kind"], "not_product")

    def test_intent_becomes_list_rows_with_the_requesters_words(self):
        by_item = {r["item"]: r for r in rows(self.be, "List")}
        self.assertEqual((by_item["eggs"]["kind"], by_item["eggs"]["by"], by_item["eggs"]["raw"]),
                         ("buy", "alex", "get two eggs"))
        self.assertEqual((by_item["bananas"]["kind"], by_item["bananas"]["until"]), ("skip", "2026-11-18"))

    def test_receipt_lines_leave_the_ledger_and_keep_their_source(self):
        receipts = {r["receipt"]: r for r in rows(self.be, "Receipts")}
        self.assertEqual(set(receipts), {"costco:B1", "photo:costco:B2", "photo:trader-joes:2026-10-01"})
        self.assertEqual(receipts["costco:B1"]["lines"], "3")
        self.assertEqual(receipts["photo:costco:B2"]["lines"], "3")   # every photo spelling, one receipt
        lines = {l["ref"]: l for l in rows(self.be, "Lines")}
        self.assertEqual(lines["costco:wh:B1:999"]["kind"], "deposit")
        self.assertEqual((lines["costco:wh:B1:300:refund"]["kind"], lines["costco:wh:B1:300:refund"]["receipt"]),
                         ("refund", "costco:B1"))
        self.assertEqual((lines["photo:costco:B2:1"]["item"], lines["photo:costco:B2:1"]["product"]),
                         ("eggs", ""))                                      # ":1" is a line number
        self.assertEqual(lines["photo:costco:B2:200#2"]["product"], "costco:200")
        from pantry_wizard import stats
        paid = stats.product_stats(book(self.be), NOW.date())["costco:200"]
        self.assertEqual((paid["last_paid"], paid["last_paid_at"]), ("14.99", "2026-10-27"))
        self.assertEqual(lines["costco:wh:B2:100#2"]["product"], "costco:100")   # the SKU survives
        self.assertEqual(lines["tj receipt photo 2026-10-01"]["item"], "cheese")
        log = rows(self.be, "Log")
        self.assertEqual([(l["kind"], l["item"], l["by"]) for l in log],
                         [("said", "eggs", "alex"), ("out", "honey-cereal", "alex"), ("bought", "bananas", "alex")])
        self.assertEqual((log[-1]["value"], log[-1]["raw"]),
                         ("Corner Market 1.29", "I picked up bananas | Bought bananas at Corner Market"))

    def test_a_photo_under_the_importers_key_does_not_block_the_official_receipt(self):
        setup = costco_bridge.snapshot(book(self.be), "costco")
        self.assertEqual(setup["known_source_refs"],
                         ["costco:wh:B1:100", "costco:wh:B1:300:refund", "costco:wh:B1:999"])

    def test_the_migrated_sheet_works_with_the_tool(self):
        say(self.be, [{"op": "done", "item": "eggs"}], by="sam")
        from pantry_wizard import brief
        b = brief.build(book(self.be), "costco")
        # Cereal: said out, nothing bought since, and only ever bought on sale.
        self.assertIn("Honey cereal: out", b["sections"]["waiting_for_sale"][0]["text"])
        self.assertEqual(b["sections"]["skip"][0]["text"], "Bananas: asked not to buy until 2026-11-18.")


if __name__ == "__main__":
    unittest.main()
