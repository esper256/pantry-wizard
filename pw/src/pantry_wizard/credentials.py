"""How `pw` finds the service-account key that lets it reach the sheet.

Three sources, in order:

1. ``PW_SERVICE_ACCOUNT_KEY`` — a path to the JSON key file. This is the
   original mechanism and is untouched: a bot that can store a file and set
   this variable never touches anything below.
2. A local cache file under the state directory. This is what a bootstrap
   leaves behind, so later runs are fast and need no sheet access.
3. Bootstrap from the sheet itself. A bot that cannot be handed a secret, but
   can be told the sheet id, reads its own key from an ``Accounts`` tab while
   the sheet is briefly shared by link, and caches it for next time.

The bootstrap key lives in the sheet in the open, on purpose: the account is
powerless by construction (it can reach nothing but this one sheet), so the
honest posture is to scope it to nothing, not to pretend a cell is a vault.
The key is never printed by any command, all the same.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re

from .errors import PwError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def state_dir(env: dict | None = None) -> str:
    """Where persistent local state lives, following XDG with an override.

    ``PW_STATE_DIR`` wins, for environments whose home is not writable.
    Otherwise ``$XDG_STATE_HOME/pantry-wizard``, else ``~/.local/state/...``.
    """
    env = os.environ if env is None else env
    if env.get("PW_STATE_DIR"):
        return env["PW_STATE_DIR"]
    base = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "pantry-wizard")


def cache_path(agent: str, env: dict | None = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", agent or "pw")
    return os.path.join(state_dir(env), "accounts", f"{safe}.json")


def resolve(spreadsheet_id: str, agent: str, *, key_path: str | None = None,
            env: dict | None = None, fetch=None, force: bool = False) -> dict:
    """Find the key for ``agent``. Returns how it was found, for the caller to load.

    ``{"source": "env"|"cache"|"bootstrap", "key_file": path|None,
       "info": dict|None, "cache_path": str}``. ``force`` skips the env and
    cache and re-reads the sheet, overwriting the cache (``pw bootstrap --force``).
    """
    env = os.environ if env is None else env
    path = key_path or env.get("PW_SERVICE_ACCOUNT_KEY")
    cache = cache_path(agent, env)
    if path and not force:
        return {"source": "env", "key_file": path, "info": None, "cache_path": cache}
    if os.path.exists(cache) and not force:
        with open(cache, encoding="utf-8") as f:
            return {"source": "cache", "key_file": None, "info": json.load(f), "cache_path": cache}
    info = _bootstrap(spreadsheet_id, agent, fetch)
    _write_cache(cache, info, force=force)
    return {"source": "bootstrap", "key_file": None, "info": info, "cache_path": cache}


def _bootstrap(spreadsheet_id: str, agent: str, fetch) -> dict:
    if not spreadsheet_id:
        raise PwError("No sheet id to bootstrap credentials from. Set PW_SHEET, or "
                      "PW_SERVICE_ACCOUNT_KEY to a key file.")
    fetch = fetch or _fetch_accounts_csv
    try:
        text = fetch(spreadsheet_id)
    except Exception as e:  # noqa: BLE001 - any transport failure is the same answer here
        raise PwError(
            "Could not read the sheet to bootstrap credentials. During setup, share it as "
            "'anyone with the link', then run `pw bootstrap`; or set PW_SERVICE_ACCOUNT_KEY "
            f"to a key file. ({e})") from None
    return _key_for_agent(text, agent)


def _key_for_agent(csv_text: str, agent: str) -> dict:
    rows = list(csv.reader(io.StringIO(csv_text)))
    header = [h.strip() for h in rows[0]] if rows else []
    if "account" not in header or "key" not in header:
        raise PwError("The sheet has no readable Accounts tab with `account` and `key` columns. "
                      "Add one (and turn on link sharing) to bootstrap, or set PW_SERVICE_ACCOUNT_KEY.")
    a, k = header.index("account"), header.index("key")
    for row in rows[1:]:
        if len(row) > max(a, k) and row[a].strip() == agent and row[k].strip():
            try:
                return json.loads(row[k])
            except json.JSONDecodeError:
                raise PwError(f"The key for agent {agent!r} in the Accounts tab is not valid JSON.") from None
    raise PwError(f"No service-account key for agent {agent!r} in the sheet's Accounts tab. Add a row "
                  "with its key, share the sheet by link, and run `pw bootstrap`.")


def _fetch_accounts_csv(spreadsheet_id: str) -> str:
    """Read just the Accounts tab through Google's export endpoint.

    This needs no credential for a sheet shared 'anyone with the link', which
    is the one moment a bootstrapping bot has before the sheet is relocked.
    """
    import urllib.parse
    import urllib.request

    url = ("https://docs.google.com/spreadsheets/d/"
           f"{urllib.parse.quote(spreadsheet_id)}/gviz/tq?tqx=out:csv&sheet=Accounts")
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed Google host
        return response.read().decode("utf-8")


def _write_cache(path: str, info: dict, *, force: bool) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return  # a cache appeared since we checked; never overwrite without --force
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
