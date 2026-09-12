"""SQLite persistence for runs, their traces, and Watcher subscriptions.

Replaces the in-memory dict the API used to keep runs in, so a plan
survives a process restart and the Watcher has something to watch. JSON
blobs, not a normalised schema - the shapes are small and always read
whole.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nikaas.sqlite3"


def _j(obj: Any) -> str:
    if is_dataclass(obj):
        obj = asdict(obj)
    return json.dumps(obj, default=lambda o: asdict(o) if is_dataclass(o) else str(o))


class Store:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:") if self.db_path == ":memory:" else None
        self._init()

    @contextmanager
    def _conn(self):
        conn = self._mem or sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            if self._mem is None:
                conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    plans TEXT, abstentions TEXT, stopped_reason TEXT,
                    error TEXT, approved_choice TEXT
                );
                CREATE TABLE IF NOT EXISTS trace_events (
                    run_id TEXT NOT NULL, step INTEGER NOT NULL, event TEXT NOT NULL,
                    PRIMARY KEY (run_id, step)
                );
                CREATE TABLE IF NOT EXISTS watch_subs (
                    run_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL, navs TEXT NOT NULL, hashes TEXT NOT NULL,
                    status TEXT NOT NULL, last_checked TEXT, last_event TEXT
                );
                """
            )

    # -- runs --

    def create_run(self, run_id: str, trigger: str, created_at: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs (run_id, status, trigger, created_at) VALUES (?,?,?,?)",
                (run_id, "running", trigger, created_at),
            )

    def finish_run(self, run_id: str, result) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET status=?, plans=?, abstentions=?, stopped_reason=? WHERE run_id=?",
                (
                    "done",
                    _j(result.plans) if result.plans is not None else None,
                    _j(result.abstentions),
                    result.stopped_reason,
                    run_id,
                ),
            )

    def fail_run(self, run_id: str, error: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE runs SET status=?, error=? WHERE run_id=?", ("error", error, run_id))

    def approve(self, run_id: str, choice: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE runs SET approved_choice=? WHERE run_id=?", (choice, run_id))

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for key in ("plans", "abstentions"):
            d[key] = json.loads(d[key]) if d[key] else None
        return d

    # -- trace --

    def append_trace(self, run_id: str, event: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO trace_events (run_id, step, event) VALUES (?,?,?)",
                (run_id, event.get("step", 0), json.dumps(event)),
            )

    def get_trace(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT event FROM trace_events WHERE run_id=? ORDER BY step", (run_id,)
            ).fetchall()
        return [json.loads(r["event"]) for r in rows]

    # -- watches --

    def add_watch(self, run_id: str, goal, navs: dict, hashes: dict, now: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO watch_subs "
                "(run_id, goal, navs, hashes, status, last_checked, last_event) VALUES (?,?,?,?,?,?,?)",
                (run_id, _j(goal), _j(navs), _j(hashes), "watching", now, "subscribed"),
            )

    def list_watches(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM watch_subs WHERE status='watching'").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("goal", "navs", "hashes"):
                d[k] = json.loads(d[k])
            out.append(d)
        return out

    def update_watch(self, run_id: str, *, hashes: dict | None = None, navs: dict | None = None,
                     last_checked: str = "", last_event: str = "") -> None:
        with self._conn() as c:
            cur = c.execute("SELECT navs, hashes FROM watch_subs WHERE run_id=?", (run_id,)).fetchone()
            if cur is None:
                return
            c.execute(
                "UPDATE watch_subs SET navs=?, hashes=?, last_checked=?, last_event=? WHERE run_id=?",
                (
                    _j(navs) if navs is not None else cur["navs"],
                    _j(hashes) if hashes is not None else cur["hashes"],
                    last_checked, last_event, run_id,
                ),
            )
