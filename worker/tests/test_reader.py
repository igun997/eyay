import threading
import time

import numpy as np

from eyeai_worker.runner import LatestFrameReader


class FakeCap:
    """Delivers frames until released, like a live capture."""

    def __init__(self, frames=None):
        self.frames, self.released = frames, threading.Event()

    def read(self):
        if self.frames is not None and not self.frames:
            return False, None
        time.sleep(0.005)
        return True, (self.frames.pop(0) if self.frames else np.zeros((4, 4, 3), np.uint8))

    def release(self):
        self.released.set()


def test_stop_then_join_with_timeout_does_not_raise():
    # regression: the reader used to shadow Thread._stop, so join(timeout) raised "'Event' object is not callable"
    r = LatestFrameReader(FakeCap())
    r.start()
    while r.latest()[0] == 0:
        time.sleep(0.005)
    r.stop()
    r.join(2)
    assert not r.is_alive() and r.ended.is_set()


def test_reader_exposes_the_newest_frame_and_releases_the_capture_at_eof():
    frames = [np.full((2, 2, 3), i, np.uint8) for i in range(3)]
    cap = FakeCap(frames)
    r = LatestFrameReader(cap)
    r.start()
    r.join(2)
    seq, frame, _ = r.latest()
    assert seq == 3 and frame is not None and int(frame[0, 0, 0]) == 2
    assert r.ended.is_set() and cap.released.is_set()
