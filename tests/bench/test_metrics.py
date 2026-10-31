import numpy as np
import pytest

from eyeai.bench.metrics import GroundTruth, Prediction, iou, latency_stats, score_detections


def test_latency_stats_percentiles():
    stats = latency_stats(list(range(1, 101)))  # 1..100 ms
    assert stats.count == 100
    assert stats.mean_ms == pytest.approx(50.5)
    assert stats.p50_ms == pytest.approx(np.percentile(np.arange(1, 101), 50))
    assert stats.p95_ms == pytest.approx(np.percentile(np.arange(1, 101), 95))
    assert stats.p99_ms == pytest.approx(np.percentile(np.arange(1, 101), 99))
    assert stats.max_ms == 100.0


def test_latency_stats_single_sample():
    stats = latency_stats([12.5])
    assert stats.count == 1
    assert stats.p50_ms == stats.p95_ms == stats.max_ms == 12.5


def test_latency_stats_rejects_empty():
    with pytest.raises(ValueError):
        latency_stats([])


def test_iou_identical_disjoint_and_partial():
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == pytest.approx(1.0)
    assert iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0.0
    # Overlap 0.5x1 = 0.5; union 1 + 1 - 0.5 = 1.5
    assert iou((0, 0, 1, 1), (0.5, 0, 1.5, 1)) == pytest.approx(1 / 3)


def test_iou_degenerate_box_is_zero():
    assert iou((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0


def test_score_perfect_match():
    gt = {0: [GroundTruth(frame=0, label="car", bbox=(0, 0, 0.5, 0.5))]}
    preds = [Prediction(frame=0, label="car", score=0.9, bbox=(0, 0, 0.5, 0.5))]
    scores = score_detections(preds, gt, frames=[0])
    s = scores["car"]
    assert (s.tp, s.fp, s.fn) == (1, 0, 0)
    assert s.precision == 1.0
    assert s.recall == 1.0


def test_score_false_positive_and_false_negative():
    gt = {0: [GroundTruth(frame=0, label="car", bbox=(0, 0, 0.5, 0.5))]}
    preds = [Prediction(frame=0, label="car", score=0.9, bbox=(0.6, 0.6, 0.9, 0.9))]
    s = score_detections(preds, gt, frames=[0])["car"]
    assert (s.tp, s.fp, s.fn) == (0, 1, 1)
    assert s.precision == 0.0
    assert s.recall == 0.0


def test_score_duplicate_prediction_counts_as_fp():
    gt = {0: [GroundTruth(frame=0, label="person", bbox=(0, 0, 0.5, 0.5))]}
    preds = [
        Prediction(frame=0, label="person", score=0.9, bbox=(0, 0, 0.5, 0.5)),
        Prediction(frame=0, label="person", score=0.8, bbox=(0, 0, 0.5, 0.5)),
    ]
    s = score_detections(preds, gt, frames=[0])["person"]
    assert (s.tp, s.fp, s.fn) == (1, 1, 0)
    assert s.precision == pytest.approx(0.5)
    assert s.recall == 1.0


def test_score_greedy_uses_highest_score_first():
    # Two GT boxes; the higher-score prediction must claim the box it overlaps best.
    gt = {0: [
        GroundTruth(frame=0, label="car", bbox=(0, 0, 0.4, 0.4)),
        GroundTruth(frame=0, label="car", bbox=(0.3, 0, 0.7, 0.4)),
    ]}
    preds = [
        Prediction(frame=0, label="car", score=0.5, bbox=(0.3, 0, 0.7, 0.4)),
        Prediction(frame=0, label="car", score=0.9, bbox=(0, 0, 0.4, 0.4)),
    ]
    s = score_detections(preds, gt, frames=[0])["car"]
    assert (s.tp, s.fp, s.fn) == (2, 0, 0)


def test_score_respects_label_and_frame_filter():
    gt = {
        0: [GroundTruth(frame=0, label="car", bbox=(0, 0, 0.5, 0.5))],
        1: [GroundTruth(frame=1, label="car", bbox=(0, 0, 0.5, 0.5))],
    }
    preds = [
        Prediction(frame=0, label="person", score=0.9, bbox=(0, 0, 0.5, 0.5)),
        Prediction(frame=1, label="car", score=0.9, bbox=(0, 0, 0.5, 0.5)),
    ]
    # Only frame 1 is scored; frame-0 prediction and GT are ignored.
    scores = score_detections(preds, gt, frames=[1])
    assert set(scores) == {"car"}
    assert (scores["car"].tp, scores["car"].fp, scores["car"].fn) == (1, 0, 0)


def test_score_recall_none_when_no_ground_truth():
    preds = [Prediction(frame=0, label="car", score=0.9, bbox=(0, 0, 0.5, 0.5))]
    s = score_detections(preds, {}, frames=[0])["car"]
    assert s.recall is None
    assert s.precision == 0.0


def test_score_rejects_invalid_iou_threshold():
    with pytest.raises(ValueError):
        score_detections([], {}, frames=[], iou_thr=0.0)
    with pytest.raises(ValueError):
        score_detections([], {}, frames=[], iou_thr=1.5)
