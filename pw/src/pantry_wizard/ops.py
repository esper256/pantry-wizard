"""Household operations.

An agent turns one chat message into a list of small operations and hands them
to `apply`. Each operation is one append or one set-by-key. The agent decides
which item was meant and what kind of statement it was; this module owns ids,
timestamps, validation, the audit trail and idempotency.

    {"op": "add", "item": "eggs"}
    {"op": "add", "text": "birthday candles", "qty": "one pack"}
    {"op": "skip", "item": "bananas", "until": "2026-11-18"}
    {"op": "remind", "text": "use the rewards check", "store": "costco"}
    {"op": "done", "item": "eggs"}
    {"op": "saw", "item": "ketchup", "kind": "out"}
    {"op": "new_item", "name": "Kewpie mayo", "stores": "costco"}
    {"op": "set_item", "item": "coffee", "stores": "safeway"}
    {"op": "link", "product": "costco:1025795", "item": "eggs"}
    {"op": "classify", "product": "costco:1440871", "kind": "once"}
    {"op": "ask", "kind": "link", "subject": "costco:1025795", "question": "..."}
    {"op": "answer", "id": "...", "answer": "eggs"}
    {"op": "receipt", "store": "costco", "date": "...", "lines": [...]}
"""

from __future__ import annotations

import re

from . import schema as S
from .store import PwError, Workbook

ITEM_FIELDS = ("name", "aliases", "kind", "stores", "only_at", "rule", "max_price", "keep", "note")


def slug(text: str) -> str:
    text = re.sub(r"['\u2019]", "", (text or "").lower())  # Trader Joe's -> trader-joes
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _list_text(value) -> str:
    """Store lists as `a; b`. Accepts a list or an already-joined string."""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


def split_list(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split(";") if p.strip()]


def _choice(value, allowed, what: str) -> str:
    value = "" if value is None else str(value)
    if value not in allowed:
        raise PwError(f"{what} must be one of {[a for a in allowed if a]} (got {value!r})")
    return value


# -- sources ---------------------------------------------------------------


def _source(wb: Workbook, kind: str, name: str, op: dict) -> str:
    """Return the short key for a person or store, adding it only when asked.

    An unknown name is an error by default, so a typo cannot create a second
    spelling. The agent repeats the operation with "new_source": true when the
    name really is new to the household.
    """
    key = slug(name)
    if not key:
        return ""
    table = wb.t("Sources")
    if table.get(f"{kind}:{key}") is None:
        if not op.get("new_source"):
            known = sorted(r["source"].split(":", 1)[1] for r in table.rows
                           if r["source"].startswith(kind + ":"))
            raise PwError(
                f"Unknown {kind} {name!r}. Known: {known or 'none yet'}. "
                'If this is new to the household, repeat with "new_source": true.'
            )
        wb.append("Sources", {"source": f"{kind}:{key}", "name": str(name).strip()})
    return key


def _stores(wb, value, op) -> str:
    return "; ".join(_source(wb, "store", s, op) for s in split_list(_list_text(value)))


def _item(wb: Workbook, key: str) -> dict:
    row = wb.t("Items").get(key or "")
    if row is None:
        raise PwError(f"No item {key!r}. Use `pw items` to find it, or new_item to create it.")
    return row


# -- apply -------------------------------------------------------------------


def apply(wb: Workbook, ops: list[dict], by: str = "", raw: str = "", ref: str = "") -> list[dict]:
    """Apply a batch from one chat message. One read, one commit."""
    wb.load("Config", "Sources", "List", "Items", "Log", "Questions", "Products")
    results = []
    for n, op in enumerate(ops):
        op = dict(op)
        op.setdefault("by", by)
        op.setdefault("raw", raw)
        if ref and "ref" not in op:
            op["ref"] = f"{ref}#{n}"
        name = op.get("op")
        handler = HANDLERS.get(name)
        if handler is None:
            raise PwError(f"Unknown op {name!r}. Known: {sorted(HANDLERS)}")
        if op.get("ref") and _seen(wb, op["ref"]):
            results.append({"op": name, "skipped": "already recorded"})
            continue
        op["by"] = _source(wb, "person", op["by"], op) if op.get("by") else ""
        if not op["by"] and name != "ask":  # an agent banks a question on its own
            raise PwError("Every operation needs `by`: the person who said it.")
        result = handler(wb, op)
        results.append(dict({"op": name}, **result))
    expire(wb)
    wb.commit()
    return results


def _seen(wb, ref: str) -> bool:
    return any(r["ref"] == ref for r in wb.t("List").rows) or \
        any(r["ref"] == ref for r in wb.t("Log").rows)


def _log(wb, op, kind: str, item: str = "", value: str = "", fixes: str = "", ref: bool = True) -> dict:
    return wb.append("Log", {
        "id": wb.new_id(), "at": wb.stamp(), "by": op["by"], "agent": wb.agent,
        "item": item, "kind": kind, "value": value, "raw": op.get("raw", ""),
        "ref": op.get("ref", "") if ref else "", "fixes": fixes,
    })


# -- the list ----------------------------------------------------------------


def _list_row(wb, op, kind: str) -> dict:
    item = _item(wb, op["item"]) if op.get("item") else None
    text = str(op.get("text") or (item or {}).get("name") or "").strip()
    if not text:
        raise PwError(f"{kind} needs an item or some text")
    return wb.append("List", {
        "id": wb.new_id(), "kind": kind, "item": (item or {}).get("item", ""), "text": text,
        "qty": str(op.get("qty") or "").strip(),  # kept in the requester's words
        "store": _stores(wb, op.get("store"), op),
        "only_if": _choice(op.get("only_if", ""), S.ONLY_IF, "only_if"),
        "max_price": op.get("max_price", ""), "until": op.get("until", ""),
        "by": op["by"], "at": wb.stamp(), "agent": wb.agent,
        "raw": op.get("raw", ""), "ref": op.get("ref", ""), "status": "open",
    })


def op_add(wb, op):
    # Duplicates are allowed on purpose. Two people asking for milk is two
    # facts; they are merged when the list is read and closed together.
    return {"added": _list_row(wb, op, "buy")["text"]}


def op_skip(wb, op):
    row = _list_row(wb, op, "skip")
    return {"skipping": row["text"], "until": row["until"] or "until cancelled"}


def op_remind(wb, op):
    return {"reminder": _list_row(wb, op, "remind")["text"]}


def _open_rows(wb, op) -> list[dict]:
    rows = wb.t("List").where(status="open")
    if op.get("id"):
        return [r for r in rows if r["id"] == op["id"]]
    if op.get("item"):
        return [r for r in rows if r["item"] == op["item"]]
    if op.get("text"):
        want = op["text"].strip().casefold()
        return [r for r in rows if r["text"].casefold() == want]
    raise PwError("done and cancel need an id, an item or the text of the request")


def close_rows(wb, rows: list[dict], status: str, by: str, closed_ref: str = "") -> list[str]:
    for r in rows:
        wb.set("List", r["id"], {
            "status": status, "closed_at": wb.stamp(), "closed_by": by, "closed_ref": closed_ref,
        })
    return [r["text"] for r in rows]


def op_done(wb, op):
    rows = [r for r in _open_rows(wb, op) if r["kind"] in ("buy", "remind")]
    return {"closed": close_rows(wb, rows, "done", op["by"], op.get("closed_ref", ""))}


def op_cancel(wb, op):
    return {"cancelled": close_rows(wb, _open_rows(wb, op), "cancelled", op["by"])}


def expire(wb) -> list[str]:
    """Close rows whose `until` date has passed."""
    today = wb.today()
    rows = [r for r in wb.t("List").where(status="open") if r["until"] and r["until"][:10] < today]
    return close_rows(wb, rows, "expired", "pw")


# -- observations ------------------------------------------------------------


def op_saw(wb, op):
    kind = _choice(op.get("kind"), [k for k in S.LOG_KINDS if k != "set"], "kind")
    item = _item(wb, op.get("item"))
    _log(wb, op, kind, item["item"], str(op.get("value") or ""), op.get("fixes", ""))
    return {"logged": f'{item["name"]}: {kind}'}


# -- items and products ------------------------------------------------------


def _item_fields(wb, op) -> dict:
    fields = {k: op[k] for k in ITEM_FIELDS if k in op}
    if "kind" in fields:
        fields["kind"] = _choice(fields["kind"], S.ITEM_KINDS, "kind")
    if "rule" in fields:
        fields["rule"] = _choice(fields["rule"], S.ITEM_RULES, "rule")
    for col in ("stores", "only_at"):
        if col in fields:
            fields[col] = _stores(wb, fields[col], op)
    if "aliases" in fields:
        fields["aliases"] = _list_text(fields["aliases"])
    return fields


def op_new_item(wb, op):
    name = str(op.get("name") or "").strip()
    if not name:
        raise PwError("new_item needs a name")
    key = slug(op.get("item") or name)
    if wb.t("Items").get(key):
        raise PwError(f"Item {key!r} already exists")
    fields = _item_fields(wb, op)
    wb.append("Items", dict(fields, item=key, name=name))
    _log(wb, op, "set", key, "new item")
    return {"item": key}


def op_set_item(wb, op):
    item = _item(wb, op.get("item"))
    fields = _item_fields(wb, op)
    if not fields:
        raise PwError(f"set_item needs at least one of {list(ITEM_FIELDS)}")
    wb.set("Items", item["item"], fields)
    _log(wb, op, "set", item["item"], "; ".join(f"{k}={v}" for k, v in fields.items()))
    return {"item": item["item"], "set": fields}


def op_link(wb, op):
    product = wb.t("Products").get(op.get("product") or "")
    if product is None:
        raise PwError(f"No product {op.get('product')!r}")
    item = _item(wb, op["item"])["item"] if op.get("item") else ""
    wb.set("Products", product["product"], {"item": item})
    _log(wb, op, "set", item, f'{product["product"]} -> {item or "unlinked"}')
    return {"product": product["product"], "item": item}


def op_classify(wb, op):
    product = wb.t("Products").get(op.get("product") or "")
    if product is None:
        raise PwError(f"No product {op.get('product')!r}")
    fields = {k: op[k] for k in ("kind", "pack_qty", "unit") if k in op}
    if "kind" in fields:
        fields["kind"] = _choice(fields["kind"], S.PRODUCT_KINDS, "kind")
    wb.set("Products", product["product"], fields)
    _log(wb, op, "set", product["item"], f'{product["product"]}: ' +
         "; ".join(f"{k}={v}" for k, v in fields.items()))
    return {"product": product["product"], "set": fields}


# -- questions ---------------------------------------------------------------


def op_ask(wb, op):
    subject = str(op.get("subject") or "")
    kind = str(op.get("kind") or "other")
    if any(q["kind"] == kind and q["subject"] == subject and q["status"] == "open"
           for q in wb.t("Questions").rows):
        return {"skipped": "already asked"}
    row = wb.append("Questions", {
        "id": wb.new_id(), "kind": kind, "subject": subject,
        "question": op.get("question", ""), "options": _list_text(op.get("options")),
        "suggested": op.get("suggested", ""), "evidence": op.get("evidence", ""),
        "asked_by": wb.agent, "asked_at": wb.stamp(), "status": "open",
    })
    return {"question": row["id"]}


def op_answer(wb, op):
    q = wb.t("Questions").get(op.get("id") or "")
    if q is None:
        raise PwError(f"No question {op.get('id')!r}")
    answer = str(op.get("answer") or "").strip()
    status = "dismissed" if op.get("dismiss") else "answered"
    wb.set("Questions", q["id"], {
        "status": status, "answer": answer, "answered_by": op["by"], "answered_at": wb.stamp(),
    })
    applied = None
    if status == "answered" and answer:
        # Two kinds of answer change the sheet directly. Anything else is
        # recorded for the agent to act on with ordinary operations.
        if q["kind"] == "link":
            applied = op_link(wb, dict(op, product=q["subject"],
                                       item="" if answer == "none" else answer))
        elif q["kind"] == "kind":
            applied = op_classify(wb, dict(op, product=q["subject"], kind=answer))
    return {"question": q["id"], "status": status, "applied": applied}


def op_receipt(wb, op):
    from .receipts import ingest
    return ingest(wb, op, by=op["by"])


HANDLERS = {
    "add": op_add, "skip": op_skip, "remind": op_remind, "done": op_done,
    "cancel": op_cancel, "saw": op_saw, "new_item": op_new_item,
    "set_item": op_set_item, "link": op_link, "classify": op_classify,
    "ask": op_ask, "answer": op_answer, "receipt": op_receipt,
}


# -- reads -------------------------------------------------------------------


def open_list(wb: Workbook, store: str = "") -> dict:
    """The list as a person would want it read: one line per thing.

    Rows for the same item are merged. Quantities stay as each person said
    them. A request tied to a store only shows for that store.
    """
    wb.load("List", "Items")
    today = wb.today()
    store = slug(store)
    items = wb.t("Items")
    merged: dict[str, dict] = {}
    skips, reminders = [], []
    for r in wb.t("List").where(status="open"):
        if r["until"] and r["until"][:10] < today:
            continue
        stores = split_list(r["store"])
        only_at = split_list((items.get(r["item"]) or {}).get("only_at", "")) if r["item"] else []
        if store and ((stores and store not in stores) or (only_at and store not in only_at)):
            continue
        if r["kind"] == "skip":
            skips.append({"item": r["item"], "text": r["text"], "until": r["until"], "by": r["by"]})
        elif r["kind"] == "remind":
            reminders.append({"id": r["id"], "text": r["text"], "by": r["by"]})
        else:
            key = r["item"] or r["text"].casefold()
            m = merged.setdefault(key, {
                "item": r["item"], "text": r["text"], "qty": [], "by": [], "since": r["at"][:10],
                "store": stores, "only_at": only_at, "only_if": "", "max_price": "", "ids": [],
            })
            if r["qty"] and r["qty"] not in m["qty"]:
                m["qty"].append(r["qty"])
            if r["by"] not in m["by"]:
                m["by"].append(r["by"])
            m["since"] = min(m["since"], r["at"][:10])
            m["ids"].append(r["id"])
            # One unconditional request outweighs a conditional one.
            m["only_if"] = r["only_if"] if len(m["ids"]) == 1 else (m["only_if"] and r["only_if"])
            m["max_price"] = m["max_price"] or r["max_price"]
    return {"buy": list(merged.values()), "skip": skips, "reminders": reminders}


def open_questions(wb: Workbook, limit: int = 8) -> list[dict]:
    """The next card of questions. Eight fit on one card."""
    rows = [q for q in wb.t("Questions").rows if q["status"] == "open"]
    return [
        {"id": q["id"], "kind": q["kind"], "subject": q["subject"], "question": q["question"],
         "options": split_list(q["options"]), "suggested": q["suggested"], "evidence": q["evidence"]}
        for q in rows[:limit]
    ]
