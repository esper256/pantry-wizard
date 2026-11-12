import copy
import unittest
from datetime import datetime, timedelta, timezone

from helpers import NOW, book, household, offer, receipt, say

from pantry_wizard import query
from pantry_wizard.store import PwError

EGGS = {"sku": "111", "text": "KS EGGS", "item": "eggs"}
TV = {"sku": "222", "text": "TV", "qty": "1", "unit_price": "499.99"}


def shopped():
    """Two warehouse trips with a refund and a fee, and one photographed grocer trip."""
    be = household()
    receipt(be, "costco", "2026-10-03", [dict(EGGS, qty="2", unit_price="6.99"), TV], rid="A1")
    receipt(be, "costco", "2026-10-20", [
        dict(EGGS, qty="1", unit_price="7.49"), dict(TV, kind="refund"),
        {"sku": "9", "text": "CA REDEMP VAL", "unit_price": "0.60", "kind": "deposit"}], rid="A2")
    receipt(be, "grocer", "2026-11-02", [{"text": "Naan", "item": "naan", "unit_price": "3.50"}],
            source="photo", total="3.85")
    return be


def ask(be, sql, **kw):
    got = query.run(book(be), [sql], **kw)[0]
    return [dict(zip(got["columns"], r)) for r in got["rows"]]


class QueryTests(unittest.TestCase):
    def test_spend_is_one_query(self):
        got = ask(shopped(), "SELECT COUNT(*) AS trips, SUM(lines_total) AS spent FROM trips "
                             "WHERE store = 'costco' AND date LIKE '2026-10%'")
        # 2 x 6.99 + 499.99, then 7.49 - 499.99 + 0.60
        self.assertEqual(got, [{"trips": 2, "spent": 22.07}])

    def test_purchases_leave_out_refunds_and_fees_and_resolve_the_item(self):
        be = shopped()
        got = ask(be, "SELECT date, item, item_name, name, amount FROM purchases ORDER BY date, name")
        self.assertEqual([(r["date"], r["name"], r["amount"]) for r in got], [
            ("2026-10-03", "KS EGGS", 13.98), ("2026-10-03", "TV", 499.99),
            ("2026-10-20", "KS EGGS", 7.49), ("2026-11-02", "Naan", 3.5)])
        # The second eggs line named no item; the product's link supplies it.
        self.assertEqual([r["item_name"] for r in got], ["Eggs", "", "Eggs", "Naan"])
        self.assertEqual(ask(be, "SELECT name, amount FROM refunds"), [{"name": "TV", "amount": 499.99}])

    def test_a_photo_replaced_by_the_official_receipt_is_not_counted_twice(self):
        be = household()
        receipt(be, "costco", "2026-10-03", [dict(EGGS, unit_price="6.99")], source="photo", total="6.99")
        receipt(be, "costco", "2026-10-03", [dict(EGGS, unit_price="6.99")], rid="A1")
        self.assertEqual(ask(be, "SELECT COUNT(*) AS n FROM Lines"), [{"n": 2}])
        self.assertEqual(ask(be, "SELECT source, amount FROM purchases"), [{"source": "import", "amount": 6.99}])
        self.assertEqual(ask(be, "SELECT COUNT(*) AS n FROM trips"), [{"n": 1}])

    def test_a_purchase_someone_only_mentioned_counts_without_a_price(self):
        be = household()
        say(be, [{"op": "saw", "item": "naan", "kind": "bought"}], by="sam", raw="picked up naan")
        self.assertEqual(ask(be, "SELECT date, item, source, amount FROM purchases"),
                         [{"date": "2026-11-11", "item": "naan", "source": "said", "amount": None}])

    def test_requests_and_what_was_said(self):
        be = household()
        say(be, [{"op": "add", "item": "eggs", "qty": "two"}, {"op": "add", "text": "Birthday candles"},
                 {"op": "saw", "item": "ketchup", "kind": "out"}], by="sam", raw="eggs x2, candles, no ketchup")
        receipt(be, "costco", "2026-11-11", [dict(EGGS, unit_price="6.99")], rid="A1")
        got = ask(be, "SELECT name, qty, by, status, closed_ref FROM requests ORDER BY name")
        self.assertEqual(got, [
            {"name": "Birthday candles", "qty": "", "by": "sam", "status": "open", "closed_ref": ""},
            {"name": "Eggs", "qty": "two", "by": "sam", "status": "done", "closed_ref": "costco:A1"}])
        said = ask(be, "SELECT kind, item_name FROM said WHERE by = 'sam' AND raw LIKE '%ketchup%' ORDER BY kind")
        self.assertEqual([r["kind"] for r in said], ["buy", "buy", "out"])

    def test_dates_are_the_households_not_utc(self):
        be = household()
        late = datetime(2026, 11, 8, 22, 30, tzinfo=timezone(timedelta(hours=-8)))
        say(be, [{"op": "add", "text": "Beer"}], now=late)
        # Written by an agent whose clock is in UTC: already the 9th there.
        be.tabs["List"][-1][be.tabs["List"][0].index("at")] = "2026-11-09T06:30:00+00:00"
        self.assertEqual(ask(be, "SELECT date, today() AS today FROM requests"),
                         [{"date": "2026-11-08", "today": NOW.date().isoformat()}])

    def test_prices_and_stock_agree_with_the_brief_and_pw_item(self):
        be = shopped()
        deal = dict(price="5.99", regular_price="7.49", sale="instant_savings", availability="in_stock",
                    sale_ends="2026-11-20", checked_at="2026-11-11T08:00:00-08:00")
        offer(be, "costco:111", sale_scope="warehouse", **deal)
        offer(be, "costco:222", sale_scope="online", **deal)
        got = ask(be, "SELECT name, price, deal, last_paid, times_bought, checked FROM prices ORDER BY name")
        self.assertEqual(got, [
            {"name": "KS EGGS", "price": 5.99, "deal": "yes", "last_paid": 7.49, "times_bought": 2,
             "checked": "2026-11-11"},
            {"name": "TV", "price": 5.99, "deal": "", "last_paid": 499.99, "times_bought": 1,
             "checked": "2026-11-11"}])
        self.assertEqual(ask(be, "SELECT COUNT(*) AS n FROM prices WHERE name LIKE '%REDEMP%'"), [{"n": 0}])
        say(be, [{"op": "saw", "item": "eggs", "kind": "low"}])
        self.assertEqual(ask(be, "SELECT stock, last_bought, days_since FROM stock WHERE item = 'eggs'"),
                         [{"stock": "low", "last_bought": "2026-10-20", "days_since": 22}])

    def test_numbers_are_numbers_and_empty_numbers_are_null(self):
        be = household()
        receipt(be, "grocer", "2026-11-02", [{"text": "Naan", "unit_price": "3.50"}, {"text": "Bag"}],
                source="photo")
        got = ask(be, "SELECT text, qty, unit_price, typeof(unit_price) AS t FROM Lines ORDER BY n")
        self.assertEqual(got, [{"text": "Naan", "qty": None, "unit_price": 3.5, "t": "real"},
                               {"text": "Bag", "qty": None, "unit_price": None, "t": "null"}])
        self.assertEqual(ask(be, "SELECT AVG(unit_price) AS a FROM Lines"), [{"a": 3.5}])

    def test_it_cannot_write(self):
        be = shopped()
        before = copy.deepcopy(be.tabs)
        for sql in ("DELETE FROM List", "UPDATE Items SET name = 'x'", "DROP VIEW trips",
                    "INSERT INTO Log (id) VALUES ('x')", "CREATE TABLE t (a)"):
            with self.assertRaises(PwError) as e:
                query.run(book(be), [sql])
            self.assertIn("only reads", str(e.exception))
        self.assertEqual(be.tabs, before)
        self.assertEqual(be.write_calls, query.run(book(be), ["SELECT 1"]) and be.write_calls)

    def test_mistakes_point_at_the_schema(self):
        be = household()
        with self.assertRaises(PwError) as e:
            query.run(book(be), ["SELECT total FROM trips"])
        self.assertIn("no such column: total", str(e.exception))
        self.assertIn("pw schema", str(e.exception))
        with self.assertRaises(PwError) as e:
            query.run(book(be), ["SELECT 1; SELECT 2"])
        self.assertIn("separate arguments", str(e.exception))

    def test_long_results_are_cut_and_say_so(self):
        be = household()
        got = query.run(book(be), ["SELECT item FROM Items ORDER BY item", "SELECT COUNT(*) FROM Items"], limit=2)
        self.assertEqual(got[0]["rows"], [["cereal"], ["eggs"]])
        self.assertEqual(got[0]["not_shown"], 4)
        self.assertIn("6 rows matched", got[0]["note"])
        self.assertEqual(got[1], {"columns": ["COUNT(*)"], "rows": [[6]]})

    def test_schema_names_every_table_says_what_is_on_record_and_its_examples_run(self):
        be = shopped()
        text = query.schema(book(be))
        for name in (*query.VIEWS, *query.TABLES, *query.RAW_TABS):
            self.assertIn(f"\n{name} (", text.replace("\n  ", "\n"))
        self.assertIn("costco: 2 trips, 2026-10-03 to 2026-10-20, all imported from the store.", text)
        self.assertIn("grocer: 1 trip, 2026-11-02 to 2026-11-02, from photos or typed in only", text)
        self.assertNotIn("v_Items", text)
        for question, sql in query.EXAMPLES:
            self.assertIn(sql, text)
            query.run(book(be), [sql])  # raises if an example has drifted from the views
        raised = ask(be, dict(query.EXAMPLES)["Which things cost more the last time than the time before?"])
        self.assertEqual([(r["name"], r["unit_price"], r["before"]) for r in raised], [("KS EGGS", 7.49, 6.99)])

    def test_trips_use_the_printed_total_when_there_is_one(self):
        be = shopped()                                    # the grocer photo printed 3.85; costco printed nothing
        got = ask(be, "SELECT store, printed_total, lines_total FROM trips ORDER BY date")
        self.assertEqual(got, [{"store": "costco", "printed_total": None, "lines_total": 513.97},
                               {"store": "costco", "printed_total": None, "lines_total": -491.9},
                               {"store": "grocer", "printed_total": 3.85, "lines_total": 3.5}])
        totals = query.show(book(be), "trips")["totals"]
        self.assertEqual(totals, {"trips": 3, "spent": 25.92, "trips_before_tax": 2})


class ShowTests(unittest.TestCase):
    """`pw purchases`, `pw trips`, `pw requests`, `pw said`, `pw prices`."""

    def test_purchases_filter_and_total_over_every_match(self):
        be = shopped()
        got = query.show(book(be), "purchases", item="Eggs", limit=1)
        self.assertEqual(got["covers"], {"item": "eggs"})
        self.assertEqual(got["totals"], {"purchases": 2, "spent": 21.47, "first": "2026-10-03", "last": "2026-10-20"})
        self.assertEqual(got["rows"], [{"date": "2026-10-20", "store": "costco", "name": "KS EGGS", "item": "eggs",
                                        "qty": 1, "unit_price": 7.49, "amount": 7.49, "source": "import"}])
        self.assertEqual(got["not_shown"], 1)
        window = query.show(book(be), "purchases", since="2026-10-10", until="2026-10-31")
        self.assertEqual((window["totals"]["purchases"], window["covers"]),
                         (1, {"since": "2026-10-10", "until": "2026-10-31"}))
        self.assertEqual(query.show(book(be), "purchases", days=10)["totals"]["purchases"], 1)   # the naan
        self.assertEqual(query.show(book(be), "purchases", text="tv")["totals"]["spent"], 499.99)

    def test_adding_up_by_item_store_or_month(self):
        be = shopped()
        by_item = query.show(book(be), "purchases", by="item")["rows"]
        self.assertEqual([(r["item"], r["purchases"], r["spent"]) for r in by_item],
                         [("Eggs", 2, 21.47), ("Naan", 1, 3.5), ("TV", 1, 499.99)])
        self.assertEqual(by_item[0]["average_price"], 7.24)
        by_month = query.show(book(be), "trips", by="month")["rows"]
        self.assertEqual([(r["month"], r["trips"], r["spent"]) for r in by_month],
                         [("2026-11", 1, 3.85), ("2026-10", 2, 22.07)])
        with self.assertRaises(PwError) as e:
            query.show(book(be), "trips", by="item")
        self.assertIn("month", str(e.exception))

    def test_requests_and_said(self):
        be = household()
        say(be, [{"op": "add", "item": "eggs", "store": "costco"}, {"op": "add", "text": "Beer"},
                 {"op": "saw", "item": "ketchup", "kind": "out"}], by="sam", raw="eggs from costco, beer, no ketchup")
        say(be, [{"op": "add", "item": "naan"}, {"op": "cancel", "text": "Beer"}], by="alex", raw="naan, skip beer")
        mine = query.show(book(be), "requests", person="Sam")
        self.assertEqual(mine["totals"], {"requests": 2, "open": 1, "done": 0, "cancelled": 1, "expired": 0})
        self.assertEqual({r["name"] for r in query.show(book(be), "requests", status="open")["rows"]},
                         {"Eggs", "Naan"})
        self.assertEqual([r["name"] for r in query.show(book(be), "requests", store="costco")["rows"]], ["Eggs"])
        said = query.show(book(be), "said", text="ketchup")
        self.assertEqual([(r["by"], r["kind"]) for r in said["rows"] if r["kind"] == "out"], [("sam", "out")])
        self.assertEqual(query.show(book(be), "said", by="person")["rows"][0]["person"], "sam")

    def test_a_typo_is_an_error_not_an_empty_answer(self):
        be = shopped()
        for bad, word in (({"item": "egs"}, "No item"), ({"store": "cosco"}, "Unknown store"),
                          ({"since": "last week"}, "date like")):
            with self.assertRaises(PwError) as e:
                query.show(book(be), "purchases", **bad)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(PwError):
            query.show(book(be), "purchases", status="open")

    def test_prices_and_deals(self):
        be = shopped()
        offer(be, "costco:111", price="5.99", regular_price="7.49", sale="instant_savings",
              sale_scope="warehouse", availability="in_stock", sale_ends="2026-11-20")
        got = query.show(book(be), "prices", deals=True)
        self.assertEqual(got["totals"], {"products": 1, "deals": 1})
        self.assertEqual((got["rows"][0]["name"], got["rows"][0]["price"], got["rows"][0]["last_paid"]),
                         ("KS EGGS", 5.99, 7.49))


class EmptyTests(unittest.TestCase):
    def test_an_empty_sheet_still_answers(self):
        from pantry_wizard import migrate
        from pantry_wizard.backend import MemoryBackend
        be = MemoryBackend()
        migrate.init(be, "America/Los_Angeles", "USD")
        self.assertEqual(ask(be, "SELECT COUNT(*) AS n FROM purchases"), [{"n": 0}])
        self.assertIn("No receipts are recorded.", query.schema(book(be)))
        self.assertEqual(query.show(book(be), "trips")["totals"], {"trips": 0})


if __name__ == "__main__":
    unittest.main()
