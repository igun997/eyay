"""Runs a detector over a video file and records latency, throughput and accuracy."""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2

from eyeai.detect.pipeline import FrameDetector

from .metrics import ClassScore, GroundTruth, LatencyStats, Prediction, latency_stats, score_detections


@dataclass(frozen=True)
class BenchReport:
    video: str
    model: str
    frames_processed: int
    warmup_frames: int
    frames_measured: int
    decode: LatencyStats
    inference: LatencyStats
    total: LatencyStats
    throughput_fps: float
    detections_by_label: dict[str, int]
    accuracy: dict[str, dict] | None
    iou_threshold: float
    started_utc: str
    finished_utc: str
    notes: list[str] = field(default_factory=list)


def load_ground_truth(path: str | Path) -> dict[int, list[GroundTruth]]:
    """JSON: {"frames": [{"index": int, "objects": [{"label": str, "bbox": [x1,y1,x2,y2]}]}]}"""
    data = json.loads(Path(path).read_text())
    out: dict[int, list[GroundTruth]] = {}
    for fr in data["frames"]:
        idx = int(fr["index"])
        out[idx] = [
            GroundTruth(frame=idx, label=str(o["label"]), bbox=tuple(float(v) for v in o["bbox"]))
            for o in fr.get("objects", [])
        ]
    return out


def run_video_benchmark(
    video_path: str | Path,
    detector: FrameDetector,
    *,
    model_name: str,
    max_frames: int | None = None,
    warmup: int = 5,
    ground_truth: dict[int, list[GroundTruth]] | None = None,
    iou_threshold: float = 0.5,
) -> BenchReport:
    if max_frames is not None and max_frames <= 0:
        raise ValueError("max_frames must be positive")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {path}")

    started = datetime.now(timezone.utc)
    decode_ms: list[float] = []
    infer_ms: list[float] = []
    total_ms: list[float] = []
    by_label: Counter[str] = Counter()
    predictions: list[Prediction] = []
    processed_frames: list[int] = []
    idx = 0
    try:
        while max_frames is None or idx < max_frames:
            t0 = time.perf_counter()
            ok, frame = cap.read()
            t1 = time.perf_counter()
            if not ok:
                break
            dets = detector.detect(frame)
            t2 = time.perf_counter()
            processed_frames.append(idx)
            for d in dets:
                predictions.append(Prediction(frame=idx, label=d.label, score=d.score, bbox=d.bbox))
            if idx >= warmup:
                decode_ms.append((t1 - t0) * 1000.0)
                infer_ms.append((t2 - t1) * 1000.0)
                total_ms.append((t2 - t0) * 1000.0)
                for d in dets:
                    by_label[d.label] += 1
            idx += 1
    finally:
        cap.release()

    if not total_ms:
        raise RuntimeError(
            f"no measured frames: processed={len(processed_frames)} warmup={warmup}"
        )
    finished = datetime.now(timezone.utc)

    accuracy: dict[str, dict] | None = None
    if ground_truth is not None:
        scores: dict[str, ClassScore] = score_detections(
            predictions, ground_truth, processed_frames, iou_thr=iou_threshold
        )
        accuracy = {k: asdict(v) for k, v in scores.items()}

    return BenchReport(
        video=str(path),
        model=model_name,
        frames_processed=len(processed_frames),
        warmup_frames=min(warmup, len(processed_frames)),
        frames_measured=len(total_ms),
        decode=latency_stats(decode_ms),
        inference=latency_stats(infer_ms),
        total=latency_stats(total_ms),
        throughput_fps=len(total_ms) / (sum(total_ms) / 1000.0),
        detections_by_label=dict(by_label),
        accuracy=accuracy,
        iou_threshold=iou_threshold,
        started_utc=started.isoformat(),
        finished_utc=finished.isoformat(),
        notes=[] if ground_truth is not None else ["no ground truth supplied: accuracy not computed"],
    )


def write_report(report: BenchReport, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(report.video).stem
    stamp = datetime.fromisoformat(report.started_utc).strftime("%Y%m%dT%H%M%SZ")
    target = out / f"{stem}-{report.model}-{stamp}.json"
    target.write_text(json.dumps(asdict(report), indent=2))
    return target
