"""The v2 sheet layout.

This module is the only place tab names, column names and allowed values are
written down. Everything else addresses cells through it, so agents never need
to know a column letter.

Each tab has one key column (always the first) and one kind of writer:

  declared  people's statements, written through `pw` operations
  importer  store data, written only by a store importer via `pw sync apply`
  derived   rebuilt whole by `pw`; never an input
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = "2.0"


@dataclass(frozen=True)
class Tab:
    name: str
    columns: tuple[str, ...]
    owner: str  # declared | importer | derived

    @property
    def key(self) -> str:
        return self.columns[0]


TABS: dict[str, Tab] = {
    t.name: t
    for t in (
        Tab("Config", ("key", "value", "note"), "declared"),
        # People, stores and receipt sources this household uses. Agents add a
        # row the first time they meet a new one. Keys look like `person:eric`.
        Tab("Sources", ("source", "name", "note"), "declared"),
        Tab(
            "List",
            (
                "id", "kind", "item", "text", "qty", "store", "only_if",
                "max_price", "until", "by", "at", "agent", "raw", "ref",
                "status", "closed_at", "closed_by", "closed_ref",
            ),
            "declared",
        ),
        Tab(
            "Items",
            (
                "item", "name", "aliases", "kind", "stores", "only_at",
                "rule", "max_price", "keep", "note",
            ),
            "declared",
        ),
        Tab(
            "Log",
            ("id", "at", "by", "agent", "item", "kind", "value", "raw", "ref", "fixes"),
            "declared",
        ),
        Tab(
            "Questions",
            (
                "id", "kind", "subject", "question", "options", "suggested",
                "evidence", "asked_by", "asked_at", "status", "answer",
                "answered_by", "answered_at",
            ),
            "declared",
        ),
        # The importer appends a row the first time a product is bought and
        # never touches it again. `item`, `kind`, `pack_qty` and `unit` belong
        # to the household.
        Tab(
            "Products",
            (
                "product", "store", "sku", "name", "receipt_name", "dept",
                "item", "kind", "pack_qty", "unit", "note",
            ),
            "declared",
        ),
        Tab(
            "Offers",
            (
                "product", "price", "regular_price", "usual_price", "sale",
                "sale_scope", "sale_ends", "availability", "price_scope",
                "changed_at", "checked_at", "misses",
            ),
            "importer",
        ),
        Tab(
            "Receipts",
            (
                "receipt", "store", "date", "total", "lines", "source",
                "status", "superseded_by", "by", "agent", "at",
            ),
            "importer",
        ),
        Tab(
            "Lines",
            (
                "line", "receipt", "n", "product", "item", "text", "qty",
                "unit_price", "kind", "ref",
            ),
            "importer",
        ),
        Tab(
            "Sync",
            (
                "store", "status", "location", "membership_fingerprint",
                "owner", "lease_until", "history_from", "history_through",
                "last_sync_at", "last_summary",
            ),
            "importer",
        ),
        Tab(
            "v_Items",
            (
                "item", "name", "stock", "last_said", "last_said_at",
                "last_bought", "last_bought_store", "buys_12mo",
                "usual_gap_days", "days_since", "on_list", "built_at",
            ),
            "derived",
        ),
        Tab("v_Brief", ("key", "store", "section", "text", "built_at"), "derived"),
    )
}

# Closed vocabularies. `pw` rejects anything else, which is what keeps six
# spellings of one person out of the sheet.
LIST_KINDS = ("buy", "skip", "remind")
LIST_STATUS = ("open", "done", "cancelled", "expired")
# any_price: buy it this time whatever the item's standing rule says.
ONLY_IF = ("", "on_sale", "any_price")
ITEM_KINDS = ("", "once")  # once = a one-time need; never suggested again
ITEM_RULES = ("", "on_sale", "when_in_stock", "never")
PRODUCT_KINDS = ("", "consumable", "once", "seasonal", "not_product")
# What someone said about stock. `bought` is a purchase reported without a
# receipt. `said` and `set` are audit rows and carry no stock meaning.
LOG_KINDS = (
    "out", "low", "last_one", "plenty", "have", "used", "bought",
    "said", "set", "fix",
)
OBSERVATIONS = ("out", "low", "last_one", "plenty", "have")
AVAILABILITY = ("", "in_stock", "low_stock", "out_of_stock", "unknown")
SALE_SCOPE = ("", "warehouse", "online", "both", "unknown")
RECEIPT_SOURCES = ("import", "photo", "manual")
RECEIPT_STATUS = ("recorded", "superseded")
LINE_KINDS = ("purchase", "refund", "deposit")
QUESTION_STATUS = ("open", "answered", "dismissed")

CONFIG_DEFAULTS = (
    ("schema_version", SCHEMA_VERSION, "Sheet layout version. pw refuses to write on a mismatch."),
    ("timezone", "", "IANA timezone, e.g. America/Los_Angeles"),
    ("currency", "", "Currency code, e.g. USD"),
)
