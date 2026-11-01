import numpy as np

from eyeai_worker.render import ACCENT_BGR, annotate, encode_jpeg, snapshot_jpeg


def test_annotate_draws_accent_box_and_resizes():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    img = annotate(frame, [("person", 0.91, (0.4, 0.3, 0.6, 0.8))], max_width=960)
    assert img.shape == (540, 960, 3)
    assert tuple(img[int(0.5 * 540), int(0.4 * 960)]) == ACCENT_BGR       # left edge of the box
    assert frame.sum() == 0                                               # input untouched


def test_jpeg_helpers():
    frame = np.full((720, 1280, 3), 90, np.uint8)
    jpeg, w, h = snapshot_jpeg(frame, max_width=640)
    assert (w, h) == (640, 360) and jpeg[:2] == b"\xff\xd8"
    assert encode_jpeg(frame)[:2] == b"\xff\xd8"
