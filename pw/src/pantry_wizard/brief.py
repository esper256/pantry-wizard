"""The trip brief.

A brief is a pure function of the sheet, so it is computed when asked and
answers in well under a second. It never waits on a store lookup. The agent
sends it straight away, marked provisional with the age of its prices, starts
a refresh of the products that matter, then calls `changes` and either
confirms the brief or says what moved.

What counts as a deal is decided here, in one place, and tested:

- the store's own promotion (instant savings), not merely a price below what
  this household usually pays;
- redeemable in the warehouse (scope `warehouse` or `both`);
- on the shelf (`in_stock`, or `low_stock` with a warning);
- not past its end date.

Anything else is not shown as a deal. One-time purchases and durables are not
shown at all unless someone put them on the list.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .ops import open_list, slug, split_list
from .stats import _d, item_stats, product_stats

SAME_PRICE_MARGIN = 0.10  # a "sale" within ten cents of the usual price is flagged
TIER_MAX_MISSES = 3       # products the store has repeatedly failed to price
TRIP_FRESH_HOURS = 2      # a price checked this recently is not asked for again


def _f(text) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _money(value) -> str:
    number = _f(value)
    return f"${number:,.2f}" if number is not None else "?"


def confirmed_deal(offer: dict, today: date) -> tuple[bool, str]:
    """Is this offer a sale the person can actually get on this trip?"""
    if "instant_savings" not in (offer.get("sale") or ""):
        return False, "no store promotion"
    if offer.get("sale_scope") not in ("warehouse", "both"):
        return False, "not confirmed for the warehouse"
    if offer.get("availability") not in ("in_stock", "low_stock"):
        return False, "not confirmed in stock"
    ends = _d(offer.get("sale_ends", ""))
    if ends is not None and ends < today:
        return False, "sale has ended"
    return True, ""


def relevant(product: dict, stats: dict | None, item: dict | None, today: date) -> bool:
    """Would this household plausibly buy this again on an ordinary trip?"""
    kind = product.get("kind", "")
    if kind in ("not_product", "once"):
        return False
    if item is not None:
        return item.get("kind") != "once" and item.get("rule") != "never"
    dates = (stats or {}).get("dates", [])
    if kind == "consumable":
        return True
    if kind == "seasonal":
        return any(abs(((d.month - today.month + 6) % 12) - 6) <= 1 and d.year < today.year
                   for d in dates)
    # Unclassified: let behaviour decide. Bought once is not a habit.
    return len(dates) >= 2 and (today - dates[-1]).days <= 365


def _buyable_here(item: dict, store: str, linked_stores: set[str]) -> bool:
    only_at = split_list(item.get("only_at", ""))
    if only_at:
        return store in only_at
    stores = split_list(item.get("stores", ""))
    return not stores or store in stores or store in linked_stores


def build(wb, store: str) -> dict:
    wb.load("Config", "Sources", "Items", "List", "Log", "Products", "Offers",
            "Receipts", "Lines", "Sync", "v_Brief")
    store = slug(store)
    today = wb.now().date()
    items = wb.t("Items")
    offers = wb.t("Offers")
    istats = item_stats(wb, today)
    pstats = product_stats(wb, today)
    listed = open_list(wb, store)

    here = [p for p in wb.t("Products").rows if p["store"] == store]
    by_item: dict[str, list[dict]] = {}
    linked_stores: dict[str, set[str]] = {}
    for p in wb.t("Products").rows:
        if p["item"]:
            linked_stores.setdefault(p["item"], set()).add(p["store"])
            if p["store"] == store:
                by_item.setdefault(p["item"], []).append(p)

    def deals_for(item_key: str) -> list[tuple[dict, dict]]:
        out = []
        for p in by_item.get(item_key, []):
            offer = offers.get(p["product"])
            if offer and confirmed_deal(offer, today)[0]:
                out.append((p, offer))
        return out

    def price_note(p: dict, offer: dict) -> str:
        st = pstats.get(p["product"], {})
        text = f'{_money(offer["price"])}'
        if _f(offer.get("regular_price")) is not None:
            text += f' (regular {_money(offer["regular_price"])})'
        if offer.get("sale_ends"):
            text += f' through {offer["sale_ends"][:10]}'
        if st.get("last_paid"):
            text += f'. Last paid {_money(st["last_paid"])} on {st["last_paid_at"]}'
        usual = _f(offer.get("usual_price"))
        price = _f(offer.get("price"))
        if p.get("unit") != "lb" and usual is not None and price is not None \
                and price >= usual - SAME_PRICE_MARGIN:
            text += ". Flag: the sale price is what you usually pay"
        if offer.get("availability") == "low_stock":
            text += ". Low stock, may be gone"
        return text + "."

    sec: dict[str, list[dict]] = {k: [] for k in (
        "reminders", "buy", "waiting_for_sale", "probably_buy",
        "check_before_leaving", "deals", "in_stock_now", "skip")}
    handled: set[str] = set()

    for r in listed["reminders"]:
        sec["reminders"].append({"key": f'remind:{r["id"]}', "text": r["text"]})

    for s in listed["skip"]:
        handled.add(s["item"])
        until = f' until {s["until"][:10]}' if s["until"] else ""
        sec["skip"].append({"key": f'skip:{s["item"] or s["text"]}',
                            "text": f'{s["text"]}: asked not to buy{until} ({s["by"]}).'})

    for m in listed["buy"]:
        ident = m["item"] or m["text"].casefold()
        handled.add(m["item"])
        qty = f' ({"; ".join(m["qty"])})' if m["qty"] else ""
        who = ", ".join(m["by"])
        deals = deals_for(m["item"]) if m["item"] else []
        ceiling = _f(m["max_price"])
        if ceiling is not None:
            deals = [d for d in deals if (_f(d[1]["price"]) or 0) <= ceiling]
        note = f' On sale: {price_note(*deals[0])}' if deals else ""
        if m["only_if"] == "on_sale" and not deals:
            sec["waiting_for_sale"].append({
                "key": f"wait:{ident}",
                "text": f'{m["text"]}{qty}: on the list only if on sale, and it is not. ({who})'})
        else:
            sec["buy"].append({"key": f"buy:{ident}",
                               "text": f'{m["text"]}{qty}, asked by {who} on {m["since"]}.{note}'})

    for key, st in istats.items():
        item = items.get(key)
        if key in handled or item["kind"] == "once" or item["rule"] == "never":
            continue
        if not _buyable_here(item, store, linked_stores.get(key, set())):
            continue
        rhythm = ""
        if st["usual_gap_days"] != "":
            rhythm = (f' Usually bought every {st["usual_gap_days"]} days;'
                      f' last bought {st["days_since"]} days ago.')
        if st["stock"] in ("out", "low"):
            sec["probably_buy"].append({
                "key": f'stock:{key}:{st["stock"]}',
                "text": f'{st["name"]}: {st["last_said"]} on {st["last_said_at"]},'
                        f' nothing bought since.{rhythm}'})
            handled.add(key)
        elif st["stock"] == "check" and st["buys_12mo"] >= 2:
            sec["check_before_leaving"].append({
                "key": f"check:{key}", "text": f'{st["name"]}:{rhythm}'
                " Purchases elsewhere may not be recorded."})

    unconfirmed = 0
    oldest_check = ""
    for p in here:
        offer = offers.get(p["product"])
        if not offer or "instant_savings" not in (offer.get("sale") or ""):
            continue
        item = items.get(p["item"]) if p["item"] else None
        if not relevant(p, pstats.get(p["product"]), item, today):
            continue
        ok, _why = confirmed_deal(offer, today)
        if not ok:
            unconfirmed += 1
            continue
        if p["item"] in listed_items(listed) or p["item"] in {s["item"] for s in listed["skip"] if s["item"]}:
            continue  # already covered in buy, or asked not to buy
        if p["item"] and istats.get(p["item"], {}).get("stock") == "plenty":
            continue
        ceiling = _f((item or {}).get("max_price"))
        if ceiling is not None and (_f(offer["price"]) or 0) > ceiling:
            continue
        standing = (item or {}).get("rule") == "on_sale"
        lead = "You buy this when it is on sale. " if standing else ""
        name = (item or {}).get("name") or p["name"] or p["receipt_name"]
        sec["deals"].append({
            "key": f'deal:{p["product"]}:{offer["price"]}:{offer["sale_ends"][:10]}:{offer["availability"]}',
            "text": f"{name}: {lead}{price_note(p, offer)}", "rank": 0 if standing else 1})
        if offer.get("checked_at") and (not oldest_check or offer["checked_at"] < oldest_check):
            oldest_check = offer["checked_at"]
    sec["deals"].sort(key=lambda d: (d.pop("rank"), d["text"].lower()))

    for item in items.rows:
        if item["rule"] != "when_in_stock" or item["item"] in handled:
            continue
        for p in by_item.get(item["item"], []):
            offer = offers.get(p["product"])
            if offer and offer.get("availability") in ("in_stock", "low_stock"):
                low = " Low stock." if offer["availability"] == "low_stock" else ""
                sec["in_stock_now"].append({
                    "key": f'instock:{p["product"]}:{offer["availability"]}',
                    "text": f'{item["name"]}: in stock at {_money(offer["price"])}.{low}'})

    sync = wb.t("Sync").get(store) or {}
    return {
        "store": store,
        "provisional": True,
        "as_of": oldest_check or sync.get("last_sync_at", ""),
        "built_at": wb.stamp(),
        "sections": {k: v for k, v in sec.items() if v},
        "unconfirmed_sales": unconfirmed,
    }


def listed_items(listed: dict) -> set[str]:
    return {m["item"] for m in listed["buy"] if m["item"]}


TITLES = {
    "reminders": "Reminders", "buy": "On the list", "waiting_for_sale": "On the list, waiting for a sale",
    "probably_buy": "Probably buy", "check_before_leaving": "Check before leaving",
    "deals": "On sale and in the warehouse", "in_stock_now": "In stock now", "skip": "Skip",
}


def render(brief: dict) -> str:
    lines = [f'Brief for {brief["store"]} (provisional; prices as of {brief["as_of"] or "unknown"})']
    for name, rows in brief["sections"].items():
        lines += ["", TITLES[name] + ":"] + [f'- {r["text"]}' for r in rows]
    if brief["unconfirmed_sales"]:
        lines += ["", f'{brief["unconfirmed_sales"]} other sale(s) left out: out of stock, '
                      "online only, or stock unknown."]
    return "\n".join(lines)


def save(wb, brief: dict) -> None:
    keep = [r for r in wb.t("v_Brief").rows if r["store"] != brief["store"]]
    mine = [{"key": r["key"], "store": brief["store"], "section": name, "text": r["text"],
             "built_at": brief["built_at"]}
            for name, rows in brief["sections"].items() for r in rows]
    wb.replace("v_Brief", keep + mine)


def changes(wb, store: str) -> dict:
    """Compare a fresh brief with the last one saved for this store."""
    wb.load("v_Brief")
    store = slug(store)
    before = {r["key"]: r["text"] for r in wb.t("v_Brief").rows if r["store"] == store}
    brief = build(wb, store)
    after = {r["key"]: r["text"] for rows in brief["sections"].values() for r in rows}
    added = [text for key, text in after.items() if key not in before]
    removed = [text for key, text in before.items() if key not in after]
    save(wb, brief)
    return {"store": store, "as_of": brief["as_of"], "confirmed": not added and not removed,
            "new_or_changed": added, "no_longer_true": removed}


def trip_tier(wb, store: str) -> dict:
    """Which products are worth re-checking before or during a trip.

    Tier 0 is what the list and standing rules depend on. Tier 1 is the rest
    of what this household actually rebuys, longest unchecked first. Products
    the store has repeatedly failed to price, and deposit lines, are never
    included. Neither is a price checked in the last couple of hours, so a
    second refresh on the same trip moves on to the next products.
    """
    wb.load("Items", "List", "Products", "Offers", "Receipts", "Lines", "Log")
    store = slug(store)
    today = wb.now().date()
    items, offers = wb.t("Items"), wb.t("Offers")
    pstats = product_stats(wb, today)
    wanted = listed_items(open_list(wb, store)) | {
        i["item"] for i in items.rows if i["rule"] in ("on_sale", "when_in_stock")}
    fresh_since = wb.now() - timedelta(hours=TRIP_FRESH_HOURS)
    tier0, tier1, fresh = [], [], 0
    for p in wb.t("Products").rows:
        if p["store"] != store or p["kind"] == "not_product":
            continue
        offer = offers.get(p["product"]) or {}
        if (_f(offer.get("misses")) or 0) >= TIER_MAX_MISSES:
            continue
        if p["item"] in wanted:
            tier = tier0
        elif relevant(p, pstats.get(p["product"]), items.get(p["item"]) if p["item"] else None, today):
            tier = tier1
        else:
            continue
        checked = _when(offer.get("checked_at", ""))
        if checked is not None and checked > fresh_since:
            fresh += 1
            continue
        tier.append((checked.timestamp() if checked else 0, p["sku"]))
    return {"store": store, "tier0": [sku for _, sku in sorted(tier0)],
            "tier1": [sku for _, sku in sorted(tier1)], "checked_recently": fresh}


def _when(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.astimezone()
