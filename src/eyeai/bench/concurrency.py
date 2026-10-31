"""Concurrent-stream throughput: how many streams at a target fps does this machine sustain?

Each stream is its own VideoPipeline over the same file with its own detector session
(realistic: one process per camera group), run in parallel threads. A stream "sustains" the
target when its achieved processing fps >= 0.95 * target_fps.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..detect.capability import plan_detection
from ..detect.stream import VideoPipeline
from ..detect.yolox import YoloxDetector


@dataclass
class StreamResult:
    stream: int
    frames: int
    seconds: float
    fps: float


@dataclass
class ConcurrencyResult:
    model: str
    video: str
    streams: int
    target_fps: float
    per_stream: list[StreamResult]
    total_fps: float
    min_stream_fps: float
    sustained: bool
    cpu_percent: float | None
    rss_mb: float | None


def _cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system


def _rss_mb() -> float | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def run_concurrency(
    video: str | Path,
    model_path: str | Path,
    *,
    streams: int,
    target_fps: float = 5.0,
    frames_per_stream: int = 40,
    labels: frozenset[str] = frozenset({"person", "car", "bus", "truck", "motorcycle", "bicycle"}),
    intra_threads: int | None = None,
) -> ConcurrencyResult:
    plan = plan_detection(labels, None)
    providers = None
    detectors = [YoloxDetector(model_path, conf_thresh=0.3, providers=providers) for _ in range(streams)]
    # warm each session once so model load / first-run cost is not counted
    import numpy as np
    size = detectors[0].input_size
    for d in detectors:
        d.detect(np.zeros((size, size, 3), dtype=np.uint8))

    results: list[StreamResult | None] = [None] * streams
    barrier = threading.Barrier(streams)

    def work(i: int) -> None:
        pipe = VideoPipeline(
            video, site_id="bench", device_id=f"cam{i}", detector=detectors[i],
            emit=lambda e: None, plan=plan, target_fps=target_fps, realtime=False,
        )
        barrier.wait()
        t0 = time.perf_counter()
        pipe.run_once(max_frames=frames_per_stream)
        dt = time.perf_counter() - t0
        frames = pipe.status()["frames"]
        results[i] = StreamResult(stream=i, frames=frames, seconds=dt, fps=frames / dt if dt > 0 else 0.0)

    cpu0, wall0 = _cpu_seconds(), time.perf_counter()
    threads = [threading.Thread(target=work, args=(i,)) for i in range(streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - wall0
    cpu = _cpu_seconds() - cpu0

    done = [r for r in results if r is not None]
    total = sum(r.frames for r in done) / wall if wall > 0 else 0.0
    min_fps = min(r.fps for r in done)
    return ConcurrencyResult(
        model=Path(model_path).stem, video=Path(video).name, streams=streams, target_fps=target_fps,
        per_stream=done, total_fps=total, min_stream_fps=min_fps,
        sustained=min_fps >= 0.95 * target_fps,
        cpu_percent=100.0 * cpu / wall if wall > 0 else None, rss_mb=_rss_mb(),
    )


def run_matrix(
    video: str | Path,
    models: list[str | Path],
    stream_counts: list[int],
    out_dir: str | Path,
    *,
    target_fps: float = 5.0,
    frames_per_stream: int = 40,
) -> list[ConcurrencyResult]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[ConcurrencyResult] = []
    for m in models:
        for n in stream_counts:
            rows.append(run_concurrency(video, m, streams=n, target_fps=target_fps,
                                        frames_per_stream=frames_per_stream))
    (out / "concurrency.json").write_text(json.dumps([asdict(r) for r in rows], indent=2))
    lines = [
        f"# Concurrent streams on `{Path(video).name}` (target {target_fps} fps/stream, CPU)\n",
        "| model | streams | total fps | min per-stream fps | sustained | CPU % | RSS MB |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.model} | {r.streams} | {r.total_fps:.1f} | {r.min_stream_fps:.2f} | "
            f"{'yes' if r.sustained else 'no'} | {r.cpu_percent:.0f} | {r.rss_mb:.0f} |"
        )
    (out / "concurrency.md").write_text("\n".join(lines) + "\n")
    return rows
