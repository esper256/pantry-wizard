# Store integrations

A store integration connects one retailer to the shared shopping workbook. Costco is the first. The next store uses the same contract.

Each store has:

- `integrations/<store>/SETUP.md` — the only procedure a Grok Bot follows when someone asks to install that store. The Bot loads it from the same GitHub tree as `schema_url`. It does not invent the steps.
- A CLI on the Bot computer. The CLI talks to the retailer and writes a mutation file. It does not write to Google. The Bot applies the file with the Sheets plugin.
- A filter to one confirmed location. Receipts from other locations of that retailer are not household history.
- One `Integrations` row: location, membership fingerprint, owner, lease, imported date span, and the sentence last told to the household.

The mutation file may include `summary` and `integration_upsert`. The Bot tells the household `summary.text` as printed. It copies history, fingerprint, location, status, and `last_summary` onto the row. It does not copy `owner` or `lease_until` from the file. The Bot renews the lease itself after a successful apply, and only then saves the daily routine.

A future `lease_until` held by a different `owner` means do not import and do not start a second schedule. A different `membership_fingerprint` means this workbook is already linked to another membership.

The retailer login is read-only. The Bot does not buy, check out, or place an order with it. Purchasing stays under human control. The Bot does not scrape an OAuth refresh token out of a browser. For Costco, the person copies the `secret` from Chrome DevTools, as [integrations/costco/SETUP.md](costco/SETUP.md) says.

## When a retailer call fails

A crash, an unexpected response, or an error from the retailer’s API is a bug to fix in this repo. File it. A lease held by another Bot, a membership mismatch, and a warehouse the person has not confirmed are household state. Do not file those.

Before the issue, and before repeating the error in chat, replace every sensitive value with `[redacted]`:

- refresh tokens, access tokens, the DevTools `secret`, cookies, and `Authorization` headers
- passwords
- email addresses and phone numbers
- membership numbers
- card numbers, approval codes, and other payment fields
- a person’s name and street address

Warehouse numbers, item numbers, dates, the command name, the exit code, and the HTTP status can stay. Do not attach a raw receipt. Write the redacted body to a file and pass that file to `gh`. The refresh token must not appear in the file or on the command line.

Search open issues first. Comment on a matching one instead of opening a duplicate.

```bash
gh issue list --repo esper256/pantry-wizard --state open --search "costco: <same failure>"
gh issue create --repo esper256/pantry-wizard --title "costco: <one line>" --body-file /tmp/costco-issue.md
```

Use these headings in the body: what the Bot ran, the exit code, the redacted output, and what the person was trying to do (setup or daily sync). If `gh` is not authenticated, show the person the redacted text and https://github.com/esper256/pantry-wizard/issues/new and ask them to submit it. Do not invent an issue number.

| Store | Setup |
|---|---|
| Costco | [integrations/costco/SETUP.md](costco/SETUP.md) |
