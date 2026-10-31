import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from eyeai.bench.metrics import GroundTruth
from eyeai.bench.runner import load_ground_truth, run_video_benchmark, write_report
from eyeai.detect.yolox import Detection


def _make_video(path: Path, frames: int = 12, size=(64, 48), fps=10.0) -> Path:
    w, h = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v writer unavailable")
    for i in range(frames):
        frame = np.full((h, w, 3), i * 10 % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path


class CountingDetector:
    """Returns one 'car' box on every frame; records how many frames it saw."""

    def __init__(self):
        self.calls = 0

    def detect(self, frame_bgr):
        assert frame_bgr.ndim == 3
        self.calls += 1
        return [Detection(label="car", class_id=2, score=0.9, bbox=(0.0, 0.0, 0.5, 0.5))]


class EmptyDetector:
    def detect(self, frame_bgr):
        return []


def test_runner_measures_frames_after_warmup(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", frames=12)
    det = CountingDetector()
    report = run_video_benchmark(video, det, model_name="fake", warmup=3)
    assert det.calls == 12
    assert report.frames_processed == 12
    assert report.warmup_frames == 3
    assert report.frames_measured == 9
    assert report.total.count == 9
    assert report.inference.count == 9
    assert report.decode.count == 9
    assert report.throughput_fps > 0
    assert report.detections_by_label == {"car": 9}
    assert report.model == "fake"
    assert report.video == str(video)


def test_runner_respects_max_frames(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", frames=12)
    det = CountingDetector()
    report = run_video_benchmark(video, det, model_name="fake", max_frames=4, warmup=1)
    assert det.calls == 4
    assert report.frames_processed == 4
    assert report.frames_measured == 3


def test_runner_without_ground_truth_has_no_accuracy(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", frames=6)
    report = run_video_benchmark(video, EmptyDetector(), model_name="fake", warmup=0)
    assert report.accuracy is None
    assert report.notes == ["no ground truth supplied: accuracy not computed"]
    assert report.detections_by_label == {}


def test_runner_with_ground_truth_computes_accuracy(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", frames=4)
    gt = {
        0: [GroundTruth(frame=0, label="car", bbox=(0.0, 0.0, 0.5, 0.5))],
        1: [GroundTruth(frame=1, label="car", bbox=(0.0, 0.0, 0.5, 0.5))],
        2: [],
        3: [],
    }
    report = run_video_benchmark(
        video, CountingDetector(), model_name="fake", warmup=0, ground_truth=gt
    )
    assert report.notes == []
    car = report.accuracy["car"]
    # Detector fires on all 4 frames: 2 matched (tp), 2 spurious (fp), no misses.
    assert (car["tp"], car["fp"], car["fn"]) == (2, 2, 0)
    assert car["precision"] == pytest.approx(0.5)
    assert car["recall"] == 1.0


def test_runner_rejects_bad_arguments(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", frames=3)
    with pytest.raises(ValueError):
        run_video_benchmark(video, EmptyDetector(), model_name="f", max_frames=0)
    with pytest.raises(ValueError):
        run_video_benchmark(video, EmptyDetector(), model_name="f", warmup=-1)


def test_runner_fails_when_warmup_consumes_all_frames(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", frames=3)
    with pytest.raises(RuntimeError, match="no measured frames"):
        run_video_benchmark(video, EmptyDetector(), model_name="f", warmup=10)


def test_runner_fails_on_unreadable_video(tmp_path):
    bad = tmp_path / "missing.mp4"
    with pytest.raises(RuntimeError, match="cannot open video"):
        run_video_benchmark(bad, EmptyDetector(), model_name="f")


def test_load_ground_truth_parses_json(tmp_path):
    gt_file = tmp_path / "gt.json"
    gt_file.write_text(json.dumps({"frames": [
        {"index": 3, "objects": [{"label": "car", "bbox": [0, 0, 0.5, 0.5]}]},
        {"index": 4},
    ]}))
    gt = load_ground_truth(gt_file)
    assert set(gt) == {3, 4}
    assert gt[3] == [GroundTruth(frame=3, label="car", bbox=(0.0, 0.0, 0.5, 0.5))]
    assert gt[4] == []


def test_write_report_round_trips_json(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", frames=4)
    report = run_video_benchmark(video, EmptyDetector(), model_name="fake", warmup=0)
    target = write_report(report, tmp_path / "out")
    assert target.parent == tmp_path / "out"
    assert target.suffix == ".json"
    data = json.loads(target.read_text())
    assert data["model"] == "fake"
    assert data["frames_processed"] == 4
    assert data["total"]["count"] == 4
