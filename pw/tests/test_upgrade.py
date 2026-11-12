"""Bringing a 2.0 or 2.1 sheet up to this pw in place."""

import unittest

from helpers import book, household, receipt, rows, say
from pantry_wizard import costco_bridge, migrate, ops
from pantry_wizard.cli import doctor
from pantry_wizard.schema import SCHEMA_VERSION, TABS
from pantry_wizard.store import PwError

OLD_SYNC = ["store", "status", "location", "membership_fingerprint", "owner", "lease_until",
            "history_from", "history_through", "last_sync_at", "last_summary"]


def sheet_on_2_0():
    """A household as pw 2.0.0 left it: one importer row per store, no account on receipts."""
    be = household()
    receipt(be, "costco", "2026-10-13", [{"sku": "1025795", "text": "KS 5DZ EGGS"}], rid="B1")
    for r in be.tabs["Config"]:
        if r[0] == "schema_version":
            r[1] = "2.0"
    be.tabs["Receipts"] = [r[:-3] for r in be.tabs["Receipts"]]
    assert "account" not in be.tabs["Receipts"][0]
    del be.tabs["PriceHistory"]
    be.tabs["Sync"] = [OLD_SYNC, ["costco", "active", "663 Example City", "abc", "grok-bot",
                                  "2026-11-12T20:00:00-08:00", "2023-09-26", "2026-10-13",
                                  "2026-11-11T08:20:00-08:00", "No new receipts."]]
    _strip_2_3(be)
    return be


def sheet_on_2_1():
    """A household as pw 2.1.0 left it: accounts, but no tax or savings and no price history."""
    be = household()
    receipt(be, "costco", "2026-10-13", [{"sku": "1025795", "text": "KS 5DZ EGGS"}], rid="B1")
    costco_bridge.lease(book(be), "costco", "grok-bot", location="663 Example City")
    for r in be.tabs["Config"]:
        if r[0] == "schema_version":
            r[1] = "2.1"
    be.tabs["Receipts"] = [r[:-2] for r in be.tabs["Receipts"]]
    assert be.tabs["Receipts"][0][-1] == "account"
    del be.tabs["PriceHistory"]
    _strip_2_3(be)
    return be


def _strip_2_3(be):
    """Remove what 2.3 added: the List `any_of` column and the Notes tab."""
    be.tabs["List"] = [r[:-1] for r in be.tabs["List"]]
    assert "any_of" not in be.tabs["List"][0]
    del be.tabs["Notes"]


class UpgradeTests(unittest.TestCase):
    def test_a_2_1_sheet_gains_the_new_columns_and_tab_and_keeps_its_accounts(self):
        be = sheet_on_2_1()
        with self.assertRaises(PwError) as err:
            say(be, [{"op": "add", "item": "eggs"}])
        self.assertIn("pw upgrade", str(err.exception))
        sync = [list(r) for r in be.tabs["Sync"]]
        report = migrate.upgrade(be)
        self.assertEqual((report["was"], report["schema"]), ("2.1", SCHEMA_VERSION))
        self.assertEqual(report["changed"], [
            "List: + any_of",
            "Notes: + " + ", ".join(TABS["Notes"].columns),
            "Receipts: + tax, savings",
            "PriceHistory: + " + ", ".join(TABS["PriceHistory"].columns)])
        self.assertEqual(be.tabs["Sync"], sync)
        self.assertEqual(be.tabs["Notes"], [list(TABS["Notes"].columns)])
        self.assertEqual(be.tabs["PriceHistory"], [list(TABS["PriceHistory"].columns)])
        self.assertEqual(doctor(book(be)), [])
        self.assertEqual(rows(be, "Receipts")[0]["receipt"], "costco:B1")
        self.assertEqual(costco_bridge.snapshot(book(be), "costco")["receipts_missing_totals"], ["B1"])

    def test_an_older_sheet_is_refused_until_it_is_upgraded(self):
        be = sheet_on_2_0()
        with self.assertRaises(PwError) as err:
            say(be, [{"op": "add", "item": "eggs"}])
        self.assertIn("pw upgrade", str(err.exception))
        self.assertIn("pw upgrade", doctor(book(be))[0])
        with self.assertRaises(PwError) as err:
            costco_bridge.snapshot(book(be), "costco")
        self.assertIn("pw upgrade", str(err.exception))

    def test_upgrade_adds_columns_and_keeps_every_row(self):
        be = sheet_on_2_0()
        before = {t: [list(r) for r in be.tabs[t]] for t in ("Items", "Lines", "Products", "Sources")}
        report = migrate.upgrade(be)
        self.assertEqual((report["was"], report["schema"]), ("2.0", SCHEMA_VERSION))
        self.assertEqual({t: be.tabs[t] for t in before}, before)
        for name, tab in TABS.items():
            self.assertEqual(set(tab.columns) - set(be.tabs[name][0]), set(), name)
        self.assertEqual(rows(be, "Receipts")[0]["receipt"], "costco:B1")

        sync = rows(be, "Sync")
        self.assertEqual(len(sync), 1)
        self.assertEqual((sync[0]["sync"], sync[0]["account"], sync[0]["prices"]), ("costco:primary", "primary", "yes"))
        self.assertEqual((sync[0]["owner"], sync[0]["membership_fingerprint"], sync[0]["history_from"]),
                         ("grok-bot", "abc", "2023-09-26"))

        # Everything works again, with no flags.
        self.assertEqual(doctor(book(be)), [])
        say(be, [{"op": "add", "item": "eggs"}])
        self.assertEqual(len(ops.open_list(book(be))["buy"]), 1)
        snap = costco_bridge.snapshot(book(be), "costco")
        self.assertEqual(snap["integrations"][0]["owner"], "grok-bot")
        self.assertEqual(costco_bridge.lease(book(be), "costco", "grok-bot")["sync"], "costco:primary")

    def test_upgrading_twice_or_upgrading_the_wrong_sheet_changes_nothing(self):
        be = sheet_on_2_0()
        migrate.upgrade(be)
        after = {t: [list(r) for r in rows_] for t, rows_ in be.tabs.items()}
        self.assertEqual(migrate.upgrade(be)["changed"], [])
        self.assertEqual(be.tabs, after)
        old = household()
        for r in old.tabs["Config"]:
            if r[0] == "schema_version":
                r[1] = "0.3"
        with self.assertRaises(PwError):
            migrate.upgrade(old)


if __name__ == "__main__":
    unittest.main()
