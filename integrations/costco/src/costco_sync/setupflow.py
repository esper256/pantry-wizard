"""Warehouse confirmation, lease checks, history windows, and import summaries.

These facts are what the Bot is allowed to tell the household. Counts come from
receipts that were actually fetched, not from the dates that were requested.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from costco_sync.decide import costco_integration, home_location, warehouse_number
from costco_sync.models import LeaseError, LocationRequired, MembershipError, Receipt, Snapshot

LEASE_HOURS = 36
HISTORY_CHUNK_DAYS = 365
MAX_HISTORY_CHUNKS = 15


def membership_fingerprint(membership_number: str) -> str:
    return hashlib.sha256(membership_number.strip().encode()).hexdigest()


def assert_lease(snapshot: Snapshot, owner: str, now: datetime) -> None:
    row = costco_integration(snapshot)
    if row is None or not row.owner or not row.lease_until:
        return
    if row.owner == owner:
        return
    expiry = _parse_timestamp(row.lease_until)
    if expiry is None or expiry <= _aware(now):
        return
    raise LeaseError(
        f"Costco import is leased to '{row.owner}' until {row.lease_until}. "
        "Do not start a second schedule."
    )


def assert_location(snapshot: Snapshot) -> str:
    location = home_location(snapshot)
    if not warehouse_number(location):
        raise LocationRequired(
            "No confirmed Costco warehouse. Ask the household before importing."
        )
    return location


def assert_membership(snapshot: Snapshot, fingerprints: set[str]) -> None:
    if len(fingerprints) > 1:
        raise MembershipError("This download includes more than one Costco membership.")
    row = costco_integration(snapshot)
    if row is None or not row.membership_fingerprint or not fingerprints:
        return
    found = next(iter(fingerprints))
    if found != row.membership_fingerprint:
        raise MembershipError("This sheet is already linked to a different Costco membership.")


def has_costco_history(snapshot: Snapshot) -> bool:
    row = costco_integration(snapshot)
    if row is not None and (row.history_from or row.history_through):
        return True
    return any(ref.startswith("costco:") for ref in snapshot.known_source_refs)


def gap_window(today: date, snapshot: Snapshot) -> tuple[date, date]:
    row = costco_integration(snapshot)
    if row is not None and row.history_through:
        start = date.fromisoformat(row.history_through[:10])
        if start > today:
            start = today
        return start, today
    return today - timedelta(days=90), today


def history_chunks(today: date, chunks: int = MAX_HISTORY_CHUNKS) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    end = today
    for _ in range(chunks):
        start = end - timedelta(days=HISTORY_CHUNK_DAYS)
        windows.append((start, end))
        end = start - timedelta(days=1)
    return windows


def preferred_receipts(receipts: list[Receipt], location: str) -> tuple[list[Receipt], list[Receipt]]:
    home = warehouse_number(location)
    kept = [receipt for receipt in receipts if receipt.warehouse_number == home]
    skipped = [receipt for receipt in receipts if receipt.warehouse_number != home]
    return kept, skipped


def build_summary(
    snapshot: Snapshot,
    home_receipts: list[Receipt],
    skipped_other_warehouses: int,
    mutations: dict,
    location: str,
) -> dict:
    imported_dates: list[str] = []
    imported = 0
    already = 0
    items: set[str] = set()
    known = snapshot.known_source_refs
    for receipt in home_receipts:
        new_lines = [line for line in receipt.lines if line.source_ref not in known]
        if receipt.lines and not new_lines:
            already += 1
            continue
        imported += 1
        if receipt.occurred_on:
            imported_dates.append(receipt.occurred_on)
        for line in new_lines:
            if line.kind == "purchase":
                items.add(line.item_number)
    row = costco_integration(snapshot)
    if imported_dates:
        oldest = min(imported_dates)
        newest = max(imported_dates)
    elif row is not None and row.history_from:
        oldest = row.history_from[:10]
        newest = (row.history_through or row.history_from)[:10]
    else:
        oldest = ""
        newest = ""
    summary = {
        "warehouse": location,
        "receipts_imported": imported,
        "receipts_already_present": already,
        "receipts_skipped_other_warehouses": skipped_other_warehouses,
        "oldest": oldest,
        "newest": newest,
        "distinct_items": len(items),
        "new_household_items": len(mutations.get("new_items") or []),
    }
    summary["text"] = summary_text(summary)
    return summary


def summary_text(summary: dict) -> str:
    skipped = summary["receipts_skipped_other_warehouses"]
    skip_sentence = f" Skipped {skipped} receipts from other warehouses."
    if summary["receipts_imported"] == 0:
        span = ""
        if summary["oldest"] and summary["newest"]:
            span = f" from {summary['oldest']} through {summary['newest']}"
        return (
            f"Costco history for {summary['warehouse']} is already in the sheet{span}. "
            f"No new receipts.{skip_sentence}"
        )
    return (
        f"Imported {summary['receipts_imported']} Costco receipts from {summary['warehouse']}, "
        f"{summary['oldest']} through {summary['newest']}, "
        f"covering {summary['distinct_items']} items "
        f"({summary['new_household_items']} new household items). "
        f"{summary['receipts_already_present']} receipts were already in the sheet."
        f"{skip_sentence}"
    )


def integration_upsert(
    snapshot: Snapshot,
    summary: dict,
    fingerprints: set[str],
    generated_at: str,
) -> dict:
    row = costco_integration(snapshot)
    history_from, history_through = _merged_span(
        row.history_from if row else "",
        row.history_through if row else "",
        summary["oldest"] if summary["receipts_imported"] else "",
        summary["newest"] if summary["receipts_imported"] else "",
    )
    fingerprint = next(iter(fingerprints), "")
    if not fingerprint and row is not None:
        fingerprint = row.membership_fingerprint
    return {
        "integration_key": "costco",
        "store": "Costco",
        "status": "active",
        "location": summary["warehouse"],
        "membership_fingerprint": fingerprint,
        "history_from": history_from,
        "history_through": history_through,
        "last_sync_at": generated_at,
        "last_summary": summary["text"],
    }


def _merged_span(existing_from: str, existing_through: str, new_oldest: str, new_newest: str) -> tuple[str, str]:
    dates = [value[:10] for value in (existing_from, existing_through, new_oldest, new_newest) if value]
    if not dates:
        return "", ""
    return min(dates), max(dates)


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _parse_timestamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _aware(parsed)
