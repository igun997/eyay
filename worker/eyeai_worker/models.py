from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceInfo:
    id: str
    code: str
    name: str
    url: str
    kind: str
    labels: tuple[str, ...]
    min_confidence: float
    enabled: bool

    @property
    def restart_key(self) -> tuple:
        """Changing any of these needs a new connection / detector."""
        return (self.url, self.kind, tuple(sorted(self.labels)), round(self.min_confidence, 4))


@dataclass(frozen=True)
class Category:
    label: str
    title: str
    severity: str
    opens_incident: bool
    cooldown_s: int
    enabled: bool


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    incident_id: str | None
    incident_code: str | None
    label: str
    track_id: int
    grouped: bool  # attached to an already-open incident
