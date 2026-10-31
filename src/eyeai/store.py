"""SQLite event store + audit log. Thread-safe (one connection, one lock).

Agents are read-only consumers: they only use `query_events`, `get_event`,
`count_events`. Writes happen through the ingest path.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from .schema import Event, Provenance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  site_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  label TEXT,
  confidence REAL NOT NULL,
  provenance TEXT NOT NULL,
  correlation_id TEXT,
  track_id INTEGER,
  zone TEXT,
  severity TEXT NOT NULL,
  clip_ref TEXT,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_site_ts ON events(site_id, ts_utc);
CREATE INDEX IF NOT EXISTS ix_events_corr ON events(correlation_id);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baseline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  ts_utc TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  note TEXT
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)

    # ---- events -------------------------------------------------------
    def insert_event(self, e: Event) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.id, _iso(e.ts_utc), e.site_id, e.device_id, e.kind, e.label,
                 e.confidence, e.provenance.value, e.correlation_id, e.track_id,
                 e.zone, e.severity, e.clip_ref, e.model_dump_json()),
            )
            self._db.commit()

    def get_event(self, event_id: str) -> Event | None:
        with self._lock:
            r = self._db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()
        return Event.model_validate_json(r["body"]) if r else None

    def query_events(
        self,
        site_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        kinds: Iterable[str] | None = None,
        provenance: Provenance | None = None,
        correlation_id: str | None = None,
        severity: str | None = None,
        limit: int = 500,
        newest_first: bool = False,
    ) -> list[Event]:
        where, args = self._where(site_id, start, end, kinds, provenance, correlation_id, severity)
        order = "DESC" if newest_first else "ASC"
        sql = f"SELECT body FROM events {where} ORDER BY ts_utc {order} LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, (*args, limit)).fetchall()
        return [Event.model_validate_json(r["body"]) for r in rows]

    def count_events(self, **kw: Any) -> int:
        where, args = self._where(
            kw.get("site_id"), kw.get("start"), kw.get("end"), kw.get("kinds"),
            kw.get("provenance"), kw.get("correlation_id"), kw.get("severity"),
        )
        with self._lock:
            return self._db.execute(f"SELECT COUNT(*) FROM events {where}", args).fetchone()[0]

    @staticmethod
    def _where(site_id, start, end, kinds, provenance, correlation_id, severity):
        c: list[str] = []
        a: list[Any] = []
        if site_id:
            c.append("site_id=?"); a.append(site_id)
        if start:
            c.append("ts_utc>=?"); a.append(_iso(start))
        if end:
            c.append("ts_utc<?"); a.append(_iso(end))
        if kinds:
            ks = list(kinds)
            c.append(f"kind IN ({','.join('?' * len(ks))})"); a.extend(ks)
        if provenance:
            c.append("provenance=?"); a.append(provenance.value)
        if correlation_id:
            c.append("correlation_id=?"); a.append(correlation_id)
        if severity:
            c.append("severity=?"); a.append(severity)
        return ("WHERE " + " AND ".join(c) if c else ""), tuple(a)

    # ---- audit --------------------------------------------------------
    def audit(self, actor: str, action: str, detail: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO audit(ts_utc, actor, action, detail) VALUES (?,?,?,?)",
                (_iso(datetime.now(timezone.utc)), actor, action, json.dumps(detail, default=str)),
            )
            self._db.commit()

    def audit_log(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts_utc, actor, action, detail FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    # ---- baseline (PDF sections 5/10: need real customer baseline) ----
    def add_baseline(self, site_id: str, metric: str, value: float, note: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO baseline(site_id, ts_utc, metric, value, note) VALUES (?,?,?,?,?)",
                (site_id, _iso(datetime.now(timezone.utc)), metric, value, note),
            )
            self._db.commit()

    def baselines(self, site_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts_utc, metric, value, note FROM baseline WHERE site_id=? ORDER BY id", (site_id,)
            ).fetchall()
        return [dict(r) for r in rows]
