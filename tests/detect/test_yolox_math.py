import numpy as np
import pytest

from eyeai.detect.coco import COCO_CLASSES
from eyeai.detect.yolox import (
    decode,
    letterbox,
    make_grids,
    nms,
    postprocess,
    to_input_tensor,
)


def test_coco_has_80_classes_in_yolox_order():
    assert len(COCO_CLASSES) == 80
    assert COCO_CLASSES[0] == "person"
    assert COCO_CLASSES[2] == "car"
    assert len(set(COCO_CLASSES)) == 80


def test_letterbox_keeps_aspect_and_pads_with_114():
    frame = np.full((100, 200, 3), 7, dtype=np.uint8)  # h=100, w=200
    canvas, r = letterbox(frame, 416)
    assert canvas.shape == (416, 416, 3)
    assert r == pytest.approx(416 / 200)
    new_h = int(100 * r)
    assert np.all(canvas[:new_h, :416] == 7)
    assert np.all(canvas[new_h:, :] == 114)


def test_to_input_tensor_is_nchw_float32_without_normalisation():
    frame = np.full((50, 50, 3), 200, dtype=np.uint8)
    tensor, r = to_input_tensor(frame, 128)
    assert tensor.shape == (1, 3, 128, 128)
    assert tensor.dtype == np.float32
    assert tensor.max() == pytest.approx(200.0)
    assert r == pytest.approx(128 / 50)


def test_make_grids_anchor_count_matches_yolox_outputs():
    grids, strides = make_grids(640)
    assert grids.shape == (8400, 2)
    assert strides.shape == (8400, 1)
    grids416, _ = make_grids(416)
    assert grids416.shape == (3549, 2)


def test_make_grids_order_and_strides():
    grids, strides = make_grids(64)  # levels 8x8 (s=8), 4x4 (s=16), 2x2 (s=32)
    assert grids.shape[0] == 8 * 8 + 4 * 4 + 2 * 2
    assert tuple(grids[0]) == (0.0, 0.0)
    assert tuple(grids[1]) == (1.0, 0.0)  # x varies fastest within a level
    assert strides[0, 0] == 8
    assert strides[-1, 0] == 32


def test_decode_single_anchor_centre_size_and_scores():
    grids = np.array([[2.0, 3.0]], dtype=np.float32)
    strides = np.array([[8.0]], dtype=np.float32)
    raw = np.zeros((1, 85), dtype=np.float32)
    raw[0, 0:2] = 0.5    # xy offset
    raw[0, 2:4] = 0.0    # exp(0)=1 -> full width = stride
    raw[0, 4] = 1.0      # objectness probability
    raw[0, 5] = 1.0      # class 0 probability
    raw[0, 6] = 0.5      # class 1 probability
    boxes, scores = decode(raw, grids, strides)
    cx, cy = (2.0 + 0.5) * 8, (3.0 + 0.5) * 8
    assert boxes[0] == pytest.approx([cx - 4, cy - 4, cx + 4, cy + 4], abs=1e-3)
    assert scores.shape == (1, 80)
    assert scores[0, 0] == pytest.approx(1.0, abs=1e-6)
    assert scores[0, 1] == pytest.approx(0.5, abs=1e-6)


def _place(raw, grids, strides, idx, centre, obj, classes):
    """Set anchor idx so decode() puts its centre at `centre` with box size = stride."""
    stride = float(strides[idx, 0])
    raw[idx, 0:2] = np.array(centre, dtype=np.float32) / stride - grids[idx]
    raw[idx, 2:4] = 0.0
    raw[idx, 4] = obj
    for cls_id, prob in classes.items():
        raw[idx, 5 + cls_id] = prob


def test_nms_suppresses_overlap_and_keeps_best():
    boxes = np.array([
        [0, 0, 10, 10],
        [1, 1, 11, 11],     # IoU ~0.68 with box 0
        [50, 50, 60, 60],   # far away
    ], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, 0.5) == [0, 2]
    assert nms(boxes, scores, 0.9) == [0, 1, 2]


def test_nms_single_box():
    boxes = np.array([[0, 0, 5, 5]], dtype=np.float32)
    assert nms(boxes, np.array([0.3], dtype=np.float32), 0.45) == [0]


def test_postprocess_multilabel_and_class_aware_nms():
    grids, strides = make_grids(64)
    raw = np.zeros((grids.shape[0], 85), dtype=np.float32)
    # Anchors 0 and 1 both decode to box [0,0,8,8] (stride-8 cells, centre (4,4)).
    _place(raw, grids, strides, 0, (4, 4), 0.9, {0: 0.9, 2: 0.8})  # person + car
    _place(raw, grids, strides, 1, (4, 4), 0.9, {0: 0.7})          # person, same box
    boxes, scores, cls = postprocess(raw, grids, strides, 0.5, 0.45, None)
    # Multi-label keeps car on the shared box; class-aware NMS drops the duplicate person.
    assert sorted(cls.tolist()) == [0, 2]
    assert np.all(scores > 0.5)
    assert boxes.shape == (2, 4)


def test_postprocess_keeps_same_class_when_boxes_do_not_overlap():
    grids, strides = make_grids(64)
    raw = np.zeros((grids.shape[0], 85), dtype=np.float32)
    _place(raw, grids, strides, 0, (4, 4), 0.9, {0: 0.9})    # box [0,0,8,8]
    _place(raw, grids, strides, 1, (12, 4), 0.9, {0: 0.9})   # box [8,0,16,8], IoU 0
    _, _, cls = postprocess(raw, grids, strides, 0.5, 0.45, None)
    assert cls.tolist() == [0, 0]


def test_postprocess_respects_class_filter():
    grids, strides = make_grids(64)
    raw = np.zeros((grids.shape[0], 85), dtype=np.float32)
    _place(raw, grids, strides, 0, (4, 4), 0.9, {2: 0.9})    # car only
    _, _, cls = postprocess(raw, grids, strides, 0.5, 0.45, np.array([0]))
    assert cls.size == 0
    _, _, cls = postprocess(raw, grids, strides, 0.5, 0.45, np.array([2]))
    assert cls.tolist() == [2]


def test_postprocess_no_detections_returns_empty_arrays():
    grids, strides = make_grids(64)
    raw = np.zeros((grids.shape[0], 85), dtype=np.float32)
    boxes, scores, cls = postprocess(raw, grids, strides, 0.5, 0.45, None)
    assert boxes.shape == (0, 4)
    assert scores.shape == (0,)
    assert cls.shape == (0,)
