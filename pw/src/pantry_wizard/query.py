"""Questions nobody planned for, answered with SQL.

`pw sql` loads the workbook into an in-memory SQLite database that lasts for
the one command. Every tab is a table under its own name. On top of them sit a
few views that already apply the rules a correct answer depends on, so the
agent does not have to rediscover them: which receipts still count, which
lines are fees, which item a product is, and what day it was in the
household's timezone.

Nothing here can write to the sheet. The database is a copy and is opened
read-only once it is built.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import brief, stats
from .errors import PwError
from .schema import TABS

ROW_LIMIT = 200

# Derived tabs are copies that may be stale. `stock` is the same thing, fresh.
RAW_TABS = tuple(name for name, tab in TABS.items() if tab.owner != "derived")

# Cells arrive as text. These columns become numbers so SUM and < work; an
# empty cell in one of them is NULL. Every other empty cell is ''.
NUMBERS = {
    "List": ("max_price",),
    "Items": ("max_price",),
    "Products": ("pack_qty",),
    "Offers": ("price", "regular_price", "usual_price", "misses"),
    "Receipts": ("total", "lines", "tax", "savings"),
    "PriceHistory": ("price", "regular_price"),
    "Lines": ("n", "qty", "unit_price"),
}

_SIGNED = "COALESCE(l.qty, 1) * l.unit_price * CASE l.kind WHEN 'refund' THEN -1 ELSE 1 END"
_ITEM = "COALESCE(NULLIF(l.item, ''), p.item, '')"

VIEWS = {
    "purchases": ("""
        SELECT r.date AS date, r.store AS store, r.account AS account, {item} AS item,
               COALESCE(i.name, '') AS item_name, COALESCE(NULLIF(p.name, ''), l.text) AS name,
               COALESCE(l.qty, 1) AS qty, l.unit_price AS unit_price,
               ROUND(COALESCE(l.qty, 1) * l.unit_price, 2) AS amount,
               r.source AS source, l.product AS product, r.receipt AS receipt
        FROM Lines l JOIN Receipts r ON r.receipt = l.receipt AND r.status = 'recorded'
        LEFT JOIN Products p ON p.product = l.product
        LEFT JOIN Items i ON i.item = {item}
        WHERE l.kind = 'purchase' AND COALESCE(p.kind, '') != 'not_product'
        UNION ALL
        SELECT local_date(g.at), '', '', g.item, COALESCE(i.name, ''), COALESCE(i.name, g.item),
               NULL, NULL, NULL, 'said', '', ''
        FROM Log g LEFT JOIN Items i ON i.item = g.item
        WHERE g.kind = 'bought' AND g.item != ''
        """.format(item=_ITEM),
        "One row per thing bought. Receipts replaced by an official copy, fees, deposits and "
        "refunds are left out. `item` is '' when the product is not linked to an item; `name` is "
        "always filled. `unit_price` is what was paid after the store's discount, before tax. "
        "`source` is import, photo, manual, or said (someone reported buying it; no store or price)."),
    "refunds": ("""
        SELECT r.date AS date, r.store AS store, r.account AS account, {item} AS item,
               COALESCE(NULLIF(p.name, ''), l.text) AS name, COALESCE(l.qty, 1) AS qty,
               l.unit_price AS unit_price, ROUND(COALESCE(l.qty, 1) * l.unit_price, 2) AS amount,
               l.product AS product, r.receipt AS receipt
        FROM Lines l JOIN Receipts r ON r.receipt = l.receipt AND r.status = 'recorded'
        LEFT JOIN Products p ON p.product = l.product
        WHERE l.kind = 'refund'
        """.format(item=_ITEM),
        "One row per thing returned. `amount` is positive: money that came back."),
    "trips": ("""
        SELECT r.date AS date, r.store AS store, r.account AS account, r.source AS source,
               (SELECT ROUND(SUM({signed}), 2) FROM Lines l WHERE l.receipt = r.receipt) AS lines_total,
               r.total AS printed_total, r.tax AS tax, r.savings AS savings,
               (SELECT COUNT(*) FROM Lines l WHERE l.receipt = r.receipt) AS lines,
               r.by AS by, r.receipt AS receipt
        FROM Receipts r WHERE r.status = 'recorded'
        """.format(signed=_SIGNED),
        "One row per shopping trip on record. `printed_total` is what was paid, tax included, as "
        "printed on the receipt; `tax` is the tax inside it and `savings` the store's discounts on "
        "that trip. All three are NULL when the receipt did not give them, as with many photos. "
        "`lines_total` is always there: the receipt's lines added up (fees included, refunds "
        "subtracted), without tax. For spending use COALESCE(printed_total, lines_total) and say "
        "that trips without a printed total are before tax."),
    "price_history": ("""
        SELECT local_date(h.at) AS date, p.store AS store, p.item AS item,
               COALESCE(NULLIF(p.name, ''), p.receipt_name) AS name, h.price AS price,
               h.regular_price AS regular_price, CASE WHEN h.sale != '' THEN 'yes' ELSE '' END AS on_sale,
               substr(h.sale_ends, 1, 10) AS sale_ends, h.product AS product
        FROM PriceHistory h JOIN Products p ON p.product = h.product
        """,
        "One row each time a store's price or sale for a product changed, from the day the "
        "importer began keeping them. The price held from `date` until the product's next row. "
        "What the household actually paid on earlier dates is in `purchases`."),
    "requests": ("""
        SELECT local_date(q.at) AS date, q.kind AS kind, q.item AS item,
               COALESCE(i.name, q.text) AS name, q.qty AS qty, q.store AS store, q.by AS by,
               q.raw AS raw, q.status AS status, local_date(q.closed_at) AS closed_date,
               q.closed_by AS closed_by, q.closed_ref AS closed_ref, q.id AS id
        FROM List q LEFT JOIN Items i ON i.item = q.item
        """,
        "Everything ever put on the list, open or closed. `kind` is buy, skip or remind. `status` "
        "is open, done, cancelled or expired. `closed_ref` names the receipt that closed it, when "
        "one did. `item` is '' for a one-off; `name` is always filled."),
    "said": ("""
        SELECT local_date(g.at) AS date, g.by AS by, g.kind AS kind, g.item AS item,
               COALESCE(i.name, '') AS item_name, g.value AS value, g.raw AS raw, g.at AS at
        FROM Log g LEFT JOIN Items i ON i.item = g.item
        UNION ALL
        SELECT local_date(q.at), q.by, q.kind, q.item, COALESCE(i.name, q.text), q.qty, q.raw, q.at
        FROM List q LEFT JOIN Items i ON i.item = q.item
        """,
        "Everything people told an agent, requests and observations together, with their own "
        "words in `raw`. `kind` is buy, skip, remind, out, low, last_one, plenty, have, used, "
        "bought, remark (a free observation tied to no item), said, or set and fix (changes to "
        "settings and corrections)."),
}

# Built in Python, from the same code `pw item` and the trip brief use.
TABLES = {
    "stock": "One row per item with what `pw item` reports: the stock word, what was last said, "
             "last purchase, purchases in 12 months, usual days between purchases, days since. "
             "The stock word is an estimate, never a count.",
    "prices": "One row per product that is not a fee: its item, the current price and sale when a "
              "store importer supplies them, and what was last paid. `deal` is yes only for a "
              "sale the trip brief would report: a store promotion, in the warehouse, in stock, "
              "not ended. A price is as old as `checked`.",
}

EXAMPLES = (
    ("How much did we spend at Costco last month?",
     "SELECT COUNT(*) AS trips, SUM(COALESCE(printed_total, lines_total)) AS spent, SUM(tax) AS tax, "
     "SUM(printed_total IS NULL) AS trips_before_tax FROM trips WHERE store = 'costco' "
     "AND date >= date(today(), 'start of month', '-1 month') AND date < date(today(), 'start of month')"),
    ("What did we buy on the last trip?",
     "SELECT name, qty, unit_price FROM purchases WHERE receipt = "
     "(SELECT receipt FROM trips ORDER BY date DESC LIMIT 1)"),
    ("How often do we buy eggs, and what do they cost?",
     "SELECT date, store, name, qty, unit_price FROM purchases WHERE item = 'eggs' ORDER BY date DESC LIMIT 12"),
    ("What did Sam ask for in the last week?",
     "SELECT date, name, qty, status FROM requests WHERE by = 'sam' AND kind = 'buy' "
     "AND date >= date(today(), '-7 days') ORDER BY date"),
    ("What did anyone say about yogurt?",
     "SELECT date, by, kind, raw FROM said WHERE raw LIKE '%yogurt%' OR item_name LIKE '%yogurt%' ORDER BY at"),
    ("What do we spend the most on?",
     "SELECT COALESCE(NULLIF(item_name, ''), name) AS what, COUNT(*) AS times, SUM(amount) AS spent "
     "FROM purchases WHERE date >= date(today(), '-12 months') GROUP BY what ORDER BY spent DESC LIMIT 15"),
    ("Which things cost more the last time than the time before?",
     "SELECT name, unit_price, before, date FROM (SELECT name, unit_price, date, "
     "LAG(unit_price) OVER (PARTITION BY product ORDER BY date) AS before, "
     "ROW_NUMBER() OVER (PARTITION BY product ORDER BY date DESC) AS latest "
     "FROM purchases WHERE product != '') WHERE latest = 1 AND unit_price > before"),
    ("What did we ask for and never get?",
     "SELECT date, name, by, status FROM requests WHERE kind = 'buy' AND status IN ('cancelled', 'expired') "
     "ORDER BY date DESC LIMIT 20"),
    ("How long do requests wait before they are bought?",
     "SELECT name, COUNT(*) AS times, ROUND(AVG(julianday(closed_date) - julianday(date)), 1) AS days "
     "FROM requests WHERE kind = 'buy' AND status = 'done' GROUP BY name ORDER BY days DESC LIMIT 15"),
    ("What is on sale that we have bought before?",
     "SELECT name, price, regular_price, deal_ends, last_paid FROM prices WHERE deal = 'yes' AND times_bought > 0"),
    ("What is the lowest price the store has had for eggs, and how often is it on sale?",
     "SELECT name, MIN(price) AS lowest, MAX(price) AS highest, SUM(on_sale = 'yes') AS sales, "
     "MIN(date) AS since FROM price_history WHERE item = 'eggs' GROUP BY product"),
    ("How much did the store's discounts save us this year?",
     "SELECT store, SUM(savings) AS saved, COUNT(savings) AS trips_counted, COUNT(*) AS trips FROM trips "
     "WHERE date >= date(today(), 'start of year') GROUP BY store"),
    ("What have we bought only once?",
     "SELECT name, MIN(date) AS date, store FROM purchases WHERE product != '' GROUP BY product "
     "HAVING COUNT(*) = 1 ORDER BY date DESC LIMIT 30"),
    ("What are we probably running low on that is not on the list?",
     "SELECT name, stock, last_said, last_bought, usual_gap_days, days_since FROM stock "
     "WHERE stock IN ('out', 'low', 'check') AND on_list = ''"),
)


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return text or None
    return int(value) if value.is_integer() and "." not in str(text) else value


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _create(db, name: str, columns: list[str], rows: list[list]) -> None:
    db.execute(f"CREATE TABLE {_q(name)} ({', '.join(_q(c) for c in columns)})")
    db.executemany(f"INSERT INTO {_q(name)} VALUES ({', '.join('?' * len(columns))})", rows)


def connect(wb) -> sqlite3.Connection:
    wb.load("Config")
    wb.load(*RAW_TABS)
    zone = wb.config("timezone")
    tz = ZoneInfo(zone) if zone else wb.now().tzinfo
    today = wb.now().date()

    def local_date(text):
        """The household's calendar day for a timestamp, whatever zone it was written in."""
        text = str(text or "")
        try:
            when = datetime.fromisoformat(text)
        except ValueError:
            return text[:10]
        return (when.astimezone(tz) if when.tzinfo else when).date().isoformat()

    db = sqlite3.connect(":memory:")
    db.create_function("local_date", 1, local_date, deterministic=True)
    db.create_function("today", 0, today.isoformat, deterministic=True)
    for name in RAW_TABS:
        table = wb.t(name)
        columns = [h for h in table.header if h]
        numeric = NUMBERS.get(name, ())
        _create(db, name, columns, [
            [_number(r[c]) if c in numeric else r[c] for c in columns] for r in table.rows])
        db.execute(f"CREATE INDEX {_q('ix_' + name)} ON {_q(name)} ({_q(TABS[name].key)})")
    db.execute("CREATE INDEX ix_lines_receipt ON Lines (receipt)")

    facts = stats.item_stats(wb, today)
    columns = ["item", "name", "aliases", "stock", "last_said", "last_said_date", "last_bought",
               "last_bought_store", "buys_12mo", "usual_gap_days", "days_since", "on_list", "rule", "stores"]
    rows = []
    for item in wb.t("Items").rows:
        f = dict(facts[item["item"]], aliases=item["aliases"], rule=item["rule"], stores=item["stores"])
        f["last_said_date"] = f["last_said_at"]
        rows.append([None if f[c] == "" and c in ("usual_gap_days", "days_since") else f[c] for c in columns])
    _create(db, "stock", columns, rows)

    bought = stats.product_stats(wb, today)
    offers = wb.t("Offers")
    items = wb.t("Items")
    columns = ["product", "store", "name", "item", "item_name", "price", "regular_price", "usual_price",
               "deal", "deal_ends", "availability", "checked", "changed", "last_paid", "last_paid_date",
               "times_bought", "kind"]
    rows = []
    for p in wb.t("Products").rows:
        if p["kind"] == "not_product":
            continue
        o = offers.get(p["product"]) or {}
        b = bought.get(p["product"]) or {}
        deal = bool(o) and brief.confirmed_deal(o, today)[0]
        rows.append([
            p["product"], p["store"], p["name"] or p["receipt_name"], p["item"],
            (items.get(p["item"]) or {}).get("name", ""),
            _number(o.get("price")), _number(o.get("regular_price")), _number(o.get("usual_price")),
            "yes" if deal else "", o.get("sale_ends", "")[:10] if deal else "",
            o.get("availability", ""), local_date(o.get("checked_at")), local_date(o.get("changed_at")),
            _number(b.get("last_paid")), b.get("last_paid_at", ""), len(b.get("dates", ())), p["kind"],
        ])
    _create(db, "prices", columns, rows)

    for name, (sql, _) in VIEWS.items():
        db.execute(f"CREATE VIEW {name} AS {sql}")
    db.commit()
    db.execute("PRAGMA query_only = ON")
    return db


def _cell(value):
    return round(value, 4) if isinstance(value, float) else value


def run(wb, queries: list[str], limit: int = ROW_LIMIT) -> list[dict]:
    with closing(connect(wb)) as db:
        return [_run(db, sql, limit) for sql in queries]


def _run(db, sql: str, limit: int) -> dict:
    try:
        cur = db.execute(sql)
    except (sqlite3.Error, sqlite3.Warning) as e:
        if "one statement" in str(e):
            hint = "Pass several queries as separate arguments."
        elif "readonly" in str(e):
            hint = "pw sql only reads. Changes go through `pw apply`."
        else:
            hint = "`pw schema` lists the tables and columns."
        raise PwError(f"{str(e).rstrip('.')}. {hint}\n  {sql}")
    if cur.description is None:
        raise PwError(f"That is not a query. pw sql only reads.\n  {sql}")
    rows = cur.fetchmany(limit + 1)
    result = {"columns": [d[0] for d in cur.description],
              "rows": [[_cell(v) for v in r] for r in rows[:limit]]}
    if len(rows) > limit:
        total = limit + 1 + len(cur.fetchall())
        result["not_shown"] = total - limit
        result["note"] = (f"{total} rows matched and the first {limit} are shown. "
                          "Count, sum or group in the query rather than reading rows.")
    return result


def _columns(db, name: str) -> str:
    return ", ".join(r[1] for r in db.execute(f"PRAGMA table_info({_q(name)})"))


def coverage(db) -> list[str]:
    """What the sheet has on record, so an empty result is not read as "never"."""
    lines = []
    for store, trips, first, last, imported in db.execute(
            "SELECT store, COUNT(*), MIN(date), MAX(date), SUM(source = 'import') FROM trips "
            "GROUP BY store ORDER BY COUNT(*) DESC"):
        how = "all imported from the store" if imported == trips else \
            f"{imported} imported from the store, {trips - imported} from photos or typed in" if imported else \
            "from photos or typed in only, so trips nobody photographed are missing"
        lines.append(f"{store}: {trips} trip{'' if trips == 1 else 's'}, {first} to {last}, {how}.")
    for sync, last in db.execute("SELECT sync, local_date(last_sync_at) FROM Sync WHERE last_sync_at != ''"):
        lines.append(f"{sync}: importer last ran {last}.")
    if not lines:
        lines.append("No receipts are recorded.")
    first, = db.execute("SELECT MIN(date) FROM said WHERE date != ''").fetchone()
    lines.append(f"Requests and what people said: since {first}." if first
                 else "Nothing anyone said is recorded yet.")
    lines.append("A store with no importer is known only from receipts someone sent. "
                 "Nothing found means nothing on record, not that it never happened.")
    return lines


def schema(wb) -> str:
    with closing(connect(wb)) as db:
        return _schema(wb, db)


def _schema(wb, db) -> str:
    out = [f"pw sql \"<query>\" runs SQLite against a copy of the sheet. Today is {wb.today()}.", "",
           "Use these first. They already leave out what should not count."]
    for name, (_, about) in VIEWS.items():
        out += ["", f"{name} ({_columns(db, name)})", f"  {about}"]
    for name, about in TABLES.items():
        out += ["", f"{name} ({_columns(db, name)})", f"  {about}"]
    out += ["", "The sheet's own tabs, for anything the views do not cover:"]
    out += [f"  {name} ({_columns(db, name)})" for name in RAW_TABS]
    out += [
        "",
        "Rules",
        "- Dates are text like 2026-11-08 in the household's timezone. Compare them as text.",
        "- today() is the household's date. Use date(today(), '-7 days'), never date('now'), which is UTC.",
        "- local_date(at) turns any timestamp column into the household's date.",
        "- People, stores and items are lowercase keys: 'sam', 'costco', 'eggs'. `pw items` lists item keys.",
        "- An empty cell is '' in a text column and NULL in a number column.",
        f"- At most {ROW_LIMIT} rows come back. Count, sum and group in the query. For a script of your",
        "  own, raise it and save the rows: pw sql --limit 100000 \"SELECT * FROM purchases\" > rows.json",
        "- Several queries can go in one command: pw sql \"<query>\" \"<query>\".",
        "",
        "What is on record",
    ]
    out += [f"- {line}" for line in coverage(db)]
    out += ["", "Examples"]
    for question, sql in EXAMPLES:
        out += [f"-- {question}", sql, ""]
    return "\n".join(out).rstrip() + "\n"


# -- the same views without writing SQL ---------------------------------------
#
# `pw purchases`, `pw trips`, `pw requests`, `pw said` and `pw prices` cover the
# questions that are one view, filtered, and perhaps added up. Totals are over
# every matching row, not only the rows shown.

_SPENT = "ROUND(SUM(COALESCE(printed_total, lines_total)), 2)"
_PERIODS = {"month": "substr(date, 1, 7)", "year": "substr(date, 1, 4)"}

SHOWS = {
    "purchases": {
        "columns": "date, store, name, item, qty, unit_price, amount, source",
        "text": ("name", "item_name"),
        "totals": "COUNT(*) AS purchases, ROUND(SUM(amount), 2) AS spent, MIN(date) AS first, MAX(date) AS last",
        "by": dict(_PERIODS, store="store", item="COALESCE(NULLIF(item_name, ''), name)"),
        "grouped": "COUNT(*) AS purchases, SUM(qty) AS qty, ROUND(SUM(amount), 2) AS spent, "
                   "ROUND(AVG(unit_price), 2) AS average_price, MIN(date) AS first, MAX(date) AS last",
    },
    "trips": {
        "columns": "date, store, account, printed_total, tax, savings, lines_total, lines, source",
        "totals": f"COUNT(*) AS trips, {_SPENT} AS spent, ROUND(SUM(tax), 2) AS tax, "
                  "ROUND(SUM(savings), 2) AS savings, SUM(printed_total IS NULL) AS trips_before_tax",
        "by": dict(_PERIODS, store="store"),
        "grouped": f"COUNT(*) AS trips, {_SPENT} AS spent, ROUND(SUM(tax), 2) AS tax, "
                   "ROUND(SUM(savings), 2) AS savings, SUM(printed_total IS NULL) AS trips_before_tax",
    },
    "requests": {
        "columns": "date, kind, name, qty, store, by, status, closed_date, raw",
        "text": ("name", "raw"),
        "totals": "COUNT(*) AS requests, SUM(status = 'open') AS open, SUM(status = 'done') AS done, "
                  "SUM(status = 'cancelled') AS cancelled, SUM(status = 'expired') AS expired",
        "by": dict(_PERIODS, person="by", item="name"),
        "grouped": "COUNT(*) AS requests, SUM(status = 'done') AS done, MAX(date) AS last",
    },
    "said": {
        "columns": "date, by, kind, item_name, value, raw",
        "text": ("raw", "item_name", "value"),
        "totals": "COUNT(*) AS rows",
        "by": dict(_PERIODS, person="by", item="item_name"),
        "grouped": "COUNT(*) AS rows, MAX(date) AS last",
        "order": "at DESC",
    },
    "prices": {
        "columns": "name, item, store, price, regular_price, deal, deal_ends, availability, checked, "
                   "last_paid, last_paid_date, times_bought",
        "text": ("name", "item_name"),
        "totals": "COUNT(*) AS products, SUM(deal = 'yes') AS deals",
        "order": "deal DESC, times_bought DESC",
        "date": None,
    },
    "price_history": {
        "columns": "date, store, name, item, price, regular_price, on_sale, sale_ends",
        "text": ("name",),
        "totals": "COUNT(*) AS changes, MIN(price) AS lowest, MAX(price) AS highest, MIN(date) AS since",
    },
}


def _day(text: str, flag: str) -> str:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise PwError(f"{flag} takes a date like 2026-10-01, not {text!r}") from None


def context(wb) -> dict:
    """A compact, slowly-changing digest to interpret what people say.

    People, stores (and which have an importer), the items the household buys
    with their aliases and rough rhythm, and the standing notes. Deliberately
    no open list, stock words or last-purchase dates: those go stale and must
    be read fresh. Stamped, so a reader knows how old a cached copy is.
    """
    from .ops import split_list

    wb.load("Config", "Sources", "Items", "Products", "Sync", "Notes")
    facts = stats.item_stats(wb, wb.now().date())
    linked: dict[str, set] = {}
    for p in wb.t("Products").rows:
        if p["item"]:
            linked.setdefault(p["item"], set()).add(p["store"])
    importer_stores = {r["store"] for r in wb.t("Sync").rows}

    def named(prefix: str) -> list[dict]:
        return [{prefix: s["source"].split(":", 1)[1], "name": s["name"]}
                for s in wb.t("Sources").rows if s["source"].startswith(f"{prefix}:")]

    people = named("person")
    stores = named("store")
    for s in stores:
        s["importer"] = s["store"] in importer_stores
    items = []
    for it in wb.t("Items").rows:
        where = (split_list(it["only_at"]) or split_list(it["stores"])
                 or sorted(linked.get(it["item"], ())))
        row = {"item": it["item"], "name": it["name"]}
        if it["aliases"]:
            row["aliases"] = split_list(it["aliases"])
        if where:
            row["stores"] = where
        for col in ("rule", "kind"):
            if it[col]:
                row[col] = it[col]
        gap = facts.get(it["item"], {}).get("usual_gap_days", "")
        if gap != "":
            row["usual_gap_days"] = gap
        items.append(row)
    notes = [n["text"] for n in wb.t("Notes").rows if n["status"] == "open"]
    return {
        "as_of": wb.stamp(),
        "note": "slowly-changing context to interpret with; read the list, stock and prices "
                "fresh before answering or writing",
        "people": people, "stores": stores, "items": items, "notes": notes,
    }


def show(wb, view: str, *, item="", store: str = "", person: str = "", text: str = "",
         status: str = "", since: str = "", until: str = "", days: int | None = None,
         deals: bool = False, by: str = "", limit: int = 50) -> dict:
    """One view, filtered. Returns what was asked, totals over everything matching, and rows."""
    with closing(connect(wb)) as db:
        return _show(wb, db, view, item, store, person, text, status, since, until, days, deals, by, limit)


def _show(wb, db, view, item, store, person, text, status, since, until, days, deals, by, limit) -> dict:
    from .ops import _item, _source, slug

    spec = SHOWS[view]
    columns = {r[1] for r in db.execute(f"PRAGMA table_info({view})")}
    where, args, covers = [], [], {}

    def need(column: str, flag: str) -> None:
        if column not in columns:
            raise PwError(f"pw {view.replace('_', ' ')} has no {flag}")

    if item:
        need("item", "--item")
        keys = [_item(wb, slug(i))["item"] for i in ([item] if isinstance(item, str) else item)]
        where.append("item IN (%s)" % ", ".join("?" * len(keys)))
        args += keys
        covers["item"] = keys[0] if len(keys) == 1 else keys
    if store:
        need("store", "--store")
        key = _source(wb, "store", store, {})
        # A request names the stores it may be bought at, or none for anywhere.
        where.append("(';' || replace(store, ' ', '') || ';') LIKE ?" if view == "requests" else "store = ?")
        args.append(f"%;{key};%" if view == "requests" else key)
        covers["store"] = key
    if person:
        need("by", "--person")
        key = _source(wb, "person", person, {})
        where.append("by = ?")
        args.append(key)
        covers["person"] = key
    if status:
        need("status", "--status")
        where.append("status = ?")
        args.append(status)
        covers["status"] = status
    if text:
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in spec["text"]) + ")")
        args += [f"%{text}%"] * len(spec["text"])
        covers["text"] = text
    if deals:
        where.append("deal = 'yes'")
        covers["deals"] = True
    if days is not None:
        since = (wb.now().date() - timedelta(days=days)).isoformat()
    for flag, value, op in (("--since", since, ">="), ("--until", until, "<=")):
        if value:
            need("date", flag)
            where.append(f"date {op} ?")
            args.append(_day(value, flag))
            covers[flag[2:]] = args[-1]
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    def rows(sql: str, params=args) -> list[dict]:
        cur = db.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [{k: _cell(v) for k, v in zip(names, r) if v not in ("", None)} for r in cur.fetchall()]

    totals = rows(f"SELECT {spec['totals']} FROM {view}{clause}")[0]
    out = {"covers": covers or "everything on record", "totals": totals}
    if by:
        groups = spec.get("by", {})
        if by not in groups:
            raise PwError(f"pw {view} can group by {sorted(groups) or 'nothing'}, not {by!r}")
        out["by"] = by
        found = rows(f"SELECT {groups[by]} AS {by}, {spec['grouped']} FROM {view}{clause} "
                     f"GROUP BY 1 ORDER BY {'1 DESC' if by in _PERIODS else '2 DESC, 1'}")
    else:
        order = spec.get("order", "date DESC")
        found = rows(f"SELECT {spec['columns']} FROM {view}{clause} ORDER BY {order}")
    out["rows"] = found[:limit]
    if len(found) > limit:
        out["not_shown"] = len(found) - limit
    return out
