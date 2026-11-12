# Costco sync

`costco-sync` reads a household's in-warehouse Costco receipts and current prices and writes a mutation file. It does not call Google. The Bot gives it the household's side with `pw sync snapshot costco` and puts the file in the sheet with `pw sync apply costco`.

This project is not affiliated with Costco. It talks to Costco's private member endpoints through [costco-mcp](https://github.com/thehesiod/costco-mcp). Those endpoints are undocumented, can change, and may conflict with Costco's terms. Use them only for the membership you are signed into.

The tool does not store the Costco password. It stores the browser refresh token that `costco-mcp` already knows how to use. It does not put that token, a membership number, or payment details into the mutation file. That login is read-only: do not use it to buy, check out, or place an order. Purchasing stays under human control.

## What a run keeps

Warehouse receipts only. Gas and car wash documents are skipped. Online orders are out of scope.

Each real receipt line is listed under its receipt as a purchase or a refund. An instant-savings child (description `/` plus the parent item number, negative amount) is folded into the parent unit price. Bottle deposits, California redemption value lines, and similar fee lines are dropped, and a deposit row left by an older import is never priced. Refunds do not move the non-sale price baseline.

Current prices are requested only for item numbers already on a warehouse receipt, already in the snapshot's `retail_memory`, or confidently matched to a household item that is requested, low, or already tied to Costco. One run prices new receipt lines, household-linked rows, and real promotions before a first pass of numbers that have never been priced, newest purchase first. While any number is still unchecked, the run prices up to 240 of that list and leaves routine rechecks for later. Once every number has been checked, a run prices up to 80 that are due, oldest due first. A promotion is checked again in about a day. A quiet price is checked in about a week, or about 30 days when the last purchase was more than a year ago or has no date. A cheaper-than-usual price with no store promotion waits with the quiet prices. A number Costco does not price stays on the sheet, and the wait before the next check doubles up to about 90 days. When some prices in a run fail, the summary says how many were missed and those numbers stay due. That schedule is in `~/.costco-sync/state.db`, not in the workbook. There is no walk of the savings circular. An ambiguous name search is left unmatched.

A trip refresh is different. When the snapshot carries `priority_item_numbers`, the run prices only those numbers, in that order, whatever the schedule says, and stops at 60. That is about three minutes of pacing. Numbers Costco has failed to price three times in a row are left for the daily run. `pw sync snapshot costco --trip` writes that list: what the open list and standing rules depend on, then what the household rebuys, longest unchecked first. There is no option that prices everything at once. A full pass takes about half an hour by design and does not belong in front of someone leaving for the store.

Numbers in the snapshot's `non_product_item_numbers` are never priced.

A warehouse sale is recorded when Costco's price before the promotion is at least $0.10 above the price after it. The end date is kept when the promotion includes one. A price at least $0.10 under this household's non-sale baseline is `below_baseline`. That is not a warehouse sale, and a lower price than the last receipt is not one either. A per-pound quote for a variable-weight item is not compared with the package price on a receipt. The same price on a later day does not add another event.

`price_scope=warehouse` is set only when the payload has a warehouse price field. A generic catalog price is recorded as `online` and is not the warehouse shelf tag.

Each priced row also records `availability` and, when the row is instant savings, `sale_scope`. Shelf stock and the in-warehouse temporary price discount come from the Costco app's warehouse-mode service, one search per item number, with a one-second pause. `inventoryStatus` maps to `in_stock`, `low_stock`, or `out_of_stock`. A missing or unrecognized status stays `unknown`. The product summary's `isBuyable` and `programTypes` are not stock. The costco.com inventory batch call returns HTTP 429 to this client and is not used.

`sale_scope=warehouse` means that service reported a temporary price discount. `online` means the summary showed instant savings and the warehouse service returned the item with no such discount. `both` means both did. If the warehouse service does not answer, an instant-savings row stays `unknown` instead of being called a warehouse sale. The importer reports these facts and does not judge them. What a trip brief shows as a deal is decided in one place, `pw/src/pantry_wizard/brief.py`: instant savings, `sale_scope` of `warehouse` or `both`, not past its end date, and `in_stock`, or `low_stock` with a warning.

The mutation file does not add household items and does not change what anyone asked for or said. A receipt SKU that the household has not already named stays unlinked. A price change updates that product's offer. It does not add a receipt line.

A household installs this through [SETUP.md](SETUP.md). That document is the procedure a Grok Bot follows. This page is the developer note for the CLI.

## Commands

```bash
cd integrations/costco
pip install -e .

costco-sync auth --account personal
costco-sync auth --account personal --storage-key '<key name>' --refresh-token-stdin

pw sync snapshot costco > household.json
costco-sync check --snapshot household.json

costco-sync warehouses

costco-sync history \
  --snapshot household.json \
  --out mutations.json \
  --owner shopping-bot

costco-sync run \
  --snapshot household.json \
  --out mutations.json \
  --owner shopping-bot

pw sync apply costco mutations.json
```

`warehouses` lists recent warehouse numbers, names, and receipt counts. It writes no mutation file. `history` walks backward about a year at a time, or fetches only the gap when the snapshot already has Costco history. `run` uses the local cursor in `~/.costco-sync/state.db`. With no cursor it looks back 90 days. After that it asks for receipts since the day before the last success, and once a week it asks for the last 45 days, because a receipt can post to the account weeks after the trip. Lines already in the sheet are skipped, so asking again is safe. Receipt details are cached in that database so a later run does not download them again.

Import keeps receipts for the confirmed warehouse, the `location` of the `costco` entry under `integrations`. Receipts from every other warehouse are counted as skipped and are not written anywhere.

The command prints a one-line summary of receipts imported, the dates actually returned, distinct item numbers, and how many receipts were already present. Exit code 1 means the warehouse is not confirmed or Costco rejected the date range. Exit code 2 means Costco auth failed. Exit code 3 means another Bot holds the lease. Exit code 4 means the membership fingerprint does not match. Those failures leave the output file untouched.

Grok Bot Secrets cannot feed this CLI. The refresh token has to be saved by `costco-sync auth` on the Bot computer. Every Bot on that Cursor account can read the computer, so keep the shopping Bot on an account where that is acceptable. Update, Recover, and Reset remove installed packages, so a routine should install the package again before it runs.

## Household snapshot

`pw sync snapshot costco` writes this JSON before each run:

```json
{
  "household_timezone": "America/Los_Angeles",
  "items": [],
  "retail_memory": [],
  "integrations": [
    {
      "integration_key": "costco",
      "location": "121 Foster City"
    }
  ],
  "known_source_refs": [],
  "non_product_item_numbers": [],
  "priority_item_numbers": []
}
```

`items` rows carry `item_id`, `canonical_name`, `aliases`, `purchase_intent` and `preferred_stores`. A product linked to an item appears in that item's `aliases` as `costco:<itemNumber>`. `retail_memory` has one row per product the household has bought at this warehouse, with the last price paid, the usual price, and the current offer. `known_source_refs` is the `source_ref` of every receipt line this importer already delivered. Re-running with those refs does not deliver the same line again. `priority_item_numbers` is present only for a trip refresh.

These names are the importer's own. They are older than the sheet's current layout and do not have to match it; `pw/src/pantry_wizard/costco_bridge.py` translates in both directions, and `pw/tests/test_importer.py` runs this package through it.

## Mutation file

```text
generated_at
receipts               new receipts: barcode, warehouse_number, date, and lines
                       (source_ref, item_number, description, name, kind,
                       quantity, unit_price, instant_savings)
retail_memory_upserts  rows where something changed: a price, a sale, stock, or a purchase
checked_item_numbers   numbers Costco priced on this run, changed or not
missed_item_numbers    numbers Costco answered for without a price
item_alias_updates     item_id and aliases, where a receipt name exactly matched an item
summary                counts and the sentence to tell the household
integration_upsert     the importer's row, without owner and lease_until
price_lookup           ok or failed
events, new_items      the schema 0.3 form of receipts; new_items is always empty
```

`pw sync apply costco mutations.json` is the only thing that applies it. It adds each receipt and its lines once, adds a product the first time its number appears and never rewrites it, replaces the offers, stamps `checked_at` for the checked numbers, fills an empty product link from `item_alias_updates`, and updates the importer's row without touching the lease. Applying the same file twice changes nothing.

If Costco auth fails, do not apply an older mutation file over current prices.

`costco-sync apply` and the `events` list are for a workbook still on schema 0.3. They go away when the last such workbook has migrated.

## Grok Bot routine

One shopping Bot runs this on its cloud computer. No public MCP server is involved.

1. Set up `pw` as `AGENT_INSTRUCTIONS.md` says.
2. Install this package from the same release.
3. Ask the person to open Account, then Orders & Purchases, then copy the `refreshtoken` secret from Chrome DevTools on their own computer, as [SETUP.md](SETUP.md) describes. Do not scrape it. Save it with `costco-sync auth --refresh-token-stdin`.
4. Follow [SETUP.md](SETUP.md). When Costco’s API fails, file a redacted issue as [integrations/README.md](../README.md) describes.
5. Save a skill: reinstall both packages, renew the lease, write the snapshot, run `costco-sync run`, apply the mutation file, and stop without writing if the command exits 2, 3, or 4.
6. Schedule that skill daily only after the setup summary has been said. It should report an auth failure or a newly reduced staple, and stay quiet otherwise.

The trip question is answered by `pw brief costco` from the sheet, at once. A trip refresh follows it and never precedes it.
