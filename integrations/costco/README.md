# Costco sync

`costco-sync` reads a household's in-warehouse Costco receipts and writes a mutation file. A Grok Bot applies that file to the shared shopping workbook with the Google Sheets plugin.

This project is not affiliated with Costco. It talks to Costco's private member endpoints through [costco-mcp](https://github.com/thehesiod/costco-mcp). Those endpoints are undocumented, can change, and may conflict with Costco's terms. Use them only for the membership you are signed into.

The tool does not store the Costco password. It stores the browser refresh token that `costco-mcp` already knows how to use. It does not put that token, a membership number, or payment details into the mutation file.

## What a run keeps

Warehouse receipts only. Gas and car wash documents are skipped. Online orders are out of scope.

Each real receipt line becomes a `purchased` event. An instant-savings child (description `/` plus the parent item number, negative amount) is folded into the parent unit price. Bottle deposits and similar fee lines are dropped. Refunds become `refunded` events and do not move the non-sale price baseline.

Current prices are requested only for item numbers already on a warehouse receipt, already in `RetailMemory`, or confidently matched to a household item that is requested, low, or already tied to Costco. There is no walk of the savings circular. An ambiguous name search is left unmatched.

A current row is a price reduction when the quoted regular price is at least $0.10 above the current price, or the current price is at least $0.10 under this household's non-sale baseline. The same price on a later day does not add another event.

`price_scope=warehouse` is set only when the payload has a warehouse price field. A generic catalog price is recorded as `online` and is not the warehouse shelf tag.

The mutation file does not change `inventory_state`, quantity, purchase intent, preferred stores, or item policy. A brand-new item is created with `inventory_state=unknown` because a receipt is not a stock count.

## Commands

```bash
cd integrations/costco
pip install -e .

costco-sync auth --account personal
costco-sync auth --account personal --refresh-token <token>

costco-sync backfill \
  --snapshot household.json \
  --out mutations.json \
  --start 2024-01-01 \
  --end 2026-09-28

costco-sync run --snapshot household.json --out mutations.json
```

`run` uses the local cursor in `~/.costco-sync/state.db`. With no cursor it looks back 90 days. `backfill` is the explicit history window. Receipt details are cached in that database so a later run does not download them again. Exit code 2 means Costco auth failed; the output file is left untouched.

Grok Bot Secrets cannot feed this CLI. The refresh token has to be saved by `costco-sync auth` on the Bot computer. Every Bot on that Cursor account can read the computer, so keep the shopping Bot on an account where that is acceptable. Update, Recover, and Reset remove installed packages, so a routine should reinstall from this checkout before it runs.

## Household snapshot

The Bot exports this JSON with the Sheets plugin before each run:

```json
{
  "household_timezone": "America/Los_Angeles",
  "preferred_costco_warehouse": "121 Foster City",
  "items": [],
  "retail_memory": [],
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
```

Apply it in this order:

1. Append a `new_items` row only when that `item_id` is not already on `Items`.
2. Write `aliases` for each `item_alias_updates` entry. Do not replace the rest of the row.
3. Append an event only when its `source_ref` is not already present.
4. Upsert `RetailMemory` by `retail_key`.

If Costco auth fails, do not apply an older mutation file over current prices.

## Grok Bot routine

One shopping Bot runs this on its cloud computer. No public MCP server is involved.

1. Connect Google Drive and Google Sheets for the household account.
2. Clone this repo under `/workspace` and install `integrations/costco`.
3. Open costco.com in the Bot's browser, log in, and save the refresh token with `costco-sync auth`.
4. Load runtime instructions v0.3.
5. Save a skill: reinstall the package, export the snapshot, run `costco-sync run`, apply the mutation file, and stop without writing if the command exits 2.
6. Schedule that skill daily. It should report an auth failure or a newly reduced staple, and stay quiet otherwise.

The trip question reads `Config`, `Items`, and the Costco rows of `RetailMemory`. It uses the sheet. It runs the sync first only when those prices are stale.
