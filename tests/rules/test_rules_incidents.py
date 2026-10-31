from datetime import datetime, timedelta, timezone
from pathlib import Path

from eyeai.rules.base import Ctx, derive
from eyeai.rules.config import CorrelatorCfg, EscalationCfg, load_config
from eyeai.rules.incidents import Escalation, IncidentCorrelator
from eyeai.schema import Event, Provenance
from eyeai.store import Store

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "rules.example.yaml"
T0 = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)  # Mon 10:00 Jakarta


def _ctx():
    cfg = load_config(EXAMPLE)
    return Ctx(cfg=cfg, store=Store(), _now=T0)


def _alert(ts, kind="cold_chain_breach", device="sensor-cold-01", zone=None, n=0):
    return derive(
        "cold_chain",
        kind,
        ts,
        site_id="site-1",
        device_id=device,
        zone=zone,
        severity="critical",
        key=(n,),
    )


def _ack(ts, alert_id):
    return Event(
        id=f"ack-{alert_id}",
        ts_utc=ts,
        site_id="site-1",
        device_id="ops-console",
        kind="ack",
        provenance=Provenance.EXTERNAL,
        confidence=1.0,
        attributes={"ack_of": alert_id},
    )


def test_unacked_alert_escalates_after_threshold():
    rule = Escalation(_ctx(), EscalationCfg(escalate_after_s=600))
    alert = _alert(T0)
    rule.on_event(alert)

    assert rule.on_tick(T0 + timedelta(seconds=599)) == []
    out = rule.on_tick(T0 + timedelta(seconds=600))
    assert len(out) == 1
    esc = out[0]
    assert esc.kind == "escalation"
    assert esc.severity == "critical"
    assert esc.provenance is Provenance.RULE
    assert esc.attributes["ack_of"] == alert.id
    assert esc.attributes["alert_kind"] == "cold_chain_breach"


def test_escalation_fires_once_per_alert():
    rule = Escalation(_ctx(), EscalationCfg(escalate_after_s=600))
    rule.on_event(_alert(T0))
    assert len(rule.on_tick(T0 + timedelta(seconds=700))) == 1
    assert rule.on_tick(T0 + timedelta(seconds=800)) == []


def test_ack_closes_alert_before_escalation():
    rule = Escalation(_ctx(), EscalationCfg(escalate_after_s=600))
    alert = _alert(T0)
    rule.on_event(alert)
    ack = _ack(T0 + timedelta(seconds=30), alert.id)
    assert rule.wants(ack)
    rule.on_event(ack)
    assert rule.on_tick(T0 + timedelta(seconds=3600)) == []


def test_escalation_ignores_info_and_self_kinds():
    rule = Escalation(_ctx(), EscalationCfg(escalate_after_s=600))
    info = derive("x", "dwell", T0, site_id="site-1", severity="info")
    assert not rule.wants(info)
    esc = derive("escalation", "escalation", T0, site_id="site-1", severity="critical")
    assert not rule.wants(esc)


def test_correlator_emits_incident_for_two_distinct_kinds_in_zone():
    rule = IncidentCorrelator(_ctx(), CorrelatorCfg(window_s=300, min_members=2))
    a = _alert(T0, kind="cold_chain_breach", device="sensor-cold-01", zone="cold_store", n=1)
    b = _alert(T0 + timedelta(seconds=60), kind="machine_anomaly", device="sensor-vib-01", zone="cold_store", n=2)
    assert rule.on_event(a) == []
    out = rule.on_event(b)
    assert len(out) == 1
    inc = out[0]
    assert inc.kind == "incident"
    assert inc.severity == "critical"
    assert inc.zone == "cold_store"
    assert inc.attributes["kinds"] == ["cold_chain_breach", "machine_anomaly"]
    assert inc.attributes["devices"] == ["sensor-cold-01", "sensor-vib-01"]
    assert inc.attributes["members"] == [a.id, b.id]


def test_correlator_does_not_count_one_repeating_detector():
    rule = IncidentCorrelator(_ctx(), CorrelatorCfg(window_s=300, min_members=2))
    for i in range(5):
        out = rule.on_event(_alert(T0 + timedelta(seconds=10 * i), zone="gate", n=i))
        assert out == []


def test_correlator_emits_once_per_group():
    rule = IncidentCorrelator(_ctx(), CorrelatorCfg(window_s=300, min_members=2))
    rule.on_event(_alert(T0, kind="a", device="d1", zone="gate", n=1))
    first = rule.on_event(_alert(T0 + timedelta(seconds=5), kind="b", device="d2", zone="gate", n=2))
    second = rule.on_event(_alert(T0 + timedelta(seconds=10), kind="c", device="d3", zone="gate", n=3))
    assert len(first) == 1
    assert second == []


def test_correlator_separates_zones_and_window():
    rule = IncidentCorrelator(_ctx(), CorrelatorCfg(window_s=300, min_members=2))
    rule.on_event(_alert(T0, kind="a", device="d1", zone="gate", n=1))
    # Different zone: separate group, so no incident.
    assert rule.on_event(_alert(T0 + timedelta(seconds=5), kind="b", device="d2", zone="dock", n=2)) == []
    # Same zone but outside the window: group restarts, so no incident.
    assert rule.on_event(_alert(T0 + timedelta(seconds=400), kind="c", device="d3", zone="gate", n=3)) == []


def test_correlator_uses_site_group_when_no_zone():
    rule = IncidentCorrelator(_ctx(), CorrelatorCfg(window_s=300, min_members=2))
    rule.on_event(_alert(T0, kind="a", device="d1", zone=None, n=1))
    out = rule.on_event(_alert(T0 + timedelta(seconds=5), kind="b", device="d2", zone=None, n=2))
    assert len(out) == 1
    assert out[0].zone is None
    assert out[0].label == "site"
