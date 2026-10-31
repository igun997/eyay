import pytest

from eyeai.detect.lines import Line


def test_direction_in_and_out():
    line = Line("gate", (0.5, 0.0), (0.5, 1.0))  # vertical, p1 top -> p2 bottom
    assert line.crossing((0.4, 0.5), (0.6, 0.5)) == "in"
    assert line.crossing((0.6, 0.5), (0.4, 0.5)) == "out"


def test_no_crossing_same_side_or_touching():
    line = Line("gate", (0.5, 0.0), (0.5, 1.0))
    assert line.crossing((0.1, 0.5), (0.3, 0.5)) is None
    assert line.crossing((0.5, 0.5), (0.7, 0.5)) is None  # starts on the line


def test_passing_around_the_segment_end_is_ignored():
    line = Line("short", (0.5, 0.4), (0.5, 0.6))
    assert line.crossing((0.4, 0.9), (0.6, 0.9)) is None
    assert line.crossing((0.4, 0.5), (0.6, 0.5)) == "in"


def test_invalid_lines_rejected():
    with pytest.raises(ValueError):
        Line("x", (0.2, 0.2), (0.2, 0.2))
    with pytest.raises(ValueError):
        Line("x", (0.0, 0.0), (1.5, 0.5))
