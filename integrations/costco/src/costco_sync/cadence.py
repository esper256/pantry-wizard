"""Which SKUs to price on this run, and when to ask again.

The schedule lives in the local cursor. It is not a sheet column. A run asks
for one paced batch: linked rows and promotions, then numbers never priced,
then quiet rechecks once that backlog is gone.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from costco_sync.models import PriceQuote, Receipt, RetailRow
from costco_sync.money import REDUCTION_THRESHOLD

STEADY_PRICE_SKUS = 80
CATCHUP_PRICE_SKUS = 240
SALE_INTERVAL_DAYS = 1
QUIET_INTERVAL_DAYS = 7
OLD_QUIET_INTERVAL_DAYS = 30
OLD_PURCHASE_DAYS = 365
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


def latest_paid_on(
    rows: dict[str, RetailRow],
    receipts: list[Receipt],
    known_source_refs: set[str],
) -> dict[str, str]:
    """Sheet dates, updated when this run is importing a newer purchase."""
    paid: dict[str, str] = {}
    for sku, row in rows.items():
        text = _day_text(row.last_paid_at)
        if text:
            paid[sku] = text
    for receipt in receipts:
        occurred = _day_text(receipt.occurred_on)
        if not occurred:
            continue
        for line in receipt.lines:
            if line.kind != "purchase" or line.source_ref in known_source_refs:
                continue
            if occurred >= paid.get(line.item_number, ""):
                paid[line.item_number] = occurred
    return paid


def select_due(
    skus: list[str],
    *,
    rows: dict[str, RetailRow],
    fresh: set[str],
    checks: dict[str, PriceCheck],
    now: datetime,
    today: date,
    buy_ids: set[str] | None = None,
) -> list[str]:
    """Linked rows and promotions, then a first pass, then quiet rechecks."""
    moment = _aware(now)
    buying = buy_ids or set()
    fresh_due: list[str] = []
    buy_due: list[str] = []
    linked_due: list[str] = []
    promo_due: list[str] = []
    never_due: list[str] = []
    quiet_due: list[str] = []
    seen: set[str] = set()
    for sku in skus:
        if sku in seen:
            continue
        seen.add(sku)
        row = rows.get(sku)
        if sku not in fresh and not _is_due(sku, checks, moment):
            continue
        if sku in fresh:
            fresh_due.append(sku)
        elif _is_linked(row):
            if row is not None and row.item_id in buying:
                buy_due.append(sku)
            else:
                linked_due.append(sku)
        elif _row_on_promotion(row, today):
            promo_due.append(sku)
        elif sku not in checks:
            never_due.append(sku)
        else:
            quiet_due.append(sku)
    head = (
        _by_sku(fresh_due)
        + _by_sku(buy_due)
        + _by_sku(linked_due)
        + _by_sku(promo_due)
    )
    if any(sku not in checks for sku in head) or never_due:
        ordered = head + _by_newest_purchase(never_due, rows)
        return ordered[:CATCHUP_PRICE_SKUS]
    ordered = head + _by_oldest_check(quiet_due, checks)
    return ordered[:STEADY_PRICE_SKUS]


def advance_checks(
    requested: list[str],
    quotes: list[PriceQuote],
    checks: dict[str, PriceCheck],
    now: datetime,
    today: date,
    paid_on: dict[str, str] | None = None,
) -> dict[str, PriceCheck]:
    """A promotion waits a day. A quiet price waits a week, or 30 days if old."""
    moment = _aware(now)
    dates = paid_on or {}
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
        if _quote_on_sale(quote, today):
            days = SALE_INTERVAL_DAYS
        else:
            days = _quiet_wait_days(dates.get(sku, ""), today)
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


def _is_linked(row: RetailRow | None) -> bool:
    return row is not None and bool(row.item_id.strip())


def _row_on_promotion(row: RetailRow | None, today: date) -> bool:
    if row is None:
        return False
    kinds = {part.strip() for part in row.reduction_kind.split(",") if part.strip()}
    if "instant_savings" in kinds:
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


def _quiet_wait_days(paid_on: str, today: date) -> int:
    paid = _parse_day(paid_on)
    if paid is None or (today - paid).days > OLD_PURCHASE_DAYS:
        return OLD_QUIET_INTERVAL_DAYS
    return QUIET_INTERVAL_DAYS


def _ends_ahead(value: str, today: date) -> bool:
    paid = _parse_day(value)
    return paid is not None and paid >= today


def _by_sku(skus: list[str]) -> list[str]:
    return sorted(skus, key=_sku_key)


def _by_newest_purchase(skus: list[str], rows: dict[str, RetailRow]) -> list[str]:
    def key(sku: str) -> tuple:
        paid = _parse_day(rows[sku].last_paid_at) if sku in rows else None
        ordinal = paid.toordinal() if paid is not None else date.min.toordinal()
        return (-ordinal, _sku_key(sku))

    return sorted(skus, key=key)


def _by_oldest_check(skus: list[str], checks: dict[str, PriceCheck]) -> list[str]:
    def key(sku: str) -> tuple:
        return (_aware(checks[sku].next_check_at), _sku_key(sku))

    return sorted(skus, key=key)


def _sku_key(sku: str) -> tuple:
    if sku.isdigit():
        return (0, int(sku), sku)
    return (1, sku)


def _day_text(value: str) -> str:
    paid = _parse_day(value)
    if paid is None:
        return ""
    return paid.isoformat()


def _parse_day(value: str) -> date | None:
    text = (value or "").strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    text = text[:10]
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _miss_wait_days(miss_count: int) -> int:
    # 2, 4, 8, ... until the cap. A later price resets the count.
    return min(MISS_CAP_DAYS, 2 ** min(miss_count, 16))


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment
