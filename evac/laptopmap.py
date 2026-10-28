"""Wide text map for a laptop screen: ~100 monospace columns with line-drawn roads, a shaded danger zone,
route arrows and road names written next to the roads. For a slow link where a picture won't load but a
few KB of text will. Every glyph used here is exactly one cell wide in Menlo/Monaco/Courier (measured);
Braille dots are not (they fall back to another font on a Mac), so roads are drawn with box-drawing lines."""
import gzip
import io
import math

from . import geo, grid, textmap
from .osm import norm_road

COLS = 100
ASPECT = 0.6  # frame height / width, in metres
LINE = {0: "─", 45: "╱", 90: "│", 135: "╲"}
HEAVY = {"─": "━", "│": "┃", "┼": "╋"}
ARROW = {(0, 1): "→", (0, -1): "←", (-1, 0): "↑", (1, 0): "↓", (-1, 1): "↗", (-1, -1): "↖", (1, 1): "↘", (1, -1): "↙"}
SHADE = "░▒▓"
PATHS = {"footway", "path", "steps", "service"}  # routed on when walking, but drawing them all is just noise
ANY = " ·░▒▓≈"  # what the YOU / hazard / shelter labels may cover
YOU = "\U0001F534"  # red dot, the same "you" as the SMS map. Emoji are the only way to get colour in a WhatsApp
# monospace block. In Menlo it is 1.66 cells wide, so it takes two cells and leaves its row ~1/3 cell short.
LEGEND = [("you", YOU + " you"), ("danger", "◆ incident   ░▒▓ danger zone, darker is closer"), ("closed", "× road closed"),
          ("route", "→ way out"), ("shelter", "⌂ shelter"), ("major", "━ main road   ─ street"),
          ("water", "≈ water"), ("veg", "· trees / park")]


class _RotProj(geo.LocalProjection):
    """Local metres rotated so Manhattan's avenues run straight up the screen."""

    def __init__(self, origin, bearing):
        super().__init__(origin)
        t = math.radians(bearing)
        self.c, self.s = math.cos(t), math.sin(t)

    def to_xy(self, latlon):
        x, y = super().to_xy(latlon)
        return x * self.c - y * self.s, x * self.s + y * self.c

    def to_latlon(self, xy):
        x, y = xy
        return super().to_latlon((x * self.c + y * self.s, -x * self.s + y * self.c))


def _line_char(bins):
    """bins: {angle: [samples, major]} for the roads crossing one cell."""
    major = any(m for _, m in bins.values())
    big = {k for k, (n, _) in bins.items() if n >= 2} or set(bins)
    if {0, 90} <= big:
        ch = "┼"
    elif {45, 135} <= big:
        ch = "╳"
    else:
        ch = LINE[max(bins, key=lambda k: bins[k][0])]
    return HEAVY.get(ch, ch) if major else ch, major


def render(scene, cols=COLS):
    """Scene -> {"lines": [[[cls, text], ...], ...], "text", "bytes", "gzip", "aerial_bytes", "m_per_char"}.
    Draws the scene's own route; a scene with no zone is plain directions (goal = destination)."""
    user, roads, shelter, half, walk = scene.you, scene.shown, scene.goal, scene.half_m, scene.walk
    focused = scene.focus is not None  # only the roads the reply is about: draw all of them, nothing else
    center = scene.zone[0] if scene.zone else None
    rotated = scene.street_grid
    aspect = ASPECT if cols >= 90 else 0.9  # narrow maps (a chat bubble) grow down instead of losing area
    # A rotated frame has to fit inside the north-up area the roads were fetched for.
    hw = half / (math.cos(math.radians(textmap.GRID_BEARING)) + aspect * math.sin(math.radians(textmap.GRID_BEARING))) if rotated else half
    g = grid.Grid(scene.center or user, hw, hw * aspect, cols)
    jpg = io.BytesIO()
    if scene.aerial is not None:  # only to report what a photo of the same area would cost
        scene.aerial.convert("RGB").save(jpg, "JPEG", quality=70)
    if rotated:
        g.proj = _RotProj(scene.center or user, textmap.GRID_BEARING)  # imagery is north-up, so no terrain on a rotated frame
    elif scene.masks is not None and not walk:
        g.add_terrain_at(scene.masks, scene.bbox())
    g.add_roads(roads, scene.closures)
    if center:
        g.add_hazard_circle(*scene.zone)
    rows = g.rows

    # Which way each road runs through each cell.
    bins = [[{} for _ in range(cols)] for _ in range(rows)]
    named = {}  # norm name -> {"label", "cells", "rank", "closed"}
    min_rank = 1 if g.cw <= 25 or walk else 2  # zoomed out, side streets are just noise (unless walking them)
    for rd in roads:
        rank = grid.ROAD_RANK.get(rd["kind"].replace("_link", ""), 1)
        if not focused and (rank < min_rank or (walk and rd["kind"] in PATHS)):
            continue
        xy = [g.proj.to_xy(p) for p in rd["pts"]]
        key = norm_road(rd["name"]) if rd["name"] else None
        for (ax, ay), (bx, by) in zip(xy, xy[1:]):
            seg = math.hypot(bx - ax, by - ay)
            if not seg:
                continue
            ang = math.degrees(math.atan2(by - ay, bx - ax)) % 180
            b = min(LINE, key=lambda k: min(abs(ang - k), 180 - abs(ang - k)))
            steps = max(1, int(seg / (g.cw / 3)))
            for i in range(steps + 1):
                x, y = ax + (bx - ax) * i / steps, ay + (by - ay) * i / steps
                c, r = int((x - g.x0) // g.cw), int((g.y1 - y) // g.ch)
                if 0 <= r < rows and 0 <= c < cols:
                    cell = bins[r][c].setdefault(b, [0, False])
                    cell[0] += 1
                    cell[1] = cell[1] or rank >= 3
                    if key:
                        info = named.setdefault(key, {"label": textmap._short(rd["name"]), "cells": [], "rank": 0, "closed": False})
                        info["cells"].append((r, c))
                        info["rank"] = max(info["rank"], rank)

    cells = [[(" ", "")] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            hot = g.hazard[r, c]
            if bins[r][c]:
                ch, major = _line_char(bins[r][c])
                if g.blocked[r, c]:
                    cells[r][c] = ("×", "closed")
                    if g.road_name[r][c]:
                        named.get(norm_road(g.road_name[r][c]), {})["closed"] = True
                else:
                    cells[r][c] = (ch, ("major" if major else "road") + (" hot" if hot else ""))
            elif hot and focused:  # the zone's edge, and its worst part, but not a fill over everything
                edge = any(not (0 <= rr < rows and 0 <= cc < cols) or not g.hazard[rr, cc]
                           for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)))
                if edge or g.hazard_depth[r, c] > 0.75:
                    cells[r][c] = ("░" if edge else "▓", "zone")
            elif hot:
                cells[r][c] = (SHADE[min(2, int(g.hazard_depth[r, c] * 3))], "zone")
            elif g.terrain[r, c] == grid.WATER:
                cells[r][c] = ("≈", "water")
            elif g.terrain[r, c] == grid.VEG and not focused:
                cells[r][c] = ("·", "veg")

    # The scene's route, as arrows.
    ucell = g.cell_of(user)
    scell = g.cell_of(shelter[1]) if shelter else None
    path = g.cells_along(scene.route)
    for (r0, c0), (r1, c1) in zip(path, path[1:]):
        cells[r1][c1] = (ARROW[((r1 > r0) - (r1 < r0), (c1 > c0) - (c1 < c0))], "route")
    for n, p in scene.marks:  # where each numbered step starts, on the route line
        mc = g.cell_of(p)
        if mc and n < 10:
            cells[mc[0]][mc[1]] = (str(n), "mark")

    taken = [[False] * cols for _ in range(rows)]
    for n, p in scene.marks:
        mc = g.cell_of(p)
        if mc:
            taken[mc[0]][mc[1]] = True
    def put(r, c, text, cls):
        for i, ch in enumerate(text):
            cells[r][c + i] = (ch, cls)
            taken[r][c + i] = True

    def free(r, c, over):
        return 0 <= r < rows and 0 <= c < cols and not taken[r][c] and cells[r][c][0] in over

    def place(anchor, text, cls, over=" ·░≈"):
        """Write text beside anchor on cells that are blank (or only the shading/water/trees in `over`)."""
        r, c = anchor
        for rr, ss in [(r, c + 2), (r, c - 1 - len(text)), (r - 1, c - len(text) // 2), (r + 1, c - len(text) // 2)]:
            if all(free(rr, ss + i, over) for i in range(-1, len(text) + 1)):
                put(rr, ss, text, cls)
                return True
        return False

    cc = g.cell_of(center) if center else None
    if cc:
        cells[cc[0]][cc[1]] = ("◆", "danger")
        taken[cc[0]][cc[1]] = True
        place(cc, (scene.hazard or "hazard").upper(), "danger label", ANY)
    if scell:
        cells[scell[0]][scell[1]] = ("⌂", "shelter")
        place(scell, shelter[0][:24], "shelter label", ANY)
    elif shelter:  # off the map: mark the edge in its direction
        sx, sy = g.proj.to_xy(shelter[1])
        t = min((g.cols * g.cw / 2) / abs(sx) if sx else 1e9, (rows * g.ch / 2) / abs(sy) if sy else 1e9) * 0.97
        edge = g.cell_of(g.proj.to_latlon((sx * t, sy * t)))
        if edge:
            km = geo.haversine_m(user, shelter[1]) / 1000
            cells[edge[0]][edge[1]] = ("⌂", "shelter")
            taken[edge[0]][edge[1]] = True
            place(edge, "%s %.1f km" % (shelter[0][:16].rstrip(), km), "shelter label", ANY)
    if ucell:
        r, c = ucell[0], min(ucell[1], cols - 2)
        cells[r][c], cells[r][c + 1] = (YOU, "you"), ("", "you")
        taken[r][c] = taken[r][c + 1] = True
        place((r, c + 1), "YOU", "you label", ANY)
    header = []  # (col, text) for avenue names above the frame
    for info in sorted(named.values(), key=lambda i: (not i["closed"], -i["rank"], -len(i["cells"]))):
        pts = info["cells"]
        cls = "label closed" if info["closed"] else "label"
        if rotated and _runs_across(pts):
            # A street: write its name on the line itself, so it can't be read as the street above or below.
            if _label_on_line(cells, taken, pts, " %s " % info["label"], cls, cols):
                continue
        if rotated and not _runs_across(pts):
            header.append((sorted(c for _, c in pts)[len(pts) // 2], info["label"]))
            continue
        mid = pts[len(pts) // 2]
        for a in sorted(set(pts), key=lambda p: abs(p[0] - mid[0]) * 2 + abs(p[1] - mid[1]))[::3]:
            if place(a, info["label"], cls):
                break

    if focused:  # a focused map has nothing above or below its route: drop the empty rows, keep one of margin
        busy = [i for i, row in enumerate(cells) if any(ch != " " for ch, _ in row)]
        if busy:
            lo, hi = max(0, busy[0] - 1), min(rows, busy[-1] + 2)
            cells, rows = cells[lo:hi], hi - lo

    # Frame, scale bar, legend.
    used = {cls.split()[0] for row in cells for _, cls in row if cls}
    nice = next((m for m in (20, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000) if m / g.cw >= 8), 10000)
    bar = "├" + "─" * max(1, int(round(nice / g.cw)) - 2) + "┤ " + geo_fmt(nice)
    up = " ↑ uptown " if rotated else " ↑ N "
    out = [[["label", line]] for line in _header_lines(header, cols)]
    out.append([["frame", "┌─" + up + "─" * (cols - 2 - len(up)) + "─┐"]])
    for row in cells:
        runs = [["frame", "│"]]
        for ch, cls in row:
            if runs[-1][0] == cls:
                runs[-1][1] += ch
            else:
                runs.append([cls, ch])
        runs.append(["frame", "│"])
        out.append(runs)
    out.append([["frame", "└─ " + bar + " " + "─" * (cols - 3 - len(bar)) + "┘"]])
    out.append([["", ""]])
    for key, text in LEGEND:
        if key in used or (key == "danger" and "zone" in used):
            if focused and key == "danger":
                text = "◆ incident   ░ edge of danger zone   ▓ worst of it"
            if key == "route" and scene.marks:
                text += "   1 2 3 steps"
            if not scene.alert:  # directions, not an alert
                text = text.replace("way out", "route").replace("shelter", "destination")
            out.append([["legend", text]])
    if rotated:
        out.append([["legend", "Avenues run up and down, streets left and right."]])
    text = "\n".join("".join(t for _, t in line) for line in out)
    return {"lines": out, "text": text, "bytes": len(text.encode()), "gzip": len(gzip.compress(text.encode())),
            "aerial_bytes": jpg.tell(), "m_per_char": round(g.cw), "cols": cols, "rows": rows}


def _runs_across(pts):
    rows, cols = {r for r, _ in pts}, {c for _, c in pts}
    return len(cols) > 2 * len(rows)


def _label_on_line(cells, taken, pts, text, cls, cols):
    """Overwrite a plain stretch of the street's own line (no crossings) with its name, nearest the middle."""
    best = None
    for r in {r for r, _ in pts}:
        c = 0
        while c < cols:
            if cells[r][c][0] in "─━" and not taken[r][c]:
                e = c
                while e < cols and cells[r][e][0] in "─━" and not taken[r][e]:
                    e += 1
                if e - c >= len(text) + 2:
                    s = c + (e - c - len(text)) // 2
                    if best is None or abs(s - cols / 2) < abs(best[1] - cols / 2):
                        best = (r, s)
                c = e
            c += 1
    if not best:
        return False
    r, s = best
    for i, ch in enumerate(text):
        cells[r][s + i] = (ch, cls)
        taken[r][s + i] = True
    return True


def _header_lines(header, cols):
    """Names centred over their columns, on up to two lines so neighbours don't collide."""
    lines = [[" "] * (cols + 2), [" "] * (cols + 2)]
    for col, text in sorted(header):
        s = max(0, min(cols + 2 - len(text), col + 1 - len(text) // 2))
        for ln in lines:
            if all(ch == " " for ch in ln[max(0, s - 1): s + len(text) + 1]):
                ln[s: s + len(text)] = list(text)
                break
    return ["".join(ln).rstrip() for ln in lines if "".join(ln).strip()][::-1]


def geo_fmt(m):
    return "%d m" % m if m < 1000 else "%g km" % (m / 1000)
