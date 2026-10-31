"""Replays a scenario through `Ingest` and records what each step produced."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from eyeai.ingest import Ingest
from eyeai.schema import Event
from eyeai.sim.scenarios import Scenario, Step
from eyeai.store import Store


@dataclass(frozen=True)
class Record:
    """One replayed step: the input, the stat counters it moved, and the derived events it produced."""

    step: Step
    outcome: tuple[str, ...]
    derived: tuple[Event, ...]

    @property
    def at(self) -> datetime:
        return self.step.ts_utc if isinstance(self.step, Event) else self.step


@dataclass(frozen=True)
class Run:
    scenario: Scenario
    records: tuple[Record, ...]
    stats: Counter
    store: Store

    @property
    def derived(self) -> list[Event]:
        return [d for record in self.records for d in record.derived]

    def kinds(self) -> set[str]:
        return {d.kind for d in self.derived}

    def missing(self) -> list[str]:
        have = self.kinds()
        return [kind for kind in self.scenario.expect if kind not in have]


def run(scenario: Scenario) -> Run:
    """Replays `scenario` into a fresh in-memory store. Deterministic for a given scenario."""
    store = Store(":memory:")
    ingest = Ingest(store, scenario.cfg)
    records: list[Record] = []
    for step in scenario.steps:
        before = Counter(ingest.stats)
        if isinstance(step, Event):
            derived = ingest.handle(step)
        else:
            derived = ingest.tick(step)
        after = ingest.stats
        changed = tuple(sorted(k for k in set(before) | set(after) if after[k] != before[k]))
        records.append(Record(step=step, outcome=changed, derived=tuple(derived)))
    return Run(scenario=scenario, records=tuple(records), stats=Counter(ingest.stats), store=store)
