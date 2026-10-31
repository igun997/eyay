from datetime import datetime, timedelta, timezone

from eyeai.rules.config import DedupCfg
from eyeai.rules.dedup import Deduplicator
from eyeai.schema import Event, Provenance

T0 = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)


def ev(prov, dt=0.0, kind="object", label="car", zone="gate", track=None, dev="cam1"):
    return Event(ts_utc=T0 + timedelta(seconds=dt), site_id="s", device_id=dev, kind=kind, label=label,
                 provenance=prov, zone=zone, track_id=track)


def test_local_object_dropped_when_native_covers_same_device_zone_label():
    d = Deduplicator(DedupCfg(window_s=5))
    assert d.admit(ev(Provenance.NATIVE, 0))
    assert not d.admit(ev(Provenance.LOCAL_MODEL, 2, track=7))
    assert d.admit(ev(Provenance.LOCAL_MODEL, 6, track=7))  # outside window


def test_native_after_local_is_kept():
    d = Deduplicator(DedupCfg(window_s=5))
    assert d.admit(ev(Provenance.LOCAL_MODEL, 0, track=1))
    assert d.admit(ev(Provenance.NATIVE, 1))


def test_local_updates_never_suppress_each_other():
    d = Deduplicator(DedupCfg(window_s=5))
    assert all(d.admit(ev(Provenance.LOCAL_MODEL, t, track=1)) for t in (0, 1, 2, 3))


def test_countable_and_stateful_kinds_are_never_dropped():
    d = Deduplicator(DedupCfg(window_s=5))
    for kind in ("line_cross", "zone_enter", "zone_exit"):
        assert d.admit(ev(Provenance.NATIVE, 0, kind=kind))
        assert d.admit(ev(Provenance.NATIVE, 1, kind=kind))  # two cars crossing within the window both count


def test_different_device_zone_or_label_not_confused():
    d = Deduplicator(DedupCfg(window_s=5))
    assert d.admit(ev(Provenance.NATIVE, 0))
    assert d.admit(ev(Provenance.LOCAL_MODEL, 1, dev="cam2"))
    assert d.admit(ev(Provenance.LOCAL_MODEL, 1, zone="dock"))
    assert d.admit(ev(Provenance.LOCAL_MODEL, 1, label="person"))
