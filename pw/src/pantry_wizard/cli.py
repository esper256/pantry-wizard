"""The `pw` command.

Reads print JSON, except `brief` and `schema`, which print text. Writes go
through `pw apply`, which takes the operations for one chat message as a JSON
array. A problem the agent can fix is printed to stderr with exit code 1 and
nothing is written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import brief as brief_mod
from . import costco_bridge, migrate as migrate_mod, ops, query, stats
from .backend import FileBackend, SheetsBackend
from .schema import SCHEMA_VERSION, TABS, UPGRADABLE
from .store import PwError, Workbook


# One module per store importer: snapshot() for it to read, apply() for what it
# wrote. Each speaks its importer's own file format. The lease is the same
# for every store.
BRIDGES = {"costco": costco_bridge}


_DATES = ("since", "until", "days")
SHOWS = {
    "purchases": ("what was bought: filter by item, store, words or dates; totals included",
                  ("item", "store", "text", *_DATES, "by")),
    "trips": ("shopping trips with what was spent, tax and savings", ("store", *_DATES, "by")),
    "requests": ("everything put on the list, open or closed", ("item", "store", "person", "status", "text",
                                                               *_DATES, "by")),
    "said": ("what people told an agent, in their words", ("item", "person", "text", *_DATES, "by")),
    "prices": ("current prices and sales per product, or their history", ("item", "store", "text", "deals",
                                                                         "history", *_DATES)),
}
SHOW_FLAGS = {
    "item": {"nargs": "+", "help": "one or more item keys or names"},
    "store": {"help": "a store"},
    "person": {"help": "who asked or said it"},
    "status": {"choices": ["open", "done", "cancelled", "expired"]},
    "text": {"help": "words to look for in names and in what was said"},
    "since": {"help": "first date, like 2026-10-01"},
    "until": {"help": "last date, like 2026-10-31"},
    "days": {"type": int, "help": "the last N days, instead of --since"},
    "by": {"help": "add up per item, store, person, month or year, where that applies"},
    "deals": {"action": "store_true", "help": "only sales a trip can actually get"},
    "history": {"action": "store_true", "help": "every price change on record, not the current price"},
}


def _backend(args):
    if args.file:
        return FileBackend(args.file)
    sheet = args.sheet or os.environ.get("PW_SHEET")
    if not sheet:
        raise PwError("Say which sheet: --sheet ID, the PW_SHEET variable, or --file for a JSON workbook.")
    return SheetsBackend(sheet, agent=args.agent)


def _out(value) -> None:
    print(json.dumps(value, indent=1, ensure_ascii=False, default=str))


def _read_json(arg: str | None):
    text = sys.stdin.read() if arg in (None, "-") else (
        open(arg, encoding="utf-8").read() if os.path.exists(arg) else arg)
    return json.loads(text)


def doctor(wb: Workbook) -> list[str]:
    have = wb.config("schema_version")
    if not have:
        return ["This sheet is not set up. Run `pw init --timezone <IANA zone> --currency <code>`."]
    if have in UPGRADABLE:
        return [f"This sheet is schema {have} and this pw is {SCHEMA_VERSION}. Run `pw upgrade`."]
    if have != SCHEMA_VERSION:
        return [f"This sheet is schema {have} and this pw is {SCHEMA_VERSION}. Install the pw release "
                "the sheet was made or upgraded with."]
    wb.load(*TABS)
    problems = []
    items = wb.t("Items")
    sources = wb.t("Sources")
    for r in wb.t("List").rows:
        if r["item"] and items.get(r["item"]) is None:
            problems.append(f'List {r["id"]}: unknown item {r["item"]}')
        if r["by"] not in ("", "pw") and sources.get(f'person:{r["by"]}') is None:
            problems.append(f'List {r["id"]}: unknown person {r["by"]}')
    for p in wb.t("Products").rows:
        if p["item"] and items.get(p["item"]) is None:
            problems.append(f'Product {p["product"]}: unknown item {p["item"]}')
    receipts = wb.t("Receipts")
    for l in wb.t("Lines").rows:
        if receipts.get(l["receipt"]) is None:
            problems.append(f'Line {l["line"]}: unknown receipt')
    for name in TABS:
        seen, dupes = set(), set()
        for r in wb.t(name).rows:
            key = r[TABS[name].key]
            (dupes if key in seen else seen).add(key)
        if dupes:
            problems.append(f"{name}: duplicate keys {sorted(dupes)[:5]}")
    return problems


def run(args) -> None:
    if args.cmd == "bootstrap":
        from . import credentials
        sheet = args.sheet or os.environ.get("PW_SHEET")
        if not sheet:
            raise PwError("Set PW_SHEET (or pass --sheet) to the id of the sheet holding the Accounts tab.")
        found = credentials.resolve(sheet, args.agent, force=args.force)
        _out({"agent": args.agent, "source": found["source"], "cached_to": found["cache_path"],
              "next": "The key is cached. You can restore the sheet to private now."
                      if found["source"] == "bootstrap" else "Already set up; nothing re-read."})
        return
    backend = _backend(args)
    wb = Workbook(backend, agent=args.agent)
    cmd = args.cmd
    if cmd in ("init", "migrate"):
        # --reset is for re-running a migration into the new sheet. It must
        # never reach the old one, whose Config and Items would be wiped.
        old = backend.read(["Config", "Events", "RetailMemory"])
        version = next((r[1] for r in old["Config"][1:] if len(r) > 1 and r[0] == "schema_version"), "")
        if version in UPGRADABLE:
            raise PwError(f"This sheet is schema {version}. Nothing was changed. Run `pw upgrade`.")
        if old["Events"] or old["RetailMemory"] or (version and version != SCHEMA_VERSION):
            raise PwError("This is a schema 0.3 workbook, or some other sheet pw did not make. "
                          "Nothing was changed. Point pw at a new, empty sheet; see docs/MIGRATION.md.")
    if cmd in ("init", "migrate") and not args.reset:
        # Both commands start from empty headers. Never do that to a sheet in use.
        have = backend.read(["List", "Items", "Log", "Lines"])
        if any(len(rows) > 1 for rows in have.values()):
            raise PwError("This workbook already holds household data. Nothing was changed. "
                          "Point pw at a new, empty sheet, or pass --reset if you mean to wipe it.")
    if cmd == "migrate" and args.old_sheet and args.old_sheet == getattr(backend, "id", None):
        raise PwError("The old and new sheets are the same. Migrate into a new workbook.")
    if cmd == "init":
        migrate_mod.init(backend, args.timezone, args.currency)
        _out({"created": list(TABS)})
    elif cmd == "items":
        _out([{"item": i["item"], "name": i["name"], "aliases": i["aliases"], "kind": i["kind"]}
              for i in wb.t("Items").rows])
    elif cmd == "list":
        _out(ops.open_list(wb, args.store or ""))
    elif cmd == "apply":
        done = ops.apply(wb, _read_json(args.ops), by=args.by or "", raw=args.raw or "", ref=args.ref or "",
                         check=args.check)
        _out({"checked": done, "written": False} if args.check else done)
    elif cmd == "item":
        wb.load("Config")
        facts = stats.item_stats(wb, wb.now().date())
        out = []
        for key in args.item:
            key = ops.slug(key)
            st = facts.get(key)
            if st is None:
                raise PwError(f"No item {key!r}")
            row = wb.t("Items").get(key)
            st.update({k: row[k] for k in ("aliases", "kind", "stores", "only_at", "rule",
                                           "max_price", "keep", "note")})
            st["products"] = [p["product"] for p in wb.t("Products").where(item=key)]
            st["recent"] = [
                {"at": r["at"][:10], "by": r["by"], "kind": r["kind"], "raw": r["raw"] or r["value"]}
                for r in wb.t("Log").rows if r["item"] == key][-5:]
            out.append(st)
        _out(out[0] if len(out) == 1 else out)
    elif cmd == "context":
        _out(query.context(wb))
    elif cmd == "brief":
        wb.load("Config")
        if args.changes:
            result = brief_mod.changes(wb, args.store)
            wb.commit()
            _out(result)
        else:
            b = brief_mod.build(wb, args.store)
            brief_mod.save(wb, b)
            wb.commit()
            _out(b) if args.json else print(brief_mod.render(b))
    elif cmd == "tier":
        wb.load("Config")
        _out(brief_mod.trip_tier(wb, args.store))
    elif cmd == "questions":
        _out(ops.open_questions(wb, args.next))
    elif cmd in SHOWS:
        view = "price_history" if cmd == "prices" and args.history else cmd
        asked = {k: getattr(args, k) for k in ("item", "store", "person", "text", "status", "since", "until",
                                                "days", "deals", "by") if getattr(args, k, None)}
        _out(query.show(wb, view, limit=args.limit, **asked))
    elif cmd == "sql":
        queries = [sys.stdin.read()] if args.query == ["-"] else args.query
        print(json.dumps(query.run(wb, queries, args.limit), ensure_ascii=False, default=str))
    elif cmd == "schema":
        print(query.schema(wb), end="")
    elif cmd == "rebuild":
        wb.load("Config")
        wb.load("v_Items")
        rows = stats.rebuild_v_items(wb)
        wb.commit()
        _out({"v_Items": len(rows)})
    elif cmd == "sync":
        wb.load("Config")
        bridge = BRIDGES.get(ops.slug(args.store))
        if bridge is None and args.action != "lease":
            raise PwError(f"No importer bridge for {args.store!r}. Known: {sorted(BRIDGES)}.")
        if args.action == "snapshot":
            tier = brief_mod.trip_tier(wb, args.store) if args.trip else None
            _out(bridge.snapshot(wb, args.store, (tier["tier0"] + tier["tier1"]) if tier else None,
                                 account=args.account))
        elif args.action == "lease":
            _out(costco_bridge.lease(wb, args.store, args.agent, args.hours, args.location, args.status,
                                     account=args.account, prices=args.prices))
        else:
            _out(bridge.apply(wb, _read_json(args.mutations), args.store, account=args.account))
    elif cmd == "upgrade":
        _out(migrate_mod.upgrade(backend))
    elif cmd == "migrate":
        old = json.load(open(args.old, encoding="utf-8")) if args.old else \
            SheetsBackend(args.old_sheet).read(["Config", "Items", "RetailMemory", "Integrations", "Events"])
        _out(migrate_mod.migrate(old, backend, once=args.once, people=args.person, stores=args.store_name))
    elif cmd == "doctor":
        report = {"problems": doctor(wb)}
        if hasattr(backend, "cred_source"):
            report["credentials"] = backend.cred_source
        _out(report)
    _log_op(args, backend)


def _log_op(args, backend) -> None:
    """One line per invocation, so latency can be measured instead of guessed."""
    try:
        folder = os.path.expanduser("~/.pantry-wizard")
        os.makedirs(folder, mode=0o700, exist_ok=True)
        with open(os.path.join(folder, "ops.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cmd": args.cmd,
                "seconds": round(time.time() - args.started, 3),
                "reads": backend.read_calls, "writes": backend.write_calls,
            }) + "\n")
    except OSError:
        pass


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pw", description=__doc__.split("\n\n")[0])
    p.add_argument("--sheet", help="Google spreadsheet id (or set PW_SHEET)")
    p.add_argument("--file", help="use a JSON workbook file instead of Google Sheets")
    p.add_argument("--agent", default=os.environ.get("PW_AGENT", "pw"), help="name of the agent writing")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write headers and settings into an empty sheet")
    s.add_argument("--timezone", default="")
    s.add_argument("--currency", default="")
    s.add_argument("--reset", action="store_true", help="wipe a workbook that already has data")
    sub.add_parser("items", help="every item's key, name and aliases, for matching what was said")
    s = sub.add_parser("list", help="the open list, merged per item")
    s.add_argument("--store")
    s = sub.add_parser("apply", help="apply the operations for one chat message")
    s.add_argument("ops", nargs="?", help="JSON array, a file, or - for stdin")
    s.add_argument("--by", help="who said it")
    s.add_argument("--raw", help="their words")
    s.add_argument("--ref", help="chat message id; makes a retry harmless")
    s.add_argument("--check", action="store_true", help="validate and report the batch; write nothing")
    s = sub.add_parser("item", help="what is known about one or more items")
    s.add_argument("item", nargs="+", help="one or more item keys or names")
    sub.add_parser("context", help="a stamped digest of people, stores, items and notes, to interpret with")
    s = sub.add_parser("brief", help="trip brief for a store, from current data")
    s.add_argument("store")
    s.add_argument("--changes", action="store_true", help="what moved since the last brief")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("tier", help="products worth re-checking for a trip")
    s.add_argument("store")
    s = sub.add_parser("questions", help="the next card of banked questions")
    s.add_argument("--next", type=int, default=8)
    for name, (about, flags) in SHOWS.items():
        s = sub.add_parser(name, help=about)
        for flag in flags:
            s.add_argument(f"--{flag}", **SHOW_FLAGS[flag])
        s.add_argument("--limit", type=int, default=50, help="most rows to print; totals cover every match")
    s = sub.add_parser("sql", help="answer a question with a read-only SQL query; see `pw schema`")
    s.add_argument("query", nargs="+", help="one SELECT per argument, or - for stdin")
    s.add_argument("--limit", type=int, default=query.ROW_LIMIT, help="most rows to print per query")
    sub.add_parser("schema", help="tables, columns, what is on record, and example queries for `pw sql`")
    sub.add_parser("rebuild", help="rewrite the v_Items tab")
    s = sub.add_parser("sync", help="exchange data with a store importer")
    s.add_argument("action", choices=["snapshot", "apply", "lease"])
    s.add_argument("store")
    s.add_argument("mutations", nargs="?")
    s.add_argument("--account", default="", help="which of the store's accounts; needed only when there "
                   "is more than one. With lease, a new name adds an account")
    s.add_argument("--prices", action="store_true",
                   help="lease: make this the account that refreshes the store's prices")
    s.add_argument("--trip", action="store_true", help="snapshot: include the trip refresh list")
    s.add_argument("--hours", type=int, default=36, help="lease: how long to hold it")
    s.add_argument("--location", default="", help="lease: confirmed home location")
    s.add_argument("--status", default="", help="lease: setup, active or paused")
    s = sub.add_parser("migrate", help="fill this (new) workbook from a schema 0.3 workbook")
    s.add_argument("--old", help="schema 0.3 workbook exported as JSON")
    s.add_argument("--old-sheet", help="schema 0.3 spreadsheet id; it is only read")
    s.add_argument("--once", action="append", default=[], help="name of a one-time item; repeatable")
    s.add_argument("--person", action="append", default=[], metavar="LABEL=NAME",
                   help="an old actor label and who it is, e.g. user=Alex; an empty name drops it; repeatable")
    s.add_argument("--store-name", action="append", default=[], metavar="LABEL=NAME",
                   help="an old store label and the store it means; an empty name drops it; repeatable")
    s.add_argument("--reset", action="store_true", help="wipe the new workbook first, e.g. to re-run")
    sub.add_parser("upgrade", help="bring a sheet on an older 2.x schema up to this pw, in place")
    sub.add_parser("doctor", help="check the sheet for broken references")
    s = sub.add_parser("bootstrap", help="read this agent's service-account key from the sheet's "
                                         "Accounts tab and cache it locally")
    s.add_argument("--force", action="store_true", help="re-read the sheet and overwrite the cached key")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    args.started = time.time()
    try:
        run(args)
    except PwError as e:
        print(f"pw: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
