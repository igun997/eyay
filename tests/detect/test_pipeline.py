from datetime import datetime, timezone

import numpy as np
import pytest

from eyeai.detect.capability import DetectionPlan, NativeCapability, plan_detection
from eyeai.detect.pipeline import LocalDetectionPipeline
from eyeai.detect.yolox import Detection
from eyeai.schema import Event, Provenance


class FakeDetector:
    def __init__(self, detections):
        self._detections = detections
        self.calls = 0

    def detect(self, frame_bgr):
        self.calls += 1
        return list(self._detections)


def _det(label, class_id, score, bbox=(0.1, 0.2, 0.3, 0.4)):
    return Detection(label=label, class_id=class_id, score=score, bbox=bbox)


def _frame():
    return np.zeros((432, 768, 3), dtype=np.uint8)


def test_emits_only_gap_labels_with_local_provenance():
    emitted: list[Event] = []
    det = FakeDetector([_det("car", 2, 0.9), _det("person", 0, 0.8)])
    cap = NativeCapability(device_id="cam-1", native_labels=frozenset({"person"}), source="onvif")
    plan = plan_detection(["person", "car"], cap)
    pipe = LocalDetectionPipeline(
        site_id="site-a", device_id="cam-1", detector=det, emit=emitted.append,
        plan=plan, model_name="yolox_s", zone="lane-1",
    )
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    events = pipe.process_frame(_frame(), ts_utc=ts)

    assert det.calls == 1
    assert [e.label for e in events] == ["car"]
    assert emitted == events
    ev = events[0]
    assert ev.kind == "object"
    assert ev.provenance is Provenance.LOCAL_MODEL
    assert ev.confidence == pytest.approx(0.9)
    assert ev.bbox == (0.1, 0.2, 0.3, 0.4)
    assert ev.site_id == "site-a"
    assert ev.device_id == "cam-1"
    assert ev.zone == "lane-1"
    assert ev.ts_utc == ts
    assert ev.ts_utc.tzinfo is not None
    assert ev.attributes["model"] == "yolox_s"
    assert ev.attributes["class_id"] == 2
    assert ev.attributes["frame_size"] == [768, 432]


def test_detector_not_called_when_fully_covered_natively():
    emitted: list[Event] = []
    det = FakeDetector([_det("car", 2, 0.9)])
    cap = NativeCapability(device_id="cam-1", native_labels=frozenset({"car"}), source="isapi")
    plan = plan_detection(["car"], cap)
    pipe = LocalDetectionPipeline(
        site_id="s", device_id="d", detector=det, emit=emitted.append,
        plan=plan, model_name="yolox_s",
    )
    assert pipe.process_frame(_frame()) == []
    assert det.calls == 0
    assert emitted == []


def test_default_timestamp_is_tz_aware_utc():
    det = FakeDetector([_det("car", 2, 0.5)])
    pipe = LocalDetectionPipeline(
        site_id="s", device_id="d", detector=det, emit=lambda e: None,
        plan=plan_detection(["car"], None), model_name="yolox_s",
    )
    (ev,) = pipe.process_frame(_frame())
    assert ev.ts_utc.tzinfo is not None
    assert ev.ts_utc.utcoffset().total_seconds() == 0


def test_zone_defaults_to_none():
    det = FakeDetector([_det("car", 2, 0.5)])
    pipe = LocalDetectionPipeline(
        site_id="s", device_id="d", detector=det, emit=lambda e: None,
        plan=plan_detection(["car"], None), model_name="yolox_s",
    )
    (ev,) = pipe.process_frame(_frame())
    assert ev.zone is None


def test_rejects_gap_label_outside_coco():
    plan = DetectionPlan(
        required_labels=frozenset({"forklift"}),
        native_labels=frozenset(),
        gap_labels=frozenset({"forklift"}),
    )
    with pytest.raises(ValueError, match="forklift"):
        LocalDetectionPipeline(
            site_id="s", device_id="d", detector=FakeDetector([]), emit=lambda e: None,
            plan=plan, model_name="yolox_s",
        )


def test_rejects_empty_site_or_device():
    plan = plan_detection(["car"], None)
    with pytest.raises(ValueError):
        LocalDetectionPipeline(
            site_id="", device_id="d", detector=FakeDetector([]), emit=lambda e: None,
            plan=plan, model_name="yolox_s",
        )
    with pytest.raises(ValueError):
        LocalDetectionPipeline(
            site_id="s", device_id="", detector=FakeDetector([]), emit=lambda e: None,
            plan=plan, model_name="yolox_s",
        )


def test_no_detections_emits_nothing():
    emitted: list[Event] = []
    pipe = LocalDetectionPipeline(
        site_id="s", device_id="d", detector=FakeDetector([]), emit=emitted.append,
        plan=plan_detection(["car"], None), model_name="yolox_s",
    )
    assert pipe.process_frame(_frame()) == []
    assert emitted == []
