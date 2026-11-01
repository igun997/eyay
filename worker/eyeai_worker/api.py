from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse

from .classifier import ClassifierService
from .config import Settings
from .manager import WorkerManager
from .render import annotate, encode_jpeg
from .repo import PgRepository

log = logging.getLogger("eyeai.api")
BOUNDARY = "frame"
MAX_LIVE_FPS = 8
MAX_WIDTH = 960


def create_app(
    settings: Settings,
    manager: WorkerManager | None = None,
    repo: PgRepository | None = None,
    classifier: ClassifierService | None = None,
) -> FastAPI:
    own = manager is None
    cls_repo: PgRepository | None = None
    if own:
        repo = PgRepository(settings.database_url)
        manager = WorkerManager(repo, settings)
        # the classifier gets its own small pool so detection and classification never contend for connections
        cls_repo = PgRepository(settings.database_url, max_size=2)
        classifier = ClassifierService(cls_repo, settings.classifier)
    mgr = manager

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        thread = None
        if own:
            def boot():
                repo.wait_ready(mgr._stop)
                mgr.run_forever()

            def boot_classifier():
                cls_repo.wait_ready(classifier.stop)
                classifier.run_forever()

            thread = threading.Thread(target=boot, daemon=True, name="manager")
            thread.start()
            threading.Thread(target=boot_classifier, daemon=True, name="classifier").start()
        yield
        if own:
            classifier.stop.set()
            mgr.stop()
            if repo:
                repo.close()
            if cls_repo:
                cls_repo.close()

    app = FastAPI(title="eyeai-worker", lifespan=lifespan)

    def _runner(source_id: str):
        try:
            uuid.UUID(source_id)
        except ValueError:
            raise HTTPException(404, "unknown source") from None
        r = mgr.runner(source_id)
        if r is None:
            raise HTTPException(404, "source is not running")
        return r

    def _render(runner) -> tuple[int, bytes | None]:
        seq, frame, dets = runner.live()
        if frame is None:
            return seq, None
        watched = set(runner.source.labels)
        return seq, encode_jpeg(annotate(frame, [d for d in dets if d[0] in watched], MAX_WIDTH))

    @app.get("/health")
    def health():
        return {
            "ok": True, "worker_id": mgr.settings.worker_id, "model": mgr.settings.model, "sources": mgr.running(),
            "classifier": classifier.status() if classifier else {"enabled": False, "model": None, "url": None, "queued": 0, "failed_recent": 0},
        }

    @app.get("/snapshot/{source_id}.jpg")
    async def snapshot(source_id: str):
        r = _runner(source_id)
        _, jpeg = await run_in_threadpool(_render, r)
        if jpeg is None:
            raise HTTPException(503, "no frame yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/live/{source_id}.mjpg")
    async def live(source_id: str, request: Request):
        r = _runner(source_id)
        deadline = time.monotonic() + 6
        while r.live()[1] is None:  # wait for the first frame before committing to a 200
            if time.monotonic() > deadline:
                raise HTTPException(503, "no frame yet")
            await asyncio.sleep(0.1)

        async def frames():
            last_key = None
            period = 1.0 / MAX_LIVE_FPS
            while not await request.is_disconnected():
                t0 = time.monotonic()
                if mgr.runner(source_id) is None:
                    return
                seq, _, dets = r.live()
                key = (seq, tuple((d[0], round(d[1], 2)) for d in dets))
                if key != last_key:
                    _, jpeg = await run_in_threadpool(_render, r)
                    if jpeg is not None:
                        last_key = key
                        yield (
                            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                            + jpeg + b"\r\n"
                        )
                await asyncio.sleep(max(0.0, period - (time.monotonic() - t0)))

        return StreamingResponse(
            frames(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app
