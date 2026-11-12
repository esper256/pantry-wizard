import unittest

from helpers import book, household, offer, receipt, say
from pantry_wizard import brief

SALE = dict(price="7.99", regular_price="9.99", usual_price="9.99", sale="instant_savings",
            sale_scope="both", sale_ends="2026-11-15", availability="in_stock",
            checked_at="2026-11-11T08:20:00-08:00")


def buy(be, sku, text, dates, item=None, kind=None):
    for n, d in enumerate(dates):
        receipt(be, "costco", d, [{"sku": sku, "text": text, "unit_price": "9.99"}], rid=f"{sku}-{n}")
    if item:
        say(be, [{"op": "link", "product": f"costco:{sku}", "item": item}])
    if kind:
        say(be, [{"op": "classify", "product": f"costco:{sku}", "kind": kind}])


def get(be):
    return brief.build(book(be), "costco")


def texts(b, section):
    return [r["text"] for r in b["sections"].get(section, [])]


class DealRuleTests(unittest.TestCase):
    def setUp(self):
        self.be = household()
        buy(self.be, "200", "SPINDRIFT", ["2026-08-01", "2026-09-01", "2026-10-01"])

    def deal_shown(self, **changes):
        offer(self.be, "costco:200", **dict(SALE, **changes))
        return bool(texts(get(self.be), "deals"))

    def test_confirmed_means_promotion_in_warehouse_on_the_shelf_not_ended(self):
        self.assertTrue(self.deal_shown())
        self.assertTrue(self.deal_shown(sale_scope="warehouse"))
        self.assertTrue(self.deal_shown(sale_ends=""))

    def test_everything_else_is_left_out_and_counted(self):
        for change in (dict(sale_scope="online"), dict(sale_scope="unknown"), dict(sale_scope=""),
                       dict(availability="out_of_stock"), dict(availability="unknown"),
                       dict(availability=""), dict(sale_ends="2026-11-10")):
            self.assertFalse(self.deal_shown(**change), change)
            self.assertEqual(get(self.be)["unconfirmed_sales"], 1, change)

    def test_a_price_below_usual_without_a_promotion_is_not_a_sale(self):
        self.assertFalse(self.deal_shown(sale="", price="6.00"))
        self.assertEqual(get(self.be)["unconfirmed_sales"], 0)

    def test_low_stock_is_shown_with_a_warning(self):
        self.deal_shown(availability="low_stock")
        self.assertIn("Low stock", texts(get(self.be), "deals")[0])

    def test_shows_last_paid_and_when(self):
        self.deal_shown()
        self.assertIn("Last paid $9.99 on 2026-10-01", texts(get(self.be), "deals")[0])

    def test_flags_a_sale_price_that_is_just_the_usual_price(self):
        self.deal_shown(price="9.99", regular_price="12.99")
        self.assertIn("what you usually pay", texts(get(self.be), "deals")[0])
        self.deal_shown()
        self.assertNotIn("what you usually pay", texts(get(self.be), "deals")[0])


class RelevanceTests(unittest.TestCase):
    def test_a_heater_bought_once_never_comes_back(self):
        be = household()
        buy(be, "300", "TOWER HEATER", ["2026-01-05"])
        offer(be, "costco:300", **SALE)
        self.assertEqual(texts(get(be), "deals"), [])
        # classified as a durable, it stays out however often it was bought
        buy(be, "300", "TOWER HEATER", ["2026-02-05", "2026-03-05"], kind="once")
        self.assertEqual(texts(get(be), "deals"), [])

    def test_a_durable_shows_only_when_someone_asks_for_it(self):
        be = household()
        buy(be, "300", "TOWER HEATER", ["2026-01-05"], kind="once")
        offer(be, "costco:300", **SALE)
        say(be, [{"op": "new_item", "name": "Heater"}, ])
        say(be, [{"op": "link", "product": "costco:300", "item": "heater"},
                 {"op": "add", "item": "heater"}])
        b = get(be)
        self.assertEqual(texts(b, "deals"), [])
        self.assertIn("On sale", texts(b, "buy")[0])

    def test_a_rarely_bought_consumable_shows_once_classified(self):
        be = household()
        buy(be, "400", "FOIL 2PK", ["2025-05-23"])
        offer(be, "costco:400", **SALE)
        self.assertEqual(texts(get(be), "deals"), [])
        say(be, [{"op": "classify", "product": "costco:400", "kind": "consumable"}])
        self.assertEqual(len(texts(get(be), "deals")), 1)

    def test_retired_one_time_items_stay_out(self):
        be = household()
        say(be, [{"op": "new_item", "name": "Potluck cheese", "kind": "once"}])
        buy(be, "500", "CHEESE TRAY", ["2026-09-01", "2026-10-01"], item="potluck-cheese")
        offer(be, "costco:500", **SALE)
        self.assertEqual(texts(get(be), "deals"), [])


class BriefTests(unittest.TestCase):
    def test_reads_once_and_never_waits_on_the_store(self):
        be = household()
        be.read_calls = 0
        get(be)
        self.assertEqual(be.read_calls, 1)

    def test_reminder_leads_until_done(self):
        be = household()
        say(be, [{"op": "remind", "text": "Use the rewards check", "store": "costco"}])
        b = get(be)
        self.assertEqual(list(b["sections"])[0], "reminders")
        self.assertTrue(brief.render(b).splitlines()[2].startswith("Reminders"))
        say(be, [{"op": "done", "text": "use the rewards check"}])
        self.assertNotIn("reminders", get(be)["sections"])

    def test_only_if_on_sale_waits_until_there_is_a_confirmed_sale(self):
        be = household()
        buy(be, "600", "HONEY CEREAL", ["2026-06-01", "2026-08-01"], item="cereal")
        say(be, [{"op": "add", "item": "cereal", "only_if": "on_sale"}])
        offer(be, "costco:600", **dict(SALE, sale="", sale_scope="", sale_ends=""))
        self.assertEqual(len(texts(get(be), "waiting_for_sale")), 1)
        offer(be, "costco:600", **SALE)
        b = get(be)
        self.assertNotIn("waiting_for_sale", b["sections"])
        self.assertIn("On sale", texts(b, "buy")[0])

    def test_standing_on_sale_rule_leads_the_deals(self):
        be = household()
        buy(be, "200", "SPINDRIFT", ["2026-08-01", "2026-09-01"])
        buy(be, "600", "HONEY CEREAL", ["2026-06-01"], item="cereal")
        offer(be, "costco:200", **SALE)
        offer(be, "costco:600", **SALE)
        deals = texts(get(be), "deals")
        self.assertIn("You buy this when it is on sale", deals[0])
        self.assertEqual(len(deals), 2)

    def test_when_in_stock_items_are_mentioned_without_being_on_the_list(self):
        be = household()
        buy(be, "700", "SMASH BURGERS", ["2026-07-01"], item="smash-burgers")
        offer(be, "costco:700", price="23.99", availability="out_of_stock")
        self.assertNotIn("in_stock_now", get(be)["sections"])
        offer(be, "costco:700", price="23.99", availability="in_stock")
        self.assertEqual(len(texts(get(be), "in_stock_now")), 1)

    def test_out_and_due_items_get_their_own_sections(self):
        be = household()
        say(be, [{"op": "saw", "item": "ketchup", "kind": "out"}], raw="we're out of ketchup")
        buy(be, "1025795", "KS 5DZ EGGS", ["2026-08-20", "2026-09-10", "2026-09-30", "2026-10-13"],
            item="eggs")
        b = get(be)
        self.assertIn("nothing bought since", texts(b, "probably_buy")[0])
        check = texts(b, "check_before_leaving")[0]
        self.assertIn("every 20 days", check)
        self.assertIn("29 days ago", check)
        self.assertNotIn("left", check)  # facts about rhythm, never a count

    def test_skip_suppresses_and_is_said(self):
        be = household()
        buy(be, "200", "SPINDRIFT", ["2026-08-01", "2026-09-01"])
        say(be, [{"op": "new_item", "name": "Sparkling water"}])
        say(be, [{"op": "link", "product": "costco:200", "item": "sparkling-water"},
                 {"op": "skip", "item": "sparkling-water", "until": "2026-11-20"}])
        offer(be, "costco:200", **SALE)
        b = get(be)
        self.assertEqual(texts(b, "deals"), [])
        self.assertIn("asked not to buy", texts(b, "skip")[0])

    def test_items_for_another_store_stay_out(self):
        be = household()
        say(be, [{"op": "saw", "item": "naan", "kind": "out"}])  # naan is a grocer item
        self.assertNotIn("probably_buy", get(be)["sections"])

    def test_follow_up_confirms_or_lists_what_changed(self):
        be = household()
        buy(be, "200", "SPINDRIFT", ["2026-08-01", "2026-09-01"])
        buy(be, "210", "PANERA SOUP", ["2026-08-01", "2026-09-01"])
        offer(be, "costco:200", **SALE)
        wb = book(be)
        first = brief.build(wb, "costco")
        brief.save(wb, first)
        wb.commit()
        self.assertTrue(first["provisional"])
        self.assertEqual(first["as_of"], "2026-11-11T08:20:00-08:00")

        wb = book(be)
        self.assertTrue(brief.changes(wb, "costco")["confirmed"])
        wb.commit()

        offer(be, "costco:210", **dict(SALE, price="5.99"))   # the refresh found a new sale
        offer(be, "costco:200", **dict(SALE, availability="out_of_stock"))  # and one sold out
        delta = brief.changes(book(be), "costco")
        self.assertFalse(delta["confirmed"])
        self.assertEqual(len(delta["new_or_changed"]), 1)
        self.assertIn("PANERA", delta["new_or_changed"][0])
        self.assertIn("SPINDRIFT", delta["no_longer_true"][0])


class TierTests(unittest.TestCase):
    def test_trip_refresh_covers_what_matters_and_nothing_else(self):
        be = household()
        buy(be, "1025795", "KS 5DZ EGGS", ["2026-09-30", "2026-10-13"], item="eggs")
        buy(be, "200", "SPINDRIFT", ["2026-08-01", "2026-09-01"])
        buy(be, "300", "TOWER HEATER", ["2026-01-05"])
        buy(be, "800", "DISCONTINUED SNACK", ["2026-08-01", "2026-09-01"])
        receipt(be, "costco", "2026-11-01", [{"sku": "999", "text": "CA REDEMP VAL", "kind": "deposit"}],
                rid="dep")
        offer(be, "costco:800", misses="7")
        say(be, [{"op": "add", "item": "eggs"}])
        tier = brief.trip_tier(book(be), "costco")
        self.assertEqual(tier["tier0"], ["1025795"])
        self.assertEqual(tier["tier1"], ["200"])

    def test_longest_unchecked_first_and_nothing_checked_in_the_last_two_hours(self):
        be = household()
        for sku in ("200", "210", "220", "230"):
            buy(be, sku, f"THING {sku}", ["2026-08-01", "2026-09-01"])
        offer(be, "costco:200", checked_at="2026-11-11T08:20:00-08:00")   # this morning
        offer(be, "costco:210", checked_at="2026-11-04T08:20:00-08:00")   # last week
        offer(be, "costco:230", checked_at="2026-11-11T10:30:00-08:00")   # a quarter of an hour ago
        tier = brief.trip_tier(book(be), "costco")
        self.assertEqual(tier["tier1"], ["220", "210", "200"])            # never checked, then oldest
        self.assertEqual(tier["checked_recently"], 1)


if __name__ == "__main__":
    unittest.main()
