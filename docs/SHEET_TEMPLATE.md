# Sheet Template Structure

**Schema version:** 0.3 · **Protocol version:** 0.8 (`AGENT_RUNTIME_INSTRUCTIONS.md`) · **Behavior spec version:** 0.8  
**Template sheet:** https://docs.google.com/spreadsheets/d/1_y9qlX15wQshsmXMEK9uGuXCzr7tS58lNc1y1mdkARs/edit (view-only; use **File → Make a copy**)  
**Authoritative spec:** [`GOOGLE_SHEETS_SCHEMA.md`](../GOOGLE_SHEETS_SCHEMA.md). If this page and the spec disagree, the spec wins.  
**Related v2.0 discussion:** [#38 (Costco trip postmortem and v2.0 plan)](https://github.com/esper256/pantry-wizard/issues/38) · [#39 (general v2.0 improvements)](https://github.com/esper256/pantry-wizard/issues/39)

This page is a quick visual reference for what a Pantry Wizard workbook looks like, so a reviewer can follow the v2.0 proposals without opening a real household sheet. The template sheet itself starts **empty**: tabs, header rows, the `Config` key rows, data-validation dropdowns, frozen headers and formatting, and no data rows. Every data row on this page is **fictional** and exists only to illustrate the shape.

The template sheet was refreshed to this structure on Nov 11, 2026 (it previously had only `Config`, `Items` and `Events` at schema 0.1 / protocol 0.2).

---

## 1. Tabs at a glance

| Tab | Role | Path | Rows |
|---|---|---|---|
| `Config` | Small key/value control plane: versions, spec URLs, timezone, currency | Hot: read on every interaction | Fixed set of keys (below) |
| `Items` | Materialized current state, one row per canonical household item | Hot: read on every interaction | One per tracked household item |
| `RetailMemory` | Replaceable commercial cache, one row per store location × retailer SKU | Warm: read only for a named-store briefing | One per SKU the household has bought or linked |
| `Integrations` | One row per connected store importer (sync state and lease) | Cold: setup and the scheduled importer | One per store |
| `Events` | Append-only evidence ledger | Cold: corrections, reconciliation, analysis | One per atomic observation or receipt line |

```text
 normal interaction ──► Config + Items  (hot path)
                              │ named-store briefing
                              ▼
                        RetailMemory    (commercial cache)
                              │ state-changing observation
                              ▼
                           Events       (cold, append-only history)
```

### Relationships

```text
Items.item_id ◄──────── Events.item_id          (many events per item; blank = receipt line not yet linked)
Items.item_id ◄──────── RetailMemory.item_id    (many SKUs per item; blank = SKU not matched to an item)
Items.last_event_id ───► Events.event_id        (latest event used to derive the row)
Events.supersedes_event_id ─► Events.event_id   (explicit correction of an earlier event)
Integrations.store / location ─► RetailMemory.store / location  (the importer fills RetailMemory and Events)
```

There are no row-number foreign keys. `item_id` and `event_id` are opaque unique IDs (UUIDs in practice); `retail_key` and `integration_key` are stable text keys.

### Physical rules (all tabs)

- Headers are in row 1, row 1 is frozen, and a basic filter is on. No title rows, no merged cells, no formulas required for correctness.
- Formatting is a convenience, never semantics. An agent that can only read and write cell values must still work.
- Machine-written timestamps are ISO-8601 **text** with an explicit offset (`2026-09-10T14:15:23-07:00`). Date-only (`2026-09-10`) is fine where evidence has no time. Timestamp columns are formatted Plain text so Sheets does not convert them into date serials.
- Enum columns carry **warning-only** dropdowns in the template (`strict: false`), so an agent that writes a value outside the list is not rejected. `Events.event_type` is a suggested vocabulary, not a closed list.
- Other formatting in the template: bold, wrapped, lightly shaded header; wrapped, top-aligned data cells; column widths sized for reading; grids trimmed to the schema's columns.

---

## 2. `Config`

Columns: `key`, `value`, `description`. Agents read the whole tab cheaply. Do not store counters or per-store keys here.

| key | Template value | Meaning |
|---|---|---|
| `schema_version` | `0.3` | Shopping Database schema version. Changes only through a schema upgrade note |
| `schema_url` | `https://raw.githubusercontent.com/esper256/pantry-wizard/main/GOOGLE_SHEETS_SCHEMA.md` | Authoritative schema; load when `schema_version` is unfamiliar |
| `protocol_version` | `0.8` | Required agent runtime-instruction version |
| `runtime_instructions_url` | `https://raw.githubusercontent.com/esper256/pantry-wizard/main/AGENT_RUNTIME_INSTRUCTIONS.md` | Authoritative runtime instructions; agents verify its Protocol Version matches before persistent writes |
| `behavior_spec_version` | `0.8` | Informational behavior-spec version |
| `behavior_spec_url` | `https://raw.githubusercontent.com/esper256/pantry-wizard/main/AGENT_BEHAVIOR_SPEC.md` | Full behavior specification |
| `household_timezone` | *(blank; set during setup)* | IANA timezone, e.g. `America/Los_Angeles` |
| `currency` | *(blank; set during setup)* | Currency code, e.g. `USD` |
| `timestamp_format` | `ISO-8601 with explicit timezone offset` | Reminder of the machine-written timestamp convention |

Agents move `protocol_version` and `behavior_spec_version` forward (never back) when they load newer docs; that is housekeeping and needs no approval. For releases, prefer immutable tag or commit URLs over `main`.

---

## 3. `Items`

One row per canonical household item. Answering "do we need milk?" or "what's on the list?" should need only `Config` + `Items`.

| # | Column | Type | Allowed values / format | Meaning |
|---|---|---|---|---|
| A | `item_id` | text (UUID) | opaque, never a row number | Stable item identity |
| B | `canonical_name` | text | | Human-readable item name |
| C | `aliases` | text | `; `-separated | Alternate names (in practice also `costco:<sku>`-style SKU aliases) |
| D | `inventory_state` | enum | `unknown`, `out`, `very_low`, `probably_low`, `adequate`, `plenty` | Qualitative current stock; uncertainty allowed |
| E | `quantity_estimate` | number | blank when unjustified | Optional estimated quantity |
| F | `quantity_unit` | text | | Unit for the estimate |
| G | `inventory_confidence` | enum | `low`, `medium`, `high` | Confidence in D/E |
| H | `inventory_as_of` | timestamp text | ISO-8601 | Freshness of the **inventory evidence** (not of the row) |
| I | `purchase_intent` | enum | blank, `buy`, `do_not_buy` | Explicit shopping intent |
| J | `requested_quantity` | number | | Explicitly requested purchase quantity |
| K | `intent_expires_at` | timestamp text | ISO-8601 | Expiry for temporary intent |
| L | `preferred_stores` | text | `; `-separated | Store preferences |
| M | `item_policy` | text | natural language | Durable preferences / stock policy ("keep one spare; up to 4 on a strong sale") |
| N | `state_summary` | text | natural language | Short explanation of the evidence behind current state, so `Events` stays off the hot path |
| O | `last_event_id` | text | `Events.event_id` | Latest event materially used to update the row |
| P | `updated_at` | timestamp text | ISO-8601 | Freshness of the row as a whole |
| Q | `updated_by` | text | | Agent/system that made that update |

`inventory_as_of` vs `updated_at`: a September preference change must not make July inventory evidence look fresh.

**Fictional example rows**

| item_id | canonical_name | aliases | inventory_state | quantity_estimate | quantity_unit | inventory_confidence | inventory_as_of | purchase_intent | requested_quantity | intent_expires_at | preferred_stores | item_policy | state_summary | last_event_id | updated_at | updated_by |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `11111111-0000-4000-8000-000000000001` | Milk | whole milk; examplewarehouse:100001 | very_low | 0.5 | gallon | medium | 2026-01-14T08:05:00-08:00 | buy | 2 | | Example Warehouse | Whole milk only; buy 2 at a time | Alex said about half a gallon left Jan 14 morning. | `22222222-0000-4000-8000-000000000003` | 2026-01-14T08:05:10-08:00 | example-bot |
| `11111111-0000-4000-8000-000000000002` | Eggs | large eggs; examplewarehouse:100002 | adequate | 18 | eggs | medium | 2026-01-12 | | | | Example Warehouse; Corner Market | Large brown preferred; 5-dozen pack is fine when on sale | Bought a 2-dozen pack Jan 12; roughly a dozen used per week. | `22222222-0000-4000-8000-000000000002` | 2026-01-12T19:30:00-08:00 | example-bot |
| `11111111-0000-4000-8000-000000000003` | Paper towels | | plenty | | | high | 2026-01-12 | do_not_buy | | 2026-02-15 | Example Warehouse | Bulk only | 12-roll pack bought Jan 12; Sam asked not to buy more until mid-February. | `22222222-0000-4000-8000-000000000005` | 2026-01-13T10:00:00-08:00 | example-bot |

---

## 4. `RetailMemory`

One row per store location × retailer SKU that the household has bought, or has explicitly linked to an item. Found by `retail_key` and **updated in place**: a new price, receipt, or repeated import never appends a second row. Not a catalog, not a promotion history, not inventory. A price check updates cells here and writes no `Events` row.

| # | Column | Type | Allowed values / format | Meaning |
|---|---|---|---|---|
| A | `retail_key` | text | `{store}:{location number}:{sku}`; Costco: `costco:{warehouseNumber}:{itemNumber}` | Stable identity |
| B | `item_id` | text | `Items.item_id` or blank | Link to a household item; blank while identity is ambiguous |
| C | `store` | text | | Retailer name |
| D | `location` | text | | Warehouse/store location |
| E | `retailer_sku` | text | | Retailer item number |
| F | `retailer_name` | text | | Best known product name |
| G | `receipt_name` | text | | Abbreviated receipt text |
| H | `last_paid_unit_price` | number | | Most recent non-refund unit price paid, after instant savings |
| I | `last_paid_at` | date/timestamp text | date-only OK | When that price was paid |
| J | `baseline_unit_price` | number | | Typical unit price from non-sale purchases |
| K | `purchase_count` | integer | | Purchase lines folded into this row, excluding refunds |
| L | `current_price` | number | blank = no current observation | Latest observed sell price |
| M | `regular_price` | number | | Pre-discount price, when the source gives one |
| N | `reduction_kind` | enum | blank, `instant_savings`, `below_baseline`, `instant_savings,below_baseline` | Current reduction. `below_baseline` alone is **not** a sale |
| O | `reduction_ends_at` | date/timestamp text | date-only OK; blank if unknown | End of current reduction |
| P | `price_scope` | enum | `warehouse`, `online`, blank | Where `current_price` was read |
| Q | `observed_at` | timestamp text | ISO-8601 | When `current_price` and `availability` were observed together |
| R | `availability` | enum | `in_stock`, `low_stock`, `out_of_stock`, `unknown`, blank | Shelf stock at this location. Blank = never checked; only `in_stock` means a normal shelf |
| S | `sale_scope` | enum | `warehouse`, `online`, `both`, `unknown`; blank when no instant savings | Where an instant-savings promotion redeems |

A briefing may call a deal **confirmed** only when `availability = in_stock`, `reduction_kind` contains `instant_savings`, and `sale_scope` is `warehouse` or `both`; everything else is "unconfirmed". No payment details, membership numbers, or credentials are ever stored here.

**Fictional example rows**

| retail_key | item_id | store | location | retailer_sku | retailer_name | receipt_name | last_paid_unit_price | last_paid_at | baseline_unit_price | purchase_count | current_price | regular_price | reduction_kind | reduction_ends_at | price_scope | observed_at | availability | sale_scope |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `examplewarehouse:000:100001` | `…0001` (Milk) | Example Warehouse | 000 Example City | 100001 | Example Farms Whole Milk, 2 × 1 gal | EX WHL MILK | 6.49 | 2026-01-05 | 6.49 | 14 | 6.49 | | | | warehouse | 2026-01-14T06:00:00-08:00 | in_stock | |
| `examplewarehouse:000:100002` | `…0002` (Eggs) | Example Warehouse | 000 Example City | 100002 | Example Large Brown Eggs, 5 dozen | EX LG EGGS 5DZ | 9.99 | 2025-12-20 | 9.99 | 6 | 7.99 | 9.99 | instant_savings,below_baseline | 2026-01-25 | warehouse | 2026-01-14T06:00:00-08:00 | low_stock | warehouse |
| `examplewarehouse:000:100003` | `…0003` (Paper towels) | Example Warehouse | 000 Example City | 100003 | Example Paper Towels, 12 rolls | EX PPR TWL 12 | 21.99 | 2026-01-12 | 22.99 | 3 | 22.99 | | | | warehouse | 2026-01-14T06:00:00-08:00 | in_stock | |
| `examplewarehouse:000:100004` | | Example Warehouse | 000 Example City | 100004 | Example Patio Heater | EX PATIO HTR | 149.99 | 2025-11-02 | 149.99 | 1 | | | | | | | | |

The last row is a one-time durable purchase with no `item_id` link. Telling durables apart from consumables is open v2.0 work (#38, #39).

---

## 5. `Integrations`

One row per connected store importer, found by `integration_key` and updated in place. A daily sync never appends a row; `last_summary` replaces the previous sentence. The lease (`owner`, `lease_until`) stops two Bots from scheduling the same import.

| # | Column | Type | Allowed values / format | Meaning |
|---|---|---|---|---|
| A | `integration_key` | text | store slug, e.g. `costco` | Stable identity |
| B | `store` | text | | Retailer name |
| C | `status` | enum | `setup`, `active`, `paused` | Importer state |
| D | `location` | text | | Confirmed home location |
| E | `membership_fingerprint` | text | SHA-256 hex | Hash of the membership number; the number itself is never stored |
| F | `owner` | text | | Which Bot runs the schedule |
| G | `lease_until` | timestamp text | ISO-8601 | When that owner's claim expires (a daily run renews ~36 h) |
| H | `history_from` | date text | date-only | Oldest receipt date actually imported |
| I | `history_through` | date text | date-only | Newest receipt date actually imported |
| J | `last_sync_at` | timestamp text | ISO-8601 | Last successful import |
| K | `last_summary` | text | | The sentence the household was told after that import |

**Fictional example row**

| integration_key | store | status | location | membership_fingerprint | owner | lease_until | history_from | history_through | last_sync_at | last_summary |
|---|---|---|---|---|---|---|---|---|---|---|
| `examplewarehouse` | Example Warehouse | active | 000 Example City | `0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef` | example-bot | 2026-01-15T18:00:00-08:00 | 2025-07-01 | 2026-01-12 | 2026-01-14T06:02:00-08:00 | Imported 1 receipt (Jan 12): 9 lines; eggs are on sale through Jan 25. |

---

## 6. `Events`

Append-only evidence ledger. One row is one atomic observation, so one chat message can produce several rows. Rows are never edited for meaning; corrections are new events that point back with `supersedes_event_id`. A receipt import appends one `purchased` or `refunded` row per receipt line, and `source_ref` is its idempotency key (never appended twice). Price checks are **not** events.

| # | Column | Type | Allowed values / format | Meaning |
|---|---|---|---|---|
| A | `event_id` | text (UUID) | globally unique | Stable identity; row order is not chronology |
| B | `recorded_at` | timestamp text | ISO-8601 | When the database recorded it |
| C | `occurred_at` | date/timestamp text | evidence precision only | When it actually happened, if known |
| D | `actor` | text | | Person/entity who reported or caused it |
| E | `written_by` | text | | Agent/system that wrote the row |
| F | `item_id` | text | `Items.item_id` or blank | Linked item; blank for an unlinked receipt line (never create an `Items` row just to fill it) |
| G | `event_type` | text (suggested enum) | `observed_out`, `observed_low`, `observed_plenty`, `quantity_observed`, `consumed`, `opened`, `purchased`, `refunded`, `requested`, `request_cancelled`, `do_not_buy`, `preference`, `correction`, `deal_observed` | Semantic category; unforeseen types are allowed rather than losing evidence |
| H | `quantity` | number | | Explicit quantity |
| I | `unit` | text | | Quantity unit |
| J | `store` | text | | Retailer/store |
| K | `price_paid` | number | | Observed purchase price |
| L | `raw_message` | text | | Original human wording, when applicable |
| M | `interpretation` | text | | Concise agent interpretation |
| N | `source_type` | text | spec examples: `human_chat`, `receipt_import` | Origin of the evidence |
| O | `source_ref` | text | unique when present | Stable external/message reference; idempotency key for imports |
| P | `supersedes_event_id` | text | `Events.event_id` | Earlier event this one explicitly corrects |

**Fictional example rows**

| event_id | recorded_at | occurred_at | actor | written_by | item_id | event_type | quantity | unit | store | price_paid | raw_message | interpretation | source_type | source_ref | supersedes_event_id |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `22222222-…-0001` | 2026-01-14T06:01:00-08:00 | 2026-01-12 | example-sync | example-sync | `…0003` (Paper towels) | purchased | 1 | | Example Warehouse | 21.99 | | Receipt line: EX PPR TWL 12, 1 @ 21.99 | receipt_import | `examplewarehouse:000:R0000001:100003` | |
| `22222222-…-0002` | 2026-01-14T06:01:00-08:00 | 2026-01-12 | example-sync | example-sync | `…0002` (Eggs) | purchased | 1 | | Example Warehouse | 9.99 | | Receipt line: EX LG EGGS 5DZ, 1 @ 9.99 | receipt_import | `examplewarehouse:000:R0000001:100002` | |
| `22222222-…-0003` | 2026-01-14T08:05:05-08:00 | 2026-01-14T08:04:00-08:00 | alex | example-bot | `…0001` (Milk) | quantity_observed | 0.5 | gallon | | | "about half a gallon of milk left, grab two next time" | Milk about 0.5 gal remaining | human_chat | `chat:alex:msg-0001` | |
| `22222222-…-0004` | 2026-01-14T08:05:05-08:00 | 2026-01-14T08:04:00-08:00 | alex | example-bot | `…0001` (Milk) | requested | 2 | gallon | | | "about half a gallon of milk left, grab two next time" | Buy 2 milk | human_chat | `chat:alex:msg-0001` | |
| `22222222-…-0005` | 2026-01-13T10:00:00-08:00 | 2026-01-13T09:58:00-08:00 | sam | example-bot | `…0003` (Paper towels) | do_not_buy | | | | | "we're set on paper towels until February" | Don't buy paper towels until ~Feb 15 | human_chat | `chat:sam:msg-0002` | |
| `22222222-…-0006` | 2026-01-14T06:01:00-08:00 | 2026-01-12 | example-sync | example-sync | | refunded | 1 | | Example Warehouse | 4.99 | | Refund line: EX SNACK MIX | receipt_import | `examplewarehouse:000:R0000001:100099` | |

Note how one message from alex became two events (`quantity_observed` + `requested`) sharing a `raw_message`, and how the unlinked refund line leaves `item_id` blank. The importer records refund amounts as positive numbers; `event_type = refunded` carries the sign.

---

## 7. Typical access patterns

| Interaction | Reads | Writes |
|---|---|---|
| "We're out of ketchup." | `Config` + `Items` | append 1 `Events` row; narrow update of the affected `Items` cells |
| "I'm heading to Example Warehouse, what do I need?" | `Config` + `Items` + `RetailMemory` rows for that store | none |
| Daily store import | `Integrations` row, existing `RetailMemory` keys, known `Events.source_ref`s | append receipt lines to `Events`; update `RetailMemory` rows in place; update the `Integrations` row |

Agents update only the cells that changed (narrow range writes) to reduce lost updates between concurrent Bots; `Events` is the recovery layer.
