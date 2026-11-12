"""Connect the Costco importer to the sheet.

costco-sync reads a snapshot JSON, talks to Costco, and writes a mutation
file. It never calls Google. This module is the other side of that boundary:

    pw sync snapshot costco > household.json
    costco-sync run --snapshot household.json --out mutations.json --owner <bot>
    pw sync apply costco mutations.json

The snapshot is shaped the way costco-sync has always read it (`items`,
`retail_memory`, `integrations`, `known_source_refs`), so the importer needs
no knowledge of the sheet layout. From the mutation file this module reads
`receipts`, `retail_memory_upserts`, `item_alias_updates`,
`checked_item_numbers`, `missed_item_numbers`, `integration_upsert` and
`summary`. The `events` list in the same file is the schema 0.3 form of
`receipts` and is ignored.

What that means for the data:

- A receipt line becomes a `Lines` row under a `Receipts` row.
- A product row is appended the first time it is seen and never rewritten, so
  a link a person confirmed cannot be overwritten by a later sync.
- Prices go to `Offers`, which only this module writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import receipts
from .ops import open_list, slug, split_list
from .stats import product_stats, rebuild_v_items
from .store import PwError, Workbook

DEPOSIT_PREFIXES = ("CA REDEMP VAL",)
IMPORTER = "costco-sync"
SYNC_FIELDS = ("status", "location", "membership_fingerprint", "history_from",
               "history_through", "last_sync_at", "last_summary")


def _warehouse(location: str) -> str:
    return (location or "").split(" ", 1)[0]


def _plain(table) -> dict[str, dict]:
    """A tab's rows by key, as plain dicts that can be edited and written back whole."""
    return {r[table.tab.key]: {k: v for k, v in r.items() if k != "_row"} for r in table.rows}


# -- lease -------------------------------------------------------------------


def lease(wb: Workbook, store: str, owner: str, hours: int = 36,
          location: str = "", status: str = "") -> dict:
    """Claim or renew the right to run this store's importer.

    One agent schedules each store. A lease held by someone else and still in
    the future is refused; that is household state, not an error to report.
    """
    wb.load("Config", "Sources", "Sync")
    store = slug(store)
    if wb.t("Sources").get(f"store:{store}") is None:
        wb.append("Sources", {"source": f"store:{store}", "name": store.replace("-", " ").title()})
    rows = _plain(wb.t("Sync"))
    row = rows.setdefault(store, {"store": store, "status": "setup"})
    held = row.get("lease_until", "")
    if row.get("owner") and row["owner"] != owner and held:
        try:
            still_held = datetime.fromisoformat(held) > wb.now()
        except ValueError:
            still_held = False
        if still_held:
            raise PwError(f'{row["owner"]} holds the {store} lease until {held}. Nothing was changed.')
    row["owner"] = owner
    row["lease_until"] = (wb.now() + timedelta(hours=hours)).replace(microsecond=0).isoformat()
    if location:
        row["location"] = location
    if status:
        row["status"] = status
    wb.replace("Sync", list(rows.values()))
    wb.commit()
    return row


# -- sheet -> snapshot -------------------------------------------------------


def snapshot(wb: Workbook, store: str = "costco", priority: list[str] | None = None) -> dict:
    wb.load("Config", "Sources", "Items", "List", "Products", "Offers", "Receipts", "Lines", "Sync", "Log")
    store = slug(store)
    today = wb.now().date()
    sync = wb.t("Sync").get(store) or {}
    names = {r["source"].split(":", 1)[1]: r["name"] for r in wb.t("Sources").rows
             if r["source"].startswith("store:")}
    # The importer finds its rows by this name, so it is the importer's
    # spelling and not whatever the household called the store.
    store_name = store.replace("-", " ").title()
    warehouse = _warehouse(sync.get("location", ""))
    pstats = product_stats(wb, today)
    offers = wb.t("Offers")
    imported = {r["receipt"] for r in wb.t("Receipts").rows if r["source"] == "import"}

    links: dict[str, list[str]] = {}
    retail, non_products = [], []
    counts: dict[str, int] = {}
    for line in wb.t("Lines").rows:
        if line["kind"] == "purchase" and line["product"]:
            counts[line["product"]] = counts.get(line["product"], 0) + 1
    for p in wb.t("Products").rows:
        if p["store"] != store:
            continue
        if p["kind"] == "not_product":
            non_products.append(p["sku"])
            continue
        if p["item"]:
            links.setdefault(p["item"], []).append(f'{store}:{p["sku"]}')
        o = offers.get(p["product"]) or {}
        st = pstats.get(p["product"], {})
        retail.append({
            "retail_key": f'{store}:{warehouse}:{p["sku"]}',
            "item_id": p["item"], "store": store_name, "location": sync.get("location", ""),
            "retailer_sku": p["sku"], "retailer_name": p["name"], "receipt_name": p["receipt_name"],
            "last_paid_unit_price": st.get("last_paid", ""), "last_paid_at": st.get("last_paid_at", ""),
            "baseline_unit_price": o.get("usual_price", ""),
            "purchase_count": str(counts.get(p["product"], 0)),
            "current_price": o.get("price", ""), "regular_price": o.get("regular_price", ""),
            # The sheet keeps only the store's promotion. Whether a price is
            # below this household's usual is worked out again on every run.
            "reduction_kind": o.get("sale", ""), "reduction_ends_at": o.get("sale_ends", ""),
            "price_scope": o.get("price_scope", ""), "observed_at": o.get("changed_at", ""),
            "availability": o.get("availability", ""), "sale_scope": o.get("sale_scope", ""),
        })
    wanted = {m["item"] for m in open_list(wb, store)["buy"] if m["item"]}
    items = [{
        "item_id": i["item"], "canonical_name": i["name"],
        "aliases": "; ".join(filter(None, [i["aliases"]] + links.get(i["item"], []))),
        "purchase_intent": "buy" if i["item"] in wanted else "",
        "preferred_stores": "; ".join(names.get(s, s) for s in split_list(i["stores"])),
    } for i in wb.t("Items").rows]
    integration = None
    if sync:
        integration = {"integration_key": store, "store": store_name, "owner": sync.get("owner", ""),
                       "lease_until": sync.get("lease_until", "")}
        integration.update({f: sync.get(f, "") for f in SYNC_FIELDS})
    snap = {
        "household_timezone": wb.config("timezone"),
        "items": items,
        "retail_memory": retail,
        "integrations": [integration] if integration else [],
        # Only lines the importer itself wrote. A line read from a photo of
        # the same receipt must not stop the official copy from arriving.
        "known_source_refs": sorted({l["ref"] for l in wb.t("Lines").rows
                                     if l["ref"] and l["receipt"] in imported}),
        "non_product_item_numbers": sorted(non_products),
    }
    if priority is not None:
        snap["priority_item_numbers"] = priority
    return snap


# -- mutation file -> sheet --------------------------------------------------


def _is_deposit(text: str) -> bool:
    return str(text or "").upper().startswith(DEPOSIT_PREFIXES)


def apply(wb: Workbook, mut: dict, store: str = "costco") -> dict:
    wb.load("Config", "Sources", "Items", "List", "Log", "Products", "Offers", "Receipts", "Lines", "Sync", "v_Items")
    store = slug(store)
    if wb.t("Sources").get(f"store:{store}") is None:
        raise PwError(f"Store {store!r} is not in Sources. Run `pw sync lease {store}` first.")
    if mut.get("events") and "receipts" not in mut:
        raise PwError("This mutation file has events and no receipts. It was written by an older "
                      "costco-sync. Reinstall integrations/costco and run it again. Nothing was written.")
    stamp = mut.get("generated_at") or wb.stamp()
    report = {"receipts": 0, "lines": 0, "closed": [], "superseded": [], "new_products": 0,
              "offers_written": 0, "links_filled": 0}
    products_before = len(wb.t("Products").rows)

    # 1. Receipts. One already in the sheet is skipped whole.
    for rec in mut.get("receipts") or []:
        got = receipts.ingest(wb, {
            "store": store, "source": "import", "receipt_id": rec.get("barcode", ""),
            "date": rec.get("date", ""),
            "lines": [{
                "sku": l.get("item_number", ""), "text": l.get("description", ""),
                "name": l.get("name", ""), "qty": l.get("quantity", ""),
                "unit_price": l.get("unit_price", ""), "ref": l.get("source_ref", ""),
                "kind": "deposit" if _is_deposit(l.get("description")) else l.get("kind", "purchase"),
            } for l in rec.get("lines") or []],
        }, by=IMPORTER)
        if got.get("skipped"):
            continue
        report["receipts"] += 1
        report["lines"] += got["lines"]
        report["closed"] += got["closed"]
        report["superseded"] += got["superseded"]

    # 2. Current offers. The importer sends a row only when something on it
    #    changed, and separately says which numbers it asked about.
    products = wb.t("Products")
    offers = _plain(wb.t("Offers"))
    upserts = mut.get("retail_memory_upserts") or []
    checked = [str(sku) for sku in mut.get("checked_item_numbers") or []]
    missed = [str(sku) for sku in mut.get("missed_item_numbers") or []]
    for r in upserts:
        sku = str(r.get("retailer_sku") or "")
        if not sku:
            continue
        key = f"{store}:{sku}"
        if products.get(key) is None:
            wb.append("Products", {
                "product": key, "store": store, "sku": sku,
                "name": r.get("retailer_name", ""), "receipt_name": r.get("receipt_name", ""),
                "kind": "not_product" if _is_deposit(r.get("receipt_name")) else "",
            })
        if products.get(key)["kind"] == "not_product":
            continue
        old = offers.get(key, {})
        sale = "instant_savings" if "instant_savings" in str(r.get("reduction_kind", "")) else ""
        new = {
            "product": key, "price": r.get("current_price", ""),
            "regular_price": r.get("regular_price", ""),
            "usual_price": r.get("baseline_unit_price", "") or old.get("usual_price", ""),
            "sale": sale, "sale_scope": r.get("sale_scope", "") if sale else "",
            "sale_ends": r.get("reduction_ends_at", "") if sale else "",
            "availability": r.get("availability", ""), "price_scope": r.get("price_scope", ""),
        }
        # The importer also resends a row whose purchase count moved, or whose
        # price is under this household's usual. Neither is a change here.
        moved = any(str(new[k] or "") != old.get(k, "") for k in new)
        offers[key] = dict(
            new, checked_at=old.get("checked_at", ""), misses=old.get("misses", ""),
            changed_at=(r.get("observed_at", "") if moved else "") or old.get("changed_at", ""))
        report["offers_written"] += moved
    # `changed_at` moves when a value moves. `checked_at` records that the
    # store was asked, which is what tells a brief how fresh a quiet row is.
    for sku in checked:
        row = offers.get(f"{store}:{sku}")
        if row is not None:
            row["checked_at"], row["misses"] = stamp, "0"
    for sku in missed:
        key = f"{store}:{sku}"
        if products.get(key) is None or products.get(key)["kind"] == "not_product":
            continue
        row = offers.setdefault(key, {"product": key})
        row["checked_at"] = stamp
        row["misses"] = str(int(float(row.get("misses") or 0)) + 1)
    if upserts or checked or missed:
        wb.replace("Offers", list(offers.values()))
    report["new_products"] = len(products.rows) - products_before

    # 3. Links the importer inferred from an exact name. A link a person set
    #    is never replaced.
    for upd in mut.get("item_alias_updates") or []:
        item = upd.get("item_id", "")
        for alias in split_list(str(upd.get("aliases", "")).replace(",", ";")):
            row = products.get(alias) if alias.startswith(store + ":") else None
            if row is not None and not row["item"] and wb.t("Items").get(item):
                wb.set("Products", alias, {"item": item})
                wb.append("Log", {
                    "id": wb.new_id(), "at": wb.stamp(), "by": IMPORTER, "agent": wb.agent,
                    "item": item, "kind": "set", "value": f"{alias} -> {item}",
                    "raw": "name on the receipt matches the item",
                })
                report["links_filled"] += 1

    # 4. The importer's own row. The lease stays with whoever holds it.
    summary = (mut.get("summary") or {}).get("text", "")
    upsert = mut.get("integration_upsert")
    if upsert or summary:
        rows = _plain(wb.t("Sync"))
        row = rows.setdefault(store, {"store": store})
        for col in SYNC_FIELDS:
            if upsert and upsert.get(col) not in (None, ""):
                row[col] = upsert[col]
        if summary:
            row["last_summary"] = summary
        wb.replace("Sync", list(rows.values()))
    report["summary"] = summary
    # The people-readable copy of item evidence, refreshed once per sync.
    rebuild_v_items(wb)
    wb.commit()
    return report
