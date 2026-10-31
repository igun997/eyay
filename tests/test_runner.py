import pytest

from eyeai.rules.config import parse_config
from eyeai.runner import Engine
from eyeai.schema import Event, Provenance


def test_unknown_top_level_key_rejected():
    with pytest.raises(ValueError, match="unknown keys"):
        Engine.from_dict({"site_id": "s1", "videoo": []})


def test_site_id_required():
    with pytest.raises(ValueError, match="missing keys"):
        Engine.from_dict({})


def test_rules_site_must_match_engine_site():
    with pytest.raises(ValueError, match="site_id"):
        Engine.from_dict({"site_id": "s1"}, rules_cfg=parse_config({"site_id": "other"}))


def test_video_entry_missing_required_keys_rejected():
    with pytest.raises(ValueError, match="missing keys"):
        Engine.from_dict({"site_id": "s1", "video": [{"device_id": "cam1", "source": "x.mp4"}]})


def test_emit_isolates_ingest_failures_and_audits_them():
    eng = Engine.from_dict({"site_id": "s1"})

    def boom(event):
        raise RuntimeError("rule blew up")

    eng.ingest.handle = boom
    ev = Event(site_id="s1", device_id="cam1", kind="object", label="car", provenance=Provenance.NATIVE)
    eng.emit(ev)  # must not raise into the source thread
    assert eng.errors == 1
    entry = eng.store.audit_log()[0]
    assert entry["action"] == "ingest_error" and "rule blew up" in entry["detail"]["error"]


def test_events_flow_from_emit_to_store():
    eng = Engine.from_dict({"site_id": "s1"})
    ev = Event(site_id="s1", device_id="cam1", kind="object", label="car", provenance=Provenance.NATIVE)
    eng.emit(ev)
    assert eng.store.get_event(ev.id) is not None
