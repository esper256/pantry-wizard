"""Confirmed warehouse, lease, history walk, and the summary the Bot may quote."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from costco_sync.cli import main
from costco_sync.models import LeaseError, LocationRequired, MembershipError, RangeRejected
from costco_sync.run import write_history, write_import
from costco_sync.store import StateStore

FIXTURE = Path(__file__).parent / "fixtures" / "guac_receipt.json"
NOW = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)
SECRETS = ("SECRET-MEMBER-42", "XXXX1234", "APPROVAL999")

HOME_BARCODE = "21134300501862509051323"
OTHER_BARCODE = "99900000000000000000001"
HOME_REF = f"costco:wh:{HOME_BARCODE}:1553261"


def _guac() -> dict:
    return json.loads(FIXTURE.read_text())


def _other_city() -> dict:
    payload = _guac()
    payload["transactionBarcode"] = OTHER_BARCODE
    payload["warehouseNumber"] = "50"
    payload["warehouseName"] = "Other City"
    payload["transactionDate"] = "2024-03-01"
    payload["itemArray"] = [
        {
            "itemNumber": "888",
            "itemDescription01": "OLD CITY MILK",
            "amount": 4.99,
            "unit": 1,
        }
    ]
    return payload


def _fingerprint(number: str = "SECRET-MEMBER-42") -> str:
    return hashlib.sha256(number.encode()).hexdigest()


def _snapshot_path(tmp_path: Path, **overrides) -> Path:
    data = {
        "household_timezone": "America/Los_Angeles",
        "preferred_costco_warehouse": "121 Foster City",
        "items": [],
        "retail_memory": [],
        "integrations": [],
        "known_source_refs": [],
    }
    data.update(overrides)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(data))
    return path


class Catalog:
    """Receipts keyed by listing end date, in Costco's M/DD/YYYY form."""

    def __init__(self, windows: dict[str, list[dict] | Exception]) -> None:
        self.windows = windows
        self.calls: list[tuple[str, str]] = []
        self.priced_at: list[str] = []
        self.details = {}
        for batch in windows.values():
            if isinstance(batch, list):
                for payload in batch:
                    self.details[payload["transactionBarcode"]] = payload

    def list_warehouse_receipts(self, start_date: str, end_date: str) -> dict:
        self.calls.append((start_date, end_date))
        batch = self.windows.get(end_date, [])
        if isinstance(batch, Exception):
            raise batch
        return {
            "receipts": [
                {"transactionBarcode": payload["transactionBarcode"], "documentType": "warehouse"}
                for payload in batch
            ]
        }

    def get_receipt_detail(self, barcode: str) -> dict:
        return self.details[barcode]

    def lookup_product_names(self, skus, warehouse):
        del skus
        return {}

    def lookup_prices(self, skus, warehouse):
        from costco_sync.models import PriceLookupResult

        del skus
        self.priced_at.append(warehouse)
        return PriceLookupResult(ok=True, quotes=[])

    def search_products(self, query, warehouse, limit=5):
        del query, warehouse, limit
        return []


def _run(tmp_path: Path, source, **snapshot_overrides) -> dict:
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    try:
        write_import(
            _snapshot_path(tmp_path, **snapshot_overrides),
            out,
            store,
            source,
            start=date(2026, 6, 1),
            end=date(2026, 9, 28),
            now=NOW,
            owner="shopping-bot",
        )
    finally:
        store.close()
    return json.loads(out.read_text())


def test_other_warehouses_are_excluded_and_the_summary_counts_them(tmp_path: Path):
    source = Catalog({"9/28/2026": [_guac(), _other_city()]})
    mutations = _run(tmp_path, source)
    encoded = json.dumps(mutations)
    for secret in SECRETS:
        assert secret not in encoded
    assert "888" not in encoded
    assert "Other City" not in encoded
    assert source.priced_at == ["121"]
    summary = mutations["summary"]
    assert summary["warehouse"] == "121 Foster City"
    assert summary["receipts_imported"] == 1
    assert summary["receipts_already_present"] == 0
    assert summary["receipts_skipped_other_warehouses"] == 1
    assert summary["oldest"] == "2026-09-01"
    assert summary["newest"] == "2026-09-01"
    assert summary["distinct_items"] == 1
    assert mutations["new_items"] == []
    assert summary["text"] in encoded
    assert "Imported 1 Costco receipts" in summary["text"]
    upsert = mutations["integration_upsert"]
    assert upsert["membership_fingerprint"] == _fingerprint()
    assert upsert["history_from"] == "2026-09-01"
    assert upsert["history_through"] == "2026-09-01"
    assert "owner" not in upsert
    assert "lease_until" not in upsert
    assert upsert["last_summary"] == summary["text"]


def test_second_setup_fetches_only_the_gap_and_does_not_duplicate(tmp_path: Path):
    source = Catalog({"9/28/2026": [_guac()], "9/27/2025": [_other_city()]})
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    snapshot = _snapshot_path(
        tmp_path,
        known_source_refs=[HOME_REF],
        integrations=[
            {
                "integration_key": "costco",
                "location": "121 Foster City",
                "membership_fingerprint": _fingerprint(),
                "history_from": "2026-09-01",
                "history_through": "2026-09-01",
                "owner": "shopping-bot",
                "lease_until": "2026-09-28T00:00:00+00:00",
            }
        ],
    )
    try:
        write_history(
            snapshot,
            out,
            store,
            source,
            today=date(2026, 9, 28),
            now=NOW,
            owner="shopping-bot",
        )
    finally:
        store.close()
    assert source.calls == [("9/01/2026", "9/28/2026")]
    mutations = json.loads(out.read_text())
    assert mutations["events"] == []
    assert mutations["new_items"] == []
    summary = mutations["summary"]
    assert summary["receipts_imported"] == 0
    assert summary["receipts_already_present"] == 1
    assert summary["receipts_skipped_other_warehouses"] == 0
    assert "already in the sheet from 2026-09-01 through 2026-09-01" in summary["text"]
    assert mutations["integration_upsert"]["history_from"] == "2026-09-01"
    assert mutations["integration_upsert"]["history_through"] == "2026-09-01"


def test_history_stops_after_the_preferred_warehouse_runs_out(tmp_path: Path):
    source = Catalog(
        {
            "9/28/2026": [_guac()],
            "9/27/2025": [_other_city()],
        }
    )
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    try:
        write_history(
            _snapshot_path(tmp_path),
            out,
            store,
            source,
            today=date(2026, 9, 28),
            now=NOW,
            owner="shopping-bot",
        )
    finally:
        store.close()
    assert [call[1] for call in source.calls] == ["9/28/2026", "9/27/2025"]
    encoded = json.loads(out.read_text())
    assert "888" not in json.dumps(encoded)
    assert encoded["summary"]["receipts_skipped_other_warehouses"] == 0
    assert encoded["summary"]["receipts_imported"] == 1


def test_a_rejected_older_range_keeps_the_receipts_already_collected(tmp_path: Path):
    source = Catalog({"9/28/2026": [_guac()], "9/27/2025": RangeRejected("too old")})
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    try:
        write_history(
            _snapshot_path(tmp_path),
            out,
            store,
            source,
            today=date(2026, 9, 28),
            now=NOW,
            owner="shopping-bot",
        )
    finally:
        store.close()
    assert out.exists()
    assert json.loads(out.read_text())["summary"]["receipts_imported"] == 1


def test_lease_held_by_another_bot_writes_nothing(tmp_path: Path):
    source = Catalog({"9/28/2026": [_guac()]})
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    with pytest.raises(LeaseError):
        write_import(
            _snapshot_path(
                tmp_path,
                integrations=[
                    {
                        "integration_key": "costco",
                        "location": "121 Foster City",
                        "owner": "other-bot",
                        "lease_until": "2026-09-29T00:00:00+00:00",
                    }
                ],
            ),
            out,
            store,
            source,
            start=date(2026, 9, 1),
            end=date(2026, 9, 28),
            now=NOW,
            owner="shopping-bot",
        )
    store.close()
    assert source.calls == []
    assert not out.exists()


def test_missing_location_does_not_guess_a_warehouse(tmp_path: Path):
    source = Catalog({"9/28/2026": [_guac()]})
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    with pytest.raises(LocationRequired):
        write_import(
            _snapshot_path(tmp_path, preferred_costco_warehouse=""),
            out,
            store,
            source,
            start=date(2026, 9, 1),
            end=date(2026, 9, 28),
            now=NOW,
        )
    store.close()
    assert source.calls == []
    assert not out.exists()


def test_a_different_membership_stops_the_import(tmp_path: Path):
    source = Catalog({"9/28/2026": [_guac()]})
    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    with pytest.raises(MembershipError):
        write_import(
            _snapshot_path(
                tmp_path,
                integrations=[
                    {
                        "integration_key": "costco",
                        "location": "121 Foster City",
                        "membership_fingerprint": _fingerprint("someone-else"),
                    }
                ],
            ),
            out,
            store,
            source,
            start=date(2026, 9, 1),
            end=date(2026, 9, 28),
            now=NOW,
            owner="shopping-bot",
        )
    store.close()
    assert not out.exists()


def test_cli_lease_and_location_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = Catalog({})
    monkeypatch.setattr("costco_sync.cli._source", lambda account: source)
    leased = _snapshot_path(
        tmp_path,
        integrations=[
            {
                "integration_key": "costco",
                "location": "121 Foster City",
                "owner": "other-bot",
                "lease_until": "2026-09-29T00:00:00+00:00",
            }
        ],
    )
    out = tmp_path / "mutations.json"
    code = main(
        [
            "run",
            "--snapshot",
            str(leased),
            "--out",
            str(out),
            "--state",
            str(tmp_path / "state.db"),
            "--owner",
            "shopping-bot",
            "--now",
            "2026-09-28T17:00:00+00:00",
        ]
    )
    assert code == 3
    assert not out.exists()

    blank = tmp_path / "blank.json"
    blank.write_text("{}\n")
    code = main(
        [
            "run",
            "--snapshot",
            str(blank),
            "--out",
            str(out),
            "--state",
            str(tmp_path / "leased-state.db"),
            "--now",
            "2026-09-28T17:00:00+00:00",
        ]
    )
    assert code == 1
    assert not out.exists()


def test_cli_prints_the_summary_and_warehouse_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    source = Catalog({"9/28/2026": [_guac(), _other_city()]})
    monkeypatch.setattr("costco_sync.cli._source", lambda account: source)
    out = tmp_path / "mutations.json"
    code = main(
        [
            "backfill",
            "--snapshot",
            str(_snapshot_path(tmp_path)),
            "--out",
            str(out),
            "--state",
            str(tmp_path / "state.db"),
            "--start",
            "2026-06-01",
            "--end",
            "2026-09-28",
            "--owner",
            "shopping-bot",
            "--now",
            "2026-09-28T17:00:00+00:00",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "Imported 1 Costco receipts from 121 Foster City" in printed
    assert "Skipped 1 receipts from other warehouses." in printed
    assert f"Wrote {out}" in printed

    second = _guac()
    second["transactionBarcode"] = "second-barcode"
    listing = Catalog({"9/28/2026": [_guac(), second]})
    monkeypatch.setattr("costco_sync.cli._source", lambda account: listing)
    code = main(
        [
            "warehouses",
            "--state",
            str(tmp_path / "warehouses.db"),
            "--since",
            "2026-04-01",
            "--now",
            "2026-09-28T17:00:00+00:00",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == "121 Foster City\t2\n"
