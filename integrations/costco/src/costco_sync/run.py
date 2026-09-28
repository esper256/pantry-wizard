"""Fetch warehouse receipts and build a mutation file for the confirmed warehouse."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from costco_sync.cadence import (
    advance_checks,
    below_baseline_skus,
    fresh_purchase_skus,
    latest_paid_on,
    rows_by_sku,
    select_due,
)
from costco_sync.decide import build_mutations, snapshot_from_json, warehouse_number
from costco_sync.models import PriceLookupResult, RangeRejected, Receipt
from costco_sync.normalize import barcodes_from_list, membership_number, parse_receipt_detail
from costco_sync.setupflow import (
    PRICE_LOOKUP_FAILURE,
    assert_lease,
    assert_location,
    assert_membership,
    build_summary,
    gap_window,
    has_costco_history,
    history_chunks,
    integration_upsert,
    membership_fingerprint,
    preferred_receipts,
)
from costco_sync.store import BELOW_BASELINE_RECONCILED, StateStore

WAREHOUSE_LOOKBACK_DAYS = 180


def _local_date(now: datetime, timezone_name: str) -> date:
    try:
        zone = ZoneInfo(timezone_name or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).date()


def to_costco_date(day: date) -> str:
    """Costco's warehouse receipt query wants M/DD/YYYY."""
    return f"{day.month}/{day.day:02d}/{day.year}"


def default_window(today: date, last_success: date | None) -> tuple[date, date]:
    if last_success is None:
        return today - timedelta(days=90), today
    start = last_success - timedelta(days=1)
    if start > today:
        start = today
    return start, today


def warehouse_listing_window(today: date, since: date | None) -> tuple[date, date]:
    if since is None:
        return today - timedelta(days=WAREHOUSE_LOOKBACK_DAYS), today
    return since, today


def history_windows(snapshot_data: dict, today: date) -> list[tuple[date, date]]:
    snapshot = snapshot_from_json(snapshot_data)
    if has_costco_history(snapshot):
        return [gap_window(today, snapshot)]
    return history_chunks(today)


def collect_window(source, store: StateStore, start: date, end: date) -> tuple[list[Receipt], set[str]]:
    listing = source.list_warehouse_receipts(to_costco_date(start), to_costco_date(end))
    receipts: list[Receipt] = []
    fingerprints: set[str] = set()
    for barcode in barcodes_from_list(listing):
        payload = store.get_receipt(barcode)
        if payload is None:
            payload = source.get_receipt_detail(barcode)
            store.put_receipt(barcode, payload)
        number = membership_number(payload)
        if number:
            fingerprints.add(membership_fingerprint(number))
        receipts.append(parse_receipt_detail(payload))
    return receipts, fingerprints


def list_warehouses(source, store: StateStore, start: date, end: date) -> list[tuple[str, str, int]]:
    """Warehouse number, name, and receipt count. Writes nothing to the sheet."""
    receipts, _fingerprints = collect_window(source, store, start, end)
    counts: dict[tuple[str, str], int] = {}
    for receipt in receipts:
        key = (receipt.warehouse_number, receipt.warehouse_name)
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    return [(number, name, count) for (number, name), count in ranked]


def format_warehouse_line(number: str, name: str, count: int) -> str:
    label = f"{number} {name}".strip()
    return f"{label}\t{count}"


def import_windows(
    snapshot_data: dict,
    source,
    store: StateStore,
    *,
    windows: list[tuple[date, date]],
    now: datetime,
    owner: str = "",
) -> dict:
    """Download, then keep only the confirmed warehouse.

    Lease, location, and membership are checked before a mutation file exists.
    A date range Costco rejects ends the walk. It does not discard receipts
    already collected from an earlier chunk.
    """
    snapshot = snapshot_from_json(snapshot_data)
    assert_lease(snapshot, owner, now)
    location = assert_location(snapshot)
    home = warehouse_number(location)
    accumulated: list[Receipt] = []
    fingerprints: set[str] = set()
    found_preferred = False
    collected_any_window = False
    for start, end in windows:
        try:
            batch, batch_fingerprints = collect_window(source, store, start, end)
        except RangeRejected:
            if not collected_any_window:
                raise
            break
        collected_any_window = True
        preferred = [receipt for receipt in batch if receipt.warehouse_number == home]
        if not batch:
            break
        if found_preferred and not preferred:
            break
        accumulated.extend(batch)
        fingerprints |= batch_fingerprints
        if preferred:
            found_preferred = True

    assert_membership(snapshot, fingerprints)
    kept, skipped = preferred_receipts(accumulated, location)
    names: dict[str, str] = {}
    skus = sorted({line.item_number for receipt in kept for line in receipt.lines})
    if skus and home:
        names = source.lookup_product_names(skus, home) or {}

    held: dict = {}
    retail_rows = rows_by_sku(snapshot.retail_memory, home)
    buy_ids = {
        item.item_id
        for item in snapshot.items
        if item.purchase_intent.strip().casefold() == "buy"
    }
    reconcile = (
        set()
        if store.has_flag(BELOW_BASELINE_RECONCILED)
        else below_baseline_skus(retail_rows)
    )

    def price_lookup(item_numbers: list[str], warehouse: str) -> PriceLookupResult:
        if not item_numbers or not warehouse:
            return PriceLookupResult(ok=True, quotes=[])
        due = select_due(
            item_numbers,
            rows=retail_rows,
            fresh=fresh_purchase_skus(kept, snapshot.known_source_refs),
            checks=store.price_checks(),
            now=now,
            today=_local_date(now, snapshot.household_timezone),
            buy_ids=buy_ids,
            reconcile=reconcile,
        )
        if not due:
            return PriceLookupResult(ok=True, quotes=[])
        result = source.lookup_prices(due, warehouse)
        held["requested"] = due
        held["result"] = result
        if not result.ok:
            print(PRICE_LOOKUP_FAILURE, file=sys.stderr)
        return result

    def search(query: str, warehouse: str):
        return source.search_products(query, warehouse, limit=5)

    mutations, samples, newly_sampled = build_mutations(
        snapshot,
        kept,
        names=names,
        price_lookup=price_lookup,
        search=search,
        baseline_samples=store.baseline_samples(),
        already_sampled=store.sampled_refs(),
        now=now,
    )
    result = held.get("result")
    requested = held.get("requested") or []
    checked = requested if result is None or result.checked is None else result.checked
    if result is not None and result.ok and checked:
        store.save_price_checks(
            advance_checks(
                checked,
                result.quotes,
                store.price_checks(),
                now,
                _local_date(now, snapshot.household_timezone),
                paid_on=latest_paid_on(retail_rows, kept, snapshot.known_source_refs),
            )
        )
    missed = 0
    status = ""
    if result is not None and result.ok and result.checked is not None:
        checked_set = set(result.checked)
        missed = sum(1 for sku in requested if sku not in checked_set)
        status = result.note
    if not store.has_flag(BELOW_BASELINE_RECONCILED):
        if not reconcile:
            store.set_flag(BELOW_BASELINE_RECONCILED)
        elif result is not None and result.ok:
            seen = set(requested if result.checked is None else result.checked)
            if all(sku in seen for sku in reconcile):
                store.set_flag(BELOW_BASELINE_RECONCILED)
    summary = build_summary(
        snapshot,
        kept,
        len(skipped),
        mutations,
        location,
        price_lookup_failed=mutations.get("price_lookup") == "failed",
        price_lookup_missed=missed,
        price_lookup_status=status,
    )
    mutations["summary"] = summary
    mutations["integration_upsert"] = integration_upsert(
        snapshot,
        summary,
        fingerprints,
        mutations["generated_at"],
    )
    return {
        "mutations": mutations,
        "samples": samples,
        "newly_sampled": newly_sampled,
        "summary": summary,
        "end": windows[0][1] if windows else None,
    }


def write_import(
    snapshot_path: Path,
    out_path: Path,
    store: StateStore,
    source,
    *,
    start: date,
    end: date,
    now: datetime,
    owner: str = "",
) -> dict:
    """Import one date window. Raises before replacing ``out_path`` on refusal."""
    return _write(snapshot_path, out_path, store, source, windows=[(start, end)], now=now, owner=owner)


def write_history(
    snapshot_path: Path,
    out_path: Path,
    store: StateStore,
    source,
    *,
    today: date,
    now: datetime,
    owner: str = "",
) -> dict:
    snapshot_data = json.loads(snapshot_path.read_text())
    windows = history_windows(snapshot_data, today)
    return _write(snapshot_path, out_path, store, source, windows=windows, now=now, owner=owner)


def _write(
    snapshot_path: Path,
    out_path: Path,
    store: StateStore,
    source,
    *,
    windows: list[tuple[date, date]],
    now: datetime,
    owner: str,
) -> dict:
    snapshot_data = json.loads(snapshot_path.read_text())
    result = import_windows(
        snapshot_data,
        source,
        store,
        windows=windows,
        now=now,
        owner=owner,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_suffix(out_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result["mutations"], indent=2, sort_keys=True) + "\n")
    temporary.replace(out_path)
    store.save_baseline_samples(result["samples"])
    store.mark_sampled(result["newly_sampled"])
    if result["end"] is not None:
        store.set_last_success_through(result["end"])
    return result["mutations"]
