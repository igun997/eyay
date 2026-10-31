"""Polls `sources` and keeps exactly one SourceRunner per enabled source."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from eyeai.detect.yolox import YoloxDetector

from .config import Settings
from .models import Category, SourceInfo
from .repo import Repository
from .runner import DetectorFactory, SourceRunner

log = logging.getLogger("eyeai.manager")


def yolox_factory(model: str, intra_op_threads: int | None = None) -> DetectorFactory:
    def make(src: SourceInfo):
        if not src.labels:
            raise ValueError("source watches no labels")
        return YoloxDetector(model, conf_thresh=src.min_confidence, classes=src.labels, intra_op_threads=intra_op_threads)

    return make


class WorkerManager:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        detector_factory: DetectorFactory | None = None,
        runner_kwargs: dict | None = None,
    ):
        self.repo = repo
        self.settings = settings
        self._factory = detector_factory or yolox_factory(settings.model, settings.ort_threads)
        self._runner_kwargs = {"target_fps": settings.target_fps, "stall_s": settings.stall_s, **(runner_kwargs or {})}
        self._runners: dict[str, SourceRunner] = {}
        self._categories: dict[str, Category] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def categories(self) -> dict[str, Category]:
        return self._categories

    def runner(self, source_id: str) -> SourceRunner | None:
        with self._lock:
            r = self._runners.get(source_id)
        return r if r and r.alive() else None

    def running(self) -> list[dict]:
        with self._lock:
            return [{"id": i, "code": r.source.code, "status": r.status} for i, r in self._runners.items()]

    def tick(self) -> None:
        sources = {s.id: s for s in self.repo.list_sources()}
        self._categories = self.repo.list_categories()
        with self._lock:
            current = dict(self._runners)
        for sid, runner in current.items():
            src = sources.get(sid)
            if src is None or not src.enabled or src.restart_key != runner.source.restart_key or not runner.alive():
                runner.stop()
                with self._lock:
                    self._runners.pop(sid, None)
                if src is not None and not src.enabled:
                    self.repo.update_source_state(sid, status="pending", last_error=None, last_seen=None, fps=None, inference_ms=None)
                log.info("%s: runner removed", runner.source.code)
            elif src != runner.source:
                runner.set_source(src)
        for sid, src in sources.items():
            if src.enabled and sid not in self._runners:
                r = SourceRunner(src, self.repo, self.categories, self._factory, **self._runner_kwargs)
                with self._lock:
                    self._runners[sid] = r
                r.start()
        self.repo.heartbeat(self.settings.worker_id, len(self._runners), self.settings.model)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - keep polling through DB outages
                log.exception("poll failed")
            self._stop.wait(self.settings.poll_s)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            runners = list(self._runners.values())
            self._runners.clear()
        for r in runners:
            r._stop.set()
        for r in runners:
            r.stop()
