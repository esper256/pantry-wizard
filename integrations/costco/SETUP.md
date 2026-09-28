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

Do this before any Costco command. Do not open Costco in the Bot browser to scrape the refresh token. The Bot cannot read that token out of the browser, and trying is what produces the first error.

Say this to the person:

> I can’t read the Costco refresh token from the browser. On your own computer, in Chrome, log in at https://www.costco.com. Open Account, then Orders & Purchases, and let that page finish loading. The home page does not store the token. Then open Developer Tools, choose Application, then Local Storage, then `https://www.costco.com`. If the key is not there, also look under `https://signin.costco.com`. Find the key whose name contains `refreshtoken`. That name also contains the sign-in policy, such as `signup_signin_214`. Paste the key name and, separately, only its `secret` value. I will use the secret to read receipts and prices. I will not use it to buy anything.

Do not ask them to paste a Costco password. Do not put the secret on a command line. Write it to a file that is mode `0600`, then:

```bash
costco-sync auth --account personal --storage-key '<key name>' --refresh-token-stdin < /tmp/costco-refresh-token
rm -f /tmp/costco-refresh-token
```

The key name is not a secret. The secret is. Do not repeat the secret.

The login is only for reading receipts and prices. Do not use the credentials, the refresh token, or a browser session to buy anything on the shopping list, check out, or place an order. Purchasing stays solely with the person. Do not put the token in the sheet, the mutation file, or a GitHub issue.

Then prove the token works:

```bash
costco-sync warehouses
```

Exit code 2 means authentication failed. A token-endpoint `invalid_grant` or `AADB2C90088` is exit 2, not a rejected date range. The tool retries once against the policy Costco names in that error. If it still fails, ask the person to copy the `secret` once more from Orders & Purchases. If that also fails, file a redacted issue as described in [integrations/README.md](../README.md). Do not continue to a download.

`warehouses` prints lines of `number name`, a tab, and a receipt count, busiest first. It does not write a mutation file.

## 4. Confirm one warehouse

Show the list. Point at the busiest recent warehouse and ask whether that is the one they shop at. Wait for an answer.

Use `costco-sync warehouses`, not the warehouse selected on costco.com. That setting is often an old house. If the site and the receipt list disagree, say so and ask. Receipts from other warehouses are counted as skipped in the summary. That is expected.

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

Export a snapshot JSON with `household_timezone`, `items`, `retail_memory`, `integrations`, and every `Events.source_ref` as `known_source_refs`. The `costco` row must include `location`, `membership_fingerprint`, `owner`, `lease_until`, `history_from`, and `history_through` when those cells exist. Then run `costco-sync check --snapshot household.json`. A plugin-less Bot applies the mutation file with `costco-sync apply`, which is the tested meaning of the Sheets write and does not call Google.

If that snapshot has no `costco:` source ref and the row has no `history_from`, say: “This sheet has no Costco history. The first download from `<location>` is about to happen, and receipts from other warehouses will be skipped.” Wait for agreement.

If those refs or `history_from` / `history_through` already exist, say the saved span and that this run will fetch only the gap. Do not download the full history again.

## 6. Download

```bash
costco-sync history --snapshot household.json --out mutations.json --owner <this-bot>
```

The command keeps receipts whose warehouse number matches `Integrations.location`. It walks backward about a year at a time until a year is empty or Costco rejects the range. On a second setup it requests only the gap.

Exit code 3 means another Bot holds the lease. Exit code 4 means the membership does not match this sheet. Stop and explain. Do not apply the file. Those two cases are not Costco API bugs. Do not file an issue for them.

Exit code 2 means auth failed. Do not apply an older mutation file over current prices. Ask for a fresh DevTools `secret` once. If it still fails, or the command crashes, or Costco’s response cannot be parsed, file a redacted issue as described in [integrations/README.md](../README.md).

## 7. Apply and tell the truth

Apply the mutation file with the Sheets plugin:

1. Leave `Items` unchanged. Do not add a household item because it appeared on a receipt.
2. Write `aliases` for each `item_alias_updates` entry. Do not replace the rest of the row.
3. Append an event only when its `source_ref` is not already present. Do not append a row for a price check.
4. Update `RetailMemory` where `retail_key` already exists. Append a row only for a new key.
5. Update the existing `costco` row from `integration_upsert`: `status`, `location`, `membership_fingerprint`, `history_from`, `history_through`, `last_sync_at`, and `last_summary`. Append that row only if it does not exist yet. Do not copy `owner` or `lease_until` from the file, and do not append a second `costco` row. The membership number itself is not in the file.

Tell the user the `summary.text` line the command printed. Quote it. Do not invent receipt counts, dates, or item counts.

A first import leaves `item_id` blank unless a household item already has the alias `costco:<itemNumber>` or its name exactly equals the receipt line. That is expected. Do not invent links. `search_products` returns nothing on purpose. A wrong SKU is worse than an unmatched row.

After the apply, for items the household buys, wants, or already ties to Costco, show `RetailMemory` rows whose `receipt_name` or `retailer_name` might be that item. Ask the person to confirm. On a yes, set that item's alias to include `costco:<itemNumber>` and set `RetailMemory.item_id`. On a later sync, rows that already have that alias get `item_id` filled in. Old `Events` rows stay as they were. The trip question can still read an unlinked `RetailMemory` row for the store. The link is what connects that price to a household item.

## 8. Keep the lease and save the routine

Set `lease_until` to now plus 36 hours and `status` to `active`.

Only after the summary has been said, save the daily routine:

1. Reinstall `integrations/costco`.
2. Export the snapshot, including `integrations` and `known_source_refs`.
3. Run `costco-sync run --snapshot household.json --out mutations.json --owner <this-bot>`.
4. Apply the mutation file the same way.
5. Renew `lease_until` for 36 hours.
6. Stay quiet unless auth failed or a staple newly went on sale. When Costco’s API errors, file a redacted GitHub issue as described in [integrations/README.md](../README.md). Do not paste the refresh token into that issue.

Setup is finished when that summary has been said. The next “I’m going to Costco” question then has history to read.
