import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eyeai.ingest import Ingest
from eyeai.rules.config import load_config
from eyeai.schema import Event, Provenance
from eyeai.sim import SCENARIOS, get_scenario, run
from eyeai.store import Store

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "config" / "rules.example.yaml"
RULES_DIR = REPO / "src" / "eyeai" / "rules"
T0 = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)  # Mon 10:00 Jakarta


def _event(ts, device="sensor-cold-01", kind="temperature", prov=Provenance.SENSOR, **kw):
    attributes = kw.pop("attributes", {"value": 4.0})
    return Event(
        id=kw.pop("id", f"e-{ts.isoformat()}-{kind}"),
        ts_utc=ts,
        site_id="site-1",
        device_id=device,
        kind=kind,
        provenance=prov,
        confidence=1.0,
        attributes=attributes,
        **kw,
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_expected_kinds_are_derived(name):
    result = run(get_scenario(name))
    assert result.missing() == [], f"{name} missing {result.missing()}"


def test_replay_is_deterministic_across_runs():
    a = run(get_scenario("cold_chain_breach"))
    b = run(get_scenario("cold_chain_breach"))
    assert [e.id for e in a.derived] == [e.id for e in b.derived]
    assert [e.kind for e in a.derived] == [e.kind for e in b.derived]


def test_implausible_temperature_is_rejected():
    ing = Ingest(Store(), load_config(EXAMPLE))
    ing.handle(_event(T0, attributes={"value": 9999.0}))
    assert ing.stats["rejected"] == 1
    assert ing.stats["accepted"] == 0


def test_non_numeric_sensor_value_is_rejected():
    ing = Ingest(Store(), load_config(EXAMPLE))
    ing.handle(_event(T0, attributes={"value": "hot"}))
    assert ing.stats["rejected"] == 1


def test_late_event_beyond_tolerance_is_rejected():
    ing = Ingest(Store(), load_config(EXAMPLE))
    ing.tick(T0)
    ing.handle(_event(T0 - timedelta(seconds=600)))
    assert ing.stats["rejected"] == 1


def test_out_of_order_within_tolerance_is_accepted():
    ing = Ingest(Store(), load_config(EXAMPLE))
    ing.tick(T0)
    ing.handle(_event(T0 - timedelta(seconds=30)))
    assert ing.stats["accepted"] == 1
    assert ing.stats["rejected"] == 0


def test_tick_rejects_naive_datetime():
    ing = Ingest(Store(), load_config(EXAMPLE))
    with pytest.raises(ValueError):
        ing.tick(datetime(2026, 1, 5, 3, 0))


def test_crashing_rule_is_isolated_and_reported(monkeypatch):
    ing = Ingest(Store(), load_config(EXAMPLE))
    crashing = next(r for r in ing.rules if r.id == "cold_chain")

    def boom(event):
        raise RuntimeError("injected")

    monkeypatch.setattr(crashing, "on_event", boom)
    out = ing.handle(_event(T0))
    assert ing.stats["errors"] >= 1
    assert any(e.kind == "rule_error" for e in out)
    # The input event was still accepted and stored despite the rule crash.
    assert ing.stats["accepted"] == 1


def test_rules_do_not_import_adapters():
    for path in RULES_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "adapters" not in node.module, f"{path.name} imports {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "adapters" not in alias.name, f"{path.name} imports {alias.name}"


def test_derived_events_are_rule_provenance_with_aware_timestamps():
    for name in ("cold_chain_breach", "traffic_counting", "camera_outage"):
        for e in run(get_scenario(name)).derived:
            assert isinstance(e, Event)
            assert e.provenance is Provenance.RULE
            assert e.ts_utc.tzinfo is not None
