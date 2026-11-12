"""A small fictional household used by every test.

Alex and Sam shop at a warehouse store (called "costco" because the importer
bridge is Costco-shaped) and a grocer. Nothing here is real household data.
"""

from datetime import datetime, timedelta, timezone

from pantry_wizard import migrate, ops
from pantry_wizard.backend import MemoryBackend
from pantry_wizard.store import Workbook

TZ = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 11, 10, 45, tzinfo=TZ)


def book(backend, now=NOW):
    return Workbook(backend, agent="test-bot", now=now)


def household():
    """An initialised sheet with two people, two stores and a few items."""
    be = MemoryBackend()
    migrate.init(be, "America/Los_Angeles", "USD")
    ops.apply(book(be), [{"op": "new_item", "name": "Eggs", "stores": "costco",
                          "new_source": True}], by="alex")
    ops.apply(book(be), [
        {"op": "new_item", "name": "Ketchup", "new_source": True},
        {"op": "new_item", "name": "Cereal", "rule": "on_sale"},
        {"op": "new_item", "name": "Smash burgers", "rule": "when_in_stock"},
        {"op": "new_item", "name": "Naan", "stores": "grocer", "new_source": True},
        {"op": "new_item", "name": "Paper towels"},
    ], by="sam")
    return be


def say(be, ops_list, by="alex", now=NOW, **kw):
    return ops.apply(book(be, now), ops_list, by=by, **kw)


def receipt(be, store, date, lines, source="import", rid="", total="", by="alex", now=NOW):
    return say(be, [{"op": "receipt", "store": store, "date": date, "source": source,
                     "receipt_id": rid, "total": total, "lines": lines}], by=by, now=now)[0]


def offer(be, product, **fields):
    """Write an Offers row the way the importer bridge would."""
    wb = book(be).load("Config", "Offers")
    rows = {o["product"]: {k: v for k, v in o.items() if k != "_row"} for o in wb.t("Offers").rows}
    rows[product] = dict({"product": product}, **fields)
    wb.replace("Offers", list(rows.values()))
    wb.commit()


def rows(be, tab):
    header = be.tabs[tab][0]
    return [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in be.tabs[tab][1:]]
