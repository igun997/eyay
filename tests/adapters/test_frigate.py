from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any, Callable

from eyeai.adapters.frigate import (
    FrigateMqttAdapter,
    count_to_event,
    frigate_event_to_events,
)
from eyeai.schema import Event, Provenance
from tests.adapters.mocks.fake_paho import FakeMqttFactory

SITE = "site-1"
DEV = "frigate-1"
CAM = "gate"
START = 1_700_000_000.0
END = 1_700_000_030.0
BASE_URL = "http://frigate.local:5000/"


def _after(**overrides: Any) -> dict[str, Any]:
    after: dict[str, Any] = {
        "id": "1700000000.1-abc",
        "camera": CAM,
        "label": "person",
        "start_time": START,
        "end_time": None,
        "box": [100, 50, 300, 250],
        "top_score": 0.9,
        "entered_zones": [],
        "has_clip": False,
        "has_snapshot": False,
        "false_positive": False,
    }
    after.update(overrides)
    return after


def _msg(event_type: str, after: dict[str, Any], before: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": event_type, "before": before or {}, "after": after}


def _convert(message: dict[str, Any], **kwargs: Any) -> list[Event]:
    return frigate_event_to_events(message, site_id=SITE, instance="nvr-a", **kwargs)


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


# --- frigate_event_to_events: shape guards -------------------------------


def test_unknown_event_type_yields_nothing() -> None:
    assert _convert(_msg("destroy", _after())) == []


def test_missing_after_yields_nothing() -> None:
    assert _convert({"type": "new", "before": {}}) == []


def test_non_dict_after_yields_nothing() -> None:
    assert _convert({"type": "new", "after": "person"}) == []


def test_missing_camera_yields_nothing() -> None:
    assert _convert(_msg("new", _after(camera=""))) == []


def test_missing_id_yields_nothing() -> None:
    assert _convert(_msg("new", _after(id=None))) == []


# --- new ------------------------------------------------------------------


def test_new_emits_object_and_one_zone_enter_per_entered_zone() -> None:
    events = _convert(_msg("new", _after(entered_zones=["door", "yard"])))
    kinds = [e.kind for e in events]
    assert kinds == ["object", "zone_enter", "zone_enter"]
    assert events[0].label == "person"
    assert events[0].zone is None
    assert [e.zone for e in events[1:]] == ["door", "yard"]


def test_new_event_carries_native_provenance_and_correlation() -> None:
    (obj,) = _convert(_msg("new", _after(entered_zones=[])))
    assert obj.provenance is Provenance.NATIVE
    assert obj.correlation_id == "1700000000.1-abc"
    assert obj.device_id == CAM
    assert obj.severity == "info"
    assert obj.attributes["frigate_event_id"] == "1700000000.1-abc"
    assert obj.attributes["frigate_type"] == "new"
    assert obj.attributes["source_instance"] == "nvr-a"


def test_new_event_timestamp_is_start_time_in_utc() -> None:
    (obj,) = _convert(_msg("new", _after()))
    assert obj.ts_utc == datetime.fromtimestamp(START, tz=UTC)
    assert obj.ts_utc.tzinfo is not None


def test_label_taken_from_after_and_non_string_label_is_none() -> None:
    (obj,) = _convert(_msg("new", _after(label="car")))
    assert obj.label == "car"
    (obj2,) = _convert(_msg("new", _after(label=7)))
    assert obj2.label is None


def test_confidence_uses_top_score_and_defaults_to_one() -> None:
    (obj,) = _convert(_msg("new", _after(top_score=0.42)))
    assert obj.confidence == 0.42
    after = _after()
    del after["top_score"]
    (obj2,) = _convert(_msg("new", after))
    assert obj2.confidence == 1.0


def test_sub_label_included_only_when_truthy() -> None:
    (obj,) = _convert(_msg("new", _after(sub_label="courier")))
    assert obj.attributes["sub_label"] == "courier"
    (obj2,) = _convert(_msg("new", _after(sub_label="")))
    assert "sub_label" not in obj2.attributes


def test_flags_are_bool_coerced() -> None:
    (obj,) = _convert(_msg("new", _after(has_clip=0, has_snapshot=1, false_positive=1)))
    assert obj.attributes["has_clip"] is False
    assert obj.attributes["has_snapshot"] is True
    assert obj.attributes["false_positive"] is True


# --- bbox normalisation ----------------------------------------------------


def test_bbox_is_none_without_frame_size() -> None:
    (obj,) = _convert(_msg("new", _after()))
    assert obj.bbox is None


def test_bbox_normalised_by_frame_size() -> None:
    (obj,) = _convert(_msg("new", _after(box=[100, 50, 300, 250])), frame_sizes={CAM: (1000, 500)})
    assert obj.bbox is not None
    assert list(obj.bbox) == [0.1, 0.1, 0.3, 0.5]


def test_bbox_clamped_to_unit_range() -> None:
    (obj,) = _convert(_msg("new", _after(box=[-50, 0, 2000, 500])), frame_sizes={CAM: (1000, 500)})
    assert obj.bbox is not None
    assert list(obj.bbox) == [0.0, 0.0, 1.0, 1.0]


def test_bbox_none_for_frame_size_of_other_camera() -> None:
    (obj,) = _convert(_msg("new", _after()), frame_sizes={"other": (1000, 500)})
    assert obj.bbox is None


def test_bbox_none_for_non_positive_frame_size() -> None:
    (obj,) = _convert(_msg("new", _after()), frame_sizes={CAM: (0, 500)})
    assert obj.bbox is None


def test_bbox_none_for_non_finite_box_value() -> None:
    (obj,) = _convert(_msg("new", _after(box=[float("nan"), 0, 10, 10])), frame_sizes={CAM: (100, 100)})
    assert obj.bbox is None


def test_bbox_none_for_wrong_length_box() -> None:
    (obj,) = _convert(_msg("new", _after(box=[1, 2, 3])), frame_sizes={CAM: (100, 100)})
    assert obj.bbox is None


def test_box_px_recorded_when_box_is_a_list() -> None:
    (obj,) = _convert(_msg("new", _after(box=[100, 50, 300, 250])))
    assert obj.attributes["box_px"] == [100, 50, 300, 250]


# --- clip and snapshot refs -----------------------------------------------


def test_clip_and_snapshot_refs_built_from_base_url() -> None:
    (obj,) = _convert(
        _msg("new", _after(has_clip=True, has_snapshot=True)),
        base_url=BASE_URL,
    )
    assert obj.clip_ref == "http://frigate.local:5000/api/events/1700000000.1-abc/clip.mp4"
    assert obj.attributes["snapshot_ref"] == (
        "http://frigate.local:5000/api/events/1700000000.1-abc/snapshot.jpg"
    )


def test_no_refs_without_base_url() -> None:
    (obj,) = _convert(_msg("new", _after(has_clip=True, has_snapshot=True)))
    assert obj.clip_ref is None
    assert "snapshot_ref" not in obj.attributes


def test_no_clip_ref_when_has_clip_false() -> None:
    (obj,) = _convert(_msg("new", _after(has_clip=False, has_snapshot=True)), base_url=BASE_URL)
    assert obj.clip_ref is None
    assert "snapshot_ref" in obj.attributes


# --- update ---------------------------------------------------------------


def test_update_emits_zone_enter_only_for_newly_entered_zones() -> None:
    before = {"entered_zones": ["door"]}
    after = _after(entered_zones=["door", "yard"])
    events = _convert(_msg("update", after, before=before))
    assert [e.kind for e in events] == ["zone_enter"]
    assert events[0].zone == "yard"


def test_update_with_no_new_zone_emits_nothing() -> None:
    before = {"entered_zones": ["door"]}
    after = _after(entered_zones=["door"])
    assert _convert(_msg("update", after, before=before)) == []


def test_update_event_type_recorded() -> None:
    before = {"entered_zones": []}
    after = _after(entered_zones=["yard"])
    (ev,) = _convert(_msg("update", after, before=before))
    assert ev.attributes["frigate_type"] == "update"


# --- end ------------------------------------------------------------------


def test_end_emits_single_object_end_with_end_time() -> None:
    after = _after(end_time=END)
    (ev,) = _convert(_msg("end", after))
    assert ev.kind == "object_end"
    assert ev.attributes["end_time"] == END
    assert ev.ts_utc == datetime.fromtimestamp(END, tz=UTC)


def test_end_falls_back_to_start_time_when_end_time_missing() -> None:
    (ev,) = _convert(_msg("end", _after(end_time=None)))
    assert ev.ts_utc == datetime.fromtimestamp(START, tz=UTC)


# --- count_to_event ---------------------------------------------------------


def test_count_to_event_shape() -> None:
    ev = count_to_event(site_id=SITE, instance="nvr-a", camera=CAM, label="person", count=3)
    assert ev.kind == "object_count"
    assert ev.device_id == CAM
    assert ev.label == "person"
    assert ev.attributes["count"] == 3
    assert ev.attributes["source_instance"] == "nvr-a"
    assert ev.provenance is Provenance.NATIVE
    assert ev.confidence == 1.0
    assert ev.ts_utc.tzinfo is not None


# --- adapter: routing, dedupe, filters -------------------------------------


def _adapter(factory: FakeMqttFactory, **kwargs: Any) -> tuple[FrigateMqttAdapter, list[Event]]:
    events: list[Event] = []
    adapter = FrigateMqttAdapter(
        SITE,
        DEV,
        events.append,
        host="broker.local",
        client_factory=factory,
        backoff_initial=0.01,
        backoff_max=0.02,
        **kwargs,
    )
    return adapter, events


def test_empty_topic_prefix_rejected() -> None:
    factory = FakeMqttFactory()
    try:
        FrigateMqttAdapter(SITE, DEV, lambda _e: None, host="h", topic_prefix="", client_factory=factory)
    except ValueError as exc:
        assert "topic_prefix" in str(exc)
    else:
        raise AssertionError("empty prefix should be rejected")


def test_trailing_slash_topic_prefix_rejected() -> None:
    factory = FakeMqttFactory()
    try:
        FrigateMqttAdapter(SITE, DEV, lambda _e: None, host="h", topic_prefix="frigate/", client_factory=factory)
    except ValueError as exc:
        assert "topic_prefix" in str(exc)
    else:
        raise AssertionError("trailing-slash prefix should be rejected")


def test_session_subscribes_to_events_and_count_topics() -> None:
    factory = FakeMqttFactory()
    adapter, _events = _adapter(factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        assert client.client_id == f"eyeai-frigate-{DEV}"
        assert set(client.subscriptions) >= {"frigate/events", "frigate/+/+"}
    finally:
        adapter.stop()


def test_event_message_routed_to_events() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter(factory, frame_sizes={CAM: (1000, 500)})
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver("frigate/events", json.dumps(_msg("new", _after())).encode())
        _wait_for(lambda: len(events) == 1)
        assert events[0].kind == "object"
        assert events[0].label == "person"
        assert list(events[0].bbox or []) == [0.1, 0.1, 0.3, 0.5]
    finally:
        adapter.stop()


def test_non_dict_event_payload_ignored() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter(factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver("frigate/events", b"[1, 2]")
        client.deliver("frigate/events", b"not json")
        client.deliver("frigate/events", json.dumps(_msg("new", _after())).encode())
        _wait_for(lambda: len(events) == 1)
        assert len(events) == 1
    finally:
        adapter.stop()


def test_count_topic_emits_count_event() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter(factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver(f"frigate/{CAM}/person", b"4")
        _wait_for(lambda: len(events) == 1)
        assert events[0].kind == "object_count"
        assert events[0].attributes["count"] == 4
        assert events[0].device_id == CAM
    finally:
        adapter.stop()


def test_count_dedupes_repeated_value_for_same_camera_label() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter(factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver(f"frigate/{CAM}/person", b"2")
        client.deliver(f"frigate/{CAM}/person", b"2 ")
        client.deliver(f"frigate/{CAM}/person", b"3")
        _wait_for(lambda: len(events) == 2)
        assert [e.attributes["count"] for e in events] == [2, 3]
    finally:
        adapter.stop()


def test_non_integer_and_negative_counts_skipped() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter(factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver(f"frigate/{CAM}/person", b"ON")
        client.deliver(f"frigate/{CAM}/person", b"OFF")
        client.deliver(f"frigate/{CAM}/person", b"-1")
        client.deliver(f"frigate/{CAM}/person", b"5")
        _wait_for(lambda: len(events) == 1)
        assert [e.attributes["count"] for e in events] == [5]
    finally:
        adapter.stop()


def test_cameras_filter_drops_other_cameras() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter(factory, cameras={"front"})
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver(f"frigate/{CAM}/person", b"1")
        client.deliver("frigate/front/person", b"1")
        client.deliver("frigate/events", json.dumps(_msg("new", _after(camera=CAM))).encode())
        client.deliver("frigate/events", json.dumps(_msg("new", _after(camera="front"))).encode())
        _wait_for(lambda: len(events) == 2)
        assert {e.device_id for e in events} == {"front"}
    finally:
        adapter.stop()


def test_unrelated_topics_ignored() -> None:
    factory = FakeMqttFactory()
    adapter, events = _adapter(factory)
    adapter.start()
    try:
        client = factory.wait_for_client()
        _wait_for(lambda: adapter.status()["connected"])
        client.deliver("other/events", json.dumps(_msg("new", _after())).encode())
        client.deliver(f"frigate/{CAM}/person/extra", b"3")
        client.deliver("frigate/events/extra", json.dumps(_msg("new", _after())).encode())
        client.deliver("frigate/events", json.dumps(_msg("new", _after())).encode())
        _wait_for(lambda: len(events) == 1)
        assert len(events) == 1
    finally:
        adapter.stop()
