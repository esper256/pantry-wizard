"""Room for the agents' fuzzy thinking: a request for a kind of thing, a
free-text remark, standing notes, reopening a wrong close, and the context
digest. `pw` keeps the record exact; these let the agent interpret it."""

import unittest

from helpers import book, household, receipt, say
from pantry_wizard import costco_bridge, ops, query
from pantry_wizard.errors import PwError


def melons(be):
    say(be, [{"op": "new_item", "name": "Watermelon"}, {"op": "new_item", "name": "Cantaloupe"}])


class AnyOfTests(unittest.TestCase):
    def test_a_request_for_a_kind_of_thing_is_closed_by_any_member(self):
        be = household()
        melons(be)
        say(be, [{"op": "add", "text": "Melons", "any_of": ["watermelon", "cantaloupe"]}])
        m = [b for b in ops.open_list(book(be))["buy"] if b["text"] == "Melons"][0]
        self.assertEqual(m["any_of"], ["cantaloupe", "watermelon"])
        r = receipt(be, "costco", "2026-11-11",
                    [{"sku": "9", "text": "CANTALOUPE", "unit_price": "3.99", "item": "cantaloupe"}])
        self.assertEqual(r["closed"], ["Melons"])
        self.assertEqual([b["text"] for b in ops.open_list(book(be))["buy"]], [])

    def test_any_of_items_must_exist(self):
        be = household()
        with self.assertRaises(PwError):
            say(be, [{"op": "add", "text": "Melons", "any_of": ["watermelon"]}])

    def test_a_never_bought_kind_is_reported_for_the_agent_to_judge(self):
        be = household()
        melons(be)
        say(be, [{"op": "add", "text": "Melons", "any_of": ["watermelon", "cantaloupe"]}])
        say(be, [{"op": "add", "text": "Fruit tray"}])
        r = receipt(be, "costco", "2026-11-11", [{"sku": "9", "text": "HONEYDEW", "unit_price": "4.99"}])
        self.assertEqual(r["closed"], [])  # a first-ever honeydew matches no item
        self.assertEqual([o["text"] for o in r["open_requests"]], ["Melons", "Fruit tray"])
        self.assertEqual(say(be, [{"op": "done", "text": "Melons"}])[0]["closed"], ["Melons"])


class CloseTests(unittest.TestCase):
    def test_closing_nothing_is_an_error_that_lists_what_is_open(self):
        be = household()
        say(be, [{"op": "add", "text": "Beer"}])
        with self.assertRaises(PwError) as e:
            say(be, [{"op": "done", "text": "Wine"}])
        self.assertIn("Beer", str(e.exception))

    def test_reopen_restores_the_request_and_keeps_its_asker(self):
        be = household()
        say(be, [{"op": "add", "text": "Beer"}], by="sam")
        say(be, [{"op": "done", "text": "Beer"}], by="alex")
        back = say(be, [{"op": "reopen", "text": "Beer"}], by="alex")
        self.assertEqual((back[0]["reopened"], back[0]["asked_by"]), ("Beer", "sam"))
        self.assertEqual([b["text"] for b in ops.open_list(book(be))["buy"]], ["Beer"])

    def test_reopen_without_a_match_is_refused(self):
        be = household()
        with self.assertRaises(PwError):
            say(be, [{"op": "reopen", "text": "ghost"}])


class RemarkAndNoteTests(unittest.TestCase):
    def test_a_free_text_observation_is_kept_and_found_in_said(self):
        be = household()
        say(be, [{"op": "saw", "value": "the kids are sick of granola bars"}])
        found = query.show(book(be), "said", text="granola")["rows"]
        self.assertEqual(found[0]["kind"], "remark")
        self.assertEqual(found[0]["value"], "the kids are sick of granola bars")

    def test_a_saw_with_neither_item_nor_words_is_refused(self):
        be = household()
        with self.assertRaises(PwError):
            say(be, [{"op": "saw"}])

    def test_a_standing_note_is_stored_and_surfaced_in_context(self):
        be = household()
        say(be, [{"op": "note", "text": "Sam is vegetarian"}], by="sam")
        self.assertEqual(query.context(book(be))["notes"], ["Sam is vegetarian"])


class ContextTests(unittest.TestCase):
    def test_context_interprets_without_leaking_volatile_state(self):
        be = household()
        costco_bridge.lease(book(be), "costco", "grok-bot", location="663 Example City")
        ctx = query.context(book(be))
        self.assertEqual({p["person"] for p in ctx["people"]}, {"alex", "sam"})
        by_store = {s["store"]: s["importer"] for s in ctx["stores"]}
        self.assertEqual((by_store["costco"], by_store["grocer"]), (True, False))
        eggs = [i for i in ctx["items"] if i["item"] == "eggs"][0]
        self.assertEqual(eggs["stores"], ["costco"])
        self.assertNotIn("stock", eggs)  # interpret with this; read stock fresh
        cereal = [i for i in ctx["items"] if i["item"] == "cereal"][0]
        self.assertEqual(cereal["rule"], "on_sale")


class MultiItemTests(unittest.TestCase):
    def test_a_read_filters_on_several_items_at_once(self):
        be = household()
        melons(be)
        receipt(be, "costco", "2026-11-08",
                [{"sku": "9", "text": "WATERMELON", "unit_price": "6.99", "item": "watermelon"}])
        out = query.show(book(be), "purchases", item=["watermelon", "cantaloupe"])
        self.assertEqual(out["covers"]["item"], ["watermelon", "cantaloupe"])
        self.assertEqual(out["totals"]["purchases"], 1)


if __name__ == "__main__":
    unittest.main()
