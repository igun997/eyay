"""Database access behind a small interface so the decision logic can be tested with a fake."""
from __future__ import annotations

import contextlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, ContextManager, Iterator, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .behavior import EventState, Flag, Rule, apply_flags
from .classifier import Classification, ClassifyJob
from .models import Category, SourceInfo

log = logging.getLogger("eyeai.repo")


class Tx(Protocol):
    def find_open_incident(self, source_id: str, label: str, since: datetime) -> str | None: ...

    def create_incident(
        self, *, title: str, severity: str, source_id: str, label: str, opened_at: datetime
    ) -> tuple[str, str]: ...

    def insert_event(
        self,
        *,
        source_id: str,
        incident_id: str | None,
        label: str,
        confidence: float,
        severity: str,
        track_id: int,
        bbox: dict[str, float],
        title: str,
        detected_at: datetime,
    ) -> str: ...

    def insert_snapshot(
        self, event_id: str, jpeg: bytes, width: int, height: int, *, bbox: dict[str, float] | None = None,
        offset_ms: int = 0, idx: int = 0,
    ) -> None: ...

    def finalize_burst(self, event_id: str, *, burst_ms: int, kind: str, indication: str) -> None: ...

    # behaviour rules (see behavior.apply_flags)
    def lock_event(self, event_id: str) -> EventState | None: ...

    def set_event_flags(self, event_id: str, flags: list[dict], suspicious: bool, severity: str) -> None: ...

    def set_incident_severity(self, incident_id: str, severity: str) -> None: ...

    def link_event_incident(self, event_id: str, incident_id: str) -> None: ...

    def add_timeline(
        self, *, incident_id: str, kind: str, title: str, detail: str | None, event_id: str | None, at: datetime
    ) -> None: ...


class Repository(Protocol):
    def transaction(self) -> ContextManager[Tx]: ...

    def list_sources(self) -> list[SourceInfo]: ...

    def list_categories(self) -> dict[str, Category]: ...

    def list_behavior_rules(self) -> list[Rule]: ...

    def update_source_state(
        self,
        source_id: str,
        *,
        status: str,
        last_error: str | None,
        last_seen: datetime | None,
        fps: float | None,
        inference_ms: float | None,
    ) -> None: ...

    def heartbeat(self, worker_id: str, sources: int, model: str | None, classifier: str | None = None) -> None: ...


class _PgTx:
    def __init__(self, conn: psycopg.Connection):
        self._c = conn

    def find_open_incident(self, source_id, label, since):
        row = self._c.execute(
            "SELECT id::text AS id FROM incidents WHERE source_id=%s AND label=%s AND status='open' "
            "AND opened_at >= %s ORDER BY opened_at DESC LIMIT 1",
            (source_id, label, since),
        ).fetchone()
        return row["id"] if row else None

    def create_incident(self, *, title, severity, source_id, label, opened_at):
        row = self._c.execute(
            "INSERT INTO incidents (title, severity, source_id, label, opened_at) VALUES (%s,%s,%s,%s,%s) "
            "RETURNING id::text AS id, code",
            (title, severity, source_id, label, opened_at),
        ).fetchone()
        return row["id"], row["code"]

    def insert_event(self, *, source_id, incident_id, label, confidence, severity, track_id, bbox, title, detected_at):
        row = self._c.execute(
            "INSERT INTO events (source_id, incident_id, label, confidence, severity, track_id, bbox, title, detected_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text AS id",
            (source_id, incident_id, label, confidence, severity, track_id, Jsonb(bbox), title, detected_at),
        ).fetchone()
        return row["id"]

    def insert_snapshot(self, event_id, jpeg, width, height, *, bbox=None, offset_ms=0, idx=0):
        self._c.execute(
            "INSERT INTO event_snapshots (event_id, idx, jpeg, width, height, bbox, offset_ms) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (event_id, idx, jpeg, width, height, Jsonb(bbox) if bbox is not None else None, offset_ms),
        )

    def finalize_burst(self, event_id, *, burst_ms, kind, indication):
        self._c.execute(
            "UPDATE events SET burst_ms=%s, indication_kind=%s, indication=%s, burst_done=true WHERE id=%s::uuid",
            (burst_ms, kind, indication, event_id),
        )

    def lock_event(self, event_id):
        r = self._c.execute(
            "SELECT e.id::text AS id, e.incident_id::text AS incident_id, i.severity AS incident_severity, e.label, "
            "e.severity, e.source_id::text AS source_id, s.name AS source_name, e.cls_flags, e.suspicious "
            "FROM events e JOIN sources s ON s.id=e.source_id LEFT JOIN incidents i ON i.id=e.incident_id "
            "WHERE e.id=%s::uuid FOR UPDATE OF e",
            (event_id,),
        ).fetchone()
        if r is None:
            return None
        return EventState(
            id=r["id"], incident_id=r["incident_id"], incident_severity=r["incident_severity"], label=r["label"],
            severity=r["severity"], source_id=r["source_id"], source_name=r["source_name"],
            flags=list(r["cls_flags"] or []), suspicious=r["suspicious"],
        )

    def set_event_flags(self, event_id, flags, suspicious, severity):
        self._c.execute(
            "UPDATE events SET cls_flags=%s, suspicious=%s, severity=%s WHERE id=%s::uuid",
            (Jsonb(flags), suspicious, severity, event_id),
        )

    def set_incident_severity(self, incident_id, severity):
        self._c.execute("UPDATE incidents SET severity=%s WHERE id=%s::uuid", (severity, incident_id))

    def link_event_incident(self, event_id, incident_id):
        self._c.execute("UPDATE events SET incident_id=%s::uuid WHERE id=%s::uuid", (incident_id, event_id))

    def add_timeline(self, *, incident_id, kind, title, detail, event_id, at):
        self._c.execute(
            "INSERT INTO incident_timeline (incident_id, kind, title, detail, event_id, at) VALUES (%s,%s,%s,%s,%s,%s)",
            (incident_id, kind, title, detail, event_id, at),
        )


class PgRepository:
    def __init__(self, dsn: str, max_size: int = 8):
        self._dsn = dsn
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row}, open=False
        )

    def wait_ready(self, stop: Any = None, interval: float = 2.0) -> None:
        """Block until Postgres answers and the schema (applied by the web app) exists, then open the pool."""
        while not (stop and stop.is_set()):
            try:
                with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5) as c:
                    # schema = migrations 001-004 (sources, events.pipeline, events.burst_done, events.cls_flags),
                    # all applied by the web app on boot
                    ok = c.execute(
                        "SELECT to_regclass('public.sources') IS NOT NULL AND to_regclass('public.behavior_rules') IS NOT NULL AND "
                        "(SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='events' "
                        "AND column_name IN ('pipeline','burst_done','cls_flags')) = 3 AS ok"
                    ).fetchone()["ok"]
                if ok:
                    self._pool.open(wait=True, timeout=30)
                    return
                log.info("database up, waiting for schema migration")
            except Exception as e:  # noqa: BLE001 - boot retry loop
                log.info("waiting for database: %s", e)
            time.sleep(interval)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._pool.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[Tx]:
        with self._pool.connection() as conn:  # commits on clean exit, rolls back on exception
            yield _PgTx(conn)

    def list_sources(self) -> list[SourceInfo]:
        with self._pool.connection() as c:
            rows = c.execute(
                "SELECT id::text AS id, code, name, url, kind, labels, min_confidence, enabled FROM sources ORDER BY code"
            ).fetchall()
        return [
            SourceInfo(
                id=r["id"], code=r["code"], name=r["name"], url=r["url"], kind=r["kind"],
                labels=tuple(r["labels"] or ()), min_confidence=float(r["min_confidence"]), enabled=r["enabled"],
            )
            for r in rows
        ]

    def list_categories(self) -> dict[str, Category]:
        with self._pool.connection() as c:
            rows = c.execute(
                "SELECT label, title, severity, opens_incident, cooldown_s, enabled, burst_s, burst_frames FROM categories"
            ).fetchall()
        return {r["label"]: Category(**r) for r in rows}

    def list_behavior_rules(self):
        with self._pool.connection() as c:
            rows = c.execute(
                "SELECT key, title, kind, question, applies_to, threshold, config, severity, opens_incident, position "
                "FROM behavior_rules WHERE enabled ORDER BY position, key"
            ).fetchall()
        return [
            Rule(key=r["key"], title=r["title"], kind=r["kind"], question=r["question"], applies_to=tuple(r["applies_to"] or ()),
                 threshold=float(r["threshold"]), config=dict(r["config"] or {}), severity=r["severity"],
                 opens_incident=r["opens_incident"], position=r["position"])
            for r in rows
        ]

    def update_source_state(self, source_id, *, status, last_error, last_seen, fps, inference_ms):
        with self._pool.connection() as c:
            c.execute(
                "UPDATE sources SET status=%s, last_error=%s, last_seen=COALESCE(%s, last_seen), fps=%s, "
                "inference_ms=%s, updated_at=now() WHERE id=%s",
                (status, last_error, last_seen, fps, inference_ms, source_id),
            )

    def heartbeat(self, worker_id, sources, model, classifier=None):
        with self._pool.connection() as c:
            c.execute(
                "INSERT INTO worker_heartbeats (worker_id, last_seen, sources, model, classifier) VALUES (%s, now(), %s, %s, %s) "
                "ON CONFLICT (worker_id) DO UPDATE SET last_seen=now(), sources=EXCLUDED.sources, model=EXCLUDED.model, "
                "classifier=EXCLUDED.classifier",
                (worker_id, sources, model, classifier),
            )

    # ---- classifier (see classifier.py). cls_at is the claim time while 'classifying', then the finish time. ----
    def claim_queued_event(self, exclude=(), frames=1):
        with self._pool.connection() as c:  # one transaction: lock, flip to 'classifying', read snapshots + queue depth
            row = c.execute(
                "SELECT e.id::text AS id, e.bbox, e.label FROM events e "
                "WHERE e.pipeline='queued' AND NOT (e.id::text = ANY(%s)) "
                "ORDER BY e.detected_at FOR UPDATE OF e SKIP LOCKED LIMIT 1",
                (list(exclude),),
            ).fetchone()
            if row is None:
                return None
            c.execute("UPDATE events SET pipeline='classifying', cls_at=now() WHERE id=%s::uuid", (row["id"],))
            snaps = c.execute(
                "SELECT jpeg, bbox FROM event_snapshots WHERE event_id=%s::uuid ORDER BY idx LIMIT %s",
                (row["id"], max(1, frames)),
            ).fetchall()
            depth = c.execute("SELECT count(*)::int AS n FROM events WHERE pipeline='queued'").fetchone()["n"]
        # idx 0 predates per-snapshot boxes on old rows: fall back to the event's own box
        shots = tuple((bytes(r["jpeg"]), r["bbox"] or (row["bbox"] if i == 0 else None)) for i, r in enumerate(snaps))
        return ClassifyJob(
            event_id=row["id"], bbox=row["bbox"], jpeg=shots[0][0] if shots else None,
            label=row["label"], snapshots=shots, queue_depth=depth,
        )

    def store_classification(self, event_id, result: Classification, flags=(), error=None):
        """Store the class answer and merge behaviour flags in ONE transaction (row-locked, see behavior.apply_flags)."""
        with self._pool.connection() as c:
            c.execute(
                "UPDATE events SET pipeline='ready', cls_label=%s, cls_confidence=%s, cls_probs=%s, cls_model=%s, "
                "cls_ms=%s, cls_error=%s, cls_at=now() WHERE id=%s::uuid",
                (result.label, result.confidence, Jsonb(result.probs), result.model, result.ms,
                 error[:300] if error else None, event_id),
            )
            if flags:
                apply_flags(_PgTx(c), event_id, flags, datetime.now(timezone.utc))

    def store_classification_failure(self, event_id, error, *, requeue):
        with self._pool.connection() as c:
            c.execute(
                "UPDATE events SET pipeline=%s, cls_error=%s, cls_at=now() WHERE id=%s::uuid",
                ("queued" if requeue else "failed", error[:300], event_id),
            )

    def skip_all_queued(self):
        with self._pool.connection() as c:
            return c.execute("UPDATE events SET pipeline='skipped' WHERE pipeline='queued'").rowcount

    def recover_stuck_classifying(self, older_than_s):
        with self._pool.connection() as c:
            return c.execute(
                "UPDATE events SET pipeline='queued' WHERE pipeline='classifying' "
                "AND (cls_at IS NULL OR cls_at < now() - make_interval(secs => %s))",
                (older_than_s,),
            ).rowcount

    def classifier_stats(self):
        with self._pool.connection() as c:
            r = c.execute(
                "SELECT count(*) FILTER (WHERE pipeline IN ('queued','classifying'))::int AS queued, "
                "count(*) FILTER (WHERE pipeline='failed' AND cls_at > now() - interval '10 minutes')::int AS failed_recent "
                "FROM events WHERE pipeline IN ('queued','classifying','failed')"
            ).fetchone()
        return {"queued": r["queued"], "failed_recent": r["failed_recent"]}
