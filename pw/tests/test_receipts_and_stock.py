import unittest
from datetime import timedelta

from helpers import NOW, book, household, receipt, rows, say
from pantry_wizard import stats


def egg_history(be, dates, store="costco"):
    for n, d in enumerate(dates):
        receipt(be, store, d, [{"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99"}],
                rid=f"R{n}")
    say(be, [{"op": "link", "product": "costco:1025795", "item": "eggs"}])


def stock(be, item, now=NOW):
    wb = book(be, now)
    return stats.item_stats(wb, now.date())[item]


class ReceiptTests(unittest.TestCase):
    def test_a_receipt_closes_what_it_covers_and_says_so(self):
        be = household()
        egg_history(be, ["2026-09-01"])
        say(be, [{"op": "add", "item": "eggs"}, {"op": "add", "item": "ketchup"}], by="sam")
        got = receipt(be, "costco", "2026-11-11",
                      [{"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99"}], rid="R9")
        self.assertEqual(got["closed"], ["Eggs"])
        open_rows = [r["text"] for r in rows(be, "List") if r["status"] == "open"]
        self.assertEqual(open_rows, ["Ketchup"])
        closed = [r for r in rows(be, "List") if r["status"] == "done"][0]
        self.assertEqual(closed["closed_ref"], "costco:R9")

    def test_the_same_receipt_twice_is_a_no_op(self):
        be = household()
        lines = [{"sku": "1", "text": "MILK", "unit_price": "4"}]
        receipt(be, "costco", "2026-11-01", lines, rid="R1")
        again = receipt(be, "costco", "2026-11-01", lines, rid="R1")
        self.assertEqual(again["skipped"], "already recorded")
        self.assertEqual(len(rows(be, "Lines")), 1)

    def test_photo_receipt_is_the_record_until_the_official_one_arrives(self):
        """A trip on another member's card: logged from a photo, counted once."""
        be = household()
        egg_history(be, ["2026-09-01", "2026-09-20", "2026-10-10"])
        photo = receipt(be, "costco", "2026-10-27",
                        [{"text": "KS 5DZ EGGS", "item": "eggs", "unit_price": "12.99"}],
                        source="photo", rid="21066300000000000000001", by="sam")
        self.assertEqual(photo["receipt"], "photo:costco:21066300000000000000001")
        self.assertEqual(stock(be, "eggs")["last_bought"], "2026-10-27")
        buys = stock(be, "eggs")["buys_12mo"]

        official = receipt(be, "costco", "2026-10-27",
                           [{"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99"}],
                           rid="21066300000000000000001")
        self.assertEqual(official["superseded"], ["photo:costco:21066300000000000000001"])
        self.assertEqual(stock(be, "eggs")["buys_12mo"], buys)  # not double counted
        status = {r["receipt"]: r["status"] for r in rows(be, "Receipts")}
        self.assertEqual(status["photo:costco:21066300000000000000001"], "superseded")
        self.assertEqual(len([l for l in rows(be, "Lines") if "photo:" in l["line"]]), 1)  # evidence kept

    def test_a_misread_barcode_still_pairs_with_the_official_receipt(self):
        be = household()
        lines = [{"sku": str(n), "text": f"THING {n}", "unit_price": "5"} for n in range(5)]
        receipt(be, "costco", "2026-10-27", lines[:4], source="photo", rid="2106630000l", by="sam")
        official = receipt(be, "costco", "2026-10-27", lines, rid="21066300001")
        self.assertEqual(official["superseded"], ["photo:costco:2106630000l"])
        # A different trip on the same day shares almost nothing and stays.
        other = receipt(be, "costco", "2026-10-27", [{"sku": "77", "text": "PIZZA", "unit_price": "9.95"},
                                                     {"sku": "0", "text": "THING 0", "unit_price": "5"}],
                        source="photo", rid="X", by="sam")
        self.assertEqual(other["status"], "recorded")

    def test_a_photo_of_a_trip_already_recorded_adds_nothing(self):
        be = household()
        lines = [{"text": "TANDOORI NAAN", "item": "naan", "unit_price": "3.49"}]
        first = receipt(be, "grocer", "2026-10-01", lines, source="photo", total="61.20", by="sam")
        again = receipt(be, "grocer", "2026-10-01", lines + [{"text": "BRIE", "unit_price": "5.99"}],
                        source="photo", total="61.2", by="alex")
        self.assertEqual((again["skipped"], again["receipt"]), ("already recorded", first["receipt"]))
        late = receipt(be, "costco", "2026-10-27", [{"sku": "1", "text": "MILK"}], source="photo", rid="R1")
        self.assertEqual(late["status"], "recorded")
        receipt(be, "costco", "2026-10-27", [{"sku": "1", "text": "MILK"}], rid="R1")
        photo_after = receipt(be, "costco", "2026-10-27", [{"sku": "1", "text": "MILK"}], source="photo")
        self.assertEqual(photo_after["status"], "superseded")   # kept as evidence, not counted

    def test_two_receipts_without_a_total_on_one_day_are_two_receipts(self):
        be = household()
        a = receipt(be, "grocer", "2026-10-01", [{"text": "NAAN", "item": "naan"}], source="photo")
        b = receipt(be, "grocer", "2026-10-01", [{"text": "KETCHUP", "item": "ketchup"}], source="photo")
        self.assertNotEqual(a["receipt"], b["receipt"])
        self.assertEqual(len(rows(be, "Receipts")), 2)

    def test_a_receipt_that_posts_late_does_not_close_a_newer_request(self):
        be = household()
        egg_history(be, ["2026-09-01"])
        say(be, [{"op": "add", "item": "eggs"}], now=NOW - timedelta(days=2))   # asked on Nov 9
        got = receipt(be, "costco", "2026-11-03",                                # bought on Nov 3
                      [{"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99"}], rid="LATE")
        self.assertEqual(got["closed"], [])
        self.assertEqual([r["status"] for r in rows(be, "List")], ["open"])

    def test_a_trip_uses_up_the_reminders_for_that_store(self):
        be = household()
        say(be, [{"op": "remind", "text": "Use the rewards check", "store": "costco"},
                 {"op": "remind", "text": "Return the library books"}], now=NOW - timedelta(days=1))
        got = receipt(be, "costco", "2026-11-11", [{"sku": "1", "text": "MILK"}], rid="R1")
        self.assertEqual(got["closed"], ["Use the rewards check"])
        self.assertEqual([r["text"] for r in rows(be, "List") if r["status"] == "open"],
                         ["Return the library books"])

    def test_naming_the_item_on_a_sku_line_links_the_product_once(self):
        be = household()
        got = receipt(be, "costco", "2026-10-13",
                      [{"sku": "1025795", "text": "KS 5DZ EGGS", "item": "eggs", "unit_price": "12.99"}],
                      source="photo", rid="R1", by="sam")
        self.assertEqual(got["linked_products"], ["costco:1025795"])
        self.assertEqual(rows(be, "Products")[0]["item"], "eggs")
        self.assertEqual(rows(be, "Lines")[0]["item"], "")        # the product says it
        audit = rows(be, "Log")[-1]
        self.assertEqual((audit["kind"], audit["by"], audit["value"]), ("set", "sam", "costco:1025795 -> eggs"))
        # A later line that names another item does not move the link.
        receipt(be, "costco", "2026-10-20",
                [{"sku": "1025795", "text": "KS 5DZ EGGS", "item": "ketchup"}], source="photo", rid="R2")
        self.assertEqual(rows(be, "Products")[0]["item"], "eggs")
        self.assertEqual(rows(be, "Lines")[1]["item"], "ketchup")

    def test_a_write_that_stops_anywhere_is_finished_by_sending_the_receipt_again(self):
        """Google can fail between any two calls of a commit."""
        class Stop(Exception):
            pass

        def scenario(fail_at=None):
            be = household()
            egg_history(be, ["2026-09-01"])
            receipt(be, "costco", "2026-11-10", [{"sku": "1025795", "text": "KS 5DZ EGGS"},
                                                 {"sku": "500", "text": "SPINDRIFT"}],
                    source="photo", rid="PH0T0", by="sam")
            say(be, [{"op": "add", "item": "eggs"}, {"op": "add", "item": "ketchup"}],
                now=NOW - timedelta(days=2))
            send = lambda: receipt(be, "costco", "2026-11-10", [
                {"sku": "1025795", "text": "KS 5DZ EGGS", "unit_price": "12.99"},
                {"sku": "500", "text": "SPINDRIFT", "unit_price": "9.99"},
                {"sku": "77", "text": "KETCHUP", "item": "ketchup", "unit_price": "8.49"}], rid="R9")
            calls = 0
            if fail_at is not None:
                real = be.commit

                def failing(appends, cells, replaces):
                    # One backend call at a time, the way Google sees them.
                    nonlocal calls
                    for step in ([({t: r}, [], {}) for t, r in appends.items()]
                                 + ([({}, cells, {})] if cells else [])):
                        if calls == fail_at:
                            raise Stop()
                        calls += 1
                        real(*step)
                be.commit = failing
                try:
                    send()
                    return None, calls, None      # the commit has fewer calls than fail_at
                except Stop:
                    be.commit = real
            got = send()
            st = stock(be, "eggs")
            return got.get("status"), calls, {
                "receipts": sorted((r["receipt"], r["status"], r["lines"]) for r in rows(be, "Receipts")),
                "lines": sorted(l["line"] for l in rows(be, "Lines")),
                "open": [r["text"] for r in rows(be, "List") if r["status"] == "open"],
                "eggs": (st["last_bought"], st["buys_12mo"]),
                "ketchup_linked": [p["item"] for p in rows(be, "Products") if p["sku"] == "77"],
            }

        _, _, clean = scenario()
        self.assertEqual(clean["open"], [])
        self.assertEqual(clean["eggs"], ("2026-11-10", 2))
        self.assertIn(("photo:costco:PH0T0", "superseded", "2"), clean["receipts"])
        stopped_at = []
        for fail_at in range(8):
            status, calls, state = scenario(fail_at)
            if state is None:
                break
            stopped_at.append(fail_at)
            self.assertEqual(status, "recorded", fail_at)   # never "already recorded" with work undone
            self.assertEqual(state, clean, fail_at)
        self.assertGreaterEqual(len(stopped_at), 4)

    def test_lines_without_a_sku_still_feed_the_rhythm(self):
        be = household()
        for d in ("2026-08-01", "2026-09-01", "2026-10-02"):
            receipt(be, "grocer", d, [{"text": "TANDOORI NAAN", "item": "naan", "unit_price": "3.49"}],
                    source="photo", total=d[-2:], by="sam")
        st = stock(be, "naan")
        self.assertEqual(st["usual_gap_days"], 31)
        self.assertEqual(len(rows(be, "Products")), 0)  # no catalogue for a store without SKUs

    def test_deposit_lines_never_count_as_purchases(self):
        be = household()
        receipt(be, "costco", "2026-11-01", [
            {"sku": "55", "text": "SPARKLING WATER", "unit_price": "9"},
            {"sku": "999", "text": "CA REDEMP VAL", "unit_price": "1.75", "kind": "deposit"}], rid="R1")
        product = {p["product"]: p for p in rows(be, "Products")}
        self.assertEqual(product["costco:999"]["kind"], "not_product")
        self.assertNotIn("costco:999", stats.product_stats(book(be), NOW.date()))


class StockWordTests(unittest.TestCase):
    def test_out_stays_out_until_something_is_bought(self):
        be = household()
        say(be, [{"op": "saw", "item": "ketchup", "kind": "out"}],
            raw="we're out of ketchup", now=NOW - timedelta(days=8))
        st = stock(be, "ketchup")
        self.assertEqual(st["stock"], "out")
        self.assertIn("we're out of ketchup", st["last_said"])
        receipt(be, "grocer", (NOW - timedelta(days=6)).date().isoformat(),
                [{"text": "KETCHUP", "item": "ketchup", "unit_price": "4"}], source="photo", total="4")
        self.assertEqual(stock(be, "ketchup")["stock"], "ok")

    def test_opening_the_last_one_is_low_not_out(self):
        be = household()
        say(be, [{"op": "saw", "item": "paper-towels", "kind": "last_one"}], raw="opened the last pack")
        self.assertEqual(stock(be, "paper-towels")["stock"], "low")

    def test_using_one_changes_nothing(self):
        be = household()
        say(be, [{"op": "saw", "item": "ketchup", "kind": "used"}], raw="I used a ketchup")
        self.assertEqual(stock(be, "ketchup")["stock"], "unknown")

    def test_rhythm_gives_ok_then_check_never_a_count(self):
        be = household()
        egg_history(be, ["2026-08-20", "2026-09-10", "2026-09-30", "2026-10-13"])
        st = stock(be, "eggs")
        self.assertEqual(st["usual_gap_days"], 20)
        self.assertEqual(st["days_since"], 29)
        self.assertEqual(st["stock"], "check")
        early = NOW.replace(month=10, day=20)
        self.assertEqual(stock(be, "eggs", early)["stock"], "ok")

    def test_an_old_out_or_low_becomes_a_reason_to_look_not_to_buy(self):
        be = household()
        say(be, [{"op": "saw", "item": "ketchup", "kind": "low"}], raw="ketchup is low",
            now=NOW - timedelta(days=61))
        st = stock(be, "ketchup")
        self.assertEqual(st["stock"], "check")
        self.assertIn("ketchup is low", st["last_said"])       # the words are still shown
        self.assertEqual(stock(be, "ketchup", NOW - timedelta(days=40))["stock"], "low")
        # With a slow rhythm it is believed for two of the usual gaps.
        egg_history(be, ["2026-03-01", "2026-05-01", "2026-07-01"])
        say(be, [{"op": "saw", "item": "eggs", "kind": "out"}], now=NOW - timedelta(days=100))
        self.assertEqual(stock(be, "eggs")["stock"], "out")

    def test_plenty_goes_stale(self):
        be = household()
        say(be, [{"op": "saw", "item": "paper-towels", "kind": "plenty"}], raw="we have tons")
        self.assertEqual(stock(be, "paper-towels")["stock"], "plenty")
        self.assertEqual(stock(be, "paper-towels", NOW + timedelta(days=45))["stock"], "unknown")

    def test_rebuild_writes_the_view(self):
        be = household()
        wb = book(be).load("Config")
        stats.rebuild_v_items(wb)
        wb.commit()
        self.assertEqual(len(rows(be, "v_Items")), 6)


if __name__ == "__main__":
    unittest.main()
