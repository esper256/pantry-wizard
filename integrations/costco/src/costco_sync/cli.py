"""Command line entry for the Grok Bot computer."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from costco_sync.privacy import private_umask, tighten_private_files

from costco_sync.models import AuthError, LeaseError, LocationRequired, MembershipError, RangeRejected
from costco_sync.run import (
    default_window,
    format_warehouse_line,
    list_warehouses,
    warehouse_listing_window,
    write_history,
    write_import,
)
from costco_sync.store import StateStore

_DEFAULT_STATE = Path.home() / ".costco-sync" / "state.db"


def main(argv: list[str] | None = None) -> int:
    private_umask()
    parser = argparse.ArgumentParser(
        prog="costco-sync",
        description="Import Costco warehouse receipts into a shopping-list mutation file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Save or check the Costco refresh token")
    auth.add_argument("--account", default=None, help="Account name, such as personal")
    auth.add_argument("--refresh-token", default=None, help="Refresh token. Prefer --refresh-token-stdin")
    auth.add_argument(
        "--refresh-token-stdin",
        action="store_true",
        help="Read the refresh token from one line of stdin",
    )
    auth.add_argument(
        "--storage-key",
        default=None,
        help="localStorage key name that contains the policy. Not the secret",
    )

    check = sub.add_parser("check", help="Check a household snapshot before import")
    check.add_argument("--snapshot", required=True, type=Path, help="Household snapshot JSON")

    apply = sub.add_parser("apply", help="Apply a mutation file to a snapshot JSON copy")
    apply.add_argument("--snapshot", required=True, type=Path, help="Household snapshot JSON")
    apply.add_argument("--mutations", required=True, type=Path, help="Mutation file from run or history")
    apply.add_argument("--out", required=True, type=Path, help="Next snapshot JSON")
    apply.add_argument("--events-out", required=True, type=Path, help="Events that still need appending")

    warehouses = sub.add_parser("warehouses", help="List recent warehouses and receipt counts")
    warehouses.add_argument("--state", type=Path, default=_DEFAULT_STATE, help="Local SQLite path")
    warehouses.add_argument("--account", default=None, help="costco-mcp account name")
    warehouses.add_argument("--now", default=None, help="Override the clock, ISO-8601, for tests")
    warehouses.add_argument("--since", default=None, help="First day, YYYY-MM-DD. Default is 180 days ago")

    run = sub.add_parser("run", help="Import receipts since the last success, defaulting to 90 days")
    _add_io(run)

    backfill = sub.add_parser("backfill", help="Import warehouse receipts for an explicit date range")
    _add_io(backfill)
    backfill.add_argument("--start", required=True, help="First day, YYYY-MM-DD")
    backfill.add_argument("--end", required=True, help="Last day, YYYY-MM-DD")

    history = sub.add_parser(
        "history",
        help="Import full preferred-warehouse history, or only the gap when history already exists",
    )
    _add_io(history)

    args = parser.parse_args(argv)
    try:
        if args.command == "auth":
            return _auth(args)
        if args.command == "check":
            return _check(args.snapshot)
        if args.command == "apply":
            return _apply(args)
        if args.command == "warehouses":
            return _warehouses(args)
        if args.command == "run":
            return _run(args)
        if args.command == "history":
            return _history(args)
        return _backfill(args)
    except LocationRequired as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except LeaseError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except MembershipError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except RangeRejected as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        tighten_private_files()


def _add_io(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--snapshot", required=True, type=Path, help="Household snapshot JSON")
    parser.add_argument("--out", required=True, type=Path, help="Mutation file to write")
    parser.add_argument("--state", type=Path, default=_DEFAULT_STATE, help="Local SQLite path")
    parser.add_argument("--account", default=None, help="costco-mcp account name")
    parser.add_argument("--owner", default="", help="Bot identity compared with Integrations.owner")
    parser.add_argument("--now", default=None, help="Override the clock, ISO-8601, for tests")


def _auth(args: argparse.Namespace) -> int:
    from costco_sync.b2c import policy_from_storage_key, remember_policy
    from costco_sync.live import CostcoSource

    if args.refresh_token and args.refresh_token_stdin:
        print("Pass the refresh token only once.", file=sys.stderr)
        return 1
    policy = ""
    if args.storage_key:
        policy = policy_from_storage_key(args.storage_key)
        if policy:
            remember_policy(policy)
    source = CostcoSource(args.account, policy=policy or None)
    secret = args.refresh_token
    if args.refresh_token_stdin:
        secret = sys.stdin.readline().strip()
        if not secret:
            print("No refresh token on stdin.", file=sys.stderr)
            return 1
    if secret:
        if args.refresh_token:
            print(
                "Warning: the refresh token was passed on the command line. "
                "Prefer --refresh-token-stdin.",
                file=sys.stderr,
            )
        source.save_refresh_token(secret)
        print(f"Saved a Costco refresh token for account '{source.account}'.")
        return 0
    status = source.status()
    print(f"Account: {status.get('account')}")
    print(f"Refresh token present: {status.get('has_refresh_token')}")
    if not status.get("has_refresh_token"):
        print(
            "Do not scrape the refresh token from a browser. On your own computer, in Chrome, "
            "log in at https://www.costco.com, then open Account, then Orders & Purchases, and let it load. "
            "Open Developer Tools, Application, Local Storage, https://www.costco.com "
            "(also check https://signin.costco.com). Copy the secret from the key whose name contains "
            "refreshtoken. That key name also contains the sign-in policy, such as signup_signin_214. "
            "Save the secret with: costco-sync auth --account personal --refresh-token-stdin"
        )
    return 0


def _check(snapshot_path: Path) -> int:
    import json

    from costco_sync.sheetapply import validate_snapshot

    problems = validate_snapshot(json.loads(snapshot_path.read_text()))
    if not problems:
        print("Snapshot is ready for costco-sync.")
        return 0
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1


def _apply(args: argparse.Namespace) -> int:
    import json

    from costco_sync.sheetapply import apply_mutations

    snapshot, events = apply_mutations(
        json.loads(args.snapshot.read_text()),
        json.loads(args.mutations.read_text()),
    )
    args.out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    args.events_out.write_text(json.dumps(events, indent=2, sort_keys=True) + "\n")
    print(f"Next snapshot written to {args.out}")
    print(f"Events still to append: {len(events)}")
    return 0


def _warehouses(args: argparse.Namespace) -> int:
    store = StateStore(args.state)
    try:
        today = _today(args.now)
        since = date.fromisoformat(args.since) if args.since else None
        start, end = warehouse_listing_window(today, since)
        rows = list_warehouses(_source(args.account), store, start, end)
    finally:
        store.close()
    if not rows:
        print("No warehouse receipts in this window.")
        return 0
    for number, name, count in rows:
        print(format_warehouse_line(number, name, count))
    return 0


def _run(args: argparse.Namespace) -> int:
    store = StateStore(args.state)
    try:
        today = _today(args.now)
        start, end = default_window(today, store.last_success_through())
        mutations = write_import(
            args.snapshot,
            args.out,
            store,
            _source(args.account),
            start=start,
            end=end,
            now=_now(args.now),
            owner=args.owner,
        )
    finally:
        store.close()
    _print_result(mutations, args.out)
    return 0


def _backfill(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        print("--start must be on or before --end", file=sys.stderr)
        return 1
    store = StateStore(args.state)
    try:
        mutations = write_import(
            args.snapshot,
            args.out,
            store,
            _source(args.account),
            start=start,
            end=end,
            now=_now(args.now),
            owner=args.owner,
        )
    finally:
        store.close()
    _print_result(mutations, args.out)
    return 0


def _history(args: argparse.Namespace) -> int:
    store = StateStore(args.state)
    try:
        mutations = write_history(
            args.snapshot,
            args.out,
            store,
            _source(args.account),
            today=_today(args.now),
            now=_now(args.now),
            owner=args.owner,
        )
    finally:
        store.close()
    _print_result(mutations, args.out)
    return 0


def _print_result(mutations: dict, out: Path) -> None:
    text = (mutations.get("summary") or {}).get("text")
    if text:
        print(text)
    print(f"Wrote {out}")


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
