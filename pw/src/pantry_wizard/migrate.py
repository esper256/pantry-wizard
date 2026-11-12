"""Create an empty workbook, and fill one from a schema 0.3 workbook.

The migration reads the old workbook and writes a new one. The old sheet is
never modified, so it is the rollback.

Nothing household-specific is coded here. People and stores are discovered
from the old data. The old sheet spelled some of them several ways, so the
command line can say which label is which person or store, and which items
were one-time needs.

Run it on a copy first and read the report: every count in it should match
the old workbook.
"""

from __future__ import annotations

import re

from . import schema as S
from .costco_bridge import DEPOSIT_PREFIXES, IMPORTER
from .ops import slug, split_list
from .store import Workbook

IMPORTER_ACTORS = {"costco-sync", "membership import"}
OBSERVATION_TYPES = {
    "observed_out": "out", "observed_low": "low", "observed_plenty": "plenty",
    "quantity_observed": "have", "inventory_reported": "have", "consumed": "used",
    "opened": "used", "correction": "fix",
}
ON_SALE_RULE = re.compile(r"only\s+(if|when)\s+on\s+sale", re.I)
IN_STOCK_RULE = re.compile(r"when(ever)?\s+(it\s+is\s+|it's\s+)?in\s+stock", re.I)
# Receipt-line keys that name the receipt and the SKU:
#   costco:wh:<barcode>:<item number>[:refund][#n]   costco-sync, and photos logged its way
#   photo:<store>:<receipt id>:<item number>[#n]     a photo
# A photo key whose last part is a short line number has no SKU in it.
IMPORT_REF = re.compile(r"^[a-z]+:wh:(?P<barcode>[^:#]+):(?P<sku>[^:#]+)(:refund)?(#\d+)?$")
PHOTO_REF = re.compile(r"^photo:[^:]+:(?P<barcode>[^:#]+):(?P<sku>[^:#]+)(#\d+)?$")


def init(backend, timezone: str = "", currency: str = "") -> None:
    """Write headers for every tab and the starting Config rows."""
    if hasattr(backend, "ensure_tabs"):
        backend.ensure_tabs(S.TABS)
    config = [list(S.TABS["Config"].columns)]
    for key, value, note in S.CONFIG_DEFAULTS:
        value = {"timezone": timezone, "currency": currency}.get(key, value)
        config.append([key, value, note])
    replaces = {name: [list(tab.columns)] for name, tab in S.TABS.items()}
    replaces["Config"] = config
    backend.commit({}, [], replaces)


def _dicts(rows: list[list[str]]) -> list[dict]:
    if not rows:
        return []
    header = rows[0]
    return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header)}
            for r in rows[1:] if any(r)]


def _aliases(pairs: list[str] | None) -> dict[str, str]:
    """`"old label=name"` pairs. An empty name drops the label."""
    out = {}
    for pair in pairs or []:
        label, _, name = pair.partition("=")
        out[label.strip().casefold()] = name.strip()
    return out


def migrate(old: dict[str, list[list[str]]], backend, once: list[str] | None = None,
            people: list[str] | None = None, stores: list[str] | None = None,
            agent: str = "pw-migrate") -> dict:
    cfg = {r["key"]: r["value"] for r in _dicts(old.get("Config", []))}
    init(backend, cfg.get("household_timezone", ""), cfg.get("currency", ""))
    wb = Workbook(backend, agent=agent)
    wb.load(*S.TABS)

    items = _dicts(old.get("Items", []))
    retail = _dicts(old.get("RetailMemory", []))
    events = _dicts(old.get("Events", []))
    once_names = {n.casefold() for n in (once or [])}
    renamed = {"person": _aliases(people), "store": _aliases(stores)}
    report = {"old": {"items": len(items), "retail_rows": len(retail), "events": len(events)},
              "purchases_without_a_receipt": 0}

    def source(kind: str, label: str) -> str:
        """The key for a person or store, declared the first time it is met."""
        label = (label or "").split("(")[0].strip()   # "Sam (whiteboard)" is Sam
        name = renamed[kind].get(label.casefold(), label)
        key = slug(name)
        if key and wb.t("Sources").get(f"{kind}:{key}") is None:
            shown = name if name != name.lower() else name.title()
            wb.append("Sources", {"source": f"{kind}:{key}", "name": shown})
        return key

    def person(actor: str) -> str:
        return IMPORTER if (actor or "").strip().lower() in IMPORTER_ACTORS else source("person", actor)

    # Items. The old opaque id maps to a readable key.
    ids: dict[str, str] = {}
    sku_links: dict[str, str] = {}
    for it in items:
        key = base = slug(it["canonical_name"])
        n = 2
        while wb.t("Items").get(key):
            key, n = f"{base}-{n}", n + 1
        ids[it["item_id"]] = key
        names = []
        for alias in split_list(it.get("aliases", "").replace(",", ";")):
            m = re.fullmatch(r"([a-z]+):(\w+)", alias)
            if m:
                sku_links[f"{m.group(1)}:{m.group(2)}"] = key
            else:
                names.append(alias)
        policy = it.get("item_policy", "")
        wb.append("Items", {
            "item": key, "name": it["canonical_name"], "aliases": "; ".join(names),
            "kind": "once" if it["canonical_name"].casefold() in once_names else "",
            "stores": "; ".join(filter(None, (source("store", s)
                                              for s in split_list(it.get("preferred_stores", ""))))),
            "rule": "on_sale" if ON_SALE_RULE.search(policy) else
                    "when_in_stock" if IN_STOCK_RULE.search(policy) else "",
            "keep": policy,
        })

    # Products and current offers, split out of RetailMemory.
    receipt_names: dict[str, str] = {}
    old_products: set[str] = set()
    for r in retail:
        store = source("store", r["store"])
        sku = r["retailer_sku"]
        key = f"{store}:{sku}"
        receipt_names[key] = r.get("receipt_name", "")
        old_products.add(key)
        if not store or not sku or wb.t("Products").get(key):
            continue
        deposit = r.get("receipt_name", "").upper().startswith(DEPOSIT_PREFIXES)
        wb.append("Products", {
            "product": key, "store": store, "sku": sku, "name": r.get("retailer_name", ""),
            "receipt_name": r.get("receipt_name", ""),
            "item": ids.get(r.get("item_id", ""), "") or sku_links.get(key, ""),
            "kind": "not_product" if deposit else "",
        })
        if deposit:
            continue
        sale = "instant_savings" if "instant_savings" in r.get("reduction_kind", "") else ""
        wb.append("Offers", {
            "product": key, "price": r.get("current_price", ""),
            "regular_price": r.get("regular_price", ""), "usual_price": r.get("baseline_unit_price", ""),
            "sale": sale, "sale_scope": r.get("sale_scope", "") if sale else "",
            "sale_ends": r.get("reduction_ends_at", "") if sale else "",
            "availability": r.get("availability", ""), "price_scope": r.get("price_scope", ""),
            # Schema 0.3 only stamped a row when its value changed, so this is
            # the best available "last checked" and may be older than the truth.
            "changed_at": r.get("observed_at", ""), "checked_at": r.get("observed_at", ""),
        })

    # Receipt lines out of Events; everything else into Log.
    counters: dict[str, int] = {}
    latest_request: dict[str, dict] = {}
    for e in events:
        etype = e["event_type"]
        item = ids.get(e.get("item_id", ""), "")
        by = person(e["actor"])
        ref = e.get("source_ref", "")
        source_type = e.get("source_type", "")
        date = (e.get("occurred_at") or "")[:10]
        store = source("store", e.get("store", ""))
        m = IMPORT_REF.match(ref) or PHOTO_REF.match(ref)
        on_receipt = m or "receipt" in source_type
        if etype in ("purchased", "refunded") and on_receipt and date and store:
            src = "import" if source_type == "receipt_import" else "photo"
            sku = m.group("sku") if m else ""
            if sku and f"{store}:{sku}" not in old_products:
                sku = ""  # a line number, or a product the old sheet never knew
            tail = m.group("barcode") if m else date  # else one receipt per store and day
            key = f"{store}:{tail}" if src == "import" else f"{src}:{store}:{tail}"
            if wb.t("Receipts").get(key) is None:
                wb.append("Receipts", {
                    "receipt": key, "store": store, "date": date, "source": src,
                    "status": "recorded", "by": by, "agent": e.get("written_by", ""),
                    "at": e.get("recorded_at", ""), "lines": 0,
                })
            n = counters[key] = counters.get(key, 0) + 1
            wb.set("Receipts", key, {"lines": n})
            product = f"{store}:{sku}" if sku and wb.t("Products").get(f"{store}:{sku}") else ""
            text = receipt_names.get(product) or e.get("interpretation") or e.get("raw_message", "")
            kind = "refund" if etype == "refunded" else "purchase"
            if text.upper().startswith(DEPOSIT_PREFIXES):
                kind = "deposit"
            wb.append("Lines", {
                "line": f"{key}:{n}", "receipt": key, "n": n, "product": product,
                "item": "" if product and wb.t("Products").get(product)["item"] == item else item,
                "text": text, "qty": e.get("quantity", ""), "unit_price": e.get("price_paid", ""),
                "kind": kind, "ref": ref,
            })
            continue
        if etype == "requested" and item:
            prev = latest_request.get(item)
            if prev is None or e.get("recorded_at", "") >= prev.get("recorded_at", ""):
                latest_request[item] = e
        kind = OBSERVATION_TYPES.get(etype, "said")
        value = "" if etype in OBSERVATION_TYPES else etype
        if etype == "purchased":
            # Someone said they bought it. That counts toward buying rhythm
            # when the item is known, and is kept as said otherwise.
            report["purchases_without_a_receipt"] += 1
            kind = "bought" if item else "said"
            value = " ".join(filter(None, [e.get("store", ""), e.get("price_paid", "")]))
        if e.get("quantity"):
            value = f'{e["quantity"]} {e.get("unit", "")} {value if etype == "purchased" else ""}'.strip()
        # The person's words, then what the agent took them to mean.
        said, meant = e.get("raw_message", ""), e.get("interpretation", "")
        wb.append("Log", {
            "id": e["event_id"], "at": e.get("occurred_at") or e.get("recorded_at", ""),
            "by": by, "agent": e.get("written_by", ""), "item": item,
            "kind": kind, "value": value,
            "raw": f"{said} | {meant}" if said and meant and said != meant else said or meant,
            "ref": ref, "fixes": e.get("supersedes_event_id", ""),
        })

    # The open list, from the intent columns.
    people_keys = [r["source"].split(":", 1)[1] for r in wb.t("Sources").rows
                   if r["source"].startswith("person:")]
    for it in items:
        intent = it.get("purchase_intent", "")
        if intent not in ("buy", "do_not_buy"):
            continue
        key = ids[it["item_id"]]
        req = latest_request.get(key, {})
        wb.append("List", {
            "id": wb.new_id(), "kind": "buy" if intent == "buy" else "skip", "item": key,
            "text": it["canonical_name"], "qty": it.get("requested_quantity", ""),
            "until": it.get("intent_expires_at", ""),
            "by": person(req["actor"]) if req.get("actor") else "",
            "at": req.get("recorded_at") or it.get("updated_at", ""),
            "agent": agent, "raw": req.get("raw_message", ""), "status": "open",
        })

    for row in _dicts(old.get("Integrations", [])):
        store = source("store", row.get("store") or row["integration_key"])
        wb.append("Sync", {
            "store": store,
            **{c: row.get(c, "") for c in S.TABS["Sync"].columns if c != "store"},
        })

    wb.commit()
    t = wb.t
    report["new"] = {
        "items": len(t("Items").rows), "products": len(t("Products").rows),
        "offers": len(t("Offers").rows), "linked_products": len([p for p in t("Products").rows if p["item"]]),
        "receipts": len(t("Receipts").rows), "lines": len(t("Lines").rows),
        "log": len(t("Log").rows), "open_list": len(t("List").rows),
        "people": people_keys,
        "stores": [r["source"].split(":", 1)[1] for r in t("Sources").rows
                   if r["source"].startswith("store:")],
    }
    report["checks"] = {
        "every item migrated": report["new"]["items"] == len(items),
        "every product migrated": report["new"]["products"] == len(old_products),
        "every event is a receipt line or a log row":
            report["new"]["lines"] + report["new"]["log"] == len(events),
    }
    return report

