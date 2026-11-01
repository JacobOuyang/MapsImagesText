"""Routing around danger (evac/grid.py), on a synthetic open grid. Offline.

    .venv/bin/python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evac import geo, grid  # noqa: E402

CENTRE = (40.6760, -74.0080)


def walk(g, a, b, kind=None):
    path = g.route(g.cell_of(a), {g.cell_of(b)}, kind)
    return [g.cell_center(*c) for c in path]


def east(m):
    """A point m metres east of the centre."""
    return (CENTRE[0], CENTRE[1] + m / (111320 * 0.7585))


class Danger(unittest.TestCase):
    def test_blocked_spot_is_walked_around(self):
        g = grid.Grid(CENTRE, 300, 300, 120)
        a, b = east(-200), east(200)
        straight = walk(g, a, b)
        self.assertLess(min(geo.haversine_m(p, CENTRE) for p in straight), 10)  # the straight line passes the centre
        g.block_circle(CENTRE, 40)
        around = walk(g, a, b)
        self.assertGreater(min(geo.haversine_m(p, CENTRE) for p in around), 35)

    def test_flood_pushes_harder_than_fire(self):
        flood, fire = grid.Grid(CENTRE, 300, 300, 120), grid.Grid(CENTRE, 300, 300, 120)
        for g in (flood, fire):
            g.add_hazard_circle(CENTRE, 120)
        a, b = east(-250), east(250)
        closest = lambda path: min(geo.haversine_m(p, CENTRE) for p in path)
        self.assertGreater(closest(walk(flood, a, b, "flood")), closest(walk(fire, a, b, "fire")))
        self.assertGreater(closest(walk(flood, a, b, "flood")), 100)  # nearly all the way round the 120 m zone


if __name__ == "__main__":
    unittest.main()
