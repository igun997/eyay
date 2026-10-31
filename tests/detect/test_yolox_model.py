from pathlib import Path

import cv2
import pytest

from eyeai.detect.yolox import YoloxDetector

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data" / "models" / "yolox_s.onnx"
VIDEO = ROOT / "data" / "samples" / "car-detection.mp4"


@pytest.fixture(scope="module")
def detector():
    if not MODEL.exists():
        pytest.skip(f"model not downloaded: {MODEL}")
    return YoloxDetector(MODEL, conf_thresh=0.25)


@pytest.mark.skipif(not VIDEO.exists(), reason="sample video not downloaded")
def test_detects_car_on_sample_frame(detector):
    cap = cv2.VideoCapture(str(VIDEO))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 60)
        ok, frame = cap.read()
    finally:
        cap.release()
    assert ok, "could not read frame 60"
    dets = detector.detect(frame)
    cars = [d for d in dets if d.label == "car"]
    assert cars, f"expected a car on frame 60, got {[(d.label, round(d.score, 2)) for d in dets]}"
    best = max(cars, key=lambda d: d.score)
    assert best.score >= 0.5
    x1, y1, x2, y2 = best.bbox
    assert 0.0 <= x1 < x2 <= 1.0
    assert 0.0 <= y1 < y2 <= 1.0


def test_empty_frame_gives_no_detections(detector):
    import numpy as np

    blank = np.full((432, 768, 3), 128, dtype=np.uint8)
    dets = detector.detect(blank)
    assert all(d.score < 0.5 for d in dets), dets


def test_missing_model_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        YoloxDetector(tmp_path / "nope.onnx")
