# pw

The one tool both household agents use to read and write a Pantry Wizard sheet. Agents follow [`AGENT_INSTRUCTIONS.md`](../AGENT_INSTRUCTIONS.md) at the repository root; this page is for people working on the tool.

```
pip install -e "pw[sheets]"        # from the repository root
cd pw && PYTHONPATH=src:tests python -m unittest discover -s tests    # no network, about a second
```

| Module | Holds |
| --- | --- |
| `schema.py` | tabs, columns, owners, closed vocabularies |
| `backend.py` | in-memory fake, JSON file, Google Sheets |
| `store.py` | a workbook loaded once and committed once; append and set-by-key |
| `ops.py` | the operations agents send, and the merged list |
| `receipts.py` | receipt ingest, auto-close, photo and official copies of one trip |
| `stats.py` | purchases per item, buying rhythm, the stock word |
| `brief.py` | trip brief, the confirmed-deal rule, relevance, change detection, trip refresh tier |
| `costco_bridge.py` | snapshot for the importer, applying its mutation file, the lease |
| `migrate.py` | empty workbook, and schema 0.3 to 2.0 |
| `cli.py` | the `pw` command, `doctor`, the operations log |

Try it without Google:

```
pw --file /tmp/book.json init --timezone America/Los_Angeles
pw --file /tmp/book.json apply --by alex '[{"op": "new_item", "name": "Milk", "new_source": true}, {"op": "add", "item": "milk"}]'
pw --file /tmp/book.json list
pw --file /tmp/book.json brief costco
```

## What is and is not tested

- Every operation, the stock word, the deal rule, the brief, receipts and the migration run against an in-memory workbook.
- `tests/test_importer.py` runs the real `costco-sync` from `integrations/costco` through the bridge against a fake Costco, so the two packages cannot drift apart on the snapshot or the mutation file without a test failing.
- `tests/test_sheets_backend.py` drives `SheetsBackend` against a stand-in for the Google client. It checks the ranges and bodies `pw` sends. It is not Google. The first run against a real sheet should be `pw doctor` on a copy.

## How a write reaches the sheet

A command reads the tabs it needs in one `values.batchGet`. Changes are held in memory and committed together: one `values.append` per tab that gains rows, one `values.batchUpdate` for every changed cell, and for a replaced tab an update followed by a clear of the rows below it. Adding to the list is one read and one append. Values are written as text, so Sheets never reformats a SKU or a timestamp.

The commit is several calls, not one transaction. If it stops halfway, run the same `pw apply` again with the same `--ref`: operations already recorded are skipped.

Every invocation appends one line to `~/.pantry-wizard/ops.log` with its duration and the number of Sheets reads and writes.
