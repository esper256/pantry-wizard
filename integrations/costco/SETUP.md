# Install the Costco integration

This is the procedure for “install the Costco integration.” Follow it in order. Do not invent a warehouse, a date range, or a receipt count.

Say: “I’ll follow the Costco setup from the shopping-list repo.”

## 1. Confirm the workbook

Read `Config`, `Items`, `RetailMemory`, `Events.source_ref`, and `Integrations` with the Sheets plugin.

If the `costco` row has a `lease_until` still in the future and a different `owner`, stop. Say which Bot holds the lease and until when. Do not import. Do not save another daily routine.

If `membership_fingerprint` is already set, remember it. A later download that hashes to a different membership stops the setup. Say this sheet is already linked to another Costco membership.

## 2. Install the CLI

Clone this repo under `/workspace` if it is not already there. Then:

```bash
pip install -e /workspace/integrations/costco
```

Computer Update, Recover, and Reset remove installed packages, so every later run reinstalls from this checkout.

## 3. Log in and prove the receipt list works

Open `https://www.costco.com` in this computer’s browser and let the person log in there. Do not ask them to paste a Costco password into chat.

The Costco login is only for reading receipts and prices. Do not use the credentials, the refresh token, or this browser session to buy anything on the shopping list, check out, or place an order. Purchasing stays solely with the person.

Save the browser refresh token:

```bash
costco-sync auth --account personal --refresh-token <token>
```

Then list recent warehouses:

```bash
costco-sync warehouses
```

Exit code 2 means the login did not work. Stop and retry the browser login. Do not continue to a download.

`warehouses` prints lines of `number name`, a tab, and a receipt count, busiest first. It does not write a mutation file.

## 4. Confirm one warehouse

Show the list. Point at the busiest recent warehouse and ask whether that is the one they shop at. Wait for an answer.

Do not import until they confirm. Other warehouses in the account, including a previous city’s, are not household history.

Write an `Integrations` row:

- `integration_key` = `costco`
- `store` = `Costco`
- `status` = `setup`
- `location` = the confirmed warehouse, such as `121 Foster City`
- `owner` = this Bot’s name
- `lease_until` = now plus 36 hours

Read the row back. If `owner` is not this Bot, stop. Someone else took the lease.

## 5. Say whether this is a first download

Export a snapshot JSON with `household_timezone`, `items`, `retail_memory`, `integrations`, and every `Events.source_ref` as `known_source_refs`.

If that snapshot has no `costco:` source ref and the row has no `history_from`, say: “This sheet has no Costco history. The first download from `<location>` is about to happen, and receipts from other warehouses will be skipped.” Wait for agreement.

If those refs or `history_from` / `history_through` already exist, say the saved span and that this run will fetch only the gap. Do not download the full history again.

## 6. Download

```bash
costco-sync history --snapshot household.json --out mutations.json --owner <this-bot>
```

The command keeps receipts whose warehouse number matches `Integrations.location`. It walks backward about a year at a time until a year is empty or Costco rejects the range. On a second setup it requests only the gap.

Exit code 3 means another Bot holds the lease. Exit code 4 means the membership does not match this sheet. Stop and explain. Do not apply the file.

Exit code 2 means auth failed. Do not apply an older mutation file over current prices.

## 7. Apply and tell the truth

Apply the mutation file with the Sheets plugin:

1. Leave `Items` unchanged. Do not add a household item because it appeared on a receipt.
2. Write `aliases` for each `item_alias_updates` entry. Do not replace the rest of the row.
3. Append an event only when its `source_ref` is not already present. Do not append a row for a price check.
4. Update `RetailMemory` where `retail_key` already exists. Append a row only for a new key.
5. Update the existing `costco` row from `integration_upsert`: `status`, `location`, `membership_fingerprint`, `history_from`, `history_through`, `last_sync_at`, and `last_summary`. Append that row only if it does not exist yet. Do not copy `owner` or `lease_until` from the file, and do not append a second `costco` row. The membership number itself is not in the file.

Tell the user the `summary.text` line the command printed. Quote it. Do not invent receipt counts, dates, or item counts.

## 8. Keep the lease and save the routine

Set `lease_until` to now plus 36 hours and `status` to `active`.

Only after the summary has been said, save the daily routine:

1. Reinstall `integrations/costco`.
2. Export the snapshot, including `integrations` and `known_source_refs`.
3. Run `costco-sync run --snapshot household.json --out mutations.json --owner <this-bot>`.
4. Apply the mutation file the same way.
5. Renew `lease_until` for 36 hours.
6. Stay quiet unless auth failed or a staple newly went on sale.

Setup is finished when that summary has been said. The next “I’m going to Costco” question then has history to read.
