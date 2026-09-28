"""Local cursor for receipt payloads and non-sale price samples.

This state lives on the Grok Bot computer. It is not the shopping workbook.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                barcode TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS baseline_samples (
                retail_key TEXT PRIMARY KEY,
                samples TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS sampled_refs (
                source_ref TEXT PRIMARY KEY
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS cursor (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_success_through TEXT
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def get_receipt(self, barcode: str) -> dict | None:
        row = self._db.execute("SELECT payload FROM receipts WHERE barcode = ?", (barcode,)).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def put_receipt(self, barcode: str, payload: dict) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO receipts (barcode, payload) VALUES (?, ?)",
            (barcode, json.dumps(payload)),
        )
        self._db.commit()

    def baseline_samples(self) -> dict[str, list[str]]:
        rows = self._db.execute("SELECT retail_key, samples FROM baseline_samples").fetchall()
        return {key: list(json.loads(samples)) for key, samples in rows}

    def save_baseline_samples(self, samples: dict[str, list[str]]) -> None:
        for key, values in samples.items():
            self._db.execute(
                "INSERT OR REPLACE INTO baseline_samples (retail_key, samples) VALUES (?, ?)",
                (key, json.dumps(values)),
            )
        self._db.commit()

    def sampled_refs(self) -> set[str]:
        rows = self._db.execute("SELECT source_ref FROM sampled_refs").fetchall()
        return {row[0] for row in rows}

    def mark_sampled(self, source_refs: set[str]) -> None:
        self._db.executemany(
            "INSERT OR IGNORE INTO sampled_refs (source_ref) VALUES (?)",
            [(ref,) for ref in sorted(source_refs)],
        )
        self._db.commit()

    def last_success_through(self) -> date | None:
        row = self._db.execute("SELECT last_success_through FROM cursor WHERE id = 1").fetchone()
        if row is None or not row[0]:
            return None
        return date.fromisoformat(row[0])

    def set_last_success_through(self, day: date) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cursor (id, last_success_through) VALUES (1, ?)",
            (day.isoformat(),),
        )
        self._db.commit()
