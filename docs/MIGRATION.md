# Moving a household from schema 0.3 to 2.x

The migration reads the old workbook and writes a new one. The old sheet is never changed, so going back means pointing the agents at it again.

`pw migrate` is tested on a made-up 0.3 workbook with the quirks described in issues #38 and #39. It has not seen a real sheet. Treat the first run as a rehearsal and read the report.

## Steps

1. **Create the new workbook.** Make an empty Google Sheet. Share it with each agent's service account as Editor.
2. **Rehearse.** From the agent box, with `PW_SHEET` set to the new sheet:
   ```
   pw migrate --old-sheet <old spreadsheet id> \
       --person "user=Alex" --person "partner=Sam" \
       --store-name "Any grocery store, including Costco=" \
       --once "<one-time item>"
   pw doctor
   pw rebuild
   ```
   - `--person LABEL=NAME` says who an old actor label is. The old sheet spelled people several ways; capitalisation and a suffix in brackets, such as "(whiteboard)", are already ignored. Use it for labels such as `user` or a relationship word.
   - `--store-name LABEL=NAME` does the same for stores. An empty name drops a label that is not a store.
   - `--once` names items that were one-time needs, so they arrive retired and not as active items.
   - The service account needs to be able to read the old sheet. Run the first rehearsal without the `--person` and `--store-name` flags to see which labels exist.
3. **Read the report.** Every entry under `checks` must be `true`. Compare `new` against what you know: number of items, products, linked products, people and stores. Each person and each store should appear once. If one appears twice, add a `--person` or `--store-name` flag and run it again. `purchases_without_a_receipt` counts purchases someone only mentioned in chat; they are kept in `Log`.
4. **Spot-check behaviour.** `pw list`, `pw item <a staple>`, `pw brief <store>`. The brief should contain no one-time purchases.
5. **Pick a quiet half hour and cut over.**
   - Tell everyone not to log anything.
   - Run step 2 again with `--reset` added, so nothing said since the rehearsal is lost. `pw migrate` refuses to write into a workbook that already holds household data unless `--reset` is given.
   - Install `pw` in each agent's environment and give each agent `AGENT_INSTRUCTIONS.md` at the release tag.
   - Set `PW_AGENT` on the agent that runs the importer to the `owner` in the migrated `Sync` row, exactly as spelled. Any other name is refused the lease until it expires.
   - Reinstall `integrations/costco` on the agent that runs it, and run the daily routine in `integrations/costco/SETUP.md` step 8 once. The importer's `~/.costco-sync/state.db` carries over as it is.
   - Rename the old sheet "Pantry Wizard v1 (read only)" and remove the agents' edit access to it.
6. **Afterwards.** Bank questions for unlinked products the household buys often, and answer them in cards. Linking is what lets "do we need eggs?" be answered from purchase rhythm.

## What moves where

| Old | New |
| --- | --- |
| `Items` identity, aliases, stores, policy text | `Items` (`costco:<sku>` aliases become product links; policy text goes to `keep`; "only if on sale" becomes `rule=on_sale` and "whenever in stock" becomes `rule=when_in_stock`) |
| `Items.purchase_intent` | open `List` rows, with the requester and their words from the latest `requested` event |
| `Items` inventory columns and `state_summary` | not migrated; stock is computed from `Log` and purchases |
| `RetailMemory` | `Products` (identity, link) and `Offers` (price, sale, stock) |
| `Events` purchase and refund rows from a receipt | `Receipts` and `Lines`, grouped by receipt; the importer's `source_ref` is kept in `Lines.ref` |
| `Events` purchases someone mentioned in chat | `Log`, as `bought` |
| every other `Events` row | `Log`, keeping id, speaker and reference; `raw` holds the person's words, then ` \| `, then the old interpretation |
| `Integrations` | `Sync` |
| actor labels | `Sources`, one row per person |

## Things to check by hand on real data

- **Photo receipts for trips that never reach the importer** stay `recorded` and count. If the official receipt for the same trip is imported later, the photo receipt is superseded automatically. A photo line keeps its SKU when its old `source_ref` is `photo:<store>:<receipt id>:<sku>` or the importer's own `costco:wh:<barcode>:<sku>`, and the old sheet had a `RetailMemory` row for that SKU. It no longer stops the official receipt from being imported.
- **Deposit lines.** `RetailMemory` rows whose receipt name starts with `CA REDEMP VAL` arrive as `not_product` with no offer, and are never priced again.
- **Other standing rules.** Only "only if on sale" and "whenever in stock" are recognised in policy text. Set anything else, such as `only_at` or `max_price`, with `set_item` after the migration; the original text is in `keep`.
- **`checked_at`.** The old sheet only stamped a row when its value changed, so migrated `checked_at` values can be older than the truth. The first sync fixes this.

## From 2.0 to 2.1

A sheet made by `pw` 2.0 is upgraded where it is. Nothing moves to a new sheet.

1. Install `pw` 2.1 and, where it runs, `integrations/costco` from the same release, on every agent.
2. On one agent: `pw upgrade`, then `pw doctor`.

`pw upgrade` adds an `account` column to `Receipts` and rewrites `Sync` with `sync`, `account` and `prices` columns. The existing importer row becomes the store's `primary` account and keeps refreshing prices. No other row is touched, and running it twice does nothing. Every command keeps working without an `--account` flag.

Between the two steps, an agent on 2.1 refuses to write and says to run `pw upgrade`. After step 2, an agent still on 2.0 refuses to write and says to update. Neither can damage the sheet.

It writes the new header one column to the right of the last one on `Receipts`. Tabs made by `pw init` have room. If someone has deleted the spare columns from that tab, add one first.
