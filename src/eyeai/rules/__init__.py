"""Deterministic rule engine. Rules turn ingested events into derived RULE events.

Nothing in this package actuates anything: rules only return `Event` objects.
"""
from __future__ import annotations

from eyeai.rules.base import Ctx, Rule, derive, derived_id
from eyeai.rules.cold import ColdChain
from eyeai.rules.config import RulesConfig, load_config, parse_config
from eyeai.rules.dedup import Deduplicator
from eyeai.rules.dock import DockReconciliation
from eyeai.rules.health import DeviceHealth
from eyeai.rules.incidents import Escalation, IncidentCorrelator
from eyeai.rules.machine import MachineAnomaly
from eyeai.rules.traffic import LineCountAggregator
from eyeai.rules.zones import AfterHoursAccess, EnergyWaste, OccupancyTracker, QueueMonitor, ZoneDwell

__all__ = [
    "AfterHoursAccess",
    "ColdChain",
    "Ctx",
    "Deduplicator",
    "DeviceHealth",
    "DockReconciliation",
    "EnergyWaste",
    "Escalation",
    "IncidentCorrelator",
    "LineCountAggregator",
    "MachineAnomaly",
    "OccupancyTracker",
    "QueueMonitor",
    "Rule",
    "RulesConfig",
    "ZoneDwell",
    "build_rules",
    "derive",
    "derived_id",
    "load_config",
    "parse_config",
]


def build_rules(ctx: Ctx) -> list[Rule]:
    """Instantiate every enabled rule. Order is the dispatch order."""
    cfg = ctx.cfg
    candidates: list[Rule] = [
        ZoneDwell(ctx, cfg.zone_dwell),
        OccupancyTracker(ctx, cfg.occupancy),
        AfterHoursAccess(ctx, cfg.after_hours),
        EnergyWaste(ctx, cfg.energy_waste),
        QueueMonitor(ctx, cfg.queue_monitor),
        LineCountAggregator(ctx, cfg.line_count),
        DockReconciliation(ctx, cfg.dock_reconciliation),
        ColdChain(ctx, cfg.cold_chain),
        MachineAnomaly(ctx, cfg.machine_anomaly),
        DeviceHealth(ctx, cfg.device_health),
        Escalation(ctx, cfg.escalation),
        IncidentCorrelator(ctx, cfg.incident_correlator),
    ]
    return [rule for rule in candidates if rule.enabled()]
