"""SheetsBackend against a stand-in for the Google client.

The stand-in keeps a grid per tab and understands the handful of calls and A1
ranges the backend makes. It checks that the backend's requests are well
formed and add up to the right cells. It is not Google: quotas, auth and
Google's own reading of a range are only tested by running `pw doctor`
against a real sheet.
"""

import re
import unittest

from helpers import NOW
from pantry_wizard import migrate, ops
from pantry_wizard.backend import SheetsBackend
from pantry_wizard.schema import TABS
from pantry_wizard.store import PwError, Workbook

RANGE = re.compile(r"^'(?P<tab>[^']+)'(!(?P<col>[A-Z]+)(?P<row>\d+)?(:(?P<end>[A-Z]+))?)?$")


def _index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSheets:
    """`build("sheets", "v4").spreadsheets()`, as far as pw uses it."""

    def __init__(self):
        self.grids: dict[str, list[list[str]]] = {"Sheet1": []}
        self.calls: list[str] = []
        self.fail_after_append = 0   # answer this many appends with a 503 after writing them

    def values(self):
        return self

    def _parse(self, text):
        m = RANGE.match(text)
        assert m, f"not a range pw should send: {text!r}"
        if m["tab"] not in self.grids:
            raise RuntimeError(f"Unable to parse range: {text}")
        return m["tab"], (_index(m["col"]) if m["col"] else 0), (int(m["row"]) if m["row"] else 1), m["end"]

    def _put(self, tab, col, row, values):
        grid = self.grids[tab]
        for r, line in enumerate(values, start=row - 1):
            while len(grid) <= r:
                grid.append([])
            grid[r].extend([""] * (col + len(line) - len(grid[r])))
            for c, value in enumerate(line, start=col):
                assert isinstance(value, str), "RAW values must be strings"
                grid[r][c] = value

    def _rows(self, tab):
        """What values.get returns: no trailing empty cells or rows."""
        out = []
        for line in self.grids[tab]:
            line = list(line)
            while line and line[-1] == "":
                line.pop()
            out.append(line)
        while out and not out[-1]:
            out.pop()
        return out

    def get(self, spreadsheetId, fields):
        return _Call(lambda: {"sheets": [{"properties": {"title": t}} for t in self.grids]})

    def batchUpdate(self, spreadsheetId, body):
        def run():
            self.calls.append("batchUpdate")
            if "requests" in body:
                for request in body["requests"]:
                    self.grids[request["addSheet"]["properties"]["title"]] = []
                return {}
            assert body["valueInputOption"] == "RAW"
            for data in body["data"]:
                tab, col, row, _ = self._parse(data["range"])
                self._put(tab, col, row, data["values"])
            return {}
        return _Call(run)

    def batchGet(self, spreadsheetId, ranges):
        def run():
            self.calls.append("batchGet")
            out = []
            for r in ranges:
                tab, _col, _row, end = self._parse(r)
                rows = self._rows(tab)
                out.append({"range": r, "values": [line[:1] for line in rows] if end == "A" else rows})
            return {"valueRanges": out}
        return _Call(run)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        def run():
            self.calls.append("append")
            assert (valueInputOption, insertDataOption) == ("RAW", "INSERT_ROWS")
            tab, col, _, _ = self._parse(range)
            self._put(tab, col, len(self._rows(tab)) + 1, body["values"])
            if self.fail_after_append:
                self.fail_after_append -= 1
                error = RuntimeError("backend error")
                error.resp = type("Resp", (), {"status": 503})()
                raise error
            return {}
        return _Call(run)

    def update(self, spreadsheetId, range, valueInputOption, body):
        def run():
            self.calls.append("update")
            assert valueInputOption == "RAW"
            tab, col, row, _ = self._parse(range)
            self._put(tab, col, row, body["values"])
            return {}
        return _Call(run)

    def clear(self, spreadsheetId, range):
        def run():
            self.calls.append("clear")
            tab, col, row, end = self._parse(range)
            assert end, "a clear must be bounded"
            for line in self.grids[tab][row - 1:]:
                for c in range_(col, min(len(line), _index(end) + 1)):
                    line[c] = ""
            return {}
        return _Call(run)


range_ = range


class SheetsBackendTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSheets()
        self.be = SheetsBackend("sheet-id", api=self.api)
        migrate.init(self.be, "America/Los_Angeles", "USD")

    def book(self):
        return Workbook(self.be, agent="test-bot", now=NOW)

    def test_init_adds_every_tab_with_its_headers(self):
        for name, tab in TABS.items():
            self.assertEqual(self.api.grids[name][0], list(tab.columns))
        self.assertEqual(self.api.grids["Config"][1][:2], ["schema_version", "2.0"])

    def test_a_message_is_one_batch_read_then_appends_and_one_cell_update(self):
        ops.apply(self.book(), [{"op": "new_item", "name": "Eggs", "new_source": True},
                                {"op": "add", "item": "eggs", "qty": "2"}], by="alex")
        self.api.calls.clear()
        ops.apply(self.book(), [{"op": "done", "item": "eggs"},
                                {"op": "saw", "item": "eggs", "kind": "plenty"}], by="alex")
        # Read, check the row to change is still where it was, then write.
        self.assertEqual(self.api.calls, ["batchGet", "batchGet", "append", "batchUpdate"])
        row = dict(zip(self.api.grids["List"][0], self.api.grids["List"][1]))
        self.assertEqual((row["status"], row["closed_by"], row["qty"]), ("done", "alex", "2"))
        self.assertEqual(ops.open_list(self.book())["buy"], [])

    def test_a_cell_lands_on_the_row_it_was_read_from(self):
        ops.apply(self.book(), [{"op": "new_item", "name": n, "new_source": True}
                                for n in ("Eggs", "Milk", "Naan")], by="alex")
        ops.apply(self.book(), [{"op": "set_item", "item": "milk", "rule": "on_sale"}], by="alex")
        items = [dict(zip(self.api.grids["Items"][0], r)) for r in self.api.grids["Items"][1:]]
        self.assertEqual([(i["item"], i.get("rule", "")) for i in items],
                         [("eggs", ""), ("milk", "on_sale"), ("naan", "")])

    def test_a_sheet_sorted_between_read_and_write_is_still_written_by_key(self):
        ops.apply(self.book(), [{"op": "new_item", "name": n, "new_source": True}
                                for n in ("Eggs", "Milk", "Naan")], by="alex")
        wb = self.book().load("Config", "Sources", "List", "Items", "Log", "Questions", "Products")
        grid = self.api.grids["Items"]
        grid[1:] = sorted(grid[1:], reverse=True)          # someone sorts the tab in the browser
        ops.apply(wb, [{"op": "set_item", "item": "milk", "rule": "on_sale"}], by="alex")
        items = {r[0]: dict(zip(grid[0], r)) for r in grid[1:]}
        self.assertEqual([k for k, i in items.items() if i.get("rule")], ["milk"])

    def test_a_row_deleted_between_read_and_write_stops_the_whole_commit(self):
        ops.apply(self.book(), [{"op": "new_item", "name": "Milk", "new_source": True}], by="alex")
        wb = self.book().load("Config", "Sources", "List", "Items", "Log", "Questions", "Products")
        del self.api.grids["Items"][1]
        logged = len(self.api.grids["Log"])
        with self.assertRaises(PwError):
            ops.apply(wb, [{"op": "set_item", "item": "milk", "rule": "on_sale"}], by="alex")
        self.assertEqual(len(self.api.grids["Log"]), logged)   # the audit row was not written either

    def test_an_append_that_landed_before_a_503_is_not_sent_twice(self):
        import pantry_wizard.backend as backend
        self.addCleanup(setattr, backend.time, "sleep", backend.time.sleep)
        backend.time.sleep = lambda seconds: None
        self.api.fail_after_append = 1
        ops.apply(self.book(), [{"op": "new_item", "name": "Milk", "new_source": True}], by="alex")
        self.assertEqual([r[0] for r in self.api.grids["Items"]], ["item", "milk"])

    def test_replacing_a_tab_with_fewer_rows_leaves_nothing_behind(self):
        wb = self.book().load("Config", "Offers")
        wb.replace("Offers", [{"product": f"costco:{n}", "price": "1"} for n in range(5)])
        wb.commit()
        wb = self.book().load("Config", "Offers")
        wb.replace("Offers", [{"product": "costco:9", "price": "2"}])
        wb.commit()
        self.assertEqual([r[0] for r in self.be.read(["Offers"])["Offers"]], ["product", "costco:9"])

    def test_a_missing_tab_reads_as_empty_and_a_wrong_version_refuses_to_write(self):
        del self.api.grids["Questions"]
        self.assertEqual(self.be.read(["Config", "Questions"])["Questions"], [])
        self.api.grids["Config"][1][1] = "1.0"
        with self.assertRaises(PwError):
            ops.apply(self.book(), [{"op": "new_item", "name": "Eggs", "new_source": True}], by="alex")
        self.assertEqual(len(self.api.grids["Items"]), 1)


if __name__ == "__main__":
    unittest.main()
