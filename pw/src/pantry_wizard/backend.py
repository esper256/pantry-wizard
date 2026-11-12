"""Where the rows live.

A backend does three things: read whole tabs, and commit a batch of appends,
single-cell updates and whole-tab replacements. That is the entire surface
`pw` needs from Google Sheets, so the fake used in tests is a faithful stand-in.
"""

from __future__ import annotations

import json
import os
import time
from typing import Iterable

from .errors import PwError

Rows = list[list[str]]
Cell = tuple[str, int, int, str]  # tab, 1-based row number, 0-based column, value


class MemoryBackend:
    """In-memory workbook. Counts calls so tests can hold operations to a budget."""

    def __init__(self, tabs: dict[str, Rows] | None = None):
        self.tabs: dict[str, Rows] = {k: [list(r) for r in v] for k, v in (tabs or {}).items()}
        self.read_calls = 0
        self.write_calls = 0

    def read(self, tabs: Iterable[str]) -> dict[str, Rows]:
        self.read_calls += 1
        return {t: [list(r) for r in self.tabs.get(t, [])] for t in tabs}

    def commit(self, appends: dict[str, Rows], cells: list[Cell], replaces: dict[str, Rows]) -> None:
        for tab, rows in appends.items():
            if rows:
                self.write_calls += 1
                self.tabs.setdefault(tab, []).extend([_text(r) for r in rows])
        if cells:
            self.write_calls += 1
            for tab, row, col, value in cells:
                target = self.tabs[tab][row - 1]
                target.extend([""] * (col + 1 - len(target)))
                target[col] = str(value)
        for tab, rows in replaces.items():
            self.write_calls += 1
            self.tabs[tab] = [_text(r) for r in rows]


class FileBackend(MemoryBackend):
    """A workbook kept as one JSON file. For development, fixtures and dry runs."""

    def __init__(self, path: str):
        self.path = path
        tabs = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                tabs = json.load(f)
        super().__init__(tabs)

    def commit(self, appends, cells, replaces) -> None:
        super().commit(appends, cells, replaces)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.tabs, f, indent=1, ensure_ascii=False)


class SheetsBackend:
    """Google Sheets through the values API with a service-account key.

    One `batchGet` per read. A commit is one `values.append` per tab that gains
    rows, one `values.batchUpdate` for all cell updates, and a clear plus an
    update for each replaced tab. Values are written RAW so Sheets never
    reformats a timestamp or a SKU.

    The tests drive this class against a stand-in for the Google client, which
    checks the ranges and bodies it builds. It has not been run against Google
    itself; `pw doctor` on a copy of a sheet is the first real check.
    """

    def __init__(self, spreadsheet_id: str, key_path: str | None = None, api=None):
        self.id = spreadsheet_id
        self.api = api or self._connect(key_path)
        self.read_calls = 0
        self.write_calls = 0

    @staticmethod
    def _connect(key_path: str | None):
        from google.oauth2 import service_account  # imported here so tests need no Google packages
        from googleapiclient.discovery import build

        key_path = key_path or os.environ.get("PW_SERVICE_ACCOUNT_KEY")
        if not key_path:
            raise PwError("Set PW_SERVICE_ACCOUNT_KEY to the path of the service-account JSON key.")
        creds = service_account.Credentials.from_service_account_file(
            key_path, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()

    def _run(self, request):
        for attempt in range(5):
            try:
                return request.execute()
            except Exception as e:  # googleapiclient.errors.HttpError
                status = getattr(getattr(e, "resp", None), "status", None)
                if status in (429, 500, 502, 503) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise

    def read(self, tabs: Iterable[str]) -> dict[str, Rows]:
        tabs = list(tabs)
        self.read_calls += 1
        try:
            res = self._run(self.api.values().batchGet(spreadsheetId=self.id, ranges=[_q(t) for t in tabs]))
        except Exception as e:
            # A missing tab makes the whole batch fail. Fall back to whatever exists.
            if "Unable to parse range" not in str(e):
                raise
            meta = self._run(self.api.get(spreadsheetId=self.id, fields="sheets.properties.title"))
            have = {s["properties"]["title"] for s in meta.get("sheets", [])}
            present = [t for t in tabs if t in have]
            res = {"valueRanges": []}
            if present:
                res = self._run(self.api.values().batchGet(
                    spreadsheetId=self.id, ranges=[_q(t) for t in present]))
            got = {t: vr.get("values", []) for t, vr in zip(present, res.get("valueRanges", []))}
            return {t: got.get(t, []) for t in tabs}
        return {t: vr.get("values", []) for t, vr in zip(tabs, res.get("valueRanges", []))}

    def commit(self, appends: dict[str, Rows], cells: list[Cell], replaces: dict[str, Rows]) -> None:
        values = self.api.values()
        for tab, rows in appends.items():
            if not rows:
                continue
            self.write_calls += 1
            self._run(values.append(
                spreadsheetId=self.id, range=f"{_q(tab)}!A1", valueInputOption="RAW",
                insertDataOption="INSERT_ROWS", body={"values": [_text(r) for r in rows]},
            ))
        if cells:
            self.write_calls += 1
            data = [
                {"range": f"{_q(tab)}!{_col(col)}{row}", "values": [[str(value)]]}
                for tab, row, col, value in cells
            ]
            self._run(values.batchUpdate(
                spreadsheetId=self.id, body={"valueInputOption": "RAW", "data": data}
            ))
        for tab, rows in replaces.items():
            # New rows first, then clear whatever the old tab had below them,
            # so a reader never finds the tab empty.
            self.write_calls += 1
            self._run(values.update(
                spreadsheetId=self.id, range=f"{_q(tab)}!A1", valueInputOption="RAW",
                body={"values": [_text(r) for r in rows]},
            ))
            self._run(values.clear(spreadsheetId=self.id, range=f"{_q(tab)}!A{len(rows) + 1}:ZZ"))

    def ensure_tabs(self, names: Iterable[str]) -> None:
        meta = self._run(self.api.get(spreadsheetId=self.id, fields="sheets.properties.title"))
        have = {s["properties"]["title"] for s in meta.get("sheets", [])}
        requests = [{"addSheet": {"properties": {"title": n}}} for n in names if n not in have]
        if requests:
            self._run(self.api.batchUpdate(spreadsheetId=self.id, body={"requests": requests}))


def _q(tab: str) -> str:
    """A tab name as an A1 range. Quoted, so no name is read as a cell."""
    return "'" + tab.replace("'", "''") + "'"


def _text(row) -> list[str]:
    return ["" if v is None else str(v) for v in row]


def _col(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
