"""Polygon zones in normalised frame coordinates, with bbox-to-zone membership."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

Point = tuple[float, float]
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Zone:
    name: str
    polygon: tuple[Point, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("zone name must be non-empty")
        if len(self.polygon) < 3:
            raise ValueError("zone polygon needs at least 3 points")
        for x, y in self.polygon:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"zone {self.name!r} point ({x}, {y}) outside [0, 1]")

    def contains_point(self, x: float, y: float) -> bool:
        """Ray-casting point-in-polygon. Points on the boundary may fall either way."""
        inside = False
        pts = self.polygon
        n = len(pts)
        j = n - 1
        for i in range(n):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y):
                x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
                if x < x_cross:
                    inside = not inside
            j = i
        return inside

    def contains_bbox_foot(self, bbox: Box) -> bool:
        """Tests the bottom-centre of a normalised xyxy box, the ground-contact point of a person or vehicle."""
        x1, _, x2, y2 = bbox
        return self.contains_point((x1 + x2) / 2.0, y2)


def zone_for_bbox(zones: Sequence[Zone], bbox: Box) -> str | None:
    """Returns the name of the first zone containing the bbox foot point, or None."""
    for zone in zones:
        if zone.contains_bbox_foot(bbox):
            return zone.name
    return None
