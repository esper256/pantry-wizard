# Store integrations

A store integration connects one retailer to the shared shopping workbook. Costco is the first. The next store uses the same contract.

Each store has:

- `integrations/<store>/SETUP.md` — the only procedure a Grok Bot follows when someone asks to install that store. The Bot loads it from the same GitHub tree as `schema_url`. It does not invent the steps.
- A CLI on the Bot computer. The CLI talks to the retailer and writes a mutation file. It does not write to Google. The Bot applies the file with the Sheets plugin.
- A filter to one confirmed location. Receipts from other locations of that retailer are not household history.
- One `Integrations` row: location, membership fingerprint, owner, lease, imported date span, and the sentence last told to the household.

The mutation file may include `summary` and `integration_upsert`. The Bot tells the household `summary.text` as printed. It copies history, fingerprint, location, status, and `last_summary` onto the row. It does not copy `owner` or `lease_until` from the file. The Bot renews the lease itself after a successful apply, and only then saves the daily routine.

A future `lease_until` held by a different `owner` means do not import and do not start a second schedule. A different `membership_fingerprint` means this workbook is already linked to another membership.

| Store | Setup |
|---|---|
| Costco | [integrations/costco/SETUP.md](costco/SETUP.md) |
