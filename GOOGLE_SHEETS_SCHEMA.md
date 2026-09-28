# Google Sheets Shopping Database Schema

**Status:** Draft  
**Schema Version:** 0.2  
**Related protocol:** `AGENT_RUNTIME_INSTRUCTIONS.md` v0.6

## Purpose

This document defines the initial Google Sheets workbook used as the shared durable database for the Shared Agentic Shopping List.

The workbook is intended to be shared with household members and their AI agents. Humans normally interact through natural-language conversations; agents read and mutate the workbook on their behalf.

The schema is deliberately shaped by four goals:

1. Keep ordinary agent interactions fast and small.
2. Preserve enough historical evidence to recover from poor agent inference.
3. Remain understandable and repairable by a human directly in Google Sheets.
4. Avoid depending on advanced Google Sheets API features that may not be exposed by every AI provider or connector.

The design is not intended to turn Google Sheets into a general-purpose relational database. It exploits Sheets where Sheets is strong: durable hosted storage, collaboration, version history, human visibility, simple append/update operations, and broad agent accessibility.

---

# 1. Workbook Layout

A new household Shopping Database contains exactly five required sheets:

```text
Config         small control-plane metadata
Items          compact materialized current household state
RetailMemory   replaceable commercial cache for known retailer SKUs
Integrations   one row per connected store importer
Events         append-only evidence/history ledger
```

The important architectural distinction is:

```text
                 normal interaction
                        │
                        ▼
                 Config + Items
                    HOT PATH
                        │
                        │ named-store briefing
                        ▼
                  RetailMemory
               COMMERCIAL CACHE
                        │
                        │ state-changing observation
                        ▼
                      Events
                    COLD HISTORY
```

Routine agent operation SHOULD NOT require scanning `Events`.

`Items` exists specifically to make current household state cheap to retrieve. `RetailMemory` exists so a named-store briefing can see recent purchase prices and current price reductions without scanning `Events`. `Events` exists to preserve evidence, provide auditability, and allow state to be reconstructed when inference or concurrent writes go wrong.

Ordinary inventory questions SHOULD ignore `RetailMemory` and `Integrations`. A question such as "I'm heading to Costco" SHOULD read the `RetailMemory` rows for that store. Store setup and the daily importer read the one `Integrations` row for that store.

---

# 2. Why Not Use One Sheet?

A single append-only ledger would preserve history cleanly, but every question such as:

> "I'm going to FoodMaxx. What do I need to know?"

would eventually require an agent to retrieve and reason over months or years of household events.

That is inefficient in two different ways:

- it increases Google Sheets API payload and processing work;
- more importantly, it consumes AI context with old evidence that usually has no relevance to the current decision.

Conversely, storing only current state would be efficient but would destroy the evidence needed to understand why the state exists or recover from a bad inference.

The `Items` + `Events` split therefore behaves similarly to an event log plus a materialized view:

```text
Events  = what was reported or observed
Items   = best current interpretation
```

An event is ground-truth evidence that an observation or report occurred. It is not necessarily ground truth about physical reality; humans and agents can be mistaken.

---

# 3. Google Sheets API Design Constraints

The initial schema assumes only ordinary spreadsheet reads, range updates, and row appends.

Google currently documents Sheets API quotas of 300 read requests and 300 write requests per minute per project, with 60 reads and 60 writes per minute per user per project. Batch requests count as one API request, and Google recommends batching multiple reads or writes when practical. Google also recommends keeping individual request payloads around 2 MB or smaller for performance.

For ordinary household use, request quotas are therefore unlikely to be the limiting factor. The more important constraints are round trips, payload size, AI context size, portability across agent connectors, and concurrent-write behavior.

A direct Sheets API implementation can efficiently batch multiple range reads and writes. `spreadsheets.batchUpdate` also supports multiple spreadsheet mutations in one atomic request. However, an AI provider's Google Sheets integration may expose only higher-level operations.

**Correctness MUST NOT depend on every agent supporting advanced batching or transactions.**

The portable baseline is:

```text
READ   Config + Items
READ   RetailMemory when briefing a named store
READ   Integrations when running or installing a store importer
WRITE  append one or more Events
WRITE  update affected fields in Items, RetailMemory, or Integrations
```

Implementations that support batching MAY reduce those operations further.

Official references:

- Google Sheets API — Usage limits: https://developers.google.com/workspace/sheets/api/limits
- Google Sheets API — Read and write cell values: https://developers.google.com/workspace/sheets/api/guides/values
- Google Sheets API — Update spreadsheets: https://developers.google.com/workspace/sheets/api/guides/batchupdate
- Google Sheets API — Developer metadata: https://developers.google.com/workspace/sheets/api/guides/metadata

---

# 4. `Config` Sheet

`Config` is a small key/value control plane. Agents SHOULD be able to read the entire sheet cheaply.

## Columns

| Column | Meaning |
|---|---|
| `key` | Stable configuration key |
| `value` | Configuration value |
| `description` | Human-readable explanation |

## Initial Rows

| `key` | Initial `value` | `description` |
|---|---|---|
| `schema_version` | `0.2` | Shopping Database schema version |
| `schema_url` | *(set during setup)* | Authoritative `GOOGLE_SHEETS_SCHEMA.md` |
| `protocol_version` | `0.6` | Required agent runtime-instruction version |
| `runtime_instructions_url` | *(set during setup)* | Authoritative `AGENT_RUNTIME_INSTRUCTIONS.md` |
| `behavior_spec_version` | `0.6` | Informational behavior-spec version |
| `behavior_spec_url` | *(set during setup)* | Full behavior specification |
| `household_timezone` | *(set during setup)* | IANA timezone such as `America/Los_Angeles` |
| `currency` | *(set during setup)* | Currency such as `USD` |
| `timestamp_format` | `ISO-8601 with explicit timezone offset` | Machine-written timestamp convention |

Example `schema_url`:

```text
https://raw.githubusercontent.com/esper256/shared-agentic-shopping-list/main/GOOGLE_SHEETS_SCHEMA.md
```

Agents SHOULD load `schema_url` when `schema_version` is unfamiliar.

`timestamp_format` summarizes the machine-written timestamp convention defined below. Live workbooks SHOULD keep this key so agents and humans can recover the expected representation without rereading the full schema.

`schema_url` and `runtime_instructions_url` MAY point at a mutable branch such as `main` during development. For releases, prefer immutable tag or commit URLs over `main`.

Agents MUST NOT casually modify protocol or schema configuration. These values change only as part of explicit system administration or upgrade.

Do not store frequently changing counters such as `next_event_id` in `Config`; shared counters create needless concurrency hazards.

Do not add a `Config` key per store. A confirmed warehouse or store location belongs on `Integrations.location`. `Config` stays the small control plane above.

---

# 5. Machine-Written Timestamps

The following fields are machine-written timestamps when populated by an agent:

```text
recorded_at
occurred_at
inventory_as_of
intent_expires_at
updated_at
observed_at
reduction_ends_at
last_paid_at
lease_until
last_sync_at
```

Agents MUST serialize these values as ISO-8601 text with an explicit timezone offset.

Example:

```text
2026-09-10T14:15:23-07:00
```

Agents MUST NOT write:

- locale strings such as `9/10/26 2:15 PM`;
- Google Sheets native date serials;
- ambiguous timezone-less date-times for machine writes.

Spreadsheet columns for these fields SHOULD be formatted as Plain text so Sheets does not rewrite the stored representation.

For `occurred_at` and `last_paid_at`, store only the precision supported by evidence. A date-only value such as `2026-09-10` is acceptable when the evidence does not establish a time. Agents MUST NOT invent an exact time.

`reduction_ends_at` MAY be date-only when the promotion evidence gives a last day and no time. Leave it blank when no end is known. Do not invent one.

`Integrations.history_from` and `Integrations.history_through` are dates of receipts actually imported, not the start of a query window. Date-only is correct.

---

# 6. `Items` Sheet

`Items` is the hot-path materialized view of current household state.

There is one row per canonical household item.

A normal inventory interaction SHOULD be answerable from `Config` + `Items` without reading historical `Events` or `RetailMemory`. A named-store briefing also reads `RetailMemory`.

## Columns

| Column | Meaning |
|---|---|
| `item_id` | Stable opaque item identifier; never a row number |
| `canonical_name` | Human-readable household item name |
| `aliases` | Alternate names, separated consistently |
| `inventory_state` | Qualitative current inventory state |
| `quantity_estimate` | Optional estimated quantity; blank when unjustified |
| `quantity_unit` | Unit associated with the estimate |
| `inventory_confidence` | `low`, `medium`, or `high` |
| `inventory_as_of` | Timestamp of evidence supporting current inventory knowledge |
| `purchase_intent` | Blank, `buy`, or `do_not_buy` |
| `requested_quantity` | Optional explicitly requested purchase quantity |
| `intent_expires_at` | Optional expiration for temporary intent |
| `preferred_stores` | Store preferences, represented compactly |
| `item_policy` | Concise durable natural-language preferences or stock policy |
| `state_summary` | Concise explanation of the important evidence behind current state |
| `last_event_id` | Most recent event materially used to update this item |
| `updated_at` | Timestamp of the last modification to any derived item state |
| `updated_by` | Agent/system responsible for that modification |

## Recommended `inventory_state` Values

```text
unknown
out
very_low
probably_low
adequate
plenty
```

These states intentionally permit uncertainty.

## Why `inventory_as_of` and `updated_at` are different

These timestamps MUST NOT be conflated.

Suppose an item has inventory evidence from July, but in September a user says:

> "We prefer Heinz ketchup."

The item row was modified in September, but the inventory evidence is still from July.

Therefore:

```text
inventory_as_of = freshness of inventory evidence
updated_at       = freshness of the row as a whole
```

Using only `updated_at` would make stale inventory appear falsely fresh whenever an unrelated preference changes.

## `item_policy`

`item_policy` intentionally allows concise natural-language state rather than normalizing every possible household preference into separate columns.

Example:

```text
FoodMaxx preferred; Heinz preferred; keep one unopened reserve when practical;
up to 4 total is reasonable during a strong sale.
```

This is a deliberate agentic design choice. The consumer of the data is normally another language model. Frequently queried state should be structured; infrequent nuanced policy can remain human-readable natural language.

If later experience shows that a policy attribute needs reliable mechanical filtering or computation, it can be promoted into a structured column in a future schema version.

## `state_summary`

`state_summary` is important to keeping `Events` off the hot path.

Example:

```text
Opened last tube Sep 8; one in use and no unopened reserve known.
```

An agent can usually understand the current interpretation without retrieving the historical ledger. The ledger remains available when the summary appears contradictory, needs correction, or requires deeper reconstruction.

---

# 6A. `RetailMemory` Sheet

`RetailMemory` is the hot-path cache of commercial facts for retailer SKUs the household already buys or has explicitly connected to a household item.

There is one row per store, warehouse or location, and retailer SKU. Find that row by `retail_key` and update it. A later price, a later receipt, or a repeated import MUST NOT append another row for the same key. The sheet is not a promotion ledger and it is not household inventory.

A row exists only for a SKU purchased at a confirmed location, or already linked to a household item. Do not add a row per catalog deal, per receipt line, or per day the price was checked. Other locations of the same retailer are not extra rows.

A normal inventory question SHOULD ignore this sheet. A named-store briefing SHOULD read the rows for that store. The expected size is the household's own known SKUs, not the retailer's catalog, so reading the sheet is cheap.

## Columns

| Column | Meaning |
|---|---|
| `retail_key` | Stable identity, never a row number. Costco keys look like `costco:{warehouseNumber}:{itemNumber}` |
| `item_id` | Link to `Items.item_id` when the SKU is matched to a household item. Blank when identity is still ambiguous |
| `store` | Retailer name, such as `Costco` |
| `location` | Warehouse or store location, such as `121 Foster City` |
| `retailer_sku` | Retailer item number |
| `retailer_name` | Best product name known for that SKU |
| `receipt_name` | Abbreviated name as printed on a receipt, when known |
| `last_paid_unit_price` | Most recent non-refund unit price actually paid, after instant savings |
| `last_paid_at` | When that price was paid. Date-only is correct for a receipt date |
| `baseline_unit_price` | Typical unit price from purchases that were not themselves instant savings |
| `purchase_count` | Count of purchase lines folded into this row, excluding refunds |
| `current_price` | Latest observed sell price. Blank when no current observation exists |
| `regular_price` | Pre-discount price when the source provides one |
| `reduction_kind` | Blank, `instant_savings`, `below_baseline`, or `instant_savings,below_baseline` |
| `reduction_ends_at` | When the current reduction ends, if known |
| `price_scope` | `warehouse` or `online`. Blank when no current price was observed |
| `observed_at` | When `current_price` was observed |

`reduction_kind` blank means there is no current price reduction to act on. A sale does not change `Items.inventory_state`.

Importers MUST NOT copy payment details, membership numbers, or credentials into this sheet.

Price observations are replaceable. Re-running an import updates the current-price columns in place. A price observation MUST NOT append an `Events` row. The current reduction is `reduction_kind` together with `current_price` and `regular_price`. When a later observation is not a reduction, clear `reduction_kind` and `reduction_ends_at` in place and keep the latest `current_price`.

# 6B. `Integrations` Sheet

`Integrations` is one row per connected store importer. Costco is the first store. A later store uses the same columns. Do not add a store-specific sheet, a receipt-archive sheet, a sync log, or a `Config` key per retailer.

Ordinary inventory questions ignore this sheet. A store briefing does not need it. Setup and the scheduled importer read the one row for that store.

Find the row by `integration_key` and update it. A daily sync MUST NOT append another row. If no row exists for that key, append one. `last_summary` replaces the previous sentence. It is not a history of syncs. `history_from` and `history_through` are two date cells on that same row, not one row per import chunk. The lease is the `owner` and `lease_until` cells on that same row, not a lease log.

## Columns

| Column | Meaning |
|---|---|
| `integration_key` | Stable store slug, such as `costco`. Never a row number |
| `store` | Retailer name, such as `Costco` |
| `status` | `setup`, `active`, or `paused` |
| `location` | Confirmed home location, such as `121 Foster City` |
| `membership_fingerprint` | SHA-256 of the membership number. The number itself is not stored |
| `owner` | Which Bot runs the schedule, such as `shopping-bot` |
| `lease_until` | When that owner's claim expires. A daily run renews it for 36 hours |
| `history_from` | Oldest receipt date actually imported for this location |
| `history_through` | Newest receipt date actually imported for this location |
| `last_sync_at` | When the last successful import was applied |
| `last_summary` | The sentence the household was told after that import |

The lease is a household courtesy, not a transaction. Before a long download, the Bot writes its `owner`, reads the row back, and continues only if it still owns the row. A second Bot that sees a future `lease_until` and a different `owner` does not start another schedule and does not import. The same owner, or an expired lease, may continue. A different `membership_fingerprint` means this sheet is already linked to another membership; stop and say so. The fingerprint is a hash. Do not add a column or a cell for the membership number.

# 7. `Events` Sheet

`Events` is an append-only evidence ledger.

One row represents one atomic semantic event. `item_id` links that evidence to a household item when one already exists. Leave `item_id` blank when a receipt line is not yet linked. Do not create an `Items` row solely to fill it in.

A receipt import appends one `purchased` or `refunded` row per receipt line. `source_ref` is the idempotency key: the same value MUST NOT be appended again. A price check is not an event. Record the observed price on `RetailMemory`.

## Columns

| Column | Meaning |
|---|---|
| `event_id` | Globally unique stable event identifier |
| `recorded_at` | When the Shopping Database recorded the event |
| `occurred_at` | When the event actually occurred, if known |
| `actor` | Human/entity responsible for or reporting the underlying event |
| `written_by` | Agent/system that wrote the row |
| `item_id` | Stable reference to `Items.item_id` |
| `event_type` | Semantic event category |
| `quantity` | Optional explicit quantity |
| `unit` | Optional quantity unit |
| `store` | Optional retailer/store |
| `price_paid` | Optional observed purchase price |
| `raw_message` | Original human wording when applicable |
| `interpretation` | Concise agent interpretation of the evidence |
| `source_type` | Origin such as `human_chat` or `receipt_import` |
| `source_ref` | Stable external/message reference when available |
| `supersedes_event_id` | Optional earlier event explicitly corrected/superseded |

## Suggested Event Types

Initial vocabulary may include:

```text
observed_out
observed_low
observed_plenty
quantity_observed
consumed
opened
purchased
requested
request_cancelled
do_not_buy
preference
correction
deal_observed
```

The first schema SHOULD NOT rely on a hard validation list for correctness. If a legitimate unforeseen event cannot be represented by the preferred vocabulary, preserving the evidence is better than rejecting it.

## Atomic Events

A single user message may generate multiple event rows.

Example:

> "We're out of ketchup and I bought two milks."

should normally create two events, each associated with the appropriate item. Repeating the same `raw_message` is acceptable. This avoids introducing another Interaction table merely to normalize chat messages.

## Event IDs

`event_id` MUST NOT use row number as identity.

Concurrent appends can make physical row ordering unreliable as an identifier. Use an opaque globally unique value such as a UUID, ULID, or equivalent unique identifier produced by the agent/tooling.

Physical row order is not authoritative chronological order; timestamps and identifiers are.

---

# 8. Normal API Access Pattern

## Routine state-changing message

Example:

> "We're out of ketchup."

Preferred flow:

```text
1. Read Config + Items.
2. Resolve Ketchup to an existing item_id.
3. Append an observed_out Event.
4. Update only the affected Ketchup fields in Items.
5. Verify the resulting state if the connector makes this practical.
```

A connector capable of batch reads SHOULD retrieve `Config` and `Items` together.

A connector capable of an atomic batch mutation MAY append the event and update the item in a single request.

Correctness MUST NOT depend on that capability.

## Read-only store briefing

Example:

> "I'm going to FoodMaxx. What do I need to know?"

Preferred flow:

```text
1. Read Config + Items.
2. Read RetailMemory rows for the named store.
3. Synthesize the briefing.
4. Read Events only if a particular current state needs deeper reconciliation.
```

Routine store briefings SHOULD NOT scan the full ledger. `RetailMemory` is the commercial hot path: recent prices paid and current reductions for known SKUs. Other questions SHOULD skip it.

---

# 9. Why `Events` Is a Cold Path

The ledger may grow forever.

If a household records 20 events per day, it would accumulate more than 7,000 rows per year and more than 14,000 rows in a two-year conversation lifetime.

That scale is not especially large for Google Sheets, but repeatedly injecting the entire history into an AI context would be wasteful and eventually harmful.

`Items` therefore acts as a compact current-state cache/materialized view.

Agents consult historical events when needed for:

- explicit corrections;
- contradictory current state;
- recovery after a partial write;
- suspected concurrent-write loss;
- historical learning or analysis;
- debugging why an item reached its current state.

This design optimizes both API access and language-model context.

---

# 10. Why There Is No Index Sheet in Schema v0.1

Google Sheets is not a relational query engine. The standard value API is naturally range-oriented.

Google supports developer metadata that can be attached to spreadsheet locations and queried through `DataFilter`, which could eventually provide a form of stable row lookup. However, schema v0.1 deliberately does not depend on it.

Reasons:

1. Developer metadata is invisible to ordinary human spreadsheet users.
2. Different AI connectors may not expose it.
3. Every participating agent would need to maintain the index correctly.
4. A corrupt secondary index creates another class of repair problem.
5. A household `Items` table should initially remain small enough to read cheaply in full.

Therefore the portable baseline is to keep `Items` compact and retrieve it as needed.

If real usage demonstrates that `Items` becomes too large, future versions MAY add indexing, partitioning, metadata, an Apps Script façade, or another backend. Optimization should respond to measured need rather than anticipated database scale.

---

# 11. Concurrent Write Strategy

Google Sheets collaboration does not provide the same row-level compare-and-swap semantics as a conventional transactional application database.

Two agents may read the same item and then attempt different updates.

Schema v0.1 mitigates this rather than pretending the problem does not exist.

Agents SHOULD:

1. read current relevant state shortly before writing;
2. append evidence before or alongside derived state;
3. update only the fields that actually changed when the connector permits narrow range updates;
4. avoid replacing an entire row when only one field changed;
5. preserve unique event IDs and timestamps;
6. re-read/reconcile when conflicting evidence is detected.

Narrow updates are particularly important. If one agent changes `preferred_stores` while another changes `inventory_state`, two whole-row replacements could unnecessarily overwrite one another. Two narrow range updates are much less likely to conflict.

The append-only `Events` sheet is the recovery layer if derived `Items` state is lost or overwritten.

Schema 0.2 adds one lease on `Integrations` for store importers. It does not lock ordinary household edits. Two Bots must not schedule the same store import while `lease_until` is in the future for a different `owner`. The lease is two cells on the store's existing row.

A future implementation MAY add stronger concurrency control. Correctness of ordinary household edits still does not require a general locking service.

---

# 12. Physical Spreadsheet Rules

The workbook SHOULD remain boring and predictable.

For all required sheets:

- headers are in row 1;
- freeze row 1 for human usability;
- ordinary filtering MAY be enabled;
- do not merge cells in the data region;
- do not put decorative titles above the header row;
- do not rely on row ordering for identity;
- do not use row numbers as foreign keys;
- do not require formulas for correctness;
- do not require formatting for semantics.

`Events` SHOULD contain only its tabular ledger in the primary data area so append operations can reliably locate the end of the table.

Humans MAY sort `Items`; stable `item_id` values ensure sorting does not change identity.

Formatting and data validation MAY improve human usability, but an agent capable only of reading and writing ordinary cell values should still be able to participate correctly.

Human-facing column widths and wrap for headers and long text fields (`item_policy`, `state_summary`, `raw_message`, `interpretation`) are recommended so a person can read and repair the workbook in place. That formatting is not required for API correctness.

Timestamp columns listed in section 5 SHOULD be formatted as Plain text so Sheets does not rewrite machine-written values.

---

# 13. Blank Workbook Definition

A conforming blank Shopping Database contains:

```text
Shared Agentic Shopping Database

├── Config
│   ├── key
│   ├── value
│   └── description
│
├── Items
│   ├── item_id
│   ├── canonical_name
│   ├── aliases
│   ├── inventory_state
│   ├── quantity_estimate
│   ├── quantity_unit
│   ├── inventory_confidence
│   ├── inventory_as_of
│   ├── purchase_intent
│   ├── requested_quantity
│   ├── intent_expires_at
│   ├── preferred_stores
│   ├── item_policy
│   ├── state_summary
│   ├── last_event_id
│   ├── updated_at
│   └── updated_by
│
├── Integrations
│   ├── integration_key
│   ├── store
│   ├── status
│   ├── location
│   ├── membership_fingerprint
│   ├── owner
│   ├── lease_until
│   ├── history_from
│   ├── history_through
│   ├── last_sync_at
│   └── last_summary
│
├── RetailMemory
│   ├── retail_key
│   ├── item_id
│   ├── store
│   ├── location
│   ├── retailer_sku
│   ├── retailer_name
│   ├── receipt_name
│   ├── last_paid_unit_price
│   ├── last_paid_at
│   ├── baseline_unit_price
│   ├── purchase_count
│   ├── current_price
│   ├── regular_price
│   ├── reduction_kind
│   ├── reduction_ends_at
│   ├── price_scope
│   └── observed_at
│
└── Events
    ├── event_id
    ├── recorded_at
    ├── occurred_at
    ├── actor
    ├── written_by
    ├── item_id
    ├── event_type
    ├── quantity
    ├── unit
    ├── store
    ├── price_paid
    ├── raw_message
    ├── interpretation
    ├── source_type
    ├── source_ref
    └── supersedes_event_id
```

`Items`, `RetailMemory`, `Integrations`, and `Events` contain only their header rows when a household starts.

`Config` contains the initial configuration rows defined above, including `schema_url` and `timestamp_format`.

Timestamp columns SHOULD be formatted as Plain text in a blank workbook so Sheets does not rewrite later machine writes. Column widths and wrap for headers and long text fields MAY be applied for human repairability; they are not required for API correctness.

---

# 14. Explicit Non-Goals for v0.1

Schema 0.2 adds `RetailMemory` and `Integrations` and no other sheet. A workbook that is still on schema 0.1 is upgraded in place. There is no second copy of the workbook.

## Upgrading a schema 0.1 workbook

A Bot MAY do this when the person asks to install a store integration, or when `schema_version` is older than the schema loaded from `schema_url`. It MUST NOT rewrite `Items` or `Events`. It MUST NOT delete sheets.

1. Add a `RetailMemory` sheet and an `Integrations` sheet if they are missing. Row 1 is the header row in the order in section 13. Both sheets are otherwise empty.
2. Format `last_paid_at`, `observed_at`, `reduction_ends_at`, `lease_until`, and `last_sync_at` as Plain text.
3. Set `Config` `schema_version` to `0.2`, `protocol_version` to `0.6`, and `behavior_spec_version` to `0.6`. If `schema_url`, `runtime_instructions_url`, `behavior_spec_url`, or `timestamp_format` is missing, add the row from section 4.
4. Leave every existing `Config`, `Items`, and `Events` value unchanged.

A later schema bump SHOULD ship the same kind of note: which sheets and columns to add, which `Config` values to set, and a statement that existing evidence rows stay. The Bot that the person asked to install or upgrade MAY apply that note. It MUST refuse importer writes that need a sheet the workbook does not have yet, tell the person, and offer this upgrade.

`RetailMemory` holds the current price of a known SKU. Updating a price replaces cells. It does not add a promotion-history table, a row per day, or an `Events` row.

`Integrations` holds one row per connected store, including a lease so only one Bot schedules that store's import. A sync updates that row. It does not add a log.

A receipt import MUST NOT insert an `Items` row because a SKU appeared on a receipt. Years of purchases belong in `Events`, once per line, and in `RetailMemory`, one row per SKU. The hot `Items` sheet stays limited to items the household actually tracks.

The other v0.1 non-goals still apply.

Schema v0.1 intentionally does NOT include:

- a secondary index sheet;
- formulas required for correctness;
- a separate normalized stores table;
- a separate aliases table;
- a separate chat-interactions table;
- a separate preferences table;
- a permanent promotion-history table;
- a general locking service for household edits;
- row-version compare-and-swap machinery;
- developer metadata indexes;
- Apps Script;
- a custom backend service.

These may become appropriate later. Each adds complexity, agent interoperability requirements, and additional shared state that can become inconsistent.

The initial design should validate the core premise first: independent household agents can safely maintain a useful shared state using ordinary Google Sheets operations.

---

# 15. Future Scaling Triggers

The schema should evolve because of observed problems rather than speculative scale.

Potential reasons to revisit the architecture include:

- `Items` becomes expensive to retrieve in full;
- AI connectors repeatedly exceed useful context sizes;
- concurrent writes frequently corrupt derived state;
- event history becomes difficult to reconcile efficiently;
- retailer/deal imports create high event volume;
- mechanical filtering becomes more important than LLM interpretation;
- agents need transactional operations not exposed by generic Sheets integrations;
- API quotas become a practical household limitation.

Possible future responses include:

- Google Sheets developer metadata indexes;
- additional materialized-view sheets;
- an Apps Script or Cloud Run API façade;
- partitioned/archive event sheets;
- a real database while retaining Sheets as a human-facing view.

None are required for schema v0.1.

---

# 16. Central Design Principle

The database should be optimized for the common case:

```text
Human says something casually
        ↓
Agent reads small current state
        ↓
Agent preserves the observation
        ↓
Agent updates a compact materialized view
        ↓
Future humans and agents share the improved state
```

Google Sheets is being used deliberately, not accidentally.

`Items` keeps routine agent context small. `Events` makes inference recoverable. `Config` keeps long-lived agents synchronized with the current protocol. Ordinary cells keep the entire system human-visible and portable across agent providers.

The schema should remain as simple as possible until real household use proves that additional machinery is worth its cost.