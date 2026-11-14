"""What the evidence says about each item, computed when asked.

Nothing here is stored as truth. `v_Items` is a copy for people to look at.

The stock word is deliberately crude and deliberately honest:

  out, low   someone said so lately and nothing has been bought since
  plenty     someone said so recently and nothing suggests otherwise
  ok         bought recently relative to the usual gap
  check      the usual gap has passed, or an old "out" or "low" was never
             followed by a purchase; purchases elsewhere may be unrecorded
  unknown    no usable evidence

It never claims a count. The model reading it still sees what was said, when,
and the buying rhythm, and can judge better than the word does.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from statistics import median

from .schema import OBSERVATIONS

# A leftover instant-savings refund line from an import before costco-sync
# 0.4.2 netted them: its text is `/` then the parent SKU. New imports fold the
# discount into the purchase and emit no such line, so this only corrects old
# data, and never double-counts.
_DISCOUNT_TEXT = re.compile(r"/\s*(\d+)")

MIN_DATES_FOR_RHYTHM = 3
RECENT_GAPS = 12
OK_FRACTION = 0.75        # of the usual gap
SAID_MIN_DAYS = 30        # what someone said stays believable at least this long
NO_RHYTHM_OK_DAYS = 14


def _d(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def purchases(wb) -> list[dict]:
    """Every purchase line that counts, with its item resolved.

    Lines from superseded receipts are left out, which is how a photo receipt
    stops counting once the official receipt for the same trip arrives.
    """
    wb.load("Receipts", "Lines", "Products", "Log")
    receipts = {r["receipt"]: r for r in wb.t("Receipts").rows}
    products = wb.t("Products")
    discounts = _leftover_discounts(wb.t("Lines").rows)
    consumed: set[tuple[str, str]] = set()
    out = []
    for line in wb.t("Lines").rows:
        if line["kind"] != "purchase":
            continue
        rec = receipts.get(line["receipt"])
        if rec is None or rec["status"] == "superseded":
            continue
        when = _d(rec["date"])
        if when is None:
            continue
        product = products.get(line["product"]) if line["product"] else None
        sku = line["product"].split(":", 1)[1] if ":" in line["product"] else ""
        key = (line["receipt"], sku)
        unit_price = line["unit_price"]
        discount = discounts.get(key)
        if discount is not None and key not in consumed:
            # Net the leftover discount into this line's unit price, once.
            unit_price = _net_unit_price(unit_price, line["qty"], discount)
            consumed.add(key)
        out.append({
            "date": when,
            "store": rec["store"],
            "item": line["item"] or (product or {}).get("item", ""),
            "product": line["product"],
            "unit_price": unit_price,
            "receipt": rec["receipt"],
        })
    for row in wb.t("Log").where(kind="bought"):
        when = _d(row["at"])
        if when and row["item"]:
            out.append({"date": when, "store": "", "item": row["item"], "product": "",
                        "unit_price": "", "receipt": ""})
    return out


def _dec(text) -> Decimal | None:
    try:
        return Decimal(str(text))
    except (InvalidOperation, ValueError):
        return None


def _leftover_discounts(lines: list[dict]) -> dict[tuple[str, str], Decimal]:
    """Per (receipt, parent SKU), the total of any leftover discount refund lines."""
    discounts: dict[tuple[str, str], Decimal] = {}
    for line in lines:
        if line["kind"] != "refund":
            continue
        match = _DISCOUNT_TEXT.fullmatch((line["text"] or "").strip())
        amount = _dec(line["unit_price"])
        if not match or amount is None:
            continue
        qty = _dec(line["qty"]) or Decimal(1)
        key = (line["receipt"], match.group(1))
        discounts[key] = discounts.get(key, Decimal(0)) + amount * qty
    return discounts


def _net_unit_price(unit_price, qty, discount: Decimal) -> str:
    gross = _dec(unit_price)
    count = _dec(qty) or Decimal(1)
    if gross is None or count == 0:
        return unit_price
    net = (gross * count - discount) / count
    return str(net.quantize(Decimal("0.01")))


def rhythm(dates: list[date]) -> int | None:
    """Usual days between purchases, from the most recent gaps."""
    days = sorted(set(dates))
    if len(days) < MIN_DATES_FOR_RHYTHM:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:])][-RECENT_GAPS:]
    return round(median(gaps))


def product_stats(wb, today: date) -> dict[str, dict]:
    by: dict[str, list[dict]] = {}
    for p in purchases(wb):
        if p["product"]:
            by.setdefault(p["product"], []).append(p)
    out = {}
    for product, rows in by.items():
        rows.sort(key=lambda r: r["date"])
        dates = sorted({r["date"] for r in rows})
        last = rows[-1]
        out[product] = {
            "dates": dates,
            "buys_12mo": sum(1 for d in dates if (today - d).days <= 365),
            "last_paid": last["unit_price"],
            "last_paid_at": last["date"].isoformat(),
            "usual_gap_days": rhythm(dates),
        }
    return out


def item_stats(wb, today: date) -> dict[str, dict]:
    wb.load("Items", "Log", "List")
    bought: dict[str, list[dict]] = {}
    for p in purchases(wb):
        if p["item"]:
            bought.setdefault(p["item"], []).append(p)
    said: dict[str, dict] = {}
    for row in wb.t("Log").rows:
        if row["kind"] in OBSERVATIONS and row["item"]:
            prev = said.get(row["item"])
            if prev is None or row["at"] >= prev["at"]:
                said[row["item"]] = row
    on_list = {r["item"] for r in wb.t("List").where(status="open", kind="buy") if r["item"]}

    out = {}
    for item in wb.t("Items").rows:
        slug = item["item"]
        rows = sorted(bought.get(slug, []), key=lambda r: r["date"])
        dates = sorted({r["date"] for r in rows})
        last = rows[-1] if rows else None
        gap = rhythm(dates)
        days_since = (today - last["date"]).days if last else None
        obs = said.get(slug)
        out[slug] = {
            "item": slug,
            "name": item["name"],
            "stock": stock_word(obs, last["date"] if last else None, gap, today),
            "last_said": _said_text(obs),
            "last_said_at": (obs or {}).get("at", "")[:10],
            "last_bought": last["date"].isoformat() if last else "",
            "last_bought_store": last["store"] if last else "",
            "buys_12mo": sum(1 for d in dates if (today - d).days <= 365),
            "usual_gap_days": gap if gap is not None else "",
            "days_since": days_since if days_since is not None else "",
            "on_list": "yes" if slug in on_list else "",
        }
    return out


def _said_text(obs: dict | None) -> str:
    if not obs:
        return ""
    words = obs["raw"] or obs["value"]
    return f'{obs["kind"]}: "{words}" ({obs["by"]})' if words else f'{obs["kind"]} ({obs["by"]})'


def stock_word(obs: dict | None, last_bought: date | None, gap: int | None, today: date) -> str:
    if obs is not None:
        said_on = _d(obs["at"])
        bought_since = bool(last_bought and said_on and last_bought >= said_on)
        if not bought_since and said_on is not None:
            # What someone said is believed for a while, then goes stale.
            # People do not report everything, least of all a trip to a store
            # with no receipts here.
            keep_days = max(SAID_MIN_DAYS, 2 * gap) if gap else SAID_MIN_DAYS
            fresh = (today - said_on).days <= keep_days
            if obs["kind"] in ("out", "low", "last_one"):
                return ("out" if obs["kind"] == "out" else "low") if fresh else "check"
            if fresh:
                return "plenty" if obs["kind"] == "plenty" else "ok"
    if last_bought is None:
        return "unknown"
    days = (today - last_bought).days
    if gap is None:
        return "ok" if days <= NO_RHYTHM_OK_DAYS else "unknown"
    return "ok" if days < OK_FRACTION * gap else "check"


def rebuild_v_items(wb) -> list[dict]:
    built = wb.stamp()
    rows = [dict(s, built_at=built) for s in item_stats(wb, wb.now().date()).values()]
    rows.sort(key=lambda r: r["name"].lower())
    wb.replace("v_Items", rows)
    return rows
