"""Video file frame source yielding BGR frames with tz-aware UTC timestamps."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from ..schema import utcnow


@dataclass(frozen=True)
class Frame:
    index: int
    ts_utc: datetime
    image_bgr: np.ndarray


def iter_video_frames(
    path: str | Path,
    *,
    start_utc: datetime | None = None,
    max_frames: int | None = None,
) -> Iterator[Frame]:
    """Yields frames in order. Timestamps are start_utc + index / fps.

    If the container reports no usable FPS, timestamps fall back to a 30 fps grid.
    """
    if start_utc is not None and start_utc.tzinfo is None:
        raise ValueError("start_utc must be timezone-aware")
    origin = start_utc if start_utc is not None else utcnow()
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=timezone.utc)

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video: {path}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or fps != fps:
            fps = 30.0
        index = 0
        while True:
            if max_frames is not None and index >= max_frames:
                break
            ok, image = cap.read()
            if not ok:
                break
            ts = origin + timedelta(seconds=index / fps)
            yield Frame(index=index, ts_utc=ts, image_bgr=image)
            index += 1
    finally:
        cap.release()
