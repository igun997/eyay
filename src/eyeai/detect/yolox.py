"""YOLOX ONNX detector (PDF section 1: local model only fills capability gaps).

The exported ONNX graph returns ``[1, N, 85]`` where each row is
``[cx_offset, cy_offset, log_w, log_h, obj_prob, cls_prob x 80]``. The export
applies sigmoid to objectness and class scores already, but not to box
offsets. Grid decoding (offset, stride, exp for size) and NMS are done here,
mirroring the Megvii YOLOX reference postprocess. Preprocessing is
the YOLOX letterbox: top-left placement on a 114-grey canvas, BGR, no
normalisation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import onnxruntime as ort

from .coco import COCO_CLASSES

_PAD_VALUE = 114
_STRIDES = (8, 16, 32)
_NUM_OUTPUTS = 5 + len(COCO_CLASSES)


@dataclass(frozen=True)
class Detection:
    label: str
    class_id: int
    score: float
    bbox: tuple[float, float, float, float]  # x1,y1,x2,y2 normalised 0..1 of the source frame


def letterbox(frame_bgr: np.ndarray, size: int) -> tuple[np.ndarray, float]:
    """Resize keeping aspect ratio, pad bottom/right with 114. Returns canvas and scale."""
    h, w = frame_bgr.shape[:2]
    r = min(size / h, size / w)
    new_w, new_h = int(w * r), int(h * r)
    resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), _PAD_VALUE, dtype=np.uint8)
    canvas[:new_h, :new_w] = resized
    return canvas, r


def to_input_tensor(frame_bgr: np.ndarray, size: int) -> tuple[np.ndarray, float]:
    canvas, r = letterbox(frame_bgr, size)
    tensor = np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], dtype=np.float32)
    return tensor, r


def make_grids(size: int, strides: tuple[int, ...] = _STRIDES) -> tuple[np.ndarray, np.ndarray]:
    """Grid cell (x, y) and stride for every anchor, in YOLOX output order."""
    grids: list[np.ndarray] = []
    stride_col: list[np.ndarray] = []
    for s in strides:
        n = size // s
        yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        grids.append(np.stack((xv, yv), axis=-1).reshape(-1, 2))
        stride_col.append(np.full((n * n, 1), s, dtype=np.float32))
    return (np.concatenate(grids).astype(np.float32),
            np.concatenate(stride_col).astype(np.float32))


def decode(raw: np.ndarray, grids: np.ndarray, strides: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """raw: (N, 85). Returns xyxy boxes (N, 4) in letterbox pixels and scores (N, 80)."""
    centre = (raw[:, :2] + grids) * strides
    half = np.exp(raw[:, 2:4]) * strides * 0.5
    boxes = np.concatenate([centre - half, centre + half], axis=1)
    scores = raw[:, 4:5] * raw[:, 5:]
    return boxes, scores


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """Greedy NMS. Returns kept indices sorted by descending score."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        if rest.size == 0:
            break
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thr]
    return keep


def postprocess(
    raw: np.ndarray,
    grids: np.ndarray,
    strides: np.ndarray,
    conf_thr: float,
    nms_thr: float,
    class_ids: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Multi-label per-class NMS. Returns (boxes xyxy, scores, class ids) in letterbox pixels."""
    boxes, scores = decode(raw, grids, strides)
    if class_ids is None:
        cols = np.arange(scores.shape[1])
    else:
        cols = class_ids
        scores = scores[:, cols]
    anchor_idx, col = np.nonzero(scores > conf_thr)
    if anchor_idx.size == 0:
        return np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), np.zeros((0,), np.int64)
    sc = scores[anchor_idx, col]
    bx = boxes[anchor_idx]
    cls = cols[col]
    # Shift each class into its own coordinate band so NMS only suppresses same-class boxes.
    span = float(np.abs(bx).max()) * 2.0 + 1.0
    keep = nms(bx + (cls[:, None] * span).astype(np.float32), sc, nms_thr)
    return bx[keep], sc[keep], cls[keep]


class YoloxDetector:
    """Runs a YOLOX ONNX model on BGR frames. Input size is read from the model file."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        conf_thresh: float = 0.25,
        nms_thresh: float = 0.45,
        classes: Iterable[str] | None = None,
        providers: list[str] | None = None,
    ):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"YOLOX model not found: {path}")
        self._session = ort.InferenceSession(str(path), providers=providers or ["CPUExecutionProvider"])
        inp = self._session.get_inputs()[0]
        _, _, h, w = inp.shape
        if not (isinstance(h, int) and isinstance(w, int)) or h != w or h % max(_STRIDES) != 0:
            raise ValueError(f"unsupported YOLOX input shape {inp.shape}; need square, multiple of 32")
        self._input_name = inp.name
        self._size = h
        self._grids, self._strides = make_grids(h)
        out = self._session.get_outputs()[0].shape
        if out[1] != self._grids.shape[0] or out[2] != _NUM_OUTPUTS:
            raise ValueError(f"unexpected YOLOX output shape {out} for input {h}")
        self._conf = conf_thresh
        self._nms = nms_thresh
        self._class_ids: np.ndarray | None = None
        if classes is not None:
            wanted = set(classes)
            unknown = wanted - set(COCO_CLASSES)
            if unknown:
                raise ValueError(f"classes not in COCO-80: {sorted(unknown)}")
            if not wanted:
                raise ValueError("classes filter is empty")
            self._class_ids = np.array(sorted(COCO_CLASSES.index(c) for c in wanted), dtype=np.int64)

    @property
    def input_size(self) -> int:
        return self._size

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            raise ValueError("frame must be HxWx3 BGR")
        h, w = frame_bgr.shape[:2]
        tensor, r = to_input_tensor(frame_bgr, self._size)
        raw = self._session.run(None, {self._input_name: tensor})[0][0]
        boxes, scores, cls = postprocess(
            raw, self._grids, self._strides, self._conf, self._nms, self._class_ids
        )
        dets: list[Detection] = []
        for (x1, y1, x2, y2), s, c in zip(boxes, scores, cls):
            bbox = (
                float(np.clip(x1 / r / w, 0.0, 1.0)),
                float(np.clip(y1 / r / h, 0.0, 1.0)),
                float(np.clip(x2 / r / w, 0.0, 1.0)),
                float(np.clip(y2 / r / h, 0.0, 1.0)),
            )
            dets.append(Detection(label=COCO_CLASSES[int(c)], class_id=int(c),
                                  score=float(s), bbox=bbox))
        return dets
