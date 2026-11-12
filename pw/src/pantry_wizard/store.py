"""A workbook loaded once, changed in memory, and committed once.

Two write shapes exist for household data: append a row, and set named columns
on the row with a given key. Importer and derived tabs may also be replaced
whole. Nothing else is possible through this class, which is the point.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .errors import PwError  # noqa: F401  (re-exported)
from .schema import SCHEMA_VERSION, TABS


class Table:
    def __init__(self, name: str, raw: list[list[str]]):
        self.tab = TABS[name]
        self.name = name
        self.empty_sheet = not raw
        self.header = list(raw[0]) if raw else list(self.tab.columns)
        missing = [c for c in self.tab.columns if c not in self.header]
        if missing:
            raise PwError(f"{name} is missing columns {missing}. Run `pw init` or `pw migrate`.")
        self.rows: list[dict] = []
        for n, values in enumerate(raw[1:], start=2):
            row = {h: (values[i] if i < len(values) else "") for i, h in enumerate(self.header)}
            if not any(row.values()):
                continue
            row["_row"] = n
            self.rows.append(row)
        self._index = {r[self.tab.key]: r for r in self.rows}

    def get(self, key: str) -> dict | None:
        return self._index.get(key)

    def where(self, **eq) -> list[dict]:
        return [r for r in self.rows if all(r.get(k, "") == v for k, v in eq.items())]

    def add(self, row: dict) -> None:
        self.rows.append(row)
        self._index[row[self.tab.key]] = row

    def as_values(self, row: dict) -> list[str]:
        return [str(row.get(h, "") or "") for h in self.header]


class Workbook:
    def __init__(self, backend, agent: str = "pw", now: datetime | None = None):
        self.backend = backend
        self.agent = agent
        self._now = now
        self.tables: dict[str, Table] = {}
        self._appends: dict[str, list[dict]] = {}
        self._cells: list[tuple[str, int, int, str, str]] = []
        self._replaced: set[str] = set()

    # -- reading ---------------------------------------------------------

    def load(self, *names: str) -> "Workbook":
        want = [n for n in names if n not in self.tables]
        if want:
            raw = self.backend.read(want)
            for n in want:
                self.tables[n] = Table(n, raw.get(n, []))
        return self

    def t(self, name: str) -> Table:
        if name not in self.tables:
            self.load(name)
        return self.tables[name]

    def config(self, key: str, default: str = "") -> str:
        row = self.t("Config").get(key)
        return (row or {}).get("value", "") or default

    # -- time and ids ----------------------------------------------------

    def now(self) -> datetime:
        if self._now is not None:
            return self._now
        zone = self.config("timezone") if "Config" in self.tables else ""
        return datetime.now(ZoneInfo(zone) if zone else None).astimezone()

    def stamp(self) -> str:
        return self.now().replace(microsecond=0).isoformat()

    def today(self) -> str:
        return self.now().date().isoformat()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:10]

    # -- writing ---------------------------------------------------------

    def append(self, name: str, row: dict) -> dict:
        table = self.t(name)
        unknown = [k for k in row if k not in table.header]
        if unknown:
            raise PwError(f"{name} has no column {unknown}")
        key = row.get(table.tab.key, "")
        if not key:
            raise PwError(f"{name} row needs a {table.tab.key}")
        if table.get(key) is not None:
            raise PwError(f"{name} already has {key}")
        row = {h: str(row.get(h, "") or "") for h in table.header}
        row["_row"] = None
        table.add(row)
        self._appends.setdefault(name, []).append(row)
        return row

    def set(self, name: str, key: str, fields: dict) -> dict:
        table = self.t(name)
        row = table.get(key)
        if row is None:
            raise PwError(f"{name} has no row {key}")
        for col, value in fields.items():
            if col not in table.header:
                raise PwError(f"{name} has no column {col}")
            if col == table.tab.key:
                raise PwError("a key cannot be changed")
            value = "" if value is None else str(value)
            if row.get(col, "") == value:
                continue
            row[col] = value
            if row["_row"] is not None and name not in self._replaced:
                self._cells.append((name, row["_row"], table.header.index(col), value, key))
        return row

    def replace(self, name: str, rows: list[dict]) -> None:
        """Rewrite a whole tab. Only importer and derived tabs allow it."""
        table = self.t(name)
        if table.tab.owner == "declared":
            raise PwError(f"{name} holds people's statements and is never rewritten whole")
        table.header = list(table.tab.columns)
        table.rows = []
        table._index = {}
        for r in rows:
            row = {h: str(r.get(h, "") or "") for h in table.header}
            row["_row"] = None
            table.add(row)
        self._replaced.add(name)
        self._appends.pop(name, None)
        self._cells = [c for c in self._cells if c[0] != name]

    def dirty(self) -> bool:
        return bool(self._appends or self._cells or self._replaced)

    def commit(self) -> None:
        if not self.dirty():
            return
        if "Config" not in self._replaced and "Config" not in self._appends:
            have = self.config("schema_version")
            if have != SCHEMA_VERSION:
                raise PwError(
                    f"This sheet is schema {have or 'unknown'} and this pw writes {SCHEMA_VERSION}. "
                    "Nothing was written. Update pw or migrate the sheet."
                )
        appends = {}
        for name, rows in self._appends.items():
            table = self.tables[name]
            values = [table.as_values(r) for r in rows]
            if table.empty_sheet:
                values.insert(0, list(table.header))
                table.empty_sheet = False
            appends[name] = values
        replaces = {}
        for name in self._replaced:
            table = self.tables[name]
            replaces[name] = [list(table.header)] + [table.as_values(r) for r in table.rows]
        self.backend.commit(appends, list(self._cells), replaces)
        self._appends, self._cells, self._replaced = {}, [], set()
        # Row numbers of what was just appended are unknown, so anything read
        # after a commit is read fresh.
        self.tables = {}
