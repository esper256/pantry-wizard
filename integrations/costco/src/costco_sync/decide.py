"""Decide which receipt lines and price quotes become sheet mutations.

The mutation file is the only thing the Grok Bot applies. It never includes
payment data, and it never changes inventory, quantity, purchase intent, or
preferred stores.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from costco_sync.models import (
    HouseholdItem,
    IntegrationRow,
    NormalizedLine,
    PriceLookupResult,
    PriceQuote,
    Receipt,
    RetailRow,
    SearchHit,
    Snapshot,
)
from costco_sync.money import (
    REDUCTION_THRESHOLD,
    median_money,
    money,
    parse_money,
    quantity_text,
)

_EVENT_NAMESPACE = uuid.UUID("8f1c0c2e-7b1a-5a1e-9c3d-0a6e5b7c9d11")
_ITEM_NAMESPACE = uuid.UUID("c2a91d44-6e08-5b7a-8f31-11d0a9c4e220")
_SEARCH_LIMIT = 20
_LOW_STATES = {"out", "very_low", "probably_low"}

ITEM_COLUMNS = (
    "item_id",
    "canonical_name",
    "aliases",
    "inventory_state",
    "quantity_estimate",
    "quantity_unit",
    "inventory_confidence",
    "inventory_as_of",
    "purchase_intent",
    "requested_quantity",
    "intent_expires_at",
    "preferred_stores",
    "item_policy",
    "state_summary",
    "last_event_id",
    "updated_at",
    "updated_by",
)

RETAIL_COLUMNS = (
    "retail_key",
    "item_id",
    "store",
    "location",
    "retailer_sku",
    "retailer_name",
    "receipt_name",
    "last_paid_unit_price",
    "last_paid_at",
    "baseline_unit_price",
    "purchase_count",
    "current_price",
    "regular_price",
    "reduction_kind",
    "reduction_ends_at",
    "price_scope",
    "observed_at",
)

EVENT_COLUMNS = (
    "event_id",
    "recorded_at",
    "occurred_at",
    "actor",
    "written_by",
    "item_id",
    "event_type",
    "quantity",
    "unit",
    "store",
    "price_paid",
    "raw_message",
    "interpretation",
    "source_type",
    "source_ref",
    "supersedes_event_id",
)


def snapshot_from_json(data: dict) -> Snapshot:
    items = [
        HouseholdItem(
            item_id=str(row.get("item_id") or ""),
            canonical_name=str(row.get("canonical_name") or ""),
            aliases=str(row.get("aliases") or ""),
            inventory_state=str(row.get("inventory_state") or ""),
            purchase_intent=str(row.get("purchase_intent") or ""),
            preferred_stores=str(row.get("preferred_stores") or ""),
        )
        for row in data.get("items") or []
        if row.get("item_id")
    ]
    retail = [
        RetailRow(
            retail_key=str(row.get("retail_key") or ""),
            item_id=str(row.get("item_id") or ""),
            store=str(row.get("store") or "Costco"),
            location=str(row.get("location") or ""),
            retailer_sku=str(row.get("retailer_sku") or ""),
            retailer_name=str(row.get("retailer_name") or ""),
            receipt_name=str(row.get("receipt_name") or ""),
            last_paid_unit_price=str(row.get("last_paid_unit_price") or ""),
            last_paid_at=str(row.get("last_paid_at") or ""),
            baseline_unit_price=str(row.get("baseline_unit_price") or ""),
            purchase_count=str(row.get("purchase_count") or ""),
            current_price=str(row.get("current_price") or ""),
            regular_price=str(row.get("regular_price") or ""),
            reduction_kind=str(row.get("reduction_kind") or ""),
            reduction_ends_at=str(row.get("reduction_ends_at") or ""),
            price_scope=str(row.get("price_scope") or ""),
            observed_at=str(row.get("observed_at") or ""),
        )
        for row in data.get("retail_memory") or []
        if row.get("retail_key")
    ]
    refs = {str(ref) for ref in data.get("known_source_refs") or [] if ref}
    integrations = [
        IntegrationRow(
            integration_key=str(row.get("integration_key") or ""),
            store=str(row.get("store") or ""),
            status=str(row.get("status") or ""),
            location=str(row.get("location") or ""),
            membership_fingerprint=str(row.get("membership_fingerprint") or ""),
            owner=str(row.get("owner") or ""),
            lease_until=str(row.get("lease_until") or ""),
            history_from=str(row.get("history_from") or ""),
            history_through=str(row.get("history_through") or ""),
            last_sync_at=str(row.get("last_sync_at") or ""),
            last_summary=str(row.get("last_summary") or ""),
        )
        for row in data.get("integrations") or []
        if row.get("integration_key")
    ]
    return Snapshot(
        household_timezone=str(data.get("household_timezone") or "UTC"),
        preferred_costco_warehouse=str(data.get("preferred_costco_warehouse") or ""),
        items=items,
        retail_memory=retail,
        integrations=integrations,
        known_source_refs=refs,
    )


def costco_integration(snapshot: Snapshot) -> IntegrationRow | None:
    for row in snapshot.integrations:
        if row.integration_key == "costco":
            return row
    return None


def home_location(snapshot: Snapshot) -> str:
    """Confirmed Costco warehouse. The integration row wins over the legacy config key."""
    row = costco_integration(snapshot)
    if row is not None and row.location.strip():
        return row.location.strip()
    return (snapshot.preferred_costco_warehouse or "").strip()


def warehouse_number(text: str) -> str:
    match = re.match(r"\s*(\d+)", text or "")
    return match.group(1) if match else ""


def build_mutations(
    snapshot: Snapshot,
    receipts: list[Receipt],
    *,
    names: dict[str, str],
    price_lookup,
    search,
    baseline_samples: dict[str, list[str]],
    already_sampled: set[str],
    now: datetime,
) -> tuple[dict, dict[str, list[str]], set[str]]:
    """Return mutations, updated baseline samples, and refs newly added to samples."""
    generated_at = _format_now(now, snapshot.household_timezone)
    home_text = home_location(snapshot)
    home = warehouse_number(home_text)
    home_name = _warehouse_name(home_text, receipts, home)

    items = list(snapshot.items)
    rows = {row.retail_key: _copy_row(row) for row in snapshot.retail_memory}
    touched: set[str] = set()
    samples = {key: list(values) for key, values in baseline_samples.items()}
    newly_sampled: set[str] = set()
    alias_updates: dict[str, str] = {}
    new_items: dict[str, dict] = {}
    events: list[dict] = []

    search_links = _search_links(snapshot, search, home)
    for item_id, sku in search_links.items():
        item = _item_by_id(items, item_id)
        if item is not None:
            _remember_alias(item, sku, alias_updates)

    interest = _interest_skus(snapshot, receipts, search_links)
    lookup = price_lookup(sorted(interest), home) if interest and home else PriceLookupResult(ok=True, quotes=[])
    quotes = {quote.item_number: quote for quote in lookup.quotes}

    for receipt in receipts:
        for line in receipt.lines:
            if line.source_ref in snapshot.known_source_refs:
                continue
            names_for_line = [names.get(line.item_number, ""), line.description, quotes.get(line.item_number, PriceQuote(line.item_number)).product_name]
            item = _match_item(items, line.item_number, names_for_line)
            if item is None and line.kind == "purchase":
                item = _create_item(
                    items,
                    new_items,
                    line,
                    names.get(line.item_number) or quotes.get(line.item_number, PriceQuote(line.item_number)).product_name,
                    generated_at,
                )
            if item is not None:
                _remember_alias(item, line.item_number, alias_updates)
            event = _line_event(line, receipt, item.item_id if item else "", generated_at)
            events.append(event)
            if item is not None and line.kind == "purchase" and item.item_id in new_items:
                new_items[item.item_id]["last_event_id"] = event["event_id"]

            if line.kind != "purchase":
                continue
            key = _retail_key(receipt.warehouse_number or home, line.item_number)
            row = _ensure_row(rows, key, line.item_number, _location(receipt), item.item_id if item else "")
            touched.add(key)
            _fold_purchase(row, line, receipt, samples, already_sampled, newly_sampled, names, quotes)

    if lookup.ok and home:
        observed_day = generated_at[:10]
        for sku in sorted(interest):
            quote = quotes.get(sku)
            if quote is None or quote.current_price is None:
                continue
            key = _retail_key(home, sku)
            item = _match_item(items, sku, [quote.product_name, names.get(sku, "")])
            row = rows.get(key)
            if row is None:
                row = _blank_row(key, sku, _location_text(home, home_name), item.item_id if item else "")
                rows[key] = row
            touched.add(key)
            if item is not None:
                row.item_id = item.item_id
            if quote.product_name and not row.retailer_name:
                row.retailer_name = quote.product_name
            elif quote.product_name and len(quote.product_name) > len(row.retailer_name):
                row.retailer_name = quote.product_name
            _apply_quote(row, quote, samples.get(key, []), generated_at)
            if row.reduction_kind:
                deal_ref = _deal_source_ref(home, sku, row.current_price, row.reduction_kind)
                if deal_ref not in snapshot.known_source_refs:
                    events.append(
                        _deal_event(row, item.item_id if item else "", deal_ref, observed_day, generated_at)
                    )

    for item_id, aliases in list(alias_updates.items()):
        if item_id in new_items:
            new_items[item_id]["aliases"] = aliases
            alias_updates.pop(item_id)

    mutations = {
        "generated_at": generated_at,
        "new_items": [new_items[key] for key in sorted(new_items)],
        "item_alias_updates": [
            {"item_id": item_id, "aliases": aliases}
            for item_id, aliases in sorted(alias_updates.items())
        ],
        "events": sorted(events, key=lambda event: event["source_ref"]),
        "retail_memory_upserts": [
            _row_dict(rows[key]) for key in sorted(touched)
        ],
    }
    return mutations, samples, newly_sampled


def _search_links(snapshot: Snapshot, search, home: str) -> dict[str, str]:
    if not home or search is None:
        return {}
    candidates = [item for item in snapshot.items if _needs_link(item)]
    candidates.sort(key=_attention_rank)
    links: dict[str, str] = {}
    for item in candidates[:_SEARCH_LIMIT]:
        hits = search(item.canonical_name, home) or []
        sku = _confident_sku(item, hits)
        if sku:
            links[item.item_id] = sku
    return links


def _interest_skus(snapshot: Snapshot, receipts: list[Receipt], search_links: dict[str, str]) -> set[str]:
    skus: set[str] = set()
    for row in snapshot.retail_memory:
        if row.store.casefold() == "costco" and row.retailer_sku:
            skus.add(row.retailer_sku)
    for receipt in receipts:
        for line in receipt.lines:
            if line.kind == "purchase":
                skus.add(line.item_number)
    skus.update(search_links.values())
    return skus


def _needs_link(item: HouseholdItem) -> bool:
    if _costco_aliases(item):
        return False
    if item.purchase_intent.strip().casefold() == "buy":
        return True
    if item.inventory_state.strip().casefold() in _LOW_STATES:
        return True
    return re.search(r"\bcostco\b", item.preferred_stores or "", re.IGNORECASE) is not None


def _attention_rank(item: HouseholdItem) -> tuple:
    if item.purchase_intent.strip().casefold() == "buy":
        group = 0
    elif item.inventory_state.strip().casefold() in _LOW_STATES:
        group = 1
    else:
        group = 2
    return (group, item.canonical_name.casefold())


def _confident_sku(item: HouseholdItem, hits: list[SearchHit]) -> str | None:
    want = _normalize(item.canonical_name)
    if len(want) < 3:
        return None
    words = want.split()
    matched: list[str] = []
    for hit in hits:
        got = _normalize(hit.name)
        exact = got == want
        phrase = len(words) >= 2 and f" {want} " in f" {got} "
        if exact or phrase:
            if hit.item_number not in matched:
                matched.append(hit.item_number)
    if len(matched) == 1:
        return matched[0]
    return None


def _fold_purchase(row, line, receipt, samples, already_sampled, newly_sampled, names, quotes) -> None:
    count = int(row.purchase_count or "0")
    row.purchase_count = str(count + 1)
    if line.description:
        row.receipt_name = line.description
    product_name = names.get(line.item_number) or quotes.get(line.item_number, PriceQuote(line.item_number)).product_name
    if product_name and (not row.retailer_name or len(product_name) > len(row.retailer_name)):
        row.retailer_name = product_name
    if not row.last_paid_at or receipt.occurred_on >= row.last_paid_at:
        row.last_paid_unit_price = money(line.unit_price_paid)
        row.last_paid_at = receipt.occurred_on
    if line.source_ref in already_sampled:
        _ensure_seed(row, samples)
        _set_baseline(row, samples)
        return
    if line.had_instant_savings:
        _ensure_seed(row, samples)
        return
    _ensure_seed(row, samples)
    samples.setdefault(row.retail_key, []).append(money(line.unit_price_paid))
    newly_sampled.add(line.source_ref)
    _set_baseline(row, samples)


def _set_baseline(row: RetailRow, samples: dict[str, list[str]]) -> None:
    values = [parse_money(sample) for sample in samples.get(row.retail_key, [])]
    midpoint = median_money([value for value in values if value is not None])
    if midpoint is not None:
        row.baseline_unit_price = money(midpoint)


def _ensure_seed(row: RetailRow, samples: dict[str, list[str]]) -> None:
    bucket = samples.setdefault(row.retail_key, [])
    if bucket or not row.baseline_unit_price:
        return
    bucket.append(row.baseline_unit_price)


def _apply_quote(row: RetailRow, quote: PriceQuote, samples: list[str], observed_at: str) -> None:
    row.current_price = money(quote.current_price)
    row.regular_price = money(quote.regular_price) if quote.regular_price is not None else row.regular_price
    row.price_scope = quote.price_scope
    row.observed_at = observed_at
    row.reduction_ends_at = quote.reduction_ends_at
    baseline_values = [parse_money(sample) for sample in samples]
    baseline_values = [value for value in baseline_values if value is not None]
    if not baseline_values and row.baseline_unit_price:
        parsed = parse_money(row.baseline_unit_price)
        if parsed is not None:
            baseline_values = [parsed]
    baseline = median_money(baseline_values)
    row.reduction_kind = _reduction_kind(quote, baseline)


def _reduction_kind(quote: PriceQuote, baseline: Decimal | None) -> str:
    kinds: list[str] = []
    current = quote.current_price
    regular = quote.regular_price
    if quote.explicit_instant_savings or (
        current is not None and regular is not None and regular - current >= REDUCTION_THRESHOLD
    ):
        kinds.append("instant_savings")
    if current is not None and baseline is not None and baseline - current >= REDUCTION_THRESHOLD:
        kinds.append("below_baseline")
    return ",".join(kinds)


def _line_event(line: NormalizedLine, receipt: Receipt, item_id: str, generated_at: str) -> dict:
    location = _location(receipt)
    if line.kind == "refund":
        event_type = "refunded"
        interpretation = (
            f"Costco warehouse {location} receipt {receipt.barcode}: "
            f"refunded {quantity_text(line.quantity)} {line.description}."
        )
    else:
        event_type = "purchased"
        savings = " after instant savings" if line.had_instant_savings else ""
        interpretation = (
            f"Costco warehouse {location} receipt {receipt.barcode}: "
            f"{quantity_text(line.quantity)} {line.description} "
            f"at {money(line.unit_price_paid)} each{savings}."
        )
    return _event(
        event_type=event_type,
        item_id=item_id,
        quantity=quantity_text(line.quantity),
        price_paid=money(line.unit_price_paid),
        occurred_at=receipt.occurred_on,
        recorded_at=generated_at,
        interpretation=interpretation,
        source_type="receipt_import",
        source_ref=line.source_ref,
    )


def _deal_event(row: RetailRow, item_id: str, source_ref: str, occurred_on: str, generated_at: str) -> dict:
    name = row.retailer_name or row.receipt_name or row.retailer_sku
    regular = f", regular {row.regular_price}" if row.regular_price else ""
    ends = f", through {row.reduction_ends_at}" if row.reduction_ends_at else ""
    scope = row.price_scope or "unspecified"
    interpretation = (
        f"Costco {row.location} {name} current {row.current_price}{regular}{ends}. "
        f"Price scope: {scope}. Reduction: {row.reduction_kind}."
    )
    return _event(
        event_type="deal_observed",
        item_id=item_id,
        quantity="",
        price_paid=row.current_price,
        occurred_at=occurred_on,
        recorded_at=generated_at,
        interpretation=interpretation,
        source_type="costco_price_check",
        source_ref=source_ref,
    )


def _event(**kwargs) -> dict:
    event = {column: "" for column in EVENT_COLUMNS}
    event.update(kwargs)
    event["event_id"] = str(uuid.uuid5(_EVENT_NAMESPACE, kwargs["source_ref"]))
    event["actor"] = "membership import"
    event["written_by"] = "costco-sync"
    event["store"] = "Costco"
    return event


def _create_item(items, new_items, line: NormalizedLine, product_name: str, generated_at: str) -> HouseholdItem:
    item_id = str(uuid.uuid5(_ITEM_NAMESPACE, f"costco-item:{line.item_number}"))
    existing = _item_by_id(items, item_id)
    if existing is not None:
        return existing
    canonical = product_name or line.description or f"Costco {line.item_number}"
    item = HouseholdItem(
        item_id=item_id,
        canonical_name=canonical,
        aliases=f"costco:{line.item_number}",
        inventory_state="unknown",
        purchase_intent="",
        preferred_stores="",
    )
    items.append(item)
    new_items[item_id] = {
        "item_id": item_id,
        "canonical_name": canonical,
        "aliases": item.aliases,
        "inventory_state": "unknown",
        "quantity_estimate": "",
        "quantity_unit": "",
        "inventory_confidence": "low",
        "inventory_as_of": "",
        "purchase_intent": "",
        "requested_quantity": "",
        "intent_expires_at": "",
        "preferred_stores": "",
        "item_policy": "",
        "state_summary": "Seen on Costco warehouse receipts; current stock unknown.",
        "last_event_id": "",
        "updated_at": generated_at,
        "updated_by": "costco-sync",
    }
    return item


def _remember_alias(item: HouseholdItem, item_number: str, updates: dict[str, str]) -> None:
    token = f"costco:{item_number}"
    parts = _split_aliases(item.aliases)
    if token in parts:
        return
    if item.aliases.strip():
        item.aliases = f"{item.aliases.strip()}; {token}"
    else:
        item.aliases = token
    updates[item.item_id] = item.aliases


def _match_item(items: list[HouseholdItem], item_number: str, names: list[str]) -> HouseholdItem | None:
    token = f"costco:{item_number}"
    for item in items:
        if token in _split_aliases(item.aliases):
            return item
    wanted = {_normalize(name) for name in names if _normalize(name)}
    if not wanted:
        return None
    for item in items:
        candidates = [_normalize(item.canonical_name), *(_normalize(part) for part in _split_aliases(item.aliases))]
        if any(candidate and candidate in wanted for candidate in candidates):
            return item
    return None


def _item_by_id(items: list[HouseholdItem], item_id: str) -> HouseholdItem | None:
    for item in items:
        if item.item_id == item_id:
            return item
    return None


def _costco_aliases(item: HouseholdItem) -> list[str]:
    return [part for part in _split_aliases(item.aliases) if re.fullmatch(r"costco:\d+", part)]


def _split_aliases(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", raw or "") if part.strip()]


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).split())


def _ensure_row(rows, key, sku, location, item_id) -> RetailRow:
    row = rows.get(key)
    if row is None:
        row = _blank_row(key, sku, location, item_id)
        rows[key] = row
    elif item_id and not row.item_id:
        row.item_id = item_id
    return row


def _blank_row(key: str, sku: str, location: str, item_id: str) -> RetailRow:
    return RetailRow(
        retail_key=key,
        item_id=item_id,
        store="Costco",
        location=location,
        retailer_sku=sku,
        purchase_count="0",
    )


def _copy_row(row: RetailRow) -> RetailRow:
    return RetailRow(**{field: getattr(row, field) for field in RETAIL_COLUMNS})


def _row_dict(row: RetailRow) -> dict:
    return {field: getattr(row, field) for field in RETAIL_COLUMNS}


def _retail_key(warehouse: str, sku: str) -> str:
    return f"costco:{warehouse}:{sku}"


def _deal_source_ref(warehouse: str, sku: str, current_price: str, kind: str) -> str:
    return f"costco:deal:{warehouse}:{sku}:{current_price}:{kind}"


def _location(receipt: Receipt) -> str:
    return _location_text(receipt.warehouse_number, receipt.warehouse_name)


def _location_text(number: str, name: str) -> str:
    name = (name or "").strip()
    if name and number and name.startswith(number):
        return name
    return f"{number} {name}".strip()


def _warehouse_name(preferred: str, receipts: list[Receipt], home: str) -> str:
    rest = re.sub(r"^\s*\d+\s*", "", preferred or "").strip()
    if rest:
        return rest
    for receipt in receipts:
        if receipt.warehouse_number == home and receipt.warehouse_name:
            return receipt.warehouse_name
    return ""


def _format_now(now: datetime, tz_name: str) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        zone = timezone.utc
    return now.astimezone(zone).isoformat(timespec="seconds")
