"""Benchmark streams: (video, model) pairs run through run_video_benchmark and written as reports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..detect.yolox import YoloxDetector
from .metrics import GroundTruth
from .runner import BenchReport, run_video_benchmark, write_report


@dataclass(frozen=True)
class BenchStream:
    name: str
    video_path: Path
    model_path: Path
    model_name: str


def default_streams(data_dir: str | Path = "data") -> list[BenchStream]:
    """Every sample video crossed with the yolox tiny and nano models."""
    root = Path(data_dir)
    videos = sorted((root / "samples").glob("*.mp4"))
    models = [("yolox_tiny", root / "models" / "yolox_tiny.onnx"), ("yolox_nano", root / "models" / "yolox_nano.onnx")]
    streams: list[BenchStream] = []
    for video in videos:
        for model_name, model_path in models:
            streams.append(
                BenchStream(
                    name=f"{video.stem}:{model_name}",
                    video_path=video,
                    model_path=model_path,
                    model_name=model_name,
                )
            )
    return streams


def run_streams(
    streams: list[BenchStream],
    out_dir: str | Path,
    *,
    max_frames: int | None = None,
    warmup: int = 5,
    ground_truth: dict[str, dict[int, list[GroundTruth]]] | None = None,
    iou_threshold: float = 0.5,
    conf_thresh: float = 0.25,
) -> list[tuple[str, Path, BenchReport]]:
    """Runs each stream and writes its report under out_dir/<stream name>/.

    ground_truth maps a video stem to its per-frame ground truth. A stream whose video has no entry runs without accuracy scoring.
    """
    if not streams:
        raise ValueError("no streams to run")
    results: list[tuple[str, Path, BenchReport]] = []
    for stream in streams:
        if not stream.model_path.is_file():
            raise FileNotFoundError(f"model not found: {stream.model_path}")
        if not stream.video_path.is_file():
            raise FileNotFoundError(f"video not found: {stream.video_path}")
        detector = YoloxDetector(stream.model_path, conf_thresh=conf_thresh)
        gt = (ground_truth or {}).get(stream.video_path.stem)
        report = run_video_benchmark(
            stream.video_path,
            detector,
            model_name=stream.model_name,
            max_frames=max_frames,
            warmup=warmup,
            ground_truth=gt,
            iou_threshold=iou_threshold,
        )
        target = Path(out_dir) / stream.name.replace(":", "__")
        report_path = write_report(report, target)
        results.append((stream.name, report_path, report))
    return results
