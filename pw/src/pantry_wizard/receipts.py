"""Receipts from any source: a store importer, a photo, or a typed list.

A receipt is one `Receipts` row and one `Lines` row per line. Lines are
append-only. Nothing is ever counted twice, because counts are derived from
lines whose receipt is still `recorded`:

- Ingesting the same receipt again is a no-op. The receipt row is written
  last, so "the same receipt" means one that was saved completely. After an
  attempt that stopped halfway, sending it again writes only what is missing.
- When an official receipt arrives for a trip already logged from a photo, the
  photo receipt is marked `superseded` and its lines stop counting.

A photo receipt is otherwise the record in its own right. Some trips never
reach the importer, for example when another member's card was scanned.
"""

from __future__ import annotations

import hashlib
import re

from . import schema as S
from .store import PwError, Workbook

SAME_TRIP_OVERLAP = 0.6  # share of the smaller receipt's SKUs found on the other
_DATED_TAIL = re.compile(r"\d{4}-\d{2}-\d{2}(:|$)")


def receipt_key(store: str, source: str, receipt_id: str, date: str, total: str, lines=()) -> str:
    """The store's own id when it was printed, else date and total, else date and lines."""
    if receipt_id:
        tail = receipt_id
    elif total:
        tail = f"{date}:{total}"
    else:
        text = "|".join(sorted(f"{l.get('sku', '')}/{l.get('text', '')}/{l.get('unit_price', '')}" for l in lines))
        tail = f"{date}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
    return f"{store}:{tail}" if source == "import" else f"{source}:{store}:{tail}"


def _printed_id(row: dict) -> str:
    """The receipt id inside a key, or "" when the key was built from a date."""
    prefix = f'{row["store"]}:' if row["source"] == "import" else f'{row["source"]}:{row["store"]}:'
    tail = row["receipt"][len(prefix):]
    return "" if _DATED_TAIL.match(tail) else tail


def _same_trip(wb, other: dict, store: str, receipt_id: str, date: str, total: str, skus: set[str]) -> bool:
    """Is `other` a record of the same shopping trip?

    The same printed id settles it. Otherwise the date must match, and then
    either the totals agree or most of the SKUs do. A misread barcode on a
    photo therefore still pairs with the official receipt.
    """
    if other["store"] != store:
        return False
    if receipt_id and _printed_id(other) == receipt_id:
        return True
    if other["date"] != date:
        return False
    try:
        return abs(float(other["total"]) - float(total)) <= 0.01
    except (TypeError, ValueError):
        pass
    theirs = {l["product"].split(":", 1)[1] for l in wb.t("Lines").where(receipt=other["receipt"])
              if l["product"]}
    if not skus or not theirs:
        return False
    return len(skus & theirs) / min(len(skus), len(theirs)) >= SAME_TRIP_OVERLAP


def ingest(wb: Workbook, rec: dict, by: str) -> dict:
    from .ops import _source, close_rows, slug

    wb.load("Sources", "Receipts", "Lines", "Products", "List", "Items", "Log")
    store = _source(wb, "store", rec.get("store", ""), rec)
    if not store:
        raise PwError("A receipt needs a store")
    source = rec.get("source", "photo")
    if source not in S.RECEIPT_SOURCES:
        raise PwError(f"source must be one of {list(S.RECEIPT_SOURCES)}")
    date = str(rec.get("date") or "")[:10]
    total = str(rec.get("total") or "")
    rid = str(rec.get("receipt_id") or "")
    if not date:
        raise PwError("A receipt needs a date")
    lines = rec.get("lines") or []
    key = receipt_key(store, source, rid, date, total, lines)
    receipts = wb.t("Receipts")
    if receipts.get(key) is not None:
        return {"receipt": key, "skipped": "already recorded"}

    # Two records of one trip. An official receipt replaces a photo of it. A
    # photo of a trip that is already recorded adds nothing.
    skus = {str(l.get("sku") or "").strip() for l in lines} - {""}
    status, superseded_by, superseded = "recorded", "", []
    for other in list(receipts.rows):
        if other["status"] != "recorded" or not _same_trip(wb, other, store, rid, date, total, skus):
            continue
        if source == "import" and other["source"] != "import":
            wb.set("Receipts", other["receipt"], {"status": "superseded", "superseded_by": key})
            superseded.append(other["receipt"])
        elif source != "import" and other["source"] == "import":
            status, superseded_by = "superseded", other["receipt"]
        elif source != "import":
            return {"receipt": other["receipt"], "skipped": "already recorded"}

    wb.append("Receipts", {
        "receipt": key, "store": store, "date": date, "total": total, "lines": len(lines),
        "source": source, "status": status, "superseded_by": superseded_by,
        "by": by, "agent": wb.agent, "at": wb.stamp(),
    })

    products, items = wb.t("Products"), wb.t("Items")
    bought_items, unlinked, linked = [], 0, []
    for n, line in enumerate(lines, start=1):
        kind = line.get("kind", "purchase")
        if kind not in S.LINE_KINDS:
            raise PwError(f"line kind must be one of {list(S.LINE_KINDS)}")
        item = slug(line.get("item") or "")
        if item and items.get(item) is None:
            raise PwError(f"Line {n} names item {item!r}, which does not exist")
        product = ""
        sku = str(line.get("sku") or "").strip()
        if sku:
            product = f"{store}:{sku}"
            row = products.get(product)
            if row is None:
                # First sighting. This row is never rewritten by an importer.
                row = wb.append("Products", {
                    "product": product, "store": store, "sku": sku,
                    "name": line.get("name") or line.get("text", ""),
                    "receipt_name": line.get("text", ""), "dept": line.get("dept", ""),
                    "kind": "not_product" if kind == "deposit" else "",
                })
            if item and not row["item"] and kind == "purchase":
                # Whoever read the receipt was sure which item this SKU is.
                # Remember it, so the next receipt needs no one to say so.
                wb.set("Products", product, {"item": item})
                wb.append("Log", {
                    "id": wb.new_id(), "at": wb.stamp(), "by": by, "agent": wb.agent, "item": item,
                    "kind": "set", "value": f"{product} -> {item}", "raw": line.get("text", ""),
                })
                linked.append(product)
        linked_item = (products.get(product) or {}).get("item", "") if product else ""
        resolved = item or linked_item
        if kind == "purchase":
            if resolved:
                bought_items.append(resolved)
            else:
                unlinked += 1
        if wb.t("Lines").get(f"{key}:{n}") is not None:
            continue  # written by an attempt that stopped before the receipt row
        wb.append("Lines", {
            "line": f"{key}:{n}", "receipt": key, "n": n,
            # The line names an item only when the product does not already say so.
            "product": product, "item": "" if item == linked_item else item,
            "text": line.get("text", ""), "qty": line.get("qty", ""),
            "unit_price": line.get("unit_price", ""), "kind": kind, "ref": line.get("ref", ""),
        })

    # A receipt is proof enough. Requests it covers close without asking, and
    # the agent tells the person what closed. A request made after the trip
    # is a new request: a receipt that posts late must not close it.
    closed = []
    if status == "recorded":
        for row in wb.t("List").where(status="open"):
            stores = [s.strip() for s in row["store"].split(";") if s.strip()]
            if row["at"][:10] > date:
                continue
            covered = row["kind"] == "buy" and row["item"] and row["item"] in bought_items \
                and (not stores or store in stores)
            # A reminder for this store was for this trip.
            reminded = row["kind"] == "remind" and store in stores
            if covered or reminded:
                closed += close_rows(wb, [row], "done", by, key)
    return {
        "receipt": key, "lines": len(lines), "closed": sorted(set(closed)),
        "superseded": superseded, "unlinked_lines": unlinked, "linked_products": linked,
        "status": status,
    }
