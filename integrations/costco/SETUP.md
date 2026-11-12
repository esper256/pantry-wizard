# Install the Costco integration

This is the procedure for “install the Costco integration.” Follow it in order. Do not invent a warehouse, a date range, or a receipt count.

Say: “I’ll follow the Costco setup from the pantry-wizard repo.”

`pw` must already work: `pw doctor` reports no problems. `PW_AGENT` is this Bot’s name. Every `pw sync` command below uses it as the lease owner.

## 1. Confirm the workbook

```bash
pw sync snapshot costco > household.json
```

Look at `integrations` in that file. An empty list means Costco has never been set up on this sheet. Go on.

If the `costco` entry has a `lease_until` still in the future and an `owner` that is not this Bot, stop. Say which Bot holds the lease and until when. Do not import. Do not save another daily routine.

If `membership_fingerprint` is already set, a later download from a different membership stops with exit code 4. Say this sheet is already linked to another Costco membership.

## 2. Install the CLI

```bash
pip install "git+https://github.com/esper256/pantry-wizard@v2.1.0#subdirectory=integrations/costco"
```

Use the same release as your `pw`. Computer Update, Recover, and Reset remove installed packages, so every later run installs both again first.

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

Then claim the import for this Bot, with the confirmed warehouse:

```bash
pw sync lease costco --location "121 Foster City" --status setup
```

The location is the number and name exactly as `warehouses` printed them. If the command says another Bot holds the lease, stop. Nothing was changed.

## 5. Say whether this is a first download

```bash
pw sync snapshot costco > household.json
costco-sync check --snapshot household.json
```

If `known_source_refs` in the snapshot is empty and the `costco` entry has no `history_from`, say: “This sheet has no Costco history. The first download from `<location>` is about to happen, and receipts from other warehouses will be skipped.” Wait for agreement.

If there are refs or a saved `history_from` / `history_through`, say the saved span and that this run will fetch only the gap. Do not download the full history again.

## 6. Download

```bash
costco-sync history --snapshot household.json --out mutations.json --owner $PW_AGENT
```

The command keeps receipts whose warehouse number matches the confirmed location. It walks backward about a year at a time until a year is empty or Costco rejects the range. On a second setup it requests only the gap.

Exit code 3 means another Bot holds the lease. Exit code 4 means the membership does not match this sheet. Stop and explain. Do not apply the file. Those two cases are not Costco API bugs. Do not file an issue for them.

Exit code 2 means auth failed. Do not apply an older mutation file over current prices. Ask for a fresh DevTools `secret` once. If it still fails, or the command crashes, or Costco’s response cannot be parsed, file a redacted issue as described in [integrations/README.md](../README.md).

## 7. Apply and tell the truth

```bash
pw sync apply costco mutations.json
```

Tell the user the `summary` line in the output. Quote it. Do not invent receipt counts, dates, or item counts. If `closed` is not empty, those requests were on a receipt and came off the list; say so.

Do not edit the sheet any other way. The apply adds receipts, their lines, and any product it has not seen before, and replaces current prices. It does not add household items, and it does not change what anyone asked for beyond closing what a receipt covers.

A first import links a product to a household item only when the item’s name or alias exactly equals the name on the receipt. That is expected. Do not guess the rest, and do not ask about them one at a time. For products the household buys often that have no item, bank one `ask` operation each with `kind` `link`, your suggested item, and the purchase count as evidence. The person answers them in cards, as `AGENT_INSTRUCTIONS.md` describes. A wrong link is worse than no link.

## 8. Keep the lease and save the routine

```bash
pw sync lease costco --status active
```

Only after the summary has been said, save the daily routine:

1. Install `pw` and `integrations/costco` again, from the release in step 2.
2. `pw sync lease costco`. If it refuses, stop without writing.
3. `pw sync snapshot costco > household.json`
4. `costco-sync run --snapshot household.json --out mutations.json --owner $PW_AGENT`. On exit code 2, 3, or 4, stop without applying.
5. `pw sync apply costco mutations.json`
6. Stay quiet unless auth failed, a request was closed by a receipt, or a staple newly went on sale. When Costco’s API errors, file a redacted GitHub issue as described in [integrations/README.md](../README.md). Do not paste the refresh token into that issue.

The daily run prices one paced batch on its own schedule and takes about four minutes. Do not try to make it check everything. A trip uses the short refresh in `AGENT_INSTRUCTIONS.md` instead: the same three commands with `--trip` on the snapshot.

Setup is finished when that summary has been said. The next “I’m going to Costco” question then has history to read.

## A second account

Do this when the household has another Costco membership, or a card whose receipts do not show in the first login’s Orders & Purchases. Its purchases then arrive by themselves, on the same products, prices and history as the first account’s. Do not register it as a separate store.

Choose a short label for it with the person, such as `second` or a first name. Use that label for `--account` in every command below. The first account is called `primary` and needs no flag.

1. Log in as in step 3, with that membership’s own `secret`, under the label:
   ```bash
   costco-sync auth --account second --storage-key '<key name>' --refresh-token-stdin < /tmp/costco-refresh-token
   costco-sync warehouses --account second
   ```
2. Confirm the warehouse as in step 4, then add the account. A new label is what adds it:
   ```bash
   pw sync lease costco --account second --location "121 Foster City" --status setup
   ```
3. Download and apply as in steps 5 to 7, naming the account each time. This is that account’s whole history, however much the first account has already imported:
   ```bash
   pw sync snapshot costco --account second > household-second.json
   costco-sync history --account second --snapshot household-second.json --out mutations-second.json --owner $PW_AGENT
   pw sync apply costco mutations-second.json --account second
   pw sync lease costco --account second --status active
   ```
4. Add the same three commands, with `costco-sync run` in place of `history`, to the daily routine after the first account’s. One Bot running every account is the simplest arrangement. A different Bot can run the second account instead; each account has its own lease.

What to expect:

- `costco-sync --account second` keeps its cursor and receipt cache in `~/.costco-sync/second/`, and its login under that account name, apart from the first account’s.
- Only the first account refreshes prices. The second account’s run asks Costco for no prices, so it is short, and its summary reports receipts only. To move the price refresh, for example when the first membership lapses: `pw sync lease costco --account second --prices`.
- If a receipt was already logged from a photo, the official copy from either account replaces it.
- `pw sync apply` refuses a file downloaded with a different membership than the account it is told to apply to. That means an `--account` was left off. Nothing was written; run it again with the flag.
- A trip refresh needs no flag. It uses the account that refreshes prices.
