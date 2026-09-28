"""Fixes for the first batch of Costco setup reports."""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from costco_sync.b2c import policy_from_exception, policy_from_storage_key
from costco_sync.live import _is_auth_failure, _is_range_rejected
from costco_sync.models import AuthError
from costco_sync.privacy import tighten_tree
from costco_sync.run import write_history
from costco_sync.sheetapply import apply_mutations, validate_snapshot
from costco_sync.store import StateStore

NOW = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "guac_receipt.json"


def test_storage_key_and_token_error_name_the_b2c_policy():
    key = (
        "oid.e0714dd4-784d-46d6-a278-3e29553483eb-"
        "b2c_1a_sso_wcs_signup_signin_214."
        "e0714dd4-784d-46d6-a278-3e29553483eb-"
        "signin.costco.com-refreshtoken-a3a5186b----"
    )
    assert policy_from_storage_key(key) == "b2c_1a_sso_wcs_signup_signin_214"

    class Response:
        text = (
            '{"error":"invalid_grant","error_description":"AADB2C90088: '
            "Actual Value : B2C_1A_SSO_WCS_signup_signin_201 and "
            'Expected Value : B2C_1A_SSO_WCS_signup_signin_214"}'
        )

    error = Exception("400")
    error.response = Response()
    assert policy_from_exception(error) == "b2c_1a_sso_wcs_signup_signin_214"


def test_policy_mismatch_retries_against_the_expected_policy(tmp_path, monkeypatch):
    import costco_mcp_server.auth as auth

    from costco_sync import b2c

    calls: list[str] = []

    def original(token: str) -> dict:
        del token
        calls.append(auth.POLICY_NAME)
        if len(calls) == 1:
            class Response:
                text = (
                    "AADB2C90088 Actual Value : B2C_1A_SSO_WCS_signup_signin_201 "
                    "Expected Value : B2C_1A_SSO_WCS_signup_signin_214"
                )
                url = "https://signin.costco.com/tenant/policy/oauth2/v2.0/token"
                status_code = 400

            error = Exception("400")
            error.response = Response()
            raise error
        return {"id_token": "id", "refresh_token": "rt"}

    previous = auth._refresh_tokens
    auth._refresh_tokens = original
    monkeypatch.setattr(b2c, "_INSTALLED", False)
    monkeypatch.setattr(b2c, "_POLICY_FILE", tmp_path / "b2c-policy")
    try:
        b2c.install_policy("b2c_1a_sso_wcs_signup_signin_201")
        assert auth._refresh_tokens("token")["id_token"] == "id"
    finally:
        auth._refresh_tokens = previous
        b2c._INSTALLED = False
    assert calls == [
        "b2c_1a_sso_wcs_signup_signin_201",
        "b2c_1a_sso_wcs_signup_signin_214",
    ]
    assert (tmp_path / "b2c-policy").read_text().strip() == "b2c_1a_sso_wcs_signup_signin_214"


def test_token_endpoint_400_is_auth_failure_not_a_rejected_range():
    class Response:
        status_code = 400
        text = '{"error":"invalid_grant","error_description":"AADB2C90088"}'
        url = "https://signin.costco.com/tenant/policy/oauth2/v2.0/token"

    error = Exception("400 Client Error")
    error.response = Response()
    assert _is_auth_failure(error)
    assert not _is_range_rejected(error)


def test_auth_failure_mid_history_does_not_write_a_partial_file(tmp_path):
    guac = json.loads(FIXTURE.read_text())

    class Once:
        def __init__(self) -> None:
            self.calls = 0

        def list_warehouse_receipts(self, start_date: str, end_date: str) -> dict:
            del start_date, end_date
            self.calls += 1
            if self.calls > 1:
                raise AuthError("invalid_grant")
            return {"receipts": [{"transactionBarcode": "21134300501862509051323", "documentType": "warehouse"}]}

        def get_receipt_detail(self, barcode: str) -> dict:
            del barcode
            return guac

        def lookup_product_names(self, skus, warehouse):
            del skus, warehouse
            return {}

        def lookup_prices(self, skus, warehouse):
            from costco_sync.models import PriceLookupResult

            del skus, warehouse
            return PriceLookupResult(ok=True, quotes=[])

        def search_products(self, query, warehouse, limit=5):
            del query, warehouse, limit
            return []

    out = tmp_path / "mutations.json"
    store = StateStore(tmp_path / "state.db")
    with pytest.raises(AuthError):
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "household_timezone": "America/Los_Angeles",
                    "preferred_costco_warehouse": "121 Foster City",
                    "items": [],
                    "retail_memory": [],
                    "integrations": [],
                    "known_source_refs": [],
                }
            )
        )
        write_history(
            snapshot,
            out,
            store,
            Once(),
            today=date(2026, 9, 28),
            now=NOW,
            owner="shopping-bot",
        )
    store.close()
    assert not out.exists()


def test_private_files_and_the_local_database_are_not_world_readable(tmp_path):
    root = tmp_path / ".costco-mcp"
    account = root / "accounts" / "personal"
    account.mkdir(parents=True)
    auth = account / "auth.json"
    auth.write_text('{"refresh_token":"x"}')
    auth.chmod(0o644)
    account.chmod(0o755)
    tighten_tree(root)
    assert auth.stat().st_mode & 0o077 == 0
    assert account.stat().st_mode & 0o077 == 0

    store = StateStore(tmp_path / "state" / "state.db")
    store.close()
    assert store.path.stat().st_mode & 0o077 == 0


def test_stdin_token_is_saved_without_a_command_line_secret(monkeypatch, capsys):
    captured: dict[str, str] = {}

    class Fake:
        account = "personal"

        def save_refresh_token(self, token: str) -> None:
            captured["token"] = token

    monkeypatch.setattr("costco_sync.live.CostcoSource", lambda account, policy=None: Fake())
    monkeypatch.setattr("sys.stdin", io.StringIO("sekrit-value\n"))
    from costco_sync.cli import main

    code = main(["auth", "--account", "personal", "--refresh-token-stdin"])
    assert code == 0
    assert captured["token"] == "sekrit-value"
    assert "sekrit-value" not in capsys.readouterr().out


def test_confirmed_alias_fills_item_id_on_an_existing_retail_row():
    from costco_sync.decide import build_mutations, snapshot_from_json
    from costco_sync.models import PriceLookupResult

    snapshot = snapshot_from_json(
        {
            "household_timezone": "America/Los_Angeles",
            "preferred_costco_warehouse": "663 Concord",
            "items": [
                {
                    "item_id": "item-bacon",
                    "canonical_name": "Bacon",
                    "aliases": "costco:1553261",
                }
            ],
            "retail_memory": [
                {
                    "retail_key": "costco:663:1553261",
                    "item_id": "",
                    "store": "Costco",
                    "retailer_sku": "1553261",
                    "receipt_name": "BACON",
                }
            ],
            "known_source_refs": [],
        }
    )

    def prices(skus, warehouse):
        del skus, warehouse
        return PriceLookupResult(ok=True, quotes=[])

    mutations, _, _ = build_mutations(
        snapshot,
        [],
        names={},
        price_lookup=prices,
        search=lambda query, warehouse: [],
        baseline_samples={},
        already_sampled=set(),
        now=NOW,
    )
    assert mutations["events"] == []
    assert mutations["new_items"] == []
    row = mutations["retail_memory_upserts"][0]
    assert row["item_id"] == "item-bacon"


def test_apply_is_idempotent_and_does_not_take_the_lease_from_the_file():
    snapshot = {
        "items": [{"item_id": "item-bacon", "aliases": ""}],
        "retail_memory": [],
        "integrations": [
            {
                "integration_key": "costco",
                "location": "663 Concord",
                "owner": "shopping-bot",
                "lease_until": "2026-09-29T00:00:00+00:00",
                "membership_fingerprint": "abc",
                "history_from": "",
                "history_through": "",
            }
        ],
        "known_source_refs": [],
    }
    mutations = {
        "item_alias_updates": [{"item_id": "item-bacon", "aliases": "costco:1"}],
        "events": [{"source_ref": "costco:wh:barcode:1", "event_type": "purchased"}],
        "retail_memory_upserts": [{"retail_key": "costco:663:1", "item_id": "", "retailer_sku": "1"}],
        "integration_upsert": {
            "integration_key": "costco",
            "location": "663 Concord",
            "status": "active",
            "owner": "other-bot",
            "lease_until": "1999-01-01T00:00:00+00:00",
        },
    }
    updated, events = apply_mutations(snapshot, mutations)
    assert [event["source_ref"] for event in events] == ["costco:wh:barcode:1"]
    assert updated["items"][0]["aliases"] == "costco:1"
    assert updated["integrations"][0]["owner"] == "shopping-bot"
    assert updated["integrations"][0]["lease_until"] == "2026-09-29T00:00:00+00:00"
    assert updated["integrations"][0]["status"] == "active"
    again, more = apply_mutations(updated, mutations)
    assert more == []
    assert again["known_source_refs"] == updated["known_source_refs"]
    assert validate_snapshot(updated) == []


def test_check_reports_a_snapshot_that_cannot_import(tmp_path):
    from costco_sync.cli import main

    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"items": []}))
    code = main(["check", "--snapshot", str(path)])
    assert code == 1
