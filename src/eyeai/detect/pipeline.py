"""Turns local-model detections into normalised Events (provenance LOCAL_MODEL)."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

import numpy as np

from eyeai.schema import Emit, Event, Provenance, utcnow

from .capability import DetectionPlan
from .coco import COCO_CLASSES
from .yolox import Detection


class FrameDetector(Protocol):
    def detect(self, frame_bgr: np.ndarray) -> list[Detection]: ...


class LocalDetectionPipeline:
    """Runs the local detector only when the plan has gap labels, and emits gap-label events."""

    def __init__(
        self,
        *,
        site_id: str,
        device_id: str,
        detector: FrameDetector,
        emit: Emit,
        plan: DetectionPlan,
        model_name: str,
        zone: str | None = None,
    ):
        unknown = plan.gap_labels - set(COCO_CLASSES)
        if unknown:
            raise ValueError(f"gap labels not produced by the local model: {sorted(unknown)}")
        if not site_id or not device_id:
            raise ValueError("site_id and device_id are required")
        self._site_id = site_id
        self._device_id = device_id
        self._detector = detector
        self._emit = emit
        self._plan = plan
        self._model_name = model_name
        self._zone = zone

    @property
    def plan(self) -> DetectionPlan:
        return self._plan

    def process_frame(self, frame_bgr: np.ndarray, ts_utc: datetime | None = None) -> list[Event]:
        if not self._plan.run_local:
            return []
        ts = ts_utc or utcnow()
        h, w = frame_bgr.shape[:2]
        events: list[Event] = []
        for det in self._detector.detect(frame_bgr):
            if det.label not in self._plan.gap_labels:
                continue
            events.append(Event(
                ts_utc=ts,
                site_id=self._site_id,
                device_id=self._device_id,
                kind="object",
                label=det.label,
                confidence=det.score,
                provenance=Provenance.LOCAL_MODEL,
                bbox=det.bbox,
                zone=self._zone,
                attributes={
                    "model": self._model_name,
                    "class_id": det.class_id,
                    "frame_size": [w, h],
                },
            ))
        for ev in events:
            self._emit(ev)
        return events
