"""Frame annotation in the design-system style (accent box, filled label tab, accent-ink text)."""
from __future__ import annotations

from typing import Iterable, Sequence

import cv2
import numpy as np

ACCENT_BGR = (216, 159, 74)   # #4A9FD8
INK_BGR = (28, 18, 6)         # #06121C

LiveDet = tuple[str, float, tuple[float, float, float, float]]  # label, score, x1y1x2y2 normalised


def resize_max_width(frame: np.ndarray, max_width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    return cv2.resize(frame, (max_width, max(1, round(h * max_width / w))), interpolation=cv2.INTER_AREA)


def annotate(frame: np.ndarray, dets: Iterable[LiveDet], max_width: int = 960) -> np.ndarray:
    img = resize_max_width(frame, max_width).copy()
    h, w = img.shape[:2]
    scale = max(0.45, w / 1600)
    thick = max(2, round(w / 480))
    for label, score, (x1, y1, x2, y2) in dets:
        p1 = (int(x1 * w), int(y1 * h))
        p2 = (int(x2 * w), int(y2 * h))
        cv2.rectangle(img, p1, p2, ACCENT_BGR, thick)
        text = f"{label.upper()} {score:.2f}"
        (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        top = max(0, p1[1] - th - base - 4)
        cv2.rectangle(img, (p1[0], top), (p1[0] + tw + 8, top + th + base + 4), ACCENT_BGR, -1)
        cv2.putText(img, text, (p1[0] + 4, top + th + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, INK_BGR, 1, cv2.LINE_AA)
    return img


def encode_jpeg(img: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()


def snapshot_jpeg(frame: np.ndarray, max_width: int = 1280, quality: int = 85) -> tuple[bytes, int, int]:
    img = resize_max_width(frame, max_width)
    return encode_jpeg(img, quality), img.shape[1], img.shape[0]
