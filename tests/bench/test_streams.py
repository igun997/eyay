from pathlib import Path

import pytest

from eyeai.bench.streams import BenchStream, default_streams, run_streams


def make_layout(root: Path, videos: list[str], models: list[str]) -> None:
    (root / "samples").mkdir(parents=True)
    (root / "models").mkdir(parents=True)
    for v in videos:
        (root / "samples" / v).write_bytes(b"")
    for m in models:
        (root / "models" / m).write_bytes(b"")


def test_default_streams_cross_videos_with_tiny_and_nano(tmp_path):
    make_layout(
        tmp_path,
        videos=["car-detection.mp4", "store-aisle-detection.mp4"],
        models=["yolox_tiny.onnx", "yolox_nano.onnx"],
    )
    streams = default_streams(tmp_path)
    assert len(streams) == 4
    names = [s.name for s in streams]
    assert names == [
        "car-detection:yolox_tiny",
        "car-detection:yolox_nano",
        "store-aisle-detection:yolox_tiny",
        "store-aisle-detection:yolox_nano",
    ]
    tiny = streams[0]
    assert tiny.model_name == "yolox_tiny"
    assert tiny.model_path == tmp_path / "models" / "yolox_tiny.onnx"
    assert tiny.video_path == tmp_path / "samples" / "car-detection.mp4"


def test_default_streams_empty_when_no_samples(tmp_path):
    (tmp_path / "samples").mkdir()
    assert default_streams(tmp_path) == []


def test_run_streams_rejects_empty_list(tmp_path):
    with pytest.raises(ValueError):
        run_streams([], tmp_path)


def test_run_streams_missing_model_fails_before_loading(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    stream = BenchStream(
        name="v:yolox_tiny",
        video_path=video,
        model_path=tmp_path / "missing.onnx",
        model_name="yolox_tiny",
    )
    with pytest.raises(FileNotFoundError, match="model not found"):
        run_streams([stream], tmp_path / "out")


def test_run_streams_missing_video_fails_before_loading(tmp_path):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"")
    stream = BenchStream(
        name="gone:yolox_tiny",
        video_path=tmp_path / "gone.mp4",
        model_path=model,
        model_name="yolox_tiny",
    )
    with pytest.raises(FileNotFoundError, match="video not found"):
        run_streams([stream], tmp_path / "out")
