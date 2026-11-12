import unittest
from datetime import timedelta

from helpers import NOW, book, household, rows, say
from pantry_wizard import ops
from pantry_wizard.store import PwError


class ListTests(unittest.TestCase):
    def test_add_is_one_read_and_one_append(self):
        be = household()
        be.read_calls = be.write_calls = 0
        say(be, [{"op": "add", "item": "eggs"}, {"op": "add", "text": "Birthday candles"}])
        self.assertEqual((be.read_calls, be.write_calls), (1, 1))

    def test_two_people_asking_is_two_rows_merged_on_read_and_closed_together(self):
        be = household()
        say(be, [{"op": "add", "item": "eggs", "qty": "the big pack"}], by="alex")
        say(be, [{"op": "add", "item": "eggs", "qty": "super extra"}], by="sam")
        self.assertEqual(len([r for r in rows(be, "List") if r["status"] == "open"]), 2)
        merged = ops.open_list(book(be))["buy"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["qty"], ["the big pack", "super extra"])  # their words, untouched
        self.assertEqual(merged[0]["by"], ["alex", "sam"])
        result = say(be, [{"op": "done", "item": "eggs"}], by="sam")
        self.assertEqual(result[0]["closed"], ["Eggs", "Eggs"])
        self.assertEqual(ops.open_list(book(be))["buy"], [])

    def test_a_request_without_an_item_needs_no_items_row(self):
        be = household()
        before = len(rows(be, "Items"))
        say(be, [{"op": "add", "text": "Birthday candles"}])
        self.assertEqual(len(rows(be, "Items")), before)
        say(be, [{"op": "done", "text": "birthday candles"}])
        self.assertEqual(ops.open_list(book(be))["buy"], [])

    def test_skip_expires_by_itself(self):
        be = household()
        say(be, [{"op": "skip", "item": "ketchup", "until": "2026-11-18"}])
        self.assertEqual(len(ops.open_list(book(be))["skip"]), 1)
        later = NOW + timedelta(days=10)
        self.assertEqual(ops.open_list(book(be, later))["skip"], [])
        say(be, [{"op": "saw", "item": "eggs", "kind": "low"}], now=later)  # any write sweeps
        self.assertEqual([r["status"] for r in rows(be, "List")], ["expired"])

    def test_a_request_for_one_store_only_shows_there(self):
        be = household()
        say(be, [{"op": "add", "item": "naan", "store": "grocer"}])
        self.assertEqual(ops.open_list(book(be), "costco")["buy"], [])
        self.assertEqual(len(ops.open_list(book(be), "grocer")["buy"]), 1)

    def test_retry_with_the_same_message_id_writes_nothing(self):
        be = household()
        say(be, [{"op": "add", "item": "eggs"}, {"op": "saw", "item": "ketchup", "kind": "out"}], ref="msg-1")
        again = say(be, [{"op": "add", "item": "eggs"}, {"op": "saw", "item": "ketchup", "kind": "out"}], ref="msg-1")
        self.assertEqual([r.get("skipped") for r in again], ["already recorded"] * 2)
        self.assertEqual(len(rows(be, "List")), 1)


class GuardTests(unittest.TestCase):
    def test_a_misspelt_person_is_refused_not_added(self):
        be = household()
        with self.assertRaises(PwError) as err:
            say(be, [{"op": "add", "item": "eggs"}], by="alexx")
        self.assertIn("alex", str(err.exception))
        self.assertEqual(len(rows(be, "List")), 0)

    def test_a_new_store_is_added_when_the_agent_says_it_is_new(self):
        be = household()
        say(be, [{"op": "add", "item": "eggs", "store": "Corner Market", "new_source": True}])
        self.assertIn("store:corner-market", [r["source"] for r in rows(be, "Sources")])

    def test_unknown_item_and_bad_vocabulary_are_refused(self):
        be = household()
        for bad in ({"op": "add", "item": "eggz"}, {"op": "saw", "item": "eggs", "kind": "empty"},
                    {"op": "set_item", "item": "eggs", "rule": "sometimes"}):
            with self.assertRaises(PwError):
                say(be, [bad])

    def test_a_failed_batch_writes_nothing(self):
        be = household()
        with self.assertRaises(PwError):
            say(be, [{"op": "add", "item": "eggs"}, {"op": "add", "item": "nope"}])
        self.assertEqual(len(rows(be, "List")), 0)

    def test_wrong_schema_version_refuses_to_write(self):
        be = household()
        for r in be.tabs["Config"]:
            if r[0] == "schema_version":
                r[1] = "1.9"
        with self.assertRaises(PwError):
            say(be, [{"op": "add", "item": "eggs"}])


class ItemTests(unittest.TestCase):
    def test_a_preference_is_one_set_with_an_audit_row(self):
        be = household()
        say(be, [{"op": "set_item", "item": "ketchup", "stores": "grocer"}],
            raw="We usually buy ketchup at the grocer")
        item = [r for r in rows(be, "Items") if r["item"] == "ketchup"][0]
        self.assertEqual(item["stores"], "grocer")
        audit = rows(be, "Log")[-1]
        self.assertEqual((audit["kind"], audit["item"], audit["by"]), ("set", "ketchup", "alex"))
        self.assertIn("usually", audit["raw"])

    def test_questions_come_eight_to_a_card_and_answers_apply(self):
        be = household()
        receipt_lines = [{"sku": str(100 + n), "text": f"THING {n}", "unit_price": "5"} for n in range(10)]
        say(be, [{"op": "receipt", "store": "costco", "date": "2026-11-01", "source": "import",
                  "receipt_id": "R1", "lines": receipt_lines}])
        say(be, [{"op": "ask", "kind": "kind", "subject": f"costco:{100 + n}",
                  "question": f"Is THING {n} something you rebuy?",
                  "options": ["consumable", "once"], "suggested": "once"} for n in range(10)])
        card = ops.open_questions(book(be))
        self.assertEqual(len(card), 8)
        say(be, [{"op": "answer", "id": card[0]["id"], "answer": "once"}], by="sam")
        product = [p for p in rows(be, "Products") if p["product"] == card[0]["subject"]][0]
        self.assertEqual(product["kind"], "once")
        self.assertEqual(len(ops.open_questions(book(be), 20)), 9)
        # asking the same thing again while it is open does nothing
        again = say(be, [{"op": "ask", "kind": "kind", "subject": "costco:105", "question": "?"}])
        self.assertEqual(again[0]["skipped"], "already asked")


if __name__ == "__main__":
    unittest.main()
