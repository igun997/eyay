"""Live/file video pipeline: frames -> local detector (gap labels only) -> tracker -> zone/line events.

Events are provenance LOCAL_MODEL. The detector runs only for labels that no native source
covers (see `plan_detection`); if native covers everything the pipeline refuses to start so
inference is never duplicated (PDF section 1).
"""
from __future__ import annotations

import os
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from ..schema import Emit, Event, Provenance, utcnow
from .capability import DetectionPlan
from .lines import Line
from .pipeline import FrameDetector
from .tracker import IouTracker
from .zones import Zone, zone_for_bbox


@dataclass
class _TrackState:
    label: str
    first_ts: datetime
    last_ts: datetime
    hits: int = 0
    confirmed: bool = False
    zone: str | None = None
    zone_since: datetime | None = None
    last_update_ts: datetime | None = None
    last_foot: tuple[float, float] | None = None
    last_bbox: tuple[float, float, float, float] | None = None
    last_score: float = 0.0


@dataclass
class _Stats:
    frames: int = 0
    dropped: int = 0
    infer_ms: list[float] = field(default_factory=list)
    window_start: datetime | None = None


class VideoPipeline:
    def __init__(
        self,
        source: str | int | Path,
        *,
        site_id: str,
        device_id: str,
        detector: FrameDetector,
        emit: Emit,
        plan: DetectionPlan,
        model_name: str = "yolox",
        zones: Sequence[Zone] = (),
        lines: Sequence[Line] = (),
        tracker: IouTracker | None = None,
        target_fps: float = 5.0,
        min_hits: int = 2,
        update_s: float = 2.0,
        heartbeat_s: float = 10.0,
        start_time: datetime | None = None,
        realtime: bool | None = None,
        snapshot_dir: str | Path | None = None,
    ):
        if not plan.run_local:
            raise ValueError("native sources cover every required label; local inference would duplicate them")
        if target_fps <= 0:
            raise ValueError("target_fps must be > 0")
        if start_time is not None and start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware")
        self._source = source
        self._site_id = site_id
        self._device_id = device_id
        self._detector = detector
        self._emit = emit
        self._plan = plan
        self._model = model_name
        self._zones = tuple(zones)
        self._lines = tuple(lines)
        self._tracker = tracker or IouTracker()
        self._target_fps = target_fps
        self._min_hits = max(1, min_hits)
        self._update_s = update_s
        self._heartbeat_s = heartbeat_s
        self._start_time = start_time
        is_file = isinstance(source, Path) or (isinstance(source, str) and "://" not in source)
        self._realtime = (not is_file) if realtime is None else realtime
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        if self._snapshot_dir:
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._states: dict[int, _TrackState] = {}
        self._stats = _Stats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_forever, name=f"video:{self._device_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def status(self) -> dict:
        return {
            "device_id": self._device_id,
            "running": bool(self._thread and self._thread.is_alive()),
            "frames": self._stats.frames,
            "last_error": self.last_error,
        }

    def _run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._run_capture(max_frames=None)
                if not self._realtime:
                    return  # file finished
                raise ConnectionError("stream ended")
            except Exception as exc:  # reconnect, never raise into the host thread
                self.last_error = f"{type(exc).__name__}: {exc}"
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)

    def run_once(self, max_frames: int | None = None) -> None:
        """Synchronous run over the source until EOF or max_frames (tests, files)."""
        self._run_capture(max_frames=max_frames)

    # ---- capture loop ---------------------------------------------------
    def _open(self) -> cv2.VideoCapture:
        src = self._source
        if isinstance(src, str) and src.lower().startswith("rtsp://"):
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
            cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        else:
            cap = cv2.VideoCapture(str(src) if not isinstance(src, int) else src)
        if not cap.isOpened():
            raise ConnectionError(f"cannot open source: {src}")
        return cap

    def _run_capture(self, max_frames: int | None) -> None:
        cap = self._open()
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not fps or fps <= 0 or fps != fps:
                fps = 30.0
            step = max(1, round(fps / self._target_fps))
            origin = self._start_time or utcnow()
            index = -1
            processed = 0
            while not self._stop.is_set():
                ok = cap.grab()
                if not ok:
                    break
                index += 1
                if index % step != 0:
                    self._stats.dropped += 1
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    break
                ts = utcnow() if self._realtime else origin + timedelta(seconds=index / fps)
                self.process_frame(frame, ts)
                processed += 1
                if max_frames is not None and processed >= max_frames:
                    break
        finally:
            cap.release()

    # ---- per-frame logic ------------------------------------------------
    def process_frame(self, frame: np.ndarray, ts: datetime) -> None:
        with self._lock:
            t0 = time.perf_counter()
            dets = [d for d in self._detector.detect(frame) if d.label in self._plan.gap_labels]
            self._stats.infer_ms.append((time.perf_counter() - t0) * 1000.0)
            self._stats.frames += 1
            if self._stats.window_start is None:
                self._stats.window_start = ts

            tracked = self._tracker.update(dets)
            seen: set[int] = set()
            for td in tracked:
                seen.add(td.track_id)
                self._handle_track(td.track_id, td.detection, ts, frame)

            active = set(self._tracker.active_track_ids)
            for tid in [t for t in self._states if t not in active]:
                self._close_track(tid, ts)

            self._maybe_heartbeat(ts)

    def _handle_track(self, tid: int, det, ts: datetime, frame: np.ndarray) -> None:
        st = self._states.get(tid)
        if st is None:
            st = self._states[tid] = _TrackState(label=det.label, first_ts=ts, last_ts=ts)
        st.hits += 1
        st.last_ts = ts
        st.last_bbox = det.bbox
        st.last_score = det.score
        x1, _, x2, y2 = det.bbox
        foot = ((x1 + x2) / 2.0, y2)

        if not st.confirmed:
            if st.hits < self._min_hits:
                st.last_foot = foot
                return
            st.confirmed = True
            st.zone = zone_for_bbox(self._zones, det.bbox)
            st.zone_since = ts if st.zone else None
            st.last_foot = foot  # crossings before confirmation are not counted
            self._object(st, tid, ts, frame, first=True)
            if st.zone:
                self._zone_event("zone_enter", st, tid, ts)
            return

        if st.last_foot is not None:
            for line in self._lines:
                direction = line.crossing(st.last_foot, foot)
                if direction:
                    self._emit_ev(Event(
                        ts_utc=ts, site_id=self._site_id, device_id=self._device_id,
                        kind="line_cross", label=st.label, confidence=st.last_score,
                        provenance=Provenance.LOCAL_MODEL, track_id=tid, bbox=det.bbox, zone=st.zone,
                        attributes={"line": line.name, "direction": direction, "model": self._model},
                    ))
        st.last_foot = foot

        zone = zone_for_bbox(self._zones, det.bbox)
        if zone != st.zone:
            if st.zone:
                self._zone_event("zone_exit", st, tid, ts)
            st.zone = zone
            st.zone_since = ts if zone else None
            if zone:
                self._zone_event("zone_enter", st, tid, ts)

        if st.last_update_ts is None or (ts - st.last_update_ts).total_seconds() >= self._update_s:
            self._object(st, tid, ts, frame, first=False)

    def _close_track(self, tid: int, ts: datetime) -> None:
        st = self._states.pop(tid)
        if st.confirmed and st.zone:
            self._zone_event("zone_exit", st, tid, st.last_ts, reason="track_lost")

    def _object(self, st: _TrackState, tid: int, ts: datetime, frame: np.ndarray, first: bool) -> None:
        st.last_update_ts = ts
        clip = None
        if first and self._snapshot_dir:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = st.last_bbox
            crop = frame[int(y1 * h):max(int(y2 * h), int(y1 * h) + 1), int(x1 * w):max(int(x2 * w), int(x1 * w) + 1)]
            path = self._snapshot_dir / f"{self._device_id}_{tid}_{int(ts.timestamp() * 1000)}.jpg"
            if crop.size and cv2.imwrite(str(path), crop):
                clip = str(path)
        self._emit_ev(Event(
            ts_utc=ts, site_id=self._site_id, device_id=self._device_id,
            kind="object", label=st.label, confidence=st.last_score,
            provenance=Provenance.LOCAL_MODEL, track_id=tid, bbox=st.last_bbox, zone=st.zone, clip_ref=clip,
            attributes={"model": self._model, "first": first, "age_s": (ts - st.first_ts).total_seconds()},
        ))

    def _zone_event(self, kind: str, st: _TrackState, tid: int, ts: datetime, reason: str | None = None) -> None:
        zone = st.zone
        dwell = (ts - st.zone_since).total_seconds() if st.zone_since else 0.0
        attrs = {"dwell_s": dwell, "model": self._model}
        if reason:
            attrs["reason"] = reason
        self._emit_ev(Event(
            ts_utc=ts, site_id=self._site_id, device_id=self._device_id,
            kind=kind, label=st.label, confidence=st.last_score,
            provenance=Provenance.LOCAL_MODEL, track_id=tid, bbox=st.last_bbox, zone=zone, attributes=attrs,
        ))

    def _maybe_heartbeat(self, ts: datetime) -> None:
        s = self._stats
        if s.window_start is None:
            return
        elapsed = (ts - s.window_start).total_seconds()
        if elapsed < self._heartbeat_s:
            return
        ms = sorted(s.infer_ms)
        p95 = ms[min(len(ms) - 1, int(0.95 * len(ms)))] if ms else 0.0
        self._emit_ev(Event(
            ts_utc=ts, site_id=self._site_id, device_id=self._device_id,
            kind="camera_frame_stats", provenance=Provenance.LOCAL_MODEL,
            attributes={
                "fps": s.frames / elapsed if elapsed > 0 else 0.0,
                "inference_ms_p50": statistics.median(ms) if ms else 0.0,
                "inference_ms_p95": p95,
                "frames": s.frames,
                "dropped": s.dropped,
                "model": self._model,
            },
        ))
        s.frames = 0
        s.dropped = 0
        s.infer_ms = []
        s.window_start = ts

    def _emit_ev(self, ev: Event) -> None:
        try:
            self._emit(ev)
        except Exception as exc:  # a failing consumer must not kill capture
            self.last_error = f"emit failed: {type(exc).__name__}: {exc}"
