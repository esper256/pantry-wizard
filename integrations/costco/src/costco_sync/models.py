"""In-memory shapes for a household snapshot, a receipt, and a price quote."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class HouseholdItem:
    item_id: str
    canonical_name: str = ""
    aliases: str = ""
    inventory_state: str = ""
    purchase_intent: str = ""
    preferred_stores: str = ""


@dataclass
class RetailRow:
    retail_key: str
    item_id: str = ""
    store: str = "Costco"
    location: str = ""
    retailer_sku: str = ""
    retailer_name: str = ""
    receipt_name: str = ""
    last_paid_unit_price: str = ""
    last_paid_at: str = ""
    baseline_unit_price: str = ""
    purchase_count: str = ""
    current_price: str = ""
    regular_price: str = ""
    reduction_kind: str = ""
    reduction_ends_at: str = ""
    price_scope: str = ""
    observed_at: str = ""


@dataclass
class IntegrationRow:
    integration_key: str
    store: str = ""
    status: str = ""
    location: str = ""
    membership_fingerprint: str = ""
    owner: str = ""
    lease_until: str = ""
    history_from: str = ""
    history_through: str = ""
    last_sync_at: str = ""
    last_summary: str = ""


@dataclass
class Snapshot:
    household_timezone: str = "UTC"
    preferred_costco_warehouse: str = ""
    items: list[HouseholdItem] = field(default_factory=list)
    retail_memory: list[RetailRow] = field(default_factory=list)
    integrations: list[IntegrationRow] = field(default_factory=list)
    known_source_refs: set[str] = field(default_factory=set)


@dataclass
class RawLine:
    item_number: str
    description: str
    unit: Decimal
    amount: Decimal


@dataclass
class NormalizedLine:
    kind: str
    item_number: str
    description: str
    quantity: Decimal
    unit_price_paid: Decimal
    pre_savings_unit_price: Decimal
    had_instant_savings: bool
    source_ref: str


@dataclass
class Receipt:
    barcode: str
    warehouse_number: str
    warehouse_name: str
    occurred_on: str
    lines: list[NormalizedLine]


@dataclass
class SearchHit:
    item_number: str
    name: str


@dataclass
class PriceQuote:
    item_number: str
    current_price: Decimal | None = None
    regular_price: Decimal | None = None
    reduction_ends_at: str = ""
    price_scope: str = ""
    product_name: str = ""
    explicit_instant_savings: bool = False
    variable_weight: bool = False


@dataclass
class PriceLookupResult:
    ok: bool
    quotes: list[PriceQuote] = field(default_factory=list)
    # None means every requested item number was in a successful response.
    checked: list[str] | None = None
    note: str = ""


class AuthError(RuntimeError):
    """Costco rejected or lacks a refresh token. Do not write a mutation file."""


class LeaseError(RuntimeError):
    """Another Bot still holds this store's import lease."""


class MembershipError(RuntimeError):
    """The download belongs to a different membership than the sheet."""


class LocationRequired(RuntimeError):
    """Import needs a confirmed home warehouse. Do not guess one."""


class RangeRejected(RuntimeError):
    """Costco refused the requested receipt date range."""
