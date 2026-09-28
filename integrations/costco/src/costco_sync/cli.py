"""Command line entry for the Grok Bot computer."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from costco_sync.models import AuthError
from costco_sync.run import default_window, write_import
from costco_sync.store import StateStore

_DEFAULT_STATE = Path.home() / ".costco-sync" / "state.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="costco-sync",
        description="Import Costco warehouse receipts into a shopping-list mutation file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Save or check the Costco refresh token")
    auth.add_argument("--account", default=None, help="Account name, such as personal")
    auth.add_argument("--refresh-token", default=None, help="Refresh token from the browser login")

    run = sub.add_parser("run", help="Import receipts since the last success, defaulting to 90 days")
    _add_io(run)

    backfill = sub.add_parser("backfill", help="Import warehouse receipts for an explicit date range")
    _add_io(backfill)
    backfill.add_argument("--start", required=True, help="First day, YYYY-MM-DD")
    backfill.add_argument("--end", required=True, help="Last day, YYYY-MM-DD")

    args = parser.parse_args(argv)
    try:
        if args.command == "auth":
            return _auth(args.account, args.refresh_token)
        if args.command == "run":
            return _run(args)
        return _backfill(args)
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _add_io(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--snapshot", required=True, type=Path, help="Household snapshot JSON")
    parser.add_argument("--out", required=True, type=Path, help="Mutation file to write")
    parser.add_argument("--state", type=Path, default=_DEFAULT_STATE, help="Local SQLite path")
    parser.add_argument("--account", default=None, help="costco-mcp account name")
    parser.add_argument("--now", default=None, help="Override the clock, ISO-8601, for tests")


def _auth(account: str | None, refresh_token: str | None) -> int:
    from costco_sync.live import CostcoSource

    source = CostcoSource(account)
    if refresh_token:
        source.save_refresh_token(refresh_token)
        print(f"Saved a Costco refresh token for account '{source.account}'.")
        return 0
    status = source.status()
    print(f"Account: {status.get('account')}")
    print(f"Refresh token present: {status.get('has_refresh_token')}")
    if not status.get("has_refresh_token"):
        print(
            "Log in at https://www.costco.com in this computer's browser, then copy the "
            "MSAL refresh-token secret. costco-auth-browser can open that browser. "
            "Save it with: costco-sync auth --account personal --refresh-token <token>"
        )
    return 0


def _run(args: argparse.Namespace) -> int:
    store = StateStore(args.state)
    try:
        today = _today(args.now)
        start, end = default_window(today, store.last_success_through())
        write_import(
            args.snapshot,
            args.out,
            store,
            _source(args.account),
            start=start,
            end=end,
            now=_now(args.now),
        )
    finally:
        store.close()
    print(f"Wrote {args.out}")
    return 0


def _backfill(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        print("--start must be on or before --end", file=sys.stderr)
        return 1
    store = StateStore(args.state)
    try:
        write_import(
            args.snapshot,
            args.out,
            store,
            _source(args.account),
            start=start,
            end=end,
            now=_now(args.now),
        )
    finally:
        store.close()
    print(f"Wrote {args.out}")
    return 0


def _source(account: str | None):
    from costco_sync.live import CostcoSource

    return CostcoSource(account)


def _now(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _today(text: str | None) -> date:
    return _now(text).date()
