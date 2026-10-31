"""Incident handling: escalation of unacknowledged alerts and correlation of related alerts.

Escalation
- Opens on every RULE-provenance alert with severity warning or critical.
- An `ack` event (provenance EXTERNAL, `attributes["ack_of"]` = alert event id) closes it.
- An alert still open after `escalate_after_s` emits one `escalation` (critical).

Correlator
- Groups warning/critical alerts by zone (or "site" when no zone) within `window_s`
  of the last alert in the group.
- Emits one `incident` (critical) per group once it holds `min_members` distinct
  (kind, device) pairs, so one repeating detector does not count as an incident.

Neither rule actuates anything. They only produce events for people to act on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from eyeai.rules.base import Rule
from eyeai.rules.config import CorrelatorCfg, EscalationCfg
from eyeai.schema import Event, Provenance

_SELF_KINDS = frozenset({"escalation", "incident", "rule_error"})


class Escalation(Rule):
    id = "escalation"

    def __init__(self, ctx, cfg: EscalationCfg) -> None:
        super().__init__(ctx, cfg)
        self._open: dict[str, Event] = {}

    def wants(self, event: Event) -> bool:
        if event.kind == "ack":
            return event.provenance is Provenance.EXTERNAL
        return (
            event.provenance is Provenance.RULE
            and event.severity in ("warning", "critical")
            and event.kind not in _SELF_KINDS
        )

    def on_event(self, event: Event) -> list[Event]:
        if event.kind == "ack":
            self._open.pop(str(event.attributes.get("ack_of") or ""), None)
        else:
            self._open.setdefault(event.id, event)
        return []

    def on_tick(self, now: datetime) -> list[Event]:
        out: list[Event] = []
        for alert_id, alert in list(self._open.items()):
            if (now - alert.ts_utc).total_seconds() < self.cfg.escalate_after_s:
                continue
            del self._open[alert_id]
            out.append(
                self.emit(
                    "escalation",
                    now,
                    device_id=alert.device_id,
                    label=alert.kind,
                    zone=alert.zone,
                    correlation_id=alert.correlation_id or alert.id,
                    severity="critical",
                    attributes={"ack_of": alert_id, "alert_kind": alert.kind, "alert_ts": alert.ts_utc.isoformat()},
                    key=(alert_id,),
                )
            )
        return out


@dataclass
class _Group:
    last: datetime
    members: list[tuple[str, str, str, str]] = field(default_factory=list)  # (ts, id, kind, device)
    emitted: bool = False


class IncidentCorrelator(Rule):
    id = "incident_correlator"

    def __init__(self, ctx, cfg: CorrelatorCfg) -> None:
        super().__init__(ctx, cfg)
        self._groups: dict[str, _Group] = {}

    def wants(self, event: Event) -> bool:
        return (
            event.severity in ("warning", "critical")
            and event.kind not in _SELF_KINDS
            and event.provenance in (Provenance.RULE, Provenance.NATIVE, Provenance.SENSOR)
        )

    def on_event(self, event: Event) -> list[Event]:
        key = event.zone or "site"
        ts = event.ts_utc
        group = self._groups.get(key)
        if group is None or (ts - group.last).total_seconds() > self.cfg.window_s:
            group = _Group(last=ts)
            self._groups[key] = group
        group.last = max(group.last, ts)
        group.members.append((ts.isoformat(), event.id, event.kind, event.device_id))
        pairs = {(kind, device) for _, _, kind, device in group.members}
        if group.emitted or len(pairs) < self.cfg.min_members:
            return []
        group.emitted = True
        first_id = group.members[0][1]
        return [
            self.emit(
                "incident",
                ts,
                zone=None if key == "site" else key,
                label=key,
                correlation_id=first_id,
                severity="critical",
                attributes={
                    "members": [m[1] for m in group.members],
                    "kinds": sorted({m[2] for m in group.members}),
                    "devices": sorted({m[3] for m in group.members}),
                },
                key=(key, first_id),
            )
        ]

    def on_tick(self, now: datetime) -> list[Event]:
        for key in list(self._groups):
            if (now - self._groups[key].last).total_seconds() > self.cfg.window_s:
                del self._groups[key]
        return []
