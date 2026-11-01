"""Behaviour rules: indicators for a human to review, never verdicts (docs/ARCHITECTURE.md "Behaviour rules").

Flags come from two places and are merged into `events.cls_flags` in one transaction with a row lock, so either
order works:
  * the classifier (kind='classifier', one yes/no question to the System One model), and
  * the snapshot burst (kind='indication', derived from the track, no model call).
A trigger marks the event suspicious, raises (never lowers) event and incident severity, adds a timeline entry and
may open an incident. Nothing here changes `events.status` or dismisses anything.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol, Sequence

log = logging.getLogger("eyeai.behavior")

SEV_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def higher(a: str, b: str) -> str:
    return a if SEV_RANK[a] >= SEV_RANK[b] else b


@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    kind: str                       # classifier | indication
    question: str | None
    applies_to: tuple[str, ...]
    threshold: float
    config: dict[str, Any]
    severity: str
    opens_incident: bool
    position: int = 0


@dataclass
class Flag:
    rule: str
    title: str
    prob: float | None              # P(yes); None for indication rules and for requests that failed
    threshold: float
    severity: str
    triggered: bool
    # not stored in cls_flags:
    opens_incident: bool = True
    detail: str | None = None       # timeline detail for a trigger
    error: str | None = None

    def to_json(self) -> dict:
        return {"rule": self.rule, "title": self.title, "prob": self.prob, "threshold": self.threshold,
                "severity": self.severity, "triggered": self.triggered}


@dataclass
class EventState:
    id: str
    incident_id: str | None
    incident_severity: str | None
    label: str
    severity: str
    source_id: str
    source_name: str
    flags: list[dict]
    suspicious: bool


class RuleTx(Protocol):
    def lock_event(self, event_id: str) -> EventState | None: ...
    def set_event_flags(self, event_id: str, flags: list[dict], suspicious: bool, severity: str) -> None: ...
    def set_incident_severity(self, incident_id: str, severity: str) -> None: ...
    def link_event_incident(self, event_id: str, incident_id: str) -> None: ...
    def create_incident(self, *, title: str, severity: str, source_id: str, label: str, opened_at: datetime) -> tuple[str, str]: ...
    def add_timeline(self, *, incident_id: str, kind: str, title: str, detail: str | None, event_id: str | None, at: datetime) -> None: ...


class RuleCache:
    """Enabled rules, refreshed at most every `ttl_s`; keeps serving the last good list if the DB hiccups."""

    def __init__(self, repo: Any, ttl_s: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self._repo, self._ttl, self._clock = repo, ttl_s, clock
        self._rules: list[Rule] = []
        self._at: float | None = None

    def get(self) -> list[Rule]:
        now = self._clock()
        if self._at is None or now - self._at >= self._ttl:
            try:
                self._rules = list(self._repo.list_behavior_rules())
            except Exception:  # noqa: BLE001
                log.exception("loading behaviour rules failed; using the previous list")
            self._at = now
        return self._rules


def indication_flags(rules: Sequence[Rule], label: str, kind: str, burst_ms: int, sentence: str) -> list[Flag]:
    """Evaluate kind='indication' rules for a finished burst."""
    out: list[Flag] = []
    for r in rules:
        if r.kind != "indication" or label not in r.applies_to:
            continue
        want = r.config.get("indication_kind")
        min_ms = int(r.config.get("min_burst_ms", 0))
        hit = kind == want and burst_ms >= min_ms
        out.append(Flag(
            rule=r.key, title=r.title, prob=None, threshold=r.threshold, severity=r.severity, triggered=hit,
            opens_incident=r.opens_incident,
            detail=f"{sentence} (indicator needs {min_ms / 1000:.1f} s or more) - observed in snapshots, needs human review",
        ))
    return out


def apply_flags(tx: RuleTx, event_id: str, new: Sequence[Flag], now: datetime) -> None:
    """Merge `new` flags into the event (row-locked), then apply the consequences of the triggered ones."""
    if not new:
        return
    st = tx.lock_event(event_id)
    if st is None:
        return
    keys = {f.rule for f in new}
    before_triggered = {f["rule"] for f in st.flags if f.get("triggered")}
    merged = [f for f in st.flags if f.get("rule") not in keys] + [f.to_json() for f in new]
    fresh = [f for f in new if f.triggered and f.rule not in before_triggered]   # no duplicate timeline on re-evaluation
    triggered = [f for f in new if f.triggered]
    if not triggered:
        tx.set_event_flags(event_id, merged, st.suspicious, st.severity)
        return
    top = max(triggered, key=lambda f: SEV_RANK[f.severity])
    severity = higher(st.severity, top.severity)
    incident_id = st.incident_id
    if incident_id:
        if SEV_RANK[top.severity] > SEV_RANK[st.incident_severity or "LOW"]:
            tx.set_incident_severity(incident_id, top.severity)
    else:
        opener = [f for f in triggered if f.opens_incident]
        if opener:
            o = max(opener, key=lambda f: SEV_RANK[f.severity])
            incident_id, code = tx.create_incident(
                title=f"Suspicious: {o.title} at {st.source_name}", severity=o.severity,
                source_id=st.source_id, label=st.label, opened_at=now,
            )
            tx.link_event_incident(event_id, incident_id)
            log.info("event %s: indicator '%s' opened %s", event_id[:8], o.title, code)
    tx.set_event_flags(event_id, merged, True, severity)
    if incident_id:
        for f in fresh:
            tx.add_timeline(incident_id=incident_id, kind="system", title=f"Behaviour indicator: {f.title}",
                            detail=f.detail, event_id=event_id, at=now)
