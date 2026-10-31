"""One source: reconnecting capture -> latest-frame reader -> detector -> tracker -> EventDecider."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Mapping

import cv2
import numpy as np

from eyeai.detect.tracker import IouTracker

from .decision import EventDecider
from .models import Category, SourceInfo
from .render import LiveDet, snapshot_jpeg
from .repo import Repository

log = logging.getLogger("eyeai.runner")

# rtsp over TCP is far more robust than UDP across NAT/docker; harmless for http(s) inputs.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

OPEN_TIMEOUT_MS = 8000
READ_TIMEOUT_MS = 8000
DETS_TTL_S = 1.5  # boxes older than this are not drawn on live frames


def open_capture(src: SourceInfo) -> cv2.VideoCapture:
    return cv2.VideoCapture(
        src.url, cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS, cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS],
    )


class LatestFrameReader(threading.Thread):
    """Drains the capture as fast as it delivers so inference always sees the newest frame."""

    def __init__(self, cap: cv2.VideoCapture, pace_fps: float | None = None):
        super().__init__(daemon=True, name="frame-reader")
        self._cap = cap
        self._pace = pace_fps
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._ts = 0.0
        self.ended = threading.Event()
        self._stop = threading.Event()

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                ok, frame = self._cap.read()
                if not ok or frame is None:
                    break
                with self._lock:
                    self._frame, self._seq, self._ts = frame, self._seq + 1, time.monotonic()
                if self._pace:
                    time.sleep(1.0 / self._pace)
        except Exception:  # noqa: BLE001
            log.exception("reader crashed")
        finally:
            self.ended.set()
            self._cap.release()

    def latest(self) -> tuple[int, np.ndarray | None, float]:
        with self._lock:
            return self._seq, self._frame, self._ts

    def stop(self) -> None:
        self._stop.set()


DetectorFactory = Callable[[SourceInfo], "object"]  # returns an object with .detect(frame) -> list[Detection]


class SourceRunner:
    def __init__(
        self,
        source: SourceInfo,
        repo: Repository,
        categories: Callable[[], Mapping[str, Category]],
        detector_factory: DetectorFactory,
        *,
        target_fps: float = 5.0,
        stall_s: float = 10.0,
        capture_factory: Callable[[SourceInfo], cv2.VideoCapture] = open_capture,
        pace_fps: float | None = None,
        state_every_s: float = 5.0,
    ):
        self.source = source
        self._repo = repo
        self._decider = EventDecider(repo, source, categories)
        self._make_detector = detector_factory
        self._target_fps = target_fps
        self._stall_s = stall_s
        self._open = capture_factory
        self._pace = pace_fps
        self._state_every = state_every_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"src-{source.code}")
        self._lock = threading.Lock()
        self._reader: LatestFrameReader | None = None
        self._dets: list[LiveDet] = []
        self._dets_at = 0.0
        self.status = "connecting"
        self.events_written = 0

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        r = self._reader
        if r:
            r.stop()
        self._thread.join(timeout)

    def alive(self) -> bool:
        return self._thread.is_alive()

    def set_source(self, source: SourceInfo) -> None:
        """Apply changes that do not need a reconnect (name, enabled flag)."""
        self.source = source
        self._decider.set_source(source)

    # -- live view -----------------------------------------------------------------------------
    def live(self) -> tuple[int, np.ndarray | None, list[LiveDet]]:
        r = self._reader
        if r is None:
            return 0, None, []
        seq, frame, _ = r.latest()
        with self._lock:
            dets = list(self._dets) if time.monotonic() - self._dets_at < DETS_TTL_S else []
        return seq, frame, dets

    # -- internals -----------------------------------------------------------------------------
    def _state(self, status: str, error: str | None = None, *, seen: bool = False, fps=None, ms=None) -> None:
        self.status = status
        try:
            self._repo.update_source_state(
                self.source.id, status=status, last_error=error,
                last_seen=datetime.now(timezone.utc) if seen else None, fps=fps, inference_ms=ms,
            )
        except Exception:  # noqa: BLE001 - DB hiccup must not kill the stream
            log.exception("%s: state update failed", self.source.code)

    def _run(self) -> None:
        src = self.source
        log.info("%s: starting (%s %s, labels=%s)", src.code, src.kind, _redact(src.url), ",".join(src.labels))
        self._state("connecting")
        detector = None
        was_online = False
        backoff = 2.0
        while not self._stop.is_set():
            if detector is None:
                try:
                    detector = self._make_detector(src)
                except Exception as e:  # noqa: BLE001 - bad labels / missing model
                    log.error("%s: detector unavailable: %s", src.code, e)
                    self._state("error", f"detector: {e}")
                    self._stop.wait(30)
                    continue
            cap = None
            try:
                cap = self._open(src)
                opened = cap.isOpened()
            except Exception as e:  # noqa: BLE001
                opened, err = False, str(e)
            else:
                err = "cannot open stream"
            if not opened:
                if cap is not None:
                    cap.release()
                log.warning("%s: %s (retry in %.0fs)", src.code, err, backoff)
                self._state("offline" if was_online else "error", err)
                self._stop.wait(backoff)
                backoff = min(30.0, backoff * 2)
                continue

            reader = LatestFrameReader(cap, self._pace or _hls_pace(src, cap))
            self._reader = reader
            reader.start()
            try:
                lost = self._serve(reader, detector)
            finally:
                reader.stop()
                reader.join(2)
                self._reader = None
            if self._stop.is_set():
                break
            if lost == "online":
                was_online, backoff = True, 2.0
            log.warning("%s: stream lost, reconnecting", src.code)
            self._state("offline", "stream lost")
            self._stop.wait(backoff)
        log.info("%s: stopped", src.code)

    def _serve(self, reader: LatestFrameReader, detector) -> str:
        """Returns 'online' if at least one frame was processed before the stream dropped, else 'never'."""
        tracker = IouTracker()
        last_seq = 0
        last_infer = 0.0
        last_new = time.monotonic()
        window_start = time.monotonic()
        window_n, window_ms = 0, 0.0
        online = False
        min_gap = 1.0 / self._target_fps
        while not self._stop.is_set():
            seq, frame, ts = reader.latest()
            now_m = time.monotonic()
            if seq != last_seq:
                last_new = now_m
            elif reader.ended.is_set() or now_m - last_new > self._stall_s:
                break
            if frame is None or seq == last_seq or now_m - last_infer < min_gap:
                self._stop.wait(0.01)
                continue
            last_seq, last_infer = seq, now_m
            if not online:
                online = True
                self._state("online", None, seen=True)
            t0 = time.perf_counter()
            dets = detector.detect(frame)
            infer_ms = (time.perf_counter() - t0) * 1000
            tracked = tracker.update(dets)
            with self._lock:
                self._dets = [(t.detection.label, t.detection.score, t.detection.bbox) for t in tracked]
                self._dets_at = time.monotonic()
            recs = self._decider.process(
                tracked, lambda f=frame: snapshot_jpeg(f), datetime.now(timezone.utc)
            )
            self.events_written += len(recs)
            window_n += 1
            window_ms += infer_ms
            if time.monotonic() - window_start >= self._state_every:
                el = time.monotonic() - window_start
                self._state("online", None, seen=True, fps=round(window_n / el, 2), ms=round(window_ms / window_n, 1))
                window_start, window_n, window_ms = time.monotonic(), 0, 0.0
        return "online" if online else "never"


def _hls_pace(src: SourceInfo, cap: cv2.VideoCapture) -> float | None:
    """HLS delivers a whole segment at once; without pacing the latest-frame reader would see one frame per
    segment. Read at slightly above the nominal fps so it never falls behind a live playlist."""
    if src.kind != "m3u8":
        return None
    fps = cap.get(cv2.CAP_PROP_FPS)
    return (fps if 1 < fps <= 60 else 25.0) * 1.1


def _redact(url: str) -> str:
    """Hide credentials in rtsp://user:pass@host/..."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.rsplit('@', 1)[1]}"
    return url
