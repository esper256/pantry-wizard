# Sheet schema 2.0

Reference for people changing `pw`. Agents do not need this page: they use `pw` commands and never address a tab or column themselves. The definitions live in `pw/src/pantry_wizard/schema.py`; if this page and that file disagree, the file is right.

## Rules the layout follows

1. Every tab has headers in row 1 and its key in column A.
2. A household write is an append, or a set of named cells on the row with a given key. Nothing else.
3. Each fact is written in one place. Nothing an agent writes summarises other cells.
4. Each tab has one kind of writer, so two writers never rewrite the same row.
5. Values that code filters on come from closed lists.
6. Anything that can be computed is computed when asked. The `v_` tabs are copies for people to read.

## Tabs

| Tab | One row per | Key | Written by | How |
| --- | --- | --- | --- | --- |
| `Config` | setting | `key` | setup | `schema_version`, `timezone`, `currency` |
| `Sources` | person or store the household uses | `source` (`person:alex`, `store:costco`) | agents, first time seen | append |
| `List` | request | `id` | agents | append; set `status`, `closed_at`, `closed_by`, `closed_ref` |
| `Items` | thing the household tracks | `item` (readable, e.g. `eggs`) | agents | append; rare set |
| `Log` | observation or audit entry | `id` | agents | append only |
| `Questions` | banked judgment call | `id` | agents ask, people answer | append; set `answer`, `status` |
| `Products` | store product | `product` (`costco:1025795`) | appended the first time a SKU is seen on a receipt; agents set `item`, `kind`, `pack_qty`, `unit` | never rewritten |
| `Offers` | product's current price and stock | `product` | importer only | replaced |
| `Receipts` | receipt | `receipt` | importer, receipt operation | append; set `status` |
| `Lines` | receipt line | `line` | importer, receipt operation | append only |
| `Sync` | store importer | `store` | importer; lease by the owning agent | replaced |
| `v_Items` | item | `item` | `pw sync apply`, `pw rebuild` | replaced |
| `v_Brief` | line of the last brief per store | `key` | `pw brief` | replaced |

## Columns worth explaining

**`List`.** `kind` is `buy`, `skip` or `remind`. `item` is blank for a one-off; `text` is what the list should say. `qty` is free text in the requester's words. `only_if` is blank, `on_sale`, or `any_price` (buy it this once whatever the item's `rule` says). `until` expires a row. Duplicate open rows for one item are expected: they are merged on read and closed together.

**`Items`.** `stores` are preferred stores; `only_at` restricts the item to those stores. `rule` is a standing instruction: `on_sale` (surface when there is a confirmed sale; a request for the item waits for one too), `when_in_stock` (mention when the store has it), `never`. `kind` `once` marks a one-time need that should not be suggested again. `keep` is stock policy in words. There are no inventory columns.

**`Log`.** `kind` is `out`, `low`, `last_one`, `plenty`, `have`, `used`, `bought`, `said`, `set` or `fix`. The newest observation per item is the stock evidence. `set` rows are written by `pw` whenever an item or product is changed, with who asked and their words.

**`Products`.** `kind` is blank (unclassified), `consumable`, `once`, `seasonal` or `not_product` (deposits, fees). Blank lets behaviour decide: bought on two or more dates in the last year counts as a habit. `item` links the product to a household item. One item can have many products.

**`Offers`.** `sale` is blank or `instant_savings`, the store's own promotion. `usual_price` is the importer's non-sale baseline. `changed_at` moves when a value moves; `checked_at` moves whenever the store answered for that product, changed or not. `misses` counts consecutive lookups the store answered without a price.

**`Receipts`.** `source` is `import`, `photo` or `manual`. The key is `<store>:<id>` for an import and `<source>:<store>:<id>` otherwise, where the id is the one printed on the receipt, or the date and total, or the date and a short hash of the lines. `status` is `recorded` or `superseded`. Two receipts are the same trip when the store matches and either the printed id matches, or the date matches and the totals agree or most SKUs are shared. An imported receipt supersedes a photo of the same trip, and the photo's lines stop counting. A second photo of a recorded trip is not stored. Nothing is deleted.

**`Lines`.** `product` when the receipt printed a SKU; `item` when the agent matched the line directly and the product does not already say so (stores without SKUs, mostly). A SKU line that names an item links the product to it if the product has no link yet. `kind` is `purchase`, `refund` or `deposit`. `ref` keeps the importer's own idempotency key; only lines under `import` receipts are reported back to the importer as known.

## What is derived, and from what

| Value | From |
| --- | --- |
| Open list | `List` rows with `status=open`, merged per item |
| Closed by a receipt | open `buy` rows whose item is on the receipt and `remind` rows for that store, if asked on or before the receipt's date |
| Last bought, usual gap, buys in 12 months | `Lines` under `recorded` receipts, plus `Log` `bought` |
| Last paid | newest purchase line for the product |
| Stock word | newest observation, purchases since, usual gap (`stats.py`). What was said is believed for 30 days or two usual gaps, whichever is longer; after that "plenty" is dropped and "out" or "low" becomes `check` |
| Confirmed deal | `Offers`: promotion, warehouse scope, on the shelf, not ended (`brief.py`) |
| Worth showing | `Products.kind`, the item link, purchase history (`brief.relevant`) |
