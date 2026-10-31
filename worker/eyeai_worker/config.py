from __future__ import annotations

import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    worker_id: str
    model: str
    port: int
    target_fps: float
    poll_s: float
    stall_s: float
    ort_threads: int | None

    @classmethod
    def from_env(cls) -> "Settings":
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("DATABASE_URL is required")
        return cls(
            database_url=dsn,
            worker_id=os.environ.get("WORKER_ID") or f"worker-{socket.gethostname()}",
            model=os.environ.get("EYEAI_MODEL", "/app/data/models/yolox_s.onnx"),
            port=int(os.environ.get("PORT", "8090")),
            target_fps=float(os.environ.get("EYEAI_TARGET_FPS", "5")),
            poll_s=float(os.environ.get("EYEAI_POLL_S", "5")),
            stall_s=float(os.environ.get("EYEAI_STALL_S", "10")),
            ort_threads=int(os.environ["EYEAI_ORT_THREADS"]) if os.environ.get("EYEAI_ORT_THREADS") else None,
        )
