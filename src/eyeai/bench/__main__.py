"""CLI: python -m eyeai.bench --video V --model M [--classes a,b] [--gt G.json]"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eyeai.detect import YoloxDetector

from .runner import load_ground_truth, run_video_benchmark, write_report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eyeai.bench")
    p.add_argument("--video", required=True)
    p.add_argument("--model", required=True, help="path to YOLOX ONNX file")
    p.add_argument("--classes", default=None, help="comma-separated COCO labels to keep")
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--nms", type=float, default=0.45)
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--gt", default=None, help="ground-truth JSON for precision/recall")
    p.add_argument("--iou", type=float, default=0.5)
    p.add_argument("--out", default="data/bench")
    args = p.parse_args(argv)

    classes = [c.strip() for c in args.classes.split(",") if c.strip()] if args.classes else None
    detector = YoloxDetector(args.model, conf_thresh=args.conf, nms_thresh=args.nms, classes=classes)
    gt = load_ground_truth(args.gt) if args.gt else None
    report = run_video_benchmark(
        args.video,
        detector,
        model_name=Path(args.model).stem,
        max_frames=args.max_frames,
        warmup=args.warmup,
        ground_truth=gt,
        iou_threshold=args.iou,
    )
    path = write_report(report, args.out)
    print(json.dumps({"report": str(path), "throughput_fps": report.throughput_fps,
                      "p95_total_ms": report.total.p95_ms}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
