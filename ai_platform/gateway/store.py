"""
SQLite log for the gateway and the agent.

  calls       one row per model call attempt (including failed attempts and
              the fallback call that followed them)
  tool_calls  one row per agent tool invocation

Both tables carry an `interaction_id` so cost per interaction is one GROUP BY.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    interaction_id TEXT,
    task_type TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    served_model TEXT NOT NULL,
    provider TEXT NOT NULL,
    is_fallback INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,            -- ok | error | budget
    error TEXT,
    stop_reason TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS calls_day ON calls(day, provider);
CREATE INDEX IF NOT EXISTS calls_interaction ON calls(interaction_id);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    tool TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    ok INTEGER NOT NULL,
    error TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tool_calls_interaction ON tool_calls(interaction_id);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            finally:
                cur.close()

    def log_call(self, **row: Any) -> int:
        now = utcnow()
        row.setdefault("ts", now.isoformat(timespec="milliseconds"))
        row.setdefault("day", now.strftime("%Y-%m-%d"))
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(f"INSERT INTO calls ({cols}) VALUES ({marks})", tuple(row.values()))
            return int(cur.lastrowid)

    def log_tool_call(self, **row: Any) -> int:
        row.setdefault("ts", utcnow().isoformat(timespec="milliseconds"))
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(f"INSERT INTO tool_calls ({cols}) VALUES ({marks})", tuple(row.values()))
            return int(cur.lastrowid)

    def spend_today(self, provider: str | None = None, day: str | None = None) -> float:
        day = day or utcnow().strftime("%Y-%m-%d")
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE day = ?"
        args: list[Any] = [day]
        if provider:
            sql += " AND provider = ?"
            args.append(provider)
        with self._cursor() as cur:
            return float(cur.execute(sql, args).fetchone()[0])

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._cursor() as cur:
            return [dict(r) for r in cur.execute(sql, args).fetchall()]

    def calls_for(self, interaction_id: str) -> list[dict]:
        return self.query("SELECT * FROM calls WHERE interaction_id = ? ORDER BY id", (interaction_id,))

    def tool_calls_for(self, interaction_id: str) -> list[dict]:
        return self.query("SELECT * FROM tool_calls WHERE interaction_id = ? ORDER BY id", (interaction_id,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
