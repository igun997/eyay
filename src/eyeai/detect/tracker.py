"""Per-label IoU tracker with Hungarian assignment: maps detections across frames to stable track ids."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from .yolox import Detection


@dataclass(frozen=True)
class TrackedDetection:
    track_id: int
    detection: Detection


@dataclass
class _Track:
    track_id: int
    label: str
    bbox: np.ndarray  # (4,) xyxy normalised
    missed: int


def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a: (T, 4), b: (D, 4) xyxy. Returns (T, D) IoU; zero where union is zero."""
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0.0, None) * np.clip(y2 - y1, 0.0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(union > 0.0, inter / union, 0.0)
    return out


class IouTracker:
    """Associates detections of the same label by IoU.

    Each frame, tracks and detections of one label are matched with the Hungarian
    algorithm on IoU; pairs below ``iou_threshold`` never match. An unmatched detection
    starts a new track. A track unmatched for more than ``max_missed`` consecutive
    updates is dropped. Track ids are monotonically increasing and never reused.
    """

    def __init__(self, *, iou_threshold: float = 0.3, max_missed: int = 10):
        if not 0.0 < iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be in (0, 1]")
        if max_missed < 0:
            raise ValueError("max_missed must be >= 0")
        self._iou_threshold = iou_threshold
        self._max_missed = max_missed
        self._tracks: list[_Track] = []
        self._next_id = 1

    @property
    def active_track_ids(self) -> list[int]:
        return [t.track_id for t in self._tracks]

    def update(self, detections: Sequence[Detection]) -> list[TrackedDetection]:
        """Returns one TrackedDetection per input detection, in input order."""
        assigned: list[int] = [-1] * len(detections)
        survivors: list[_Track] = []

        by_label: dict[str, list[int]] = {}
        for i, det in enumerate(detections):
            by_label.setdefault(det.label, []).append(i)
        tracks_by_label: dict[str, list[_Track]] = {}
        for t in self._tracks:
            tracks_by_label.setdefault(t.label, []).append(t)

        for label in set(by_label) | set(tracks_by_label):
            det_idx = by_label.get(label, [])
            tracks = tracks_by_label.get(label, [])
            matched_tracks: set[int] = set()
            matched_dets: set[int] = set()

            if tracks and det_idx:
                boxes = np.asarray([detections[i].bbox for i in det_idx], dtype=np.float64)
                track_boxes = np.stack([t.bbox for t in tracks])
                iou = _iou_matrix(track_boxes, boxes)
                rows, cols = linear_sum_assignment(-iou)
                for r, c in zip(rows, cols):
                    if iou[r, c] < self._iou_threshold:
                        continue
                    track = tracks[r]
                    det_pos = det_idx[c]
                    track.bbox = np.asarray(detections[det_pos].bbox, dtype=np.float64)
                    track.missed = 0
                    assigned[det_pos] = track.track_id
                    matched_tracks.add(r)
                    matched_dets.add(c)

            for r, track in enumerate(tracks):
                if r in matched_tracks:
                    survivors.append(track)
                    continue
                track.missed += 1
                if track.missed <= self._max_missed:
                    survivors.append(track)

            for c, det_pos in enumerate(det_idx):
                if c in matched_dets:
                    continue
                det = detections[det_pos]
                track = _Track(
                    track_id=self._next_id,
                    label=det.label,
                    bbox=np.asarray(det.bbox, dtype=np.float64),
                    missed=0,
                )
                self._next_id += 1
                survivors.append(track)
                assigned[det_pos] = track.track_id

        self._tracks = survivors
        return [TrackedDetection(track_id=assigned[i], detection=d) for i, d in enumerate(detections)]
