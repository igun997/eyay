import pytest

from eyeai.detect.tracker import IouTracker
from eyeai.detect.yolox import Detection


def det(label: str, bbox, score: float = 0.9) -> Detection:
    return Detection(label=label, class_id=0, score=score, bbox=tuple(bbox))


def test_same_object_keeps_track_id_across_frames():
    tr = IouTracker(iou_threshold=0.3)
    first = tr.update([det("car", (0.1, 0.1, 0.3, 0.3))])
    second = tr.update([det("car", (0.11, 0.1, 0.31, 0.3))])
    assert first[0].track_id == second[0].track_id


def test_two_separated_objects_get_distinct_ids():
    tr = IouTracker()
    out = tr.update([det("car", (0.0, 0.0, 0.2, 0.2)), det("car", (0.6, 0.6, 0.8, 0.8))])
    assert out[0].track_id != out[1].track_id


def test_ids_persist_when_detection_order_swaps():
    tr = IouTracker()
    a = (0.0, 0.0, 0.2, 0.2)
    b = (0.6, 0.6, 0.8, 0.8)
    first = tr.update([det("car", a), det("car", b)])
    second = tr.update([det("car", b), det("car", a)])
    assert second[0].track_id == first[1].track_id
    assert second[1].track_id == first[0].track_id


def test_different_labels_never_share_a_track():
    tr = IouTracker()
    box = (0.2, 0.2, 0.4, 0.4)
    first = tr.update([det("car", box)])
    second = tr.update([det("person", box)])
    assert second[0].track_id != first[0].track_id


def test_below_threshold_starts_new_track():
    tr = IouTracker(iou_threshold=0.5)
    first = tr.update([det("car", (0.0, 0.0, 0.2, 0.2))])
    second = tr.update([det("car", (0.15, 0.0, 0.35, 0.2))])
    assert second[0].track_id != first[0].track_id


def test_track_survives_missed_frames_within_limit():
    tr = IouTracker(max_missed=2)
    box = (0.2, 0.2, 0.4, 0.4)
    first = tr.update([det("car", box)])
    tr.update([])
    tr.update([])
    back = tr.update([det("car", box)])
    assert back[0].track_id == first[0].track_id


def test_track_dropped_after_too_many_misses_and_id_not_reused():
    tr = IouTracker(max_missed=1)
    box = (0.2, 0.2, 0.4, 0.4)
    first = tr.update([det("car", box)])
    tr.update([])
    tr.update([])
    assert tr.active_track_ids == []
    again = tr.update([det("car", box)])
    assert again[0].track_id > first[0].track_id


def test_output_preserves_input_order_and_detection():
    tr = IouTracker()
    dets = [det("car", (0.0, 0.0, 0.2, 0.2), 0.7), det("person", (0.5, 0.5, 0.6, 0.9), 0.4)]
    out = tr.update(dets)
    assert [o.detection for o in out] == dets


def test_empty_frame_returns_empty():
    assert IouTracker().update([]) == []


@pytest.mark.parametrize("thr", [0.0, -0.1, 1.5])
def test_rejects_invalid_iou_threshold(thr):
    with pytest.raises(ValueError):
        IouTracker(iou_threshold=thr)


def test_rejects_negative_max_missed():
    with pytest.raises(ValueError):
        IouTracker(max_missed=-1)
