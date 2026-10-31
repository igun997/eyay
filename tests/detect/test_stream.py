from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from eyeai.detect import Detection
from eyeai.detect.capability import NativeCapability, plan_detection
from eyeai.detect.lines import Line
from eyeai.detect.stream import VideoPipeline
from eyeai.detect.zones import Zone

T0 = datetime(2026, 10, 31, 3, 0, 0, tzinfo=timezone.utc)
FRAME = np.zeros((100, 200, 3), dtype=np.uint8)


class ScriptedDetector:
    """Returns the scripted detections for call i."""

    def __init__(self, script):
        self.script = script
        self.i = 0

    def detect(self, frame):
        out = self.script[self.i] if self.i < len(self.script) else []
        self.i += 1
        return out


def car(x1, score=0.9, label="car"):
    return Detection(label=label, class_id=2, score=score, bbox=(x1, 0.4, x1 + 0.1, 0.6))


def make(script, **kw):
    events = []
    plan = plan_detection({"car", "person"}, None)
    p = VideoPipeline(
        "unused.mp4", site_id="s1", device_id="cam1", detector=ScriptedDetector(script),
        emit=events.append, plan=plan, **kw,
    )
    return p, events


def feed(p, n, dt=0.2):
    for i in range(n):
        p.process_frame(FRAME, T0 + timedelta(seconds=i * dt))


def test_refuses_when_native_covers_everything():
    plan = plan_detection({"car"}, NativeCapability("cam1", frozenset({"car"}), "isapi"))
    with pytest.raises(ValueError, match="duplicate"):
        VideoPipeline("x.mp4", site_id="s", device_id="c", detector=ScriptedDetector([]),
                      emit=lambda e: None, plan=plan)


def test_line_cross_emitted_once_with_direction():
    script = [[car(x)] for x in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65)]
    line = Line("gate", (0.6, 0.0), (0.6, 1.0))
    p, events = make(script, lines=[line])
    feed(p, len(script))
    crosses = [e for e in events if e.kind == "line_cross"]
    assert len(crosses) == 1
    assert crosses[0].attributes["line"] == "gate"
    assert crosses[0].attributes["direction"] == "in"  # left->right across a top->bottom line
    assert crosses[0].track_id == events[0].track_id


def test_object_only_after_min_hits_and_class_filter():
    # 'cell phone' is not a required label -> must never be emitted
    phone = Detection("cell phone", 67, 0.91, (0.1, 0.1, 0.2, 0.2))
    p, events = make([[car(0.3), phone]], min_hits=2)
    feed(p, 1)
    assert [e for e in events if e.kind == "object"] == []  # one hit < min_hits
    p2, events2 = make([[car(0.3), phone], [car(0.31), phone]], min_hits=2)
    feed(p2, 2)
    objs = [e for e in events2 if e.kind == "object"]
    assert len(objs) == 1 and objs[0].label == "car" and objs[0].attributes["first"] is True


def test_zone_enter_exit_and_track_lost_exit():
    zone = Zone("dock", ((0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)))
    # car stays in zone for 4 frames then vanishes for > max_missed frames
    script = [[car(0.1)]] * 4 + [[]] * 15
    p, events = make(script, zones=[zone])
    feed(p, len(script))
    kinds = [e.kind for e in events if e.zone == "dock"]
    assert "zone_enter" in kinds
    exits = [e for e in events if e.kind == "zone_exit"]
    assert len(exits) == 1
    assert exits[0].attributes["reason"] == "track_lost"
    assert exits[0].attributes["dwell_s"] >= 0.0


def test_heartbeat_reports_fps_and_latency():
    p, events = make([[car(0.3)]] * 80, heartbeat_s=10.0)
    feed(p, 80, dt=0.2)  # 16 s of video time
    hb = [e for e in events if e.kind == "camera_frame_stats"]
    assert len(hb) == 1
    a = hb[0].attributes
    assert a["frames"] > 0 and a["inference_ms_p95"] >= a["inference_ms_p50"] >= 0.0
    assert 4.0 < a["fps"] < 6.0


def test_failing_consumer_does_not_stop_capture():
    plan = plan_detection({"car"}, None)

    def boom(e):
        raise RuntimeError("consumer down")

    p = VideoPipeline("x.mp4", site_id="s", device_id="c", plan=plan,
                      detector=ScriptedDetector([[car(0.3)]] * 3), emit=boom, min_hits=1)
    feed(p, 3)
    assert p.last_error and "consumer down" in p.last_error


def test_run_once_on_video_file_uses_video_time_and_frame_skipping(tmp_path: Path):
    path = tmp_path / "t.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for _ in range(40):
        w.write(np.zeros((48, 64, 3), dtype=np.uint8))
    w.release()
    events = []
    det = ScriptedDetector([[car(0.3)]] * 100)
    p = VideoPipeline(path, site_id="s", device_id="c", detector=det, emit=events.append,
                      plan=plan_detection({"car"}, None), target_fps=5.0, start_time=T0, min_hits=1)
    p.run_once()
    assert det.i == 20  # 40 frames @10fps, target 5fps -> every 2nd frame
    first = [e for e in events if e.kind == "object"][0]
    assert first.ts_utc == T0


SAMPLE = Path("data/samples/car-detection.mp4")
MODEL = Path("data/models/yolox_s.onnx")


@pytest.mark.skipif(not (SAMPLE.exists() and MODEL.exists()), reason="sample video or model missing")
def test_real_model_on_traffic_sample_emits_tracked_events():
    from eyeai.detect.yolox import YoloxDetector

    events = []
    det = YoloxDetector(MODEL, conf_thresh=0.3)
    zone = Zone("road", ((0.0, 0.3), (1.0, 0.3), (1.0, 1.0), (0.0, 1.0)))
    line = Line("mid", (0.0, 0.6), (1.0, 0.6))
    p = VideoPipeline(SAMPLE, site_id="s", device_id="cam", detector=det, emit=events.append,
                      plan=plan_detection({"car", "person"}, None), zones=[zone], lines=[line],
                      target_fps=5.0, start_time=T0)
    p.run_once(max_frames=40)
    objs = [e for e in events if e.kind == "object"]
    assert objs and all(e.label in ("car", "person") for e in objs)
    assert any(e.track_id is not None for e in objs)
    assert all(e.provenance.value == "local_model" and e.ts_utc.tzinfo for e in events)
