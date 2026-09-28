"""Which SKUs to price on this run, and when to ask again.

The schedule lives in the local cursor. It is not a sheet column. A run asks
for at most one batch. Sale rows and new receipt lines go before quiet history.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from costco_sync.models import PriceQuote, Receipt, RetailRow
from costco_sync.money import REDUCTION_THRESHOLD

MAX_PRICE_SKUS = 60
SALE_INTERVAL_DAYS = 1
QUIET_INTERVAL_DAYS = 7
MISS_CAP_DAYS = 90


class PriceCheck:
    def __init__(self, next_check_at: datetime, miss_count: int) -> None:
        self.next_check_at = next_check_at
        self.miss_count = miss_count


def rows_by_sku(rows: list[RetailRow], home: str) -> dict[str, RetailRow]:
    prefix = f"costco:{home}:"
    found: dict[str, RetailRow] = {}
    for row in rows:
        if row.store.casefold() != "costco" or not row.retailer_sku:
            continue
        if home and not row.retail_key.startswith(prefix):
            continue
        found[row.retailer_sku] = row
    return found


def fresh_purchase_skus(receipts: list[Receipt], known_source_refs: set[str]) -> set[str]:
    skus: set[str] = set()
    for receipt in receipts:
        for line in receipt.lines:
            if line.kind == "purchase" and line.source_ref not in known_source_refs:
                skus.add(line.item_number)
    return skus


def select_due(
    skus: list[str],
    *,
    rows: dict[str, RetailRow],
    fresh: set[str],
    checks: dict[str, PriceCheck],
    now: datetime,
    today: date,
    limit: int = MAX_PRICE_SKUS,
) -> list[str]:
    """Sale rows and new receipt lines first, then quiet history, capped."""
    moment = _aware(now)
    fresh_due: list[str] = []
    sale_due: list[str] = []
    quiet_due: list[str] = []
    for sku in skus:
        if sku not in fresh and not _is_due(sku, checks, moment):
            continue
        if sku in fresh:
            fresh_due.append(sku)
        elif _row_on_sale(rows.get(sku), today):
            sale_due.append(sku)
        else:
            quiet_due.append(sku)
    ordered = sorted(fresh_due) + sorted(sale_due) + sorted(quiet_due)
    return ordered[:limit]


def advance_checks(
    requested: list[str],
    quotes: list[PriceQuote],
    checks: dict[str, PriceCheck],
    now: datetime,
    today: date,
) -> dict[str, PriceCheck]:
    """A priced SKU waits a day on sale, else a week. A miss doubles the wait."""
    moment = _aware(now)
    priced = {
        quote.item_number: quote
        for quote in quotes
        if quote.current_price is not None
    }
    updated: dict[str, PriceCheck] = {}
    for sku in requested:
        quote = priced.get(sku)
        if quote is None:
            miss = checks[sku].miss_count + 1 if sku in checks else 1
            updated[sku] = PriceCheck(
                next_check_at=moment + timedelta(days=_miss_wait_days(miss)),
                miss_count=miss,
            )
            continue
        days = SALE_INTERVAL_DAYS if _quote_on_sale(quote, today) else QUIET_INTERVAL_DAYS
        updated[sku] = PriceCheck(
            next_check_at=moment + timedelta(days=days),
            miss_count=0,
        )
    return updated


def _is_due(sku: str, checks: dict[str, PriceCheck], now: datetime) -> bool:
    check = checks.get(sku)
    if check is None:
        return True
    return _aware(check.next_check_at) <= now


def _row_on_sale(row: RetailRow | None, today: date) -> bool:
    if row is None:
        return False
    if row.reduction_kind.strip():
        return True
    return _ends_ahead(row.reduction_ends_at, today)


def _quote_on_sale(quote: PriceQuote, today: date) -> bool:
    if quote.explicit_instant_savings:
        return True
    if _ends_ahead(quote.reduction_ends_at, today):
        return True
    current = quote.current_price
    regular = quote.regular_price
    return (
        current is not None
        and regular is not None
        and regular - current >= REDUCTION_THRESHOLD
    )


def _ends_ahead(value: str, today: date) -> bool:
    text = (value or "")[:10]
    if len(text) < 10:
        return False
    try:
        return date.fromisoformat(text) >= today
    except ValueError:
        return False


def _miss_wait_days(miss_count: int) -> int:
    # 2, 4, 8, ... until the cap. A later price resets the count.
    return min(MISS_CAP_DAYS, 2 ** min(miss_count, 16))


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment
