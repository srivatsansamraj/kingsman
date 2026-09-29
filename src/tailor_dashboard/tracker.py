"""The tracker: one row per job, beside the engine's own records.

  posting_id        the engine's posting id (data/postings/<id>.json)
  status            added, read, applied, interview, rejected, offer
  added             when the job was added (ISO date and time; a posting saved before the dashboard takes its
                    file's date)
  status_changed    when the status last changed
  notes             free text
  page_fingerprint  what the saved page was built from (library, reading, mapping, accepted edits, page mode and
                    call 3's answer); a page whose fingerprint differs is built again
  page_built        when, and page_seconds how long, its page took; page_refused the engine's reason when it
                    refused to build one
  judge             the last "Ask the judge" answer, as JSON
  title, company    the names the user gave the job, shown in place of the posting's own (a pasted posting
                    has no company); empty means the posting's
  removed           when the user removed the job from the list; the row is kept, so the saved posting is not
                    tracked again on the next start, and adding the posting again clears it
  edits             the page edits the user accepted, in order, as JSON (see `edits.py`)

A second table, `decisions`, is append-only: every judge proposal the user accepted, dismissed or undid, with the
page's measures before and after it (coverage, score and its terms, required items met), kept to learn later whether the
selector's weights or the library disagree with the user's choices.

Each call opens its own connection: the server's threads (readings, rebuilds and the judge run on several) never
share one. A tracker made before a column existed gets the column when it is opened.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

STATUSES = ("added", "read", "applied", "interview", "rejected", "offer")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    posting_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'added',
    added TEXT NOT NULL,
    status_changed TEXT,
    notes TEXT NOT NULL DEFAULT '',
    page_fingerprint TEXT,
    page_built TEXT,
    page_seconds REAL,
    page_refused TEXT,
    judge TEXT,
    title TEXT,
    company TEXT,
    removed TEXT,
    edits TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id TEXT NOT NULL,
    at TEXT NOT NULL,
    decision TEXT NOT NULL,
    edit TEXT NOT NULL,
    before TEXT,
    after TEXT
)
"""
# Columns added after the first trackers were made, with their types; `Tracker` adds any a file lacks.
_ADDED_COLUMNS = {"title": "TEXT", "company": "TEXT", "removed": "TEXT", "edits": "TEXT"}
DECISIONS = ("accepted", "dismissed", "undone")
# Columns a caller may set through `update`; the posting id and the added date are fixed once written, and
# status_changed is written by `update` alone, with each status.
_SETTABLE = frozenset(
    (
        "status",
        "notes",
        "page_fingerprint",
        "page_built",
        "page_seconds",
        "page_refused",
        "judge",
        *_ADDED_COLUMNS,
    )
)


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Tracker:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)
            present = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            for column, kind in _ADDED_COLUMNS.items():
                if column not in present:
                    # The name and type come from `_ADDED_COLUMNS` above, not from input.
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {kind}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        # One caller at a time, reads included; SQLite would otherwise answer a second writer with "database is locked".
        with self._lock:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    def rows(self) -> dict[str, dict[str, Any]]:
        with self._connection() as connection:
            return {row["posting_id"]: dict(row) for row in connection.execute("SELECT * FROM jobs")}

    def row(self, posting_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            found = connection.execute("SELECT * FROM jobs WHERE posting_id = ?", (posting_id,)).fetchone()
            return dict(found) if found else None

    def add(self, posting_id: str, *, added: str | None = None, status: str = "added") -> bool:
        """Start tracking a job; False when it is tracked already, which leaves its row as it was."""
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO jobs (posting_id, status, added) VALUES (?, ?, ?)",
                (posting_id, status, added or now()),
            )
            return cursor.rowcount == 1

    def update(self, posting_id: str, **values: Any) -> None:
        unknown = set(values) - _SETTABLE
        if unknown:
            raise ValueError(f"not a settable column: {sorted(unknown)}")
        if "status" in values:
            if values["status"] not in STATUSES:
                raise ValueError(f"unknown status {values['status']!r}")
            values.setdefault("status_changed", now())
        if not values:
            return
        # Column names come only from `_SETTABLE`, checked above, and status_changed; the values are bound.
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._connection() as connection:
            connection.execute(f"UPDATE jobs SET {assignments} WHERE posting_id = ?", (*values.values(), posting_id))

    def decide(
        self,
        posting_id: str,
        decision: str,
        edit: dict[str, Any],
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        """Record a decision on a judge proposal (accepted, dismissed or undone) with the page's measures around it."""
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision {decision!r}")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO decisions (posting_id, at, decision, edit, before, after) VALUES (?, ?, ?, ?, ?, ?)",
                (posting_id, now(), decision, json.dumps(edit), json.dumps(before), json.dumps(after)),
            )

    def decisions(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            found = connection.execute("SELECT * FROM decisions ORDER BY id").fetchall()
        return [
            {**dict(row), **{field: json.loads(row[field]) for field in ("edit", "before", "after")}} for row in found
        ]
