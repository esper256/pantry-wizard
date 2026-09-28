"""Reference rules for turning a mutation file back into workbook rows.

The Sheets plugin remains the way a Bot writes Google Sheets. This module is
the tested meaning of that write, for a Bot that applies the file itself.
It does not call Google.
"""

from __future__ import annotations

import copy
import re

_LEASE_FIELDS = ("owner", "lease_until")


def validate_snapshot(data: dict) -> list[str]:
    problems: list[str] = []
    if "known_source_refs" not in data:
        problems.append("known_source_refs is missing. Export every Events.source_ref.")
    elif not isinstance(data.get("known_source_refs"), list):
        problems.append("known_source_refs must be a list of every Events.source_ref.")
    integrations = data.get("integrations") or []
    costco = next((row for row in integrations if str(row.get("integration_key") or "") == "costco"), None)
    location = ""
    if isinstance(costco, dict):
        location = str(costco.get("location") or "")
    if not location:
        location = str(data.get("preferred_costco_warehouse") or "")
    if not re.match(r"\s*\d+", location):
        problems.append("Integrations.location has no confirmed Costco warehouse.")
    for field in ("membership_fingerprint", "owner", "lease_until", "history_from", "history_through"):
        if isinstance(costco, dict) and field not in costco:
            problems.append(f"The costco Integrations row has no {field}.")
    return problems


def apply_mutations(snapshot: dict, mutations: dict) -> tuple[dict, list[dict]]:
    """Return the next snapshot and the events that still need appending.

    Applying the same mutation file to the returned snapshot appends nothing
    and does not copy owner or lease_until out of integration_upsert.
    """
    out = copy.deepcopy(snapshot)
    items = [dict(row) for row in out.get("items") or []]
    by_id = {str(row.get("item_id") or ""): row for row in items if row.get("item_id")}
    for patch in mutations.get("item_alias_updates") or []:
        item = by_id.get(str(patch.get("item_id") or ""))
        if item is None:
            continue
        item["aliases"] = str(patch.get("aliases") or "")
    out["items"] = items

    known = {str(ref) for ref in out.get("known_source_refs") or [] if ref}
    to_append: list[dict] = []
    for event in mutations.get("events") or []:
        ref = str(event.get("source_ref") or "")
        if ref and ref in known:
            continue
        if ref:
            known.add(ref)
        to_append.append(copy.deepcopy(event))
    out["known_source_refs"] = sorted(known)

    retail = [dict(row) for row in out.get("retail_memory") or []]
    by_key = {str(row.get("retail_key") or ""): row for row in retail if row.get("retail_key")}
    for row in mutations.get("retail_memory_upserts") or []:
        key = str(row.get("retail_key") or "")
        if not key:
            continue
        incoming = dict(row)
        if key in by_key:
            by_key[key].clear()
            by_key[key].update(incoming)
        else:
            retail.append(incoming)
            by_key[key] = incoming
    out["retail_memory"] = retail

    upsert = mutations.get("integration_upsert") or {}
    if upsert:
        integrations = [dict(row) for row in out.get("integrations") or []]
        key = str(upsert.get("integration_key") or "costco")
        current = next((row for row in integrations if str(row.get("integration_key") or "") == key), None)
        incoming = {field: value for field, value in upsert.items() if field not in _LEASE_FIELDS}
        if current is None:
            integrations.append(incoming)
        else:
            for field, value in incoming.items():
                current[field] = value
        out["integrations"] = integrations
    return out, to_append
