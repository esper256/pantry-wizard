"""Two fixes for Costco data: re-filing a receipt line to the right item (#49),
and netting leftover instant-savings discount lines imported before the fix (#50)."""

import datetime
import unittest

from helpers import book, household, say
from pantry_wizard import query, stats
from pantry_wizard.errors import PwError

TODAY = datetime.date(2026, 11, 12)


def _sale_receipt(be, rid="Z1"):
    """A sale as an import before costco-sync 0.4.2: the discount is a separate
    refund line under its own SKU, text `/<parent sku>`, not folded into the buy."""
    return say(be, [{"op": "receipt", "store": "costco", "date": "2026-11-10", "source": "import",
                     "receipt_id": rid, "total": "7.99", "lines": [
                         {"sku": "897980", "text": "OLIVE OIL", "unit_price": "10.49", "qty": "1", "item": "eggs"},
                         {"sku": "370318", "text": "/  897980", "unit_price": "2.50", "qty": "1", "kind": "refund"},
                     ]}])


class LeftoverDiscountTests(unittest.TestCase):
    def test_last_paid_is_net_of_the_leftover_discount(self):
        be = household()
        _sale_receipt(be)
        ps = stats.product_stats(book(be), TODAY)
        self.assertEqual(ps["costco:897980"]["last_paid"], "7.99")  # 10.49 - 2.50

    def test_the_purchases_view_nets_price_and_amount(self):
        be = household()
        _sale_receipt(be)
        out = query.show(book(be), "purchases", item="eggs")
        self.assertEqual([(r["unit_price"], r["amount"]) for r in out["rows"]], [(7.99, 7.99)])
        self.assertEqual(out["totals"]["spent"], 7.99)

    def test_the_refunds_view_leaves_out_the_discount_line(self):
        be = household()
        _sale_receipt(be)
        say(be, [{"op": "receipt", "store": "costco", "date": "2026-11-09", "source": "import",
                  "receipt_id": "R0", "total": "-5.00", "lines": [
                      {"sku": "999", "text": "RETURNED THING", "unit_price": "5.00", "qty": "1", "kind": "refund"}]}])
        names = query.run(book(be), ["SELECT name FROM refunds ORDER BY name"], 50)[0]["rows"]
        self.assertEqual(names, [["RETURNED THING"]])

    def test_the_trip_total_still_nets_the_discount_once(self):
        be = household()
        _sale_receipt(be)
        total = query.run(book(be), ["SELECT ROUND(SUM(lines_total), 2) FROM trips"], 10)[0]["rows"]
        self.assertEqual(total, [[7.99]])

    def test_a_purchase_with_no_discount_is_unchanged(self):
        be = household()
        say(be, [{"op": "receipt", "store": "costco", "date": "2026-11-11", "source": "import",
                  "receipt_id": "P1", "total": "5.00", "lines": [
                      {"sku": "555", "text": "BREAD", "unit_price": "5.00", "qty": "2", "item": "ketchup"}]}])
        out = query.show(book(be), "purchases", item="ketchup")
        self.assertEqual([(r["unit_price"], r["amount"]) for r in out["rows"]], [(5.0, 10.0)])


class RefileTests(unittest.TestCase):
    def test_a_line_is_refiled_to_a_different_item(self):
        be = household()
        say(be, [{"op": "receipt", "store": "grocer", "date": "2026-11-10", "source": "photo",
                  "lines": [{"text": "CHEESE", "unit_price": "6.00", "qty": "1", "item": "eggs"}]}])
        line = query.run(book(be), ["SELECT line FROM Lines WHERE kind = 'purchase'"], 5)[0]["rows"][0][0]
        say(be, [{"op": "new_item", "name": "Cheddar"}])
        result = say(be, [{"op": "refile", "line": line, "item": "cheddar"}])
        self.assertEqual(result[0]["item"], "cheddar")
        self.assertEqual(query.show(book(be), "purchases", item="cheddar")["totals"]["purchases"], 1)
        self.assertEqual(query.show(book(be), "purchases", item="eggs")["totals"]["purchases"], 0)

    def test_refile_to_nothing_falls_back_to_the_product_link(self):
        be = household()
        # First buy links product costco:42 to eggs.
        say(be, [{"op": "receipt", "store": "costco", "date": "2026-11-09", "source": "import",
                  "receipt_id": "Z8", "lines": [{"sku": "42", "text": "KS X", "unit_price": "3.00", "item": "eggs"}]}])
        # Same SKU bought again, but this line is filed (wrongly) under ketchup on the line itself,
        # which overrides the product link in every view.
        say(be, [{"op": "receipt", "store": "costco", "date": "2026-11-10", "source": "import",
                  "receipt_id": "Z9", "lines": [{"sku": "42", "text": "KS X", "unit_price": "3.00", "item": "ketchup"}]}])
        self.assertEqual(query.show(book(be), "purchases", item="ketchup")["totals"]["purchases"], 1)
        line = query.run(book(be), ["SELECT line FROM Lines WHERE receipt = 'costco:Z9'"], 5)[0]["rows"][0][0]
        say(be, [{"op": "refile", "line": line}])  # clear it, defer to the product link (eggs)
        self.assertEqual(query.show(book(be), "purchases", item="ketchup")["totals"]["purchases"], 0)
        self.assertEqual(query.show(book(be), "purchases", item="eggs")["totals"]["purchases"], 2)

    def test_refiling_a_missing_line_is_refused(self):
        be = household()
        with self.assertRaises(PwError):
            say(be, [{"op": "refile", "line": "nope", "item": "eggs"}])


if __name__ == "__main__":
    unittest.main()
