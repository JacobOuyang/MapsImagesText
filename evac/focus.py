"""What a map should show: only the roads the reply is about, in a view fitted to them.

Every road in the frame used to be drawn, so the route had to be picked out of hundreds of lines. Now a Scene
carries `focus`, the few roads worth drawing, and every renderer draws only those (the picture map adds the
rest as faint context):

  walked    the roads the route runs along
  turns     at each turn, the cross street (the landmark you'd look for)
  crossed   main roads the route crosses (walking) / main roads in view (alerts, for orientation)
  closures  every road named in a closure (alerts)
  anchors   the road you are on and the destination's road

`fit` picks the view: a frame around the points that matter instead of a fixed square centred on you.
"""
import math

import numpy as np

from . import geo, grid
from .osm import norm_road

WALKED_M = 14      # a route point this close to a road is walking along it
LANDMARK_M = 35    # a turn's cross street must pass this close to the turn
TURN_DEG = 35      # heading change that counts as a turn
MAX_TURNS = 6
NEAR_M = 90        # a landmark street is drawn this far either side of its turn
CLIP_FRAC = 0.2    # one walking step: roads are drawn only this close to the route (a fifth of the view's half-width)


def _rank(rd):
    return grid.ROAD_RANK.get(rd["kind"].replace("_link", ""), 1)


class _Segments:
    """All road segments in local metres, for nearest-road queries."""

    def __init__(self, roads, proj):
        a, b, owner = [], [], []
        for i, rd in enumerate(roads):
            xy = [proj.to_xy(p) for p in rd["pts"]]
            for p, q in zip(xy, xy[1:]):
                a.append(p)
                b.append(q)
                owner.append(i)
        self.a = np.array(a, float).reshape(-1, 2)
        self.b = np.array(b, float).reshape(-1, 2)
        self.owner = np.array(owner, int)

    def dist(self, p):
        """Distance from point p (x, y) to every segment."""
        d = self.b - self.a
        t = np.clip(((p[0] - self.a[:, 0]) * d[:, 0] + (p[1] - self.a[:, 1]) * d[:, 1]) / np.maximum((d ** 2).sum(1), 1e-9), 0, 1)
        return np.hypot(self.a[:, 0] + t * d[:, 0] - p[0], self.a[:, 1] + t * d[:, 1] - p[1])

    def roads_near(self, p, m):
        """{road index: distance} for roads passing within m of p."""
        d = self.dist(p)
        out = {}
        for i in np.nonzero(d <= m)[0]:
            o = int(self.owner[i])
            out[o] = min(out.get(o, 1e9), float(d[i]))
        return out


def turns(route_xy):
    """Indices of route points where the heading changes by TURN_DEG or more (after smoothing out the
    rasterisation staircase by simplifying the line)."""
    keep = _simplify(route_xy, 12)
    out = []
    for i0, i1, i2 in zip(keep, keep[1:], keep[2:]):
        (x0, y0), (x1, y1), (x2, y2) = route_xy[i0], route_xy[i1], route_xy[i2]
        h1, h2 = math.atan2(y1 - y0, x1 - x0), math.atan2(y2 - y1, x2 - x1)
        if abs((math.degrees(h2 - h1) + 180) % 360 - 180) >= TURN_DEG:
            out.append(i1)
    return out


def _simplify(pts, tol):
    """Douglas-Peucker; returns the kept indices."""
    if len(pts) < 3:
        return list(range(len(pts)))
    stack, keep = [(0, len(pts) - 1)], {0, len(pts) - 1}
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = pts[i], pts[j]
        L = math.hypot(bx - ax, by - ay) or 1e-9
        best, k = 0, None
        for m in range(i + 1, j):
            x, y = pts[m]
            d = abs((bx - ax) * (ay - y) - (ax - x) * (by - ay)) / L
            if d > best:
                best, k = d, m
        if k is not None and best > tol:
            keep.add(k)
            stack += [(i, k), (k, j)]
    return sorted(keep)


def select(scene, minimal=False):
    """Scene -> the roads to draw (a subset of scene.roads). minimal=True (one walking step): only the streets the
    route walks along and the cross street at its turn."""
    roads = scene.roads
    if not roads:
        return []
    proj = geo.LocalProjection(scene.center or scene.you)
    segs = _Segments(roads, proj)
    rxy = [proj.to_xy(p) for p in scene.route]
    keep = set()

    # Roads walked along: the nearest road to at least 2 route points. (In an old town several streets pass
    # within a few metres of the route; only the nearest one is the one walked.)
    # A route drawn on a coarse grid sits up to ~0.7 cell off the street centreline: scale the test to its spacing.
    hops = sorted(math.hypot(bx - ax, by - ay) for (ax, ay), (bx, by) in zip(rxy, rxy[1:]))
    walked_m = max(WALKED_M, 0.8 * hops[len(hops) // 2]) if hops else WALKED_M
    hits, crossed = {}, set()
    for p in rxy:
        d = segs.dist(p)
        if len(d) and d.min() <= walked_m:
            o = int(segs.owner[d.argmin()])
            hits[o] = hits.get(o, 0) + 1
            crossed |= {int(segs.owner[i]) for i in np.nonzero(d <= 6)[0]}
    walked = {o for o, n in hits.items() if n >= 2 or len(rxy) < 6}
    keep |= walked
    walked_names = {norm_road(roads[o]["name"]) for o in walked if roads[o]["name"]}

    def near_by_name(i, p, m):
        """Road i plus the other OSM pieces of the same street within m of point p."""
        return {i} | (_same_name(roads, i) & set(segs.roads_near(p, m)) if roads[i]["name"] else set())

    # The other pieces of each walked street along the route, so it reads as one street.
    for p in rxy[::3]:
        keep |= {o for o in segs.roads_near(p, walked_m) if roads[o]["name"] and norm_road(roads[o]["name"]) in walked_names}

    # Landmarks: the named cross street at each turn, biggest first, drawn near the turn.
    for ti in turns(rxy)[:MAX_TURNS]:
        near = segs.roads_near(rxy[ti], LANDMARK_M)
        cands = [o for o in near if roads[o]["name"] and norm_road(roads[o]["name"]) not in walked_names]
        if cands:
            best = max(cands, key=lambda o: (_rank(roads[o]), -near[o]))
            keep |= near_by_name(best, rxy[ti], NEAR_M)

    if minimal:  # only the parts near the route: a square mapped as one closed way would be drawn all round
        out = []
        for i in sorted(keep):
            out += _clip(roads[i], rxy, proj, max(walked_m, CLIP_FRAC * scene.half_m))
        return out

    # Main roads: crossed by the route when walking; all in view for an alert (the roads people know).
    if scene.alert:
        keep |= {i for i, rd in enumerate(roads) if _rank(rd) >= 3}
    else:
        keep |= {o for o in crossed if _rank(roads[o]) >= 3}

    # Closures: the whole named road, so the closed stretch has its street around it.
    closed = {norm_road(c["road"]) for c in scene.closures}
    keep |= {i for i, rd in enumerate(roads) if rd["name"] and norm_road(rd["name"]) in closed}

    # Anchors: the street you are on (alerts: you may not be routed anywhere) and the destination's.
    for p in ([scene.you] if scene.alert else []) + ([scene.goal[1]] if scene.goal else []):
        xy = proj.to_xy(p)
        near = segs.roads_near(xy, 40)
        named = [o for o in near if roads[o]["name"]]
        if named:
            keep |= near_by_name(min(named, key=near.get), xy, NEAR_M)
    return [roads[i] for i in sorted(keep)]


def _clip(rd, rxy, proj, m):
    """The pieces of a road within m of the route, as road dicts of their own."""
    xy = [proj.to_xy(p) for p in rd["pts"]]
    dense = []
    for (ax, ay), (bx, by) in zip(xy, xy[1:]):
        n = max(1, int(math.hypot(bx - ax, by - ay) / 5))
        dense += [(ax + (bx - ax) * k / n, ay + (by - ay) * k / n) for k in range(n)]
    dense.append(xy[-1])
    R = np.array(rxy, float).reshape(-1, 2)
    D = np.array(dense)
    near = np.hypot(D[:, None, 0] - R[None, :, 0], D[:, None, 1] - R[None, :, 1]).min(1) <= m
    pieces, run = [], []
    for p, ok in zip(dense, near):
        if ok:
            run.append(proj.to_latlon(p))
        elif run:
            pieces.append(run)
            run = []
    pieces.append(run)
    return [dict(rd, pts=piece) for piece in pieces if len(piece) > 1]


def _same_name(roads, i):
    n = roads[i]["name"]
    return {i} if not n else {j for j, rd in enumerate(roads) if rd["name"] and norm_road(rd["name"]) == norm_road(n)}


def ahead(route, metres):
    """The route's first `metres` (all of it if shorter)."""
    out, walked = route[:1], 0.0
    for a, b in zip(route, route[1:]):
        if walked >= metres:
            break
        walked += geo.haversine_m(a, b)
        out.append(b)
    return out


def fit(points, pad_m=None, min_half_m=None, aspect=0.9):
    """A square view (centre, half-width) that holds `points` with a margin. Height is checked against
    `aspect` because the narrow monospace map is that much shorter than it is wide. By default the margin is 15%
    of what is shown, so a 40 m step and a 2 km walk are framed the same way."""
    proj = geo.LocalProjection(points[0])
    xy = [proj.to_xy(p) for p in points]
    xs, ys = [x for x, _ in xy], [y for _, y in xy]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max((max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2 / aspect)
    pad = pad_m if pad_m is not None else max(10, 0.15 * span)
    return proj.to_latlon((cx, cy)), max(min_half_m if min_half_m is not None else 30, span + pad)
