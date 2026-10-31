from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from eyeai.rules.config import ColdZone, RulesConfig, Schedule, load_config, parse_config

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "rules.example.yaml"
JKT = ZoneInfo("Asia/Jakarta")
# 2026-01-05 is a Monday, 2026-01-04 a Sunday.
MON, SUN = 5, 4


def jkt(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 1, day, h, m, tzinfo=JKT)


def test_example_config_loads_with_expected_mapping():
    cfg = load_config(EXAMPLE)
    assert isinstance(cfg, RulesConfig)
    assert cfg.zones_by_device["cam-gate-01"] == "gate"
    assert cfg.zones_by_device["sensor-cold-01"] == "cold_store"
    assert cfg.dock_reconciliation.gate_zone == "gate"


def test_unknown_top_level_key_rejected():
    with pytest.raises(ValidationError):
        parse_config({"no_such_section": {}})


def test_unknown_nested_key_rejected():
    with pytest.raises(ValidationError):
        parse_config({"dedup": {"window_s": 5, "bogus": 1}})


def test_schedule_default_boundaries_in_jakarta_time():
    s = Schedule()
    assert not s.is_open(jkt(MON, 7, 59))
    assert s.is_open(jkt(MON, 8, 0))
    assert s.is_open(jkt(MON, 17, 59))
    assert not s.is_open(jkt(MON, 18, 0))  # end is exclusive
    assert not s.is_open(jkt(MON, 0, 0))  # midnight


def test_schedule_closed_on_sunday_open_on_saturday():
    s = Schedule()
    assert not s.is_open(jkt(SUN, 10))
    assert s.is_open(datetime(2026, 1, 3, 10, tzinfo=JKT))  # Saturday


def test_schedule_compares_utc_instants_in_local_time():
    s = Schedule()
    # 01:00 UTC Monday is 08:00 Jakarta (UTC+7, no DST): open.
    assert s.is_open(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc))
    # 00:59 UTC Monday is 07:59 Jakarta: closed.
    assert not s.is_open(datetime(2026, 1, 5, 0, 59, tzinfo=timezone.utc))


def test_schedule_24h_end_allowed():
    s = Schedule(hours={"mon": ["00:00-24:00"]})
    assert s.is_open(jkt(MON, 23, 59))


def test_schedule_rejects_overnight_window():
    with pytest.raises(ValueError):
        Schedule(hours={"mon": ["22:00-02:00"]})


def test_schedule_rejects_unknown_weekday():
    with pytest.raises(ValueError):
        Schedule(hours={"funday": ["08:00-18:00"]})


def test_schedule_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        Schedule().is_open(datetime(2026, 1, 5, 10, 0))


def test_cold_zone_requires_min_below_max():
    with pytest.raises(ValidationError):
        ColdZone(zone="cold_store", min_c=8, max_c=2, sustained_s=300, stale_s=900)
