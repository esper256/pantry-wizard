# Anonymized Costco API response fixtures

These are real warehouse-mode receipt payloads captured from Costco's
orders GraphQL API and stored by costco-sync, then scrubbed for
publication. Product names, item numbers, prices, discounts, quantities,
dates, warehouse numbers, tax, and store addresses are unchanged.
Membership numbers, payment account numbers, and transaction barcodes
were replaced with obviously fake values that keep the same field types
and widths.

Source: costco-sync `StateStore.receipts` (`barcode`, `payload`). The
plugin only persists receipt **detail** responses, not the list
response, product-summary body, or warehouse-mode inventory body.

Endpoint (list and detail):
`POST https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql`

## Files

### `receipts_list.json`

List-shaped `receiptsWithCounts` body for the query the client sends
with `startDate`, `endDate`, `documentType: "warehouse"`, and
`documentSubType: "all"`. That query also requests `inWarehouse`,
`gasStation`, `carWash`, `gasAndCarWash`, a slim receipt (no
membership or `displayAccountNumber`), `itemArray { itemNumber }`,
`tenderArray { tenderTypeCode, tenderDescription, amountTender }`, and
`couponArray { upcnumberCoupon }`.

No raw list response was cached. This file was **projected** from the
three stored detail receipts below into that selection set. The count
fields are filled to match this fixture (all three receipts are
In-Warehouse; gas and car-wash counts are 0). `couponArray` is `[]`
because the stored detail payloads did not include it.

### `receipt_detail_instant_savings_spaces.json`

Detail query `receiptsWithCounts(barcode, documentType: "warehouse")`
for a 2026-01-16 warehouse sale (21 lines, warehouse 663). Instant
savings children use every spacing the importer has to handle,
including the issue #50 form with two spaces after the slash
(`/  897980`), one space (`/ 1949203`), and no space (`/1970759`).

### `receipt_detail_refund.json`

Same detail query for a 2025-12-17 `transactionType: Refund` receipt
(warehouse 663). Includes negative quantities, department-ring lines
(`itemNumber` `"0"`, `unit` 0), electronic-return coupon rows
(`CPN/ E/RETUN`, typo as printed), and a single `RTC Ecom Purchase`
tender.

### `receipt_detail_multi_tender.json`

Same detail query for a 2025-05-23 sale paid with two tenders (`CITI
Rebate` and `Cash`). Also includes a four-space instant-savings child
(`/    720`) alongside no-space `/SKU` children.

## What was anonymized

| Field | Replacement | Notes |
| --- | --- | --- |
| `membershipNumber` | `111111111111` | 12-digit string, same width as live values |
| `tenderArray[].displayAccountNumber` | `XXXX` | Live values are a 4-character last-4, not a full PAN. This API did not return a 16-digit card number, so the 4-character width is preserved. |
| `tenderArray[].approvalNumber` | *(none present)* | Would have been replaced with a six-character placeholder if set |
| `transactionBarcode` | 23-digit synthetic value | Layout kept: 3-digit document prefix + 3-digit warehouse + 3-digit register + 4-digit transaction + `yymmdd` + `1111`. `registerNumber` and `transactionNumber` were updated to match so the fixture stays internally consistent. The original barcode is a transaction identifier and was not published. |
| Names, emails, phones, tokens, cookies | *(none in these payloads)* | No member/cashier name, email, phone, or token fields appeared |

Warehouse addresses, warehouse names, product text, SKUs, prices,
dates, and tax fields were kept.

## Not in this folder

- **Gas receipts.** The stored warehouse receipts are all
  `documentType: WarehouseReceiptDetail`. The list helper skips gas and
  car-wash document types, and no gas-station payloads were cached.
- **Product summary / warehouse-mode inventory.** Those HTTP bodies were
  not stored in `state.db`. Trimmed product-summary samples already live
  next door as `../price_summary_663.json` and
  `../summary_stock_663.json`.
- **Online orders** (`getOnlineOrders`). Not used by costco-sync
  warehouse import.

`../guac_receipt.json` is a small synthetic receipt (not a raw
GraphQL envelope) used by the older pairing tests.
