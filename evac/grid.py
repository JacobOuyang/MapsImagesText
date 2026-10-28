"""Rasterize terrain + roads + hazard into a character grid, route out of danger, render ASCII."""
import heapq
import math

import numpy as np

from .geo import LocalProjection
from .osm import find_intersection, norm_road

OPEN, ROAD, VEG, BUILDING, WATER = range(5)
ROAD_RANK = {"motorway": 6, "trunk": 5, "primary": 4, "secondary": 3, "tertiary": 2}


def _within(x, y, span, slack=0.02):
    """True if (x, y) projects between the span's two ends (None = whole road)."""
    if span is None:
        return True
    (ax, ay), (bx, by) = span
    vx, vy = bx - ax, by - ay
    t = ((x - ax) * vx + (y - ay) * vy) / ((vx * vx + vy * vy) or 1)
    return -slack <= t <= 1 + slack


class Grid:
    def __init__(self, center, half_w_m, half_h_m, cols):
        self.proj = LocalProjection(center)
        self.cols = cols
        self.cw = 2 * half_w_m / cols
        self.ch = 2 * self.cw  # monospace glyphs are ~2x taller than wide
        self.rows = max(8, int(math.ceil(2 * half_h_m / self.ch)))
        self.x0, self.y1 = -half_w_m, self.rows * self.ch / 2  # top-left corner in meters
        self.terrain = np.full((self.rows, cols), OPEN, np.int8)
        self.road_name = [[""] * cols for _ in range(self.rows)]
        self.road_rank = np.zeros((self.rows, cols), np.int8)
        self.blocked = np.zeros((self.rows, cols), bool)
        self.hazard = np.zeros((self.rows, cols), bool)
        self.hazard_depth = np.zeros((self.rows, cols))  # 0 at zone edge -> 1 at center

    # --- coordinates ---
    def cell_of(self, latlon):
        x, y = self.proj.to_xy(latlon)
        c, r = int((x - self.x0) // self.cw), int((self.y1 - y) // self.ch)
        return (r, c) if 0 <= r < self.rows and 0 <= c < self.cols else None

    def cell_center(self, r, c):
        return self.proj.to_latlon((self.x0 + (c + 0.5) * self.cw, self.y1 - (r + 0.5) * self.ch))

    def bbox(self):
        s, w = self.proj.to_latlon((self.x0, self.y1 - self.rows * self.ch))
        n, e = self.proj.to_latlon((self.x0 + self.cols * self.cw, self.y1))
        return s, w, n, e

    # --- layers ---
    def add_terrain(self, masks):
        """masks: {class: HxW bool} from segment.py, image covering exactly self.bbox()."""
        h, w = next(iter(masks.values())).shape
        for r in range(self.rows):
            for c in range(self.cols):
                ys = slice(r * h // self.rows, max(r * h // self.rows + 1, (r + 1) * h // self.rows))
                xs = slice(c * w // self.cols, max(c * w // self.cols + 1, (c + 1) * w // self.cols))
                frac = {k: m[ys, xs].mean() for k, m in masks.items()}
                if frac.get("water", 0) > 0.4:
                    self.terrain[r, c] = WATER
                elif frac.get("road", 0) > 0.3:
                    self.terrain[r, c] = ROAD
                elif frac.get("building", 0) > 0.35:
                    self.terrain[r, c] = BUILDING
                elif frac.get("vegetation", 0) > 0.4:
                    self.terrain[r, c] = VEG

    def add_terrain_at(self, masks, bbox):
        """Like add_terrain, for masks covering `bbox` (s, w, n, e) rather than exactly this grid (north-up only)."""
        s, w, n, e = bbox
        h, wpx = next(iter(masks.values())).shape
        for r in range(self.rows):
            for c in range(self.cols):
                (la0, lo0), (la1, lo1) = (self.proj.to_latlon((self.x0 + c * self.cw, self.y1 - r * self.ch)),
                                          self.proj.to_latlon((self.x0 + (c + 1) * self.cw, self.y1 - (r + 1) * self.ch)))
                y0, y1 = int((n - la0) / (n - s) * h), int((n - la1) / (n - s) * h)
                x0, x1 = int((lo0 - w) / (e - w) * wpx), int((lo1 - w) / (e - w) * wpx)
                y0, x0 = min(max(y0, 0), h - 1), min(max(x0, 0), wpx - 1)
                ys, xs = slice(y0, max(y0 + 1, min(y1, h))), slice(x0, max(x0 + 1, min(x1, wpx)))
                frac = {k: m[ys, xs].mean() for k, m in masks.items()}
                if frac.get("water", 0) > 0.4:
                    self.terrain[r, c] = WATER
                elif frac.get("vegetation", 0) > 0.4:
                    self.terrain[r, c] = VEG

    def set_closures(self, roads, avoid=()):
        """avoid: [{"road", "from", "to"}] -> {road name: [span in local metres, or None for the whole road]}.
        With both cross streets found, only that stretch is closed; otherwise the whole named road in view is."""
        closures = {}
        for seg in avoid:
            ends = [find_intersection(roads, seg["road"], x) for x in (seg.get("from"), seg.get("to")) if x]
            span = tuple(self.proj.to_xy(p) for p in ends) if len(ends) == 2 and all(ends) else None
            closures.setdefault(norm_road(seg["road"]), []).append(span)
        self.closure_spans = closures
        return closures

    def cells_along(self, pts, interpolate=True):
        """[(lat, lon), ...] -> the grid cells the line passes through, in order, without repeats.
        interpolate=False takes only the points' own cells (a route that is already a chain of cell centres)."""
        out = []
        xy = [self.proj.to_xy(p) for p in pts]
        if not interpolate:
            for x, y in xy:
                c, r = int((x - self.x0) // self.cw), int((self.y1 - y) // self.ch)
                if 0 <= r < self.rows and 0 <= c < self.cols and (not out or out[-1] != (r, c)):
                    out.append((r, c))
            return out
        step = min(self.cw, self.ch) / 3
        for (ax, ay), (bx, by) in zip(xy, xy[1:] or xy):
            n = max(1, int(math.hypot(bx - ax, by - ay) / step))
            for i in range(n + 1):
                x, y = ax + (bx - ax) * i / n, ay + (by - ay) * i / n
                c, r = int((x - self.x0) // self.cw), int((self.y1 - y) // self.ch)
                if 0 <= r < self.rows and 0 <= c < self.cols and (not out or out[-1] != (r, c)):
                    out.append((r, c))
        return out

    def add_roads(self, roads, avoid=()):
        """avoid: [{"road", "from", "to"}]; see set_closures."""
        closures = self.set_closures(roads, avoid)
        step = min(self.cw, self.ch) / 2
        for rd in roads:
            rank = ROAD_RANK.get(rd["kind"].replace("_link", ""), 1)
            spans = closures.get(norm_road(rd["name"])) if rd["name"] else None
            xy = [self.proj.to_xy(p) for p in rd["pts"]]
            for (ax, ay), (bx, by) in zip(xy, xy[1:]):
                n = max(1, int(math.hypot(bx - ax, by - ay) / step))
                for i in range(n + 1):
                    x, y = ax + (bx - ax) * i / n, ay + (by - ay) * i / n
                    c, r = int((x - self.x0) // self.cw), int((self.y1 - y) // self.ch)
                    if not (0 <= r < self.rows and 0 <= c < self.cols):
                        continue
                    if rank >= self.road_rank[r, c]:
                        self.terrain[r, c] = ROAD
                        self.road_rank[r, c] = rank
                        self.road_name[r][c] = rd["name"]
                        self.blocked[r, c] = bool(spans) and any(_within(x, y, s) for s in spans)

    def add_hazard_circle(self, center, radius_m):
        cx, cy = self.proj.to_xy(center)
        for r in range(self.rows):
            for c in range(self.cols):
                x, y = self.x0 + (c + 0.5) * self.cw, self.y1 - (r + 0.5) * self.ch
                d = math.hypot(x - cx, y - cy)
                if d < radius_m:
                    self.hazard[r, c] = True
                    self.hazard_depth[r, c] = 1 - d / radius_m

    # --- routing ---
    def cell_cost(self, r, c, hazard_kind):
        t = self.terrain[r, c]
        if t == WATER and hazard_kind != "wildfire":
            return None
        base = {ROAD: 1.0, OPEN: 3.0, VEG: 8.0 if hazard_kind == "wildfire" else 4.0, BUILDING: 12.0, WATER: 30.0}[t]
        if self.blocked[r, c]:
            base = 60.0
        if self.hazard[r, c]:
            base *= 1 + 4 * self.hazard_depth[r, c]  # deeper in the zone = more expensive, pushes route outward
        return base

    def route(self, start, goals, hazard_kind):
        """Dijkstra over 8-neighbors from start cell to the cheapest of `goals` (set of cells)."""
        if not goals:
            return []
        dist, prev = {start: 0.0}, {}
        pq = [(0.0, start)]
        while pq:
            d, cur = heapq.heappop(pq)
            if cur in goals:
                path = [cur]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                return path[::-1]
            if d > dist[cur]:
                continue
            r, c = cur
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = r + dr, c + dc
                    if (dr or dc) and 0 <= nr < self.rows and 0 <= nc < self.cols:
                        cost = self.cell_cost(nr, nc, hazard_kind)
                        if cost is None:
                            continue
                        nd = d + math.hypot(dr * self.ch, dc * self.cw) * cost
                        if nd < dist.get((nr, nc), float("inf")):
                            dist[(nr, nc)] = nd
                            prev[(nr, nc)] = cur
                            heapq.heappush(pq, (nd, (nr, nc)))
        return []

    def safe_cells(self, hazard_kind=None):
        """Cells outside the zone with a buffer, preferring roads. Forest is never 'safe' from a wildfire."""
        safe = {(r, c) for r in range(self.rows) for c in range(self.cols)
                if not self.hazard[r, c] and not self._near_hazard(r, c)
                and not (hazard_kind == "wildfire" and self.terrain[r, c] == VEG)}
        on_road = {rc for rc in safe if self.terrain[rc] == ROAD and not self.blocked[rc]}
        return on_road or safe

    def _near_hazard(self, r, c, k=1):
        return self.hazard[max(0, r - k): r + k + 1, max(0, c - k): c + k + 1].any()

    # --- output ---
    def directions(self, path):
        """Collapse a cell path into 'Head NE on Sunset Blvd ~0.8 km' steps."""
        return [text for text, _, _ in self.legs(path)]

    def legs(self, path):
        """The steps as (text, start cell, metres)."""
        if len(path) < 2:
            return []
        legs, cur = [], None
        for a, b in zip(path, path[1:]):
            name = self.road_name[b[0]][b[1]] or ("unnamed road" if self.terrain[b] == ROAD else "off-road")
            step = math.hypot((b[0] - a[0]) * self.ch, (b[1] - a[1]) * self.cw)
            if cur and cur["name"] == name:
                cur["m"] += step
                cur["end"] = b
            else:
                cur = {"name": name, "m": step, "start": a, "end": b}
                legs.append(cur)
        # Merge tiny legs (rasterization noise at intersections) into the previous one.
        merged = []
        for leg in legs:
            if merged and leg["m"] < 1.5 * self.ch:
                merged[-1]["m"] += leg["m"]
                merged[-1]["end"] = leg["end"]
            else:
                merged.append(leg)
        out = []
        for leg in merged:
            (r0, c0), (r1, c1) = leg["start"], leg["end"]
            ang = math.degrees(math.atan2((c1 - c0) * self.cw, (r0 - r1) * self.ch)) % 360
            heading = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((ang + 22.5) // 45) % 8]
            dist = "%.1f km" % (leg["m"] / 1000) if leg["m"] >= 1000 else "%d m" % round(leg["m"], -1)
            out.append(("Head %s on %s ~%s" % (heading, leg["name"], dist), leg["start"], leg["m"]))
        return out
