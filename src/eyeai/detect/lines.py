"""Directional counting lines in normalised frame coordinates."""
from __future__ import annotations

from dataclasses import dataclass

Point = tuple[float, float]


def _side(p1: Point, p2: Point, p: Point) -> float:
    """Sign of the cross product (p2-p1) x (p-p1): >0 left of p1->p2, <0 right."""
    return (p2[0] - p1[0]) * (p[1] - p1[1]) - (p2[1] - p1[1]) * (p[0] - p1[0])


@dataclass(frozen=True)
class Line:
    """Counting line p1->p2. 'in' = movement from the side where (p2-p1)x(p-p1) > 0 to the side where it
    is < 0; 'out' is the reverse. In image coordinates (y down) a line drawn top->bottom therefore
    counts left->right on screen as 'in'.

    The crossing only counts when the movement segment actually intersects the line segment,
    so passing around the end of the line is ignored.
    """

    name: str
    p1: Point
    p2: Point

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("line name must be non-empty")
        if self.p1 == self.p2:
            raise ValueError("line endpoints must differ")
        for x, y in (self.p1, self.p2):
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"line {self.name!r} point ({x}, {y}) outside [0, 1]")

    def crossing(self, prev: Point, cur: Point) -> str | None:
        """Returns 'in', 'out' or None for the movement prev->cur."""
        s_prev = _side(self.p1, self.p2, prev)
        s_cur = _side(self.p1, self.p2, cur)
        # a point exactly on the line belongs to the non-positive side, so a track that lands
        # exactly on the line while passing through still counts once
        if (s_prev > 0.0) == (s_cur > 0.0):
            return None
        # movement straddles the infinite line; check the line segment also straddles the movement
        t_prev = _side(prev, cur, self.p1)
        t_cur = _side(prev, cur, self.p2)
        if (t_prev > 0.0) == (t_cur > 0.0):
            return None
        return "in" if s_prev > 0.0 else "out"
