# Costco sync

`costco-sync` reads a household's in-warehouse Costco receipts and writes a mutation file. A Grok Bot applies that file to the shared shopping workbook with the Google Sheets plugin.

This project is not affiliated with Costco. It talks to Costco's private member endpoints through [costco-mcp](https://github.com/thehesiod/costco-mcp). Those endpoints are undocumented, can change, and may conflict with Costco's terms. Use them only for the membership you are signed into.

The tool does not store the Costco password. It stores the browser refresh token that `costco-mcp` already knows how to use. It does not put that token, a membership number, or payment details into the mutation file. That login is read-only: do not use it to buy, check out, or place an order. Purchasing stays under human control.

## What a run keeps

Warehouse receipts only. Gas and car wash documents are skipped. Online orders are out of scope.

Each real receipt line becomes a `purchased` event. An instant-savings child (description `/` plus the parent item number, negative amount) is folded into the parent unit price. Bottle deposits and similar fee lines are dropped. Refunds become `refunded` events and do not move the non-sale price baseline.

Current prices are requested only for item numbers already on a warehouse receipt, already in `RetailMemory`, or confidently matched to a household item that is requested, low, or already tied to Costco. One run prices at most 60 of those that are due: new receipt lines and rows already on sale first, then quiet history. A sale is checked again in about a day, a quiet price in about a week. A number Costco does not price stays on the sheet, and the wait before the next check doubles up to about 90 days. That schedule is in `~/.costco-sync/state.db`, not in the workbook. There is no walk of the savings circular. An ambiguous name search is left unmatched.

A current row is a price reduction when the quoted regular price is at least $0.10 above the current price, or the current price is at least $0.10 under this household's non-sale baseline. The same price on a later day does not add another event.

`price_scope=warehouse` is set only when the payload has a warehouse price field. A generic catalog price is recorded as `online` and is not the warehouse shelf tag.

The mutation file does not add `Items` rows and does not change `inventory_state`, quantity, purchase intent, preferred stores, or item policy. A receipt SKU that the household has not already named stays in `RetailMemory` with a blank `item_id`. A price change updates that row. It does not append an event.

A household installs this through [SETUP.md](SETUP.md). That document is the procedure a Grok Bot follows. This page is the developer note for the CLI.

## Commands

```bash
cd integrations/costco
pip install -e .

costco-sync auth --account personal
costco-sync auth --account personal --storage-key '<key name>' --refresh-token-stdin

costco-sync check --snapshot household.json
costco-sync apply \
  --snapshot household.json \
  --mutations mutations.json \
  --out household.next.json \
  --events-out events.json

costco-sync warehouses

costco-sync history \
  --snapshot household.json \
  --out mutations.json \
  --owner shopping-bot

costco-sync run \
  --snapshot household.json \
  --out mutations.json \
  --owner shopping-bot
```

`warehouses` lists recent warehouse numbers, names, and receipt counts. It writes no mutation file. `history` walks backward about a year at a time, or fetches only the gap when the snapshot already has Costco history. `run` uses the local cursor in `~/.costco-sync/state.db`. With no cursor it looks back 90 days. Receipt details are cached in that database so a later run does not download them again.

Import keeps receipts for the confirmed warehouse on `Integrations.location`. A snapshot may still carry `preferred_costco_warehouse` when that row has no location yet. The sheet itself has no per-store `Config` key. Receipts from every other warehouse are counted as skipped and are not written into events or `RetailMemory`.

The command prints a one-line summary of receipts imported, the dates actually returned, distinct item numbers, and how many receipts were already present. Exit code 1 means the warehouse is not confirmed or Costco rejected the date range. Exit code 2 means Costco auth failed. Exit code 3 means another Bot holds the lease. Exit code 4 means the membership fingerprint does not match. Those failures leave the output file untouched.

Grok Bot Secrets cannot feed this CLI. The refresh token has to be saved by `costco-sync auth` on the Bot computer. Every Bot on that Cursor account can read the computer, so keep the shopping Bot on an account where that is acceptable. Update, Recover, and Reset remove installed packages, so a routine should reinstall from this checkout before it runs.

## Household snapshot

The Bot exports this JSON with the Sheets plugin before each run:

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
  "known_source_refs": []
}
```

`items` and `retail_memory` use the column names in `GOOGLE_SHEETS_SCHEMA.md`. `known_source_refs` is every `Events.source_ref` already stored for this import. Re-running with those refs does not append the same purchase or deal again.

## Mutation file

```text
generated_at
new_items
item_alias_updates     item_id and aliases only
events
retail_memory_upserts
summary                counts and the sentence to tell the household
integration_upsert     Integrations columns except owner and lease_until
```

Apply it in this order:

1. Leave `Items` unchanged. `new_items` is empty. Do not add a household item for a receipt SKU.
2. Write `aliases` for each `item_alias_updates` entry. Do not replace the rest of the row.
3. Append an event only when its `source_ref` is not already present. Do not append a price observation.
4. Update `RetailMemory` on the existing `retail_key`. Append a row only when that key is absent.
5. Update the one `costco` row from `integration_upsert`. Append that row only when `integration_key=costco` is absent. Leave `owner` and `lease_until` as the Bot wrote them. Do not append a second Costco row.

If Costco auth fails, do not apply an older mutation file over current prices.

## Grok Bot routine

One shopping Bot runs this on its cloud computer. No public MCP server is involved.

1. Connect Google Drive and Google Sheets for the household account.
2. Clone this repo under `/workspace` and install `integrations/costco`.
3. Ask the person to open Account, then Orders & Purchases, then copy the `refreshtoken` secret from Chrome DevTools on their own computer, as [SETUP.md](SETUP.md) describes. Do not scrape it. Save it with `costco-sync auth --refresh-token-stdin`.
4. Load runtime instructions v0.7 and follow [SETUP.md](SETUP.md). When Costco’s API fails, file a redacted issue as [integrations/README.md](../README.md) describes.
5. Save a skill: reinstall the package, export the snapshot, run `costco-sync run`, apply the mutation file, renew the lease, and stop without writing if the command exits 2, 3, or 4.
6. Schedule that skill daily only after the setup summary has been said. It should report an auth failure or a newly reduced staple, and stay quiet otherwise.

The trip question reads `Config`, `Items`, and the Costco rows of `RetailMemory`. It uses the sheet. It runs the sync first only when those prices are stale.
