# What a v2 workbook looks like

A new household starts from a blank Google Sheet. `pw init` adds every tab with its header row and the `Config` rows, and nothing else. Every row below is fictional and exists only to show the shape. Column meanings are in [SCHEMA.md](SCHEMA.md).

The layout this replaces (schema 0.3: `Items`, `RetailMemory`, `Integrations`, `Events`) is described in [`GOOGLE_SHEETS_SCHEMA.md`](../GOOGLE_SHEETS_SCHEMA.md) until every household has migrated.

**`Sources`**

| source | name | note |
| --- | --- | --- |
| person:alex | Alex | |
| person:sam | Sam | |
| store:example-warehouse | Example Warehouse | home location 000 |
| store:corner-market | Corner Market | |

**`Items`**

| item | name | aliases | kind | stores | only_at | rule | max_price | keep | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eggs | Eggs | large eggs | | example-warehouse | | | | | |
| honey-cereal | Honey cereal | | | | | on_sale | | | |
| paper-towels | Paper towels | | | example-warehouse | | | | one unopened pack | |

**`List`**

| id | kind | item | text | qty | store | only_if | until | by | at | raw | status | any_of |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3f9a… | buy | eggs | Eggs | the big pack | | | | alex | 2026-01-14T08:05:00-08:00 | get the big pack of eggs | open | |
| 71c2… | buy | | Birthday candles | | | | | sam | 2026-01-14T09:10:00-08:00 | we need birthday candles | open | |
| 8e55… | buy | | Melons | | | | | alex | 2026-01-14T09:12:00-08:00 | get some melons | open | watermelon; cantaloupe |
| a04d… | skip | paper-towels | Paper towels | | | | 2026-02-15 | sam | 2026-01-13T10:00:00-08:00 | no more paper towels until mid February | open | |

**`Notes`**

| id | at | by | text | status |
| --- | --- | --- | --- | --- |
| c1f0… | 2026-01-14T09:20:00-08:00 | sam | Sam is vegetarian | open |
| d7a2… | 2026-01-14T09:21:00-08:00 | alex | Milk always means oat milk | open |

**`Accounts`** (usually empty; `key` is filled only to bootstrap a bot that cannot be handed a key file)

| account | email | key | note |
| --- | --- | --- | --- |
| grok-bot | pantry-grok@example-project.iam.gserviceaccount.com | | shared with this account's email |

**`Log`**

| id | at | by | agent | item | kind | value | raw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 9be1… | 2026-01-14T08:05:00-08:00 | alex | example-bot | eggs | low | | about half a dozen left |
| 2d77… | 2026-01-14T08:06:00-08:00 | alex | example-bot | honey-cereal | set | rule=on_sale | only buy the honey cereal on sale |

**`Products`** and **`Offers`**

| product | store | sku | name | receipt_name | item | kind |
| --- | --- | --- | --- | --- | --- | --- |
| example-warehouse:100002 | example-warehouse | 100002 | Example Large Eggs, 5 dozen | EX LG EGGS 5DZ | eggs | |
| example-warehouse:100004 | example-warehouse | 100004 | Example Patio Heater | EX PATIO HTR | | once |

| product | price | regular_price | usual_price | sale | sale_scope | sale_ends | availability | changed_at | checked_at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| example-warehouse:100002 | 7.99 | 9.99 | 9.99 | instant_savings | warehouse | 2026-01-25 | low_stock | 2026-01-12T06:00:00-08:00 | 2026-01-14T06:00:00-08:00 |

**`Receipts`** and **`Lines`**

| receipt | store | date | total | lines | source | status | tax | savings |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| example-warehouse:R0000001 | example-warehouse | 2026-01-12 | 8.68 | 1 | import | recorded | 0.69 | 2.00 |
| photo:corner-market:2026-01-10:23.40 | corner-market | 2026-01-10 | 23.40 | 1 | photo | recorded | | |

The import's `total` is blank because the importer does not read one.

| line | receipt | n | product | item | text | qty | unit_price | kind |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| example-warehouse:R0000001:1 | example-warehouse:R0000001 | 1 | example-warehouse:100002 | | EX LG EGGS 5DZ | 1 | 9.99 | purchase |
| photo:corner-market:2026-01-10:23.40:1 | photo:corner-market:2026-01-10:23.40 | 1 | | honey-cereal | HONEY CEREAL | 1 | 4.29 | purchase |
