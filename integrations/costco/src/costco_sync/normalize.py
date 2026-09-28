"""Turn a Costco receipt payload into purchase and refund lines.

Discount children are the lines whose description is ``/`` plus the parent
item number and whose amount is negative. Fees, deposits, and those
discount rows are not products. Payment and membership fields are ignored.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from costco_sync.models import NormalizedLine, RawLine, Receipt
from costco_sync.money import to_decimal

_FEE_PHRASES = (
    "BOTTLE DEPOSIT",
    "BOTTLE FEE",
    "BAG FEE",
    "ECO FEE",
    "RECYCLE FEE",
    "CRV",
)
_CENT = Decimal("0.01")


def parse_receipt_detail(payload: dict) -> Receipt:
    receipt = _unwrap_receipt(payload)
    barcode = str(receipt.get("transactionBarcode") or receipt.get("barcode") or "")
    warehouse_number = str(receipt.get("warehouseNumber") or "")
    warehouse_name = str(receipt.get("warehouseName") or receipt.get("warehouseShortName") or "")
    occurred_on = _occurred_on(
        str(receipt.get("transactionDate") or receipt.get("transactionDateTime") or "")
    )
    lines = _pair_lines(receipt.get("itemArray") or [], barcode)
    return Receipt(
        barcode=barcode,
        warehouse_number=warehouse_number,
        warehouse_name=warehouse_name,
        occurred_on=occurred_on,
        lines=lines,
    )


def barcodes_from_list(payload: dict) -> list[str]:
    receipts = (
        payload.get("data", {})
        .get("receiptsWithCounts", {})
        .get("receipts", [])
    )
    if not receipts and isinstance(payload.get("receipts"), list):
        receipts = payload["receipts"]
    barcodes: list[str] = []
    for receipt in receipts:
        document_type = str(receipt.get("documentType") or "").casefold().replace(" ", "")
        if document_type in {"gas", "gasstation", "carwash"}:
            continue
        barcode = str(receipt.get("transactionBarcode") or "")
        if barcode:
            barcodes.append(barcode)
    return barcodes


def parse_catalog_prices(catalog_items: list[dict]) -> list:
    """Read price fields from a products GraphQL ``catalogData`` list.

    A ``warehousePrice`` is the warehouse scope. Generic ``price`` fields are
    recorded as online, because this endpoint's ordinary price is the
    costco.com member price unless a warehouse field is present.
    """
    from costco_sync.models import PriceQuote

    quotes = []
    for item in catalog_items:
        item_number = str(item.get("itemNumber") or "")
        if not item_number:
            continue
        description = item.get("description") or {}
        if isinstance(description, dict):
            name = str(description.get("shortDescription") or "")
        else:
            name = str(description or "")
        warehouse_price = _first_decimal(item, ("warehousePrice", "warehouseUnitPrice", "inWarehousePrice"))
        generic_price = _first_decimal(item, ("offerPrice", "price", "unitPrice", "sellPrice"))
        regular = _first_decimal(item, ("listPrice", "regularPrice", "basePrice", "originalPrice"))
        if warehouse_price is not None:
            current = warehouse_price
            scope = "warehouse"
        elif generic_price is not None:
            current = generic_price
            scope = "online"
        else:
            current = None
            scope = ""
        ends = str(item.get("priceValidThrough") or item.get("reductionEndsAt") or "")
        explicit = bool(item.get("explicitInstantSavings") or item.get("instantSavings"))
        quotes.append(
            PriceQuote(
                item_number=item_number,
                current_price=current,
                regular_price=regular,
                reduction_ends_at=_date_only(ends),
                price_scope=scope,
                product_name=name,
                explicit_instant_savings=explicit,
            )
        )
    return quotes


def _unwrap_receipt(payload: dict) -> dict:
    nested = (
        payload.get("data", {})
        .get("receiptsWithCounts", {})
        .get("receipts", [])
    )
    if nested:
        return nested[0]
    return payload


def _pair_lines(raw_items: list[dict], barcode: str) -> list[NormalizedLine]:
    purchases: list[RawLine] = []
    refunds: list[RawLine] = []
    savings: dict[str, Decimal] = {}

    for raw in raw_items:
        description = _description(raw)
        item_number = str(raw.get("itemNumber") or "").strip()
        amount = to_decimal(raw.get("amount")) or Decimal(0)
        unit = to_decimal(raw.get("unit"))
        if unit is None:
            unit = Decimal(1)
        parent = _discount_parent(description)
        if parent and amount < 0:
            savings[parent] = savings.get(parent, Decimal(0)) + abs(amount)
            continue
        if not item_number or _is_fee(description):
            continue
        line = RawLine(item_number=item_number, description=description, unit=unit, amount=amount)
        if amount < 0 or unit < 0:
            refunds.append(line)
        else:
            purchases.append(line)

    shares = _allocate_savings(purchases, savings)
    lines: list[NormalizedLine] = []
    purchase_counts: dict[str, int] = {}
    for index, line in enumerate(purchases):
        if line.unit == 0:
            continue
        purchase_counts[line.item_number] = purchase_counts.get(line.item_number, 0) + 1
        share = shares[index]
        pre = (line.amount / line.unit).quantize(_CENT, rounding=ROUND_HALF_UP)
        paid = ((line.amount - share) / line.unit).quantize(_CENT, rounding=ROUND_HALF_UP)
        lines.append(
            NormalizedLine(
                kind="purchase",
                item_number=line.item_number,
                description=line.description,
                quantity=abs(line.unit),
                unit_price_paid=paid,
                pre_savings_unit_price=pre,
                had_instant_savings=share > 0,
                source_ref="",
            )
        )
    # Source refs need the final duplicate count. Rebuild them now that we know it.
    seen: dict[str, int] = {}
    fixed: list[NormalizedLine] = []
    for line in lines:
        seen[line.item_number] = seen.get(line.item_number, 0) + 1
        ordinal = seen[line.item_number]
        total = purchase_counts[line.item_number]
        fixed.append(
            NormalizedLine(
                kind=line.kind,
                item_number=line.item_number,
                description=line.description,
                quantity=line.quantity,
                unit_price_paid=line.unit_price_paid,
                pre_savings_unit_price=line.pre_savings_unit_price,
                had_instant_savings=line.had_instant_savings,
                source_ref=_source_ref(barcode, line.item_number, "purchase", ordinal, total),
            )
        )

    refund_counts: dict[str, int] = {}
    refund_lines: list[NormalizedLine] = []
    prepared: list[tuple[RawLine, int]] = []
    for line in refunds:
        if line.unit == 0 and line.amount == 0:
            continue
        refund_counts[line.item_number] = refund_counts.get(line.item_number, 0) + 1
        prepared.append((line, refund_counts[line.item_number]))
    for line, ordinal in prepared:
        unit = abs(line.unit) if line.unit != 0 else Decimal(1)
        paid = (abs(line.amount) / unit).quantize(_CENT, rounding=ROUND_HALF_UP)
        refund_lines.append(
            NormalizedLine(
                kind="refund",
                item_number=line.item_number,
                description=line.description,
                quantity=unit,
                unit_price_paid=paid,
                pre_savings_unit_price=paid,
                had_instant_savings=False,
                source_ref=_source_ref(
                    barcode, line.item_number, "refund", ordinal, refund_counts[line.item_number]
                ),
            )
        )
    return fixed + refund_lines


def _allocate_savings(purchases: list[RawLine], savings: dict[str, Decimal]) -> list[Decimal]:
    shares = [Decimal(0) for _ in purchases]
    groups: dict[str, list[int]] = {}
    for index, line in enumerate(purchases):
        groups.setdefault(line.item_number, []).append(index)
    for parent, total in savings.items():
        indexes = groups.get(parent, [])
        if not indexes or total <= 0:
            continue
        gross = sum((purchases[i].amount for i in indexes), Decimal(0))
        if gross <= 0:
            continue
        remaining = total
        for position, index in enumerate(indexes):
            if position == len(indexes) - 1:
                share = remaining
            else:
                share = (total * purchases[index].amount / gross).quantize(_CENT, rounding=ROUND_HALF_UP)
                remaining -= share
            shares[index] = share
    return shares


def _source_ref(barcode: str, item_number: str, kind: str, ordinal: int, total: int | None) -> str:
    base = f"costco:wh:{barcode}:{item_number}"
    if kind == "refund":
        base = f"{base}:refund"
    if total is not None and total > 1:
        return f"{base}#{ordinal}"
    return base


def _description(raw: dict) -> str:
    first = str(raw.get("itemDescription01") or raw.get("description") or "").strip()
    second = str(raw.get("itemDescription02") or "").strip()
    if second and not second.startswith("/"):
        return f"{first} {second}".strip()
    return first


def _discount_parent(description: str) -> str | None:
    match = re.fullmatch(r"/(\d+)", description.strip())
    if match:
        return match.group(1)
    return None


def _is_fee(description: str) -> bool:
    text = description.upper()
    if any(phrase in text for phrase in _FEE_PHRASES):
        return True
    return re.search(r"\bTAX\b", text) is not None


def _occurred_on(value: str) -> str:
    return _date_only(value)


def _date_only(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "T" in text:
        text = text.split("T", 1)[0]
    for fmt, chunk in (("%Y-%m-%d", text[:10]), ("%m/%d/%Y", text), ("%m/%d/%y", text)):
        try:
            return datetime.strptime(chunk, fmt).date().isoformat()
        except ValueError:
            continue
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(1)), int(match.group(2))).isoformat()
        except ValueError:
            return ""
    return ""


def _first_decimal(item: dict, keys: tuple[str, ...]):
    for key in keys:
        found = _find_key(item, key)
        if found is not None:
            return found
    return None


def _find_key(value: object, key: str):
    if isinstance(value, dict):
        if key in value:
            number = to_decimal(value[key])
            if number is not None:
                return number
        for child in value.values():
            found = _find_key(child, key)
            if found is not None:
                return found
    return None
