"""Latency percentiles and per-class precision/recall (PDF sections 12-13)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

Box = tuple[float, float, float, float]  # x1,y1,x2,y2 normalised


@dataclass(frozen=True)
class LatencyStats:
    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(frozen=True)
class GroundTruth:
    frame: int
    label: str
    bbox: Box


@dataclass(frozen=True)
class Prediction:
    frame: int
    label: str
    score: float
    bbox: Box


@dataclass(frozen=True)
class ClassScore:
    tp: int
    fp: int
    fn: int
    precision: float | None  # None when tp + fp == 0
    recall: float | None     # None when tp + fn == 0


def latency_stats(ms: Sequence[float]) -> LatencyStats:
    if len(ms) == 0:
        raise ValueError("no latency samples")
    arr = np.asarray(ms, dtype=np.float64)
    return LatencyStats(
        count=int(arr.size),
        mean_ms=float(arr.mean()),
        p50_ms=float(np.percentile(arr, 50)),
        p95_ms=float(np.percentile(arr, 95)),
        p99_ms=float(np.percentile(arr, 99)),
        max_ms=float(arr.max()),
    )


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def score_detections(
    predictions: Sequence[Prediction],
    ground_truth: Mapping[int, Sequence[GroundTruth]],
    frames: Sequence[int],
    iou_thr: float = 0.5,
) -> dict[str, ClassScore]:
    """Greedy matching per (frame, label), highest score first. Only the given frames are scored."""
    if not 0.0 < iou_thr <= 1.0:
        raise ValueError("iou_thr must be in (0, 1]")
    frame_set = set(frames)
    preds_by: dict[tuple[int, str], list[Prediction]] = defaultdict(list)
    gts_by: dict[tuple[int, str], list[GroundTruth]] = defaultdict(list)
    for p in predictions:
        if p.frame in frame_set:
            preds_by[(p.frame, p.label)].append(p)
    for f in frame_set:
        for g in ground_truth.get(f, ()):
            gts_by[(f, g.label)].append(g)

    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    for key in set(preds_by) | set(gts_by):
        _, label = key
        gts = gts_by.get(key, [])
        matched = [False] * len(gts)
        for p in sorted(preds_by.get(key, []), key=lambda x: x.score, reverse=True):
            best_j, best_iou = -1, iou_thr
            for j, g in enumerate(gts):
                if matched[j]:
                    continue
                v = iou(p.bbox, g.bbox)
                if v >= best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0:
                matched[best_j] = True
                tp[label] += 1
            else:
                fp[label] += 1
        fn[label] += sum(1 for m in matched if not m)

    out: dict[str, ClassScore] = {}
    for label in sorted(set(tp) | set(fp) | set(fn)):
        t, f_p, f_n = tp[label], fp[label], fn[label]
        out[label] = ClassScore(
            tp=t,
            fp=f_p,
            fn=f_n,
            precision=t / (t + f_p) if t + f_p > 0 else None,
            recall=t / (t + f_n) if t + f_n > 0 else None,
        )
    return out
