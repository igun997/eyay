from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest

from eyeai.detect.video import iter_video_frames


def write_clip(path, n_frames: int, fps: float = 10.0, size=(64, 48)) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert writer.isOpened()
    for i in range(n_frames):
        frame = np.full((size[1], size[0], 3), i * 10 % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_yields_all_frames_with_index_and_utc_timestamps(tmp_path):
    clip = tmp_path / "clip.avi"
    write_clip(clip, n_frames=5, fps=10.0)
    start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    frames = list(iter_video_frames(clip, start_utc=start))
    assert [f.index for f in frames] == [0, 1, 2, 3, 4]
    assert all(f.ts_utc.tzinfo is not None for f in frames)
    assert frames[0].ts_utc == start
    assert frames[3].ts_utc == start + timedelta(seconds=0.3)
    assert frames[0].image_bgr.shape == (48, 64, 3)


def test_max_frames_limits_output(tmp_path):
    clip = tmp_path / "clip.avi"
    write_clip(clip, n_frames=6)
    frames = list(iter_video_frames(clip, start_utc=datetime.now(timezone.utc), max_frames=2))
    assert len(frames) == 2


def test_default_start_is_timezone_aware(tmp_path):
    clip = tmp_path / "clip.avi"
    write_clip(clip, n_frames=1)
    (frame,) = list(iter_video_frames(clip))
    assert frame.ts_utc.tzinfo is not None


def test_rejects_naive_start(tmp_path):
    clip = tmp_path / "clip.avi"
    write_clip(clip, n_frames=1)
    with pytest.raises(ValueError):
        list(iter_video_frames(clip, start_utc=datetime(2025, 1, 1)))


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(iter_video_frames(tmp_path / "nope.mp4"))
