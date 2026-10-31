"""Server-side classification of event snapshots with a System One decision model.

A separate thread (own DB pool) claims events with pipeline='queued', crops the stored snapshot around the
detection box and asks `POST {CLASSIFIER_URL}/v1/systemone` (Ollama serving e.g. Liquid AI `d1`) one `choice`
question: person / vehicle / false_alarm. The answer is only stored; it never dismisses an event or touches
incidents, and detection threads never wait on this module.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Collection, Protocol

import cv2
import numpy as np

log = logging.getLogger("eyeai.classifier")

LABELS = ("person", "vehicle", "false_alarm")
STATE = "Snapshot from a security camera, cropped around a possible detection."
QUESTION = {
    "class": {
        "type": "choice",
        "instructions": "What does this security-camera snapshot show?",
        "criteria": {
            "person": "A person is clearly visible",
            "vehicle": "A car, truck, bus or motorcycle is clearly visible",
            "false_alarm": "No person or vehicle: shadow, lighting, animal, goods or an empty scene",
        },
    }
}
CROP_MARGIN = 0.15
MIN_CROP_PX = 40
STATS_EVERY_S = 5.0
RECOVER_EVERY_S = 60.0


class ClassifierError(Exception):
    """Anything that makes one classification attempt fail (network, HTTP, malformed answer, bad image)."""


@dataclass(frozen=True)
class ClassifierConfig:
    url: str | None = None
    model: str = "d1"
    timeout_s: float = 60.0
    poll_s: float = 2.0
    max_attempts: int = 3

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    @property
    def label(self) -> str | None:
        """Value of worker_heartbeats.classifier, e.g. "d1 @ http://classifier:11434"."""
        return f"{self.model} @ {self.url}" if self.url else None

    @classmethod
    def from_env(cls, env: "os._Environ[str] | dict[str, str] | None" = None) -> "ClassifierConfig":
        e = os.environ if env is None else env
        url = (e.get("CLASSIFIER_URL") or "").strip().rstrip("/") or None
        return cls(
            url=url,
            model=e.get("CLASSIFIER_MODEL") or "d1",
            timeout_s=float(e.get("CLASSIFIER_TIMEOUT_S") or 60),
            poll_s=float(e.get("CLASSIFIER_POLL_S") or 2),
            max_attempts=int(e.get("CLASSIFIER_MAX_ATTEMPTS") or 3),
        )


@dataclass(frozen=True)
class ClassifyJob:
    event_id: str
    bbox: dict[str, float] | None
    jpeg: bytes | None


@dataclass(frozen=True)
class Classification:
    label: str
    confidence: float  # probability of `label`
    probs: dict[str, float]
    model: str
    ms: int


class ClassifierRepository(Protocol):
    def claim_queued_event(self, exclude: Collection[str] = ()) -> ClassifyJob | None: ...

    def store_classification(self, event_id: str, result: Classification) -> None: ...

    def store_classification_failure(self, event_id: str, error: str, *, requeue: bool) -> None: ...

    def skip_all_queued(self) -> int: ...

    def recover_stuck_classifying(self, older_than_s: float) -> int: ...

    def classifier_stats(self) -> dict[str, int]: ...


def crop_for_classification(jpeg: bytes, bbox: dict[str, float] | None) -> bytes:
    """Crop the snapshot to bbox (+15% of the larger side on each side, clamped); full frame if the crop is tiny."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ClassifierError("snapshot is not a decodable image")
    H, W = img.shape[:2]
    out = img
    if bbox:
        x1, y1 = bbox["x"] * W, bbox["y"] * H
        x2, y2 = x1 + bbox["w"] * W, y1 + bbox["h"] * H
        m = CROP_MARGIN * max(x2 - x1, y2 - y1)
        cx1, cy1 = max(0, int(x1 - m)), max(0, int(y1 - m))
        cx2, cy2 = min(W, int(round(x2 + m))), min(H, int(round(y2 + m)))
        if cx2 - cx1 >= MIN_CROP_PX and cy2 - cy1 >= MIN_CROP_PX:
            out = img[cy1:cy2, cx1:cx2]
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ClassifierError("could not re-encode crop")
    return buf.tobytes()


class DecisionClient:
    """Minimal client for the System One wire (`/v1/systemone`) with one image and one choice question."""

    def __init__(self, cfg: ClassifierConfig):
        if not cfg.url:
            raise ValueError("CLASSIFIER_URL is not set")
        self.cfg = cfg

    def request_body(self, jpeg: bytes) -> dict:
        return {
            "model": self.cfg.model,
            "state": STATE,
            "images": [base64.b64encode(jpeg).decode("ascii")],
            "questions": QUESTION,
        }

    def classify(self, jpeg: bytes) -> Classification:
        req = urllib.request.Request(
            f"{self.cfg.url}/v1/systemone",
            data=json.dumps(self.request_body(jpeg)).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(200).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise ClassifierError(f"HTTP {e.code} from classifier {detail}".strip()) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ClassifierError(f"classifier unreachable: {getattr(e, 'reason', e)}") from e
        except ValueError as e:
            raise ClassifierError(f"classifier returned invalid JSON: {e}") from e
        ms = int((time.monotonic() - t0) * 1000)
        return self.parse(payload, ms)

    def parse(self, payload: dict, ms: int) -> Classification:
        try:
            ans = payload["answers"]["class"]
            choice = ans["choice"]
            probs = {k: float(v) for k, v in ans["probabilities"].items()}
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ClassifierError(f"unexpected classifier answer: {str(payload)[:150]}") from e
        if choice not in LABELS or choice not in probs:
            raise ClassifierError(f"unknown class {choice!r}")
        return Classification(label=choice, confidence=probs[choice], probs=probs, model=self.cfg.model, ms=ms)


@dataclass
class ClassifierService:
    """The classifier thread. `run_once` is one claim+classify step (what the tests drive)."""

    repo: ClassifierRepository
    cfg: ClassifierConfig
    client: DecisionClient | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    _attempts: dict[str, int] = field(default_factory=dict, init=False)
    _retry_at: dict[str, float] = field(default_factory=dict, init=False)
    _stats: dict = field(default_factory=dict, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.cfg.enabled and self.client is None:
            self.client = DecisionClient(self.cfg)

    # ---- one step -------------------------------------------------------------------------------------------
    def run_once(self, now: float | None = None) -> bool:
        """Classify at most one queued event. Returns True when an event was processed (success or failure)."""
        now = time.monotonic() if now is None else now
        backoff = [i for i, t in self._retry_at.items() if t > now]
        job = self.repo.claim_queued_event(exclude=backoff)
        if job is None:
            return False
        try:
            if job.jpeg is None:
                self.repo.store_classification_failure(job.event_id, "event has no snapshot", requeue=False)
                return True
            result = self.client.classify(crop_for_classification(job.jpeg, job.bbox))  # type: ignore[union-attr]
        except ClassifierError as e:
            n = self._attempts.get(job.event_id, 0) + 1
            if n < self.cfg.max_attempts:
                self._attempts[job.event_id] = n
                self._retry_at[job.event_id] = now + min(30.0, self.cfg.poll_s * (2 ** n))
                log.warning("event %s classification failed (attempt %d/%d): %s", job.event_id, n, self.cfg.max_attempts, e)
                self.repo.store_classification_failure(job.event_id, str(e), requeue=True)
            else:
                log.error("event %s classification failed for good after %d attempts: %s", job.event_id, n, e)
                self._attempts.pop(job.event_id, None)
                self._retry_at.pop(job.event_id, None)
                self.repo.store_classification_failure(job.event_id, str(e), requeue=False)
            return True
        self._attempts.pop(job.event_id, None)
        self._retry_at.pop(job.event_id, None)
        self.repo.store_classification(job.event_id, result)
        log.info("event %s -> %s %.2f (%d ms)", job.event_id, result.label, result.confidence, result.ms)
        return True

    # ---- thread ---------------------------------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "enabled": self.cfg.enabled,
            "model": self.cfg.model if self.cfg.enabled else None,
            "url": self.cfg.url,
            "queued": self._stats.get("queued", 0),
            "failed_recent": self._stats.get("failed_recent", 0),
        }

    def _refresh_stats(self) -> None:
        try:
            self._stats = self.repo.classifier_stats()
        except Exception:  # noqa: BLE001 - /health must not depend on the DB being up
            log.debug("classifier stats unavailable", exc_info=True)

    def run_forever(self) -> None:
        last_stats = last_recover = 0.0
        if self.cfg.enabled:
            try:
                n = self.repo.recover_stuck_classifying(2 * self.cfg.timeout_s)
                if n:
                    log.info("requeued %d events stuck in 'classifying'", n)
            except Exception:  # noqa: BLE001
                log.exception("recovering stuck events failed")
            last_recover = time.monotonic()
        while not self.stop.is_set():
            try:
                if self.cfg.enabled:
                    busy = self.run_once()
                    if time.monotonic() - last_recover > RECOVER_EVERY_S:
                        self.repo.recover_stuck_classifying(2 * self.cfg.timeout_s)
                        last_recover = time.monotonic()
                else:
                    n = self.repo.skip_all_queued()
                    if n:
                        log.info("no classifier configured: %d events marked 'skipped'", n)
                    busy = False
                if time.monotonic() - last_stats > STATS_EVERY_S:
                    self._refresh_stats()
                    last_stats = time.monotonic()
            except Exception:  # noqa: BLE001 - keep the loop alive through DB hiccups
                log.exception("classifier iteration failed")
                busy = False
            if not busy:
                self.stop.wait(self.cfg.poll_s)

    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="classifier")
        self._thread.start()
        return self._thread

    def shutdown(self) -> None:
        self.stop.set()
        if self._thread:
            self._thread.join(timeout=5)
