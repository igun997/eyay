"""Offline benchmark harness for detectors (latency, throughput, precision/recall)."""
from .metrics import ClassScore, GroundTruth, LatencyStats, Prediction, iou, latency_stats, score_detections
from .runner import BenchReport, load_ground_truth, run_video_benchmark, write_report

__all__ = [
    "BenchReport",
    "ClassScore",
    "GroundTruth",
    "LatencyStats",
    "Prediction",
    "iou",
    "latency_stats",
    "load_ground_truth",
    "run_video_benchmark",
    "score_detections",
    "write_report",
]
