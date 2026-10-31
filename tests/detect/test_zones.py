import pytest

from eyeai.detect.zones import Zone, zone_for_bbox

SQUARE = Zone(name="door", polygon=((0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)))


def test_point_inside_and_outside():
    assert SQUARE.contains_point(0.5, 0.5)
    assert not SQUARE.contains_point(0.1, 0.5)
    assert not SQUARE.contains_point(0.5, 0.95)


def test_concave_polygon_excludes_notch():
    # U-shape: the notch between x 0.4..0.6 from the top is outside the zone
    u = Zone(
        name="u",
        polygon=((0.0, 0.0), (0.4, 0.0), (0.4, 0.6), (0.6, 0.6), (0.6, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
    )
    assert u.contains_point(0.2, 0.3)
    assert not u.contains_point(0.5, 0.3)


def test_bbox_uses_bottom_centre_foot_point():
    # Box straddles the top edge but its foot (bottom-centre) is inside.
    assert SQUARE.contains_bbox_foot((0.4, 0.1, 0.6, 0.5))
    # Box lies entirely inside horizontally but its foot is below the zone.
    assert not SQUARE.contains_bbox_foot((0.4, 0.5, 0.6, 0.95))


def test_zone_for_bbox_returns_first_match_or_none():
    other = Zone(name="aisle", polygon=((0.0, 0.0), (1.0, 0.0), (1.0, 0.1), (0.0, 0.1)))
    assert zone_for_bbox([other, SQUARE], (0.4, 0.3, 0.6, 0.7)) == "door"
    assert zone_for_bbox([SQUARE], (0.0, 0.0, 0.1, 0.1)) is None
    assert zone_for_bbox([], (0.4, 0.3, 0.6, 0.7)) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "", "polygon": ((0, 0), (1, 0), (1, 1))},
        {"name": "x", "polygon": ((0, 0), (1, 0))},
        {"name": "x", "polygon": ((0, 0), (1.2, 0), (1, 1))},
    ],
)
def test_rejects_invalid_zone(kwargs):
    with pytest.raises(ValueError):
        Zone(**kwargs)
