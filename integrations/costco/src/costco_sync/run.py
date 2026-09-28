"""Fetch a date window of warehouse receipts and build a mutation file."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from costco_sync.decide import build_mutations, snapshot_from_json, warehouse_number
from costco_sync.models import AuthError, PriceLookupResult
from costco_sync.normalize import barcodes_from_list, parse_receipt_detail
from costco_sync.store import StateStore


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


def import_window(
    snapshot_data: dict,
    source,
    store: StateStore,
    *,
    start: date,
    end: date,
    now: datetime,
) -> dict:
    snapshot = snapshot_from_json(snapshot_data)
    listing = source.list_warehouse_receipts(to_costco_date(start), to_costco_date(end))
    barcodes = barcodes_from_list(listing)
    receipts = []
    for barcode in barcodes:
        payload = store.get_receipt(barcode)
        if payload is None:
            payload = source.get_receipt_detail(barcode)
            store.put_receipt(barcode, payload)
        receipts.append(parse_receipt_detail(payload))

    home = warehouse_number(snapshot.preferred_costco_warehouse)
    if not home:
        for receipt in receipts:
            if receipt.warehouse_number:
                home = receipt.warehouse_number
                break
    names: dict[str, str] = {}
    skus = sorted({line.item_number for receipt in receipts for line in receipt.lines})
    if skus and home:
        names = source.lookup_product_names(skus, home) or {}

    def price_lookup(item_numbers: list[str], warehouse: str) -> PriceLookupResult:
        if not item_numbers or not warehouse:
            return PriceLookupResult(ok=True, quotes=[])
        return source.lookup_prices(item_numbers, warehouse)

    def search(query: str, warehouse: str):
        return source.search_products(query, warehouse, limit=5)

    mutations, samples, newly_sampled = build_mutations(
        snapshot,
        receipts,
        names=names,
        price_lookup=price_lookup,
        search=search,
        baseline_samples=store.baseline_samples(),
        already_sampled=store.sampled_refs(),
        now=now,
    )
    return {
        "mutations": mutations,
        "samples": samples,
        "newly_sampled": newly_sampled,
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
) -> dict:
    """Run an import. Raises AuthError before replacing ``out_path``."""
    snapshot_data = json.loads(snapshot_path.read_text())
    try:
        result = import_window(snapshot_data, source, store, start=start, end=end, now=now)
    except AuthError:
        raise
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_suffix(out_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result["mutations"], indent=2, sort_keys=True) + "\n")
    temporary.replace(out_path)
    store.save_baseline_samples(result["samples"])
    store.mark_sampled(result["newly_sampled"])
    store.set_last_success_through(end)
    return result["mutations"]
