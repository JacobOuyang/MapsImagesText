"""Text maps that stay aligned inside an iPhone Messages bubble.

Messages draws texts in SF Pro, a proportional font, so a map only lines up if every cell is
the same width. Measured at 17 pt: U+2007 FIGURE SPACE and `= # + < > 0 $ ¤ £ ¥ ×` are exactly one
digit wide. Narrow glyphs are padded with thin/hair spaces to a digit's width, and a few slightly
wide ones (Λ +6%, S +1%) are used at most once or twice per row so drift stays under a pixel.

Two layouts:
  render_grid    - Manhattan-style street grid: rows are streets, columns are avenues, placed in the
                   grid's own frame (uptown is up), for incidents named at an intersection.
  render_raster  - anything else: a coarse version of the routing grid, north up.
Both return {"rows", "keys", "drift", "title", "footer"}; legend() turns the keys into explanation lines.
"""
import math
import re

from . import grid as gridmod
from .geo import LocalProjection
from .osm import norm_road

BLANK = " "
TOKENS = {
    "blank": BLANK,
    "road": "=",
    "closed": "×",           # ×
    "danger": "#",
    "zone": "+",
    "water": "~",
    "left": "<",
    "right": ">",
    "up": "Λ",               # Λ (+6%: keep to one per row)
    "down": " v ",      # v padded with hair spaces (+0.3%)
    "you": " \U0001F534 ",  # red dot emoji (17 px) padded to TWO cells (-0.4 px); see _place_you
    "skip": "",                   # the cell the two-cell "you" marker covers
    "shelter": "S",               # +1.2%
    "avenue": " | ",     # | padded (exact)
}
# Width error of each token as a fraction of one cell, for the drift check.
DRIFT = {"up": 0.063, "down": 0.003, "you": -0.04, "shelter": 0.012}

LEGEND = {
    "danger": "# {hazard}: AVOID",
    "zone": "+ {zone_note}",
    "closed": "× road closed: AVOID",
    "road": "= open road",
    "water": "~ water",
    "route": "< > Λ v way out",
    "you": "\U0001F534 you",
    "shelter": "S shelter: {shelter}",
}
ZONE_NOTE = {
    "wildfire": "evacuation zone: leave",
    "fire": "near the fire: expect smoke and crews",
    "gas": "near the leak: stay clear",
    "police": "police activity: stay clear",
}


def legend(keys, hazard, shelter=None):
    out = []
    for k in ["danger", "zone", "closed", "road", "water", "route", "you", "shelter"]:
        if k in keys:
            out.append(LEGEND[k].format(hazard=hazard or "hazard", shelter=shelter or "",
                                        zone_note=ZONE_NOTE.get(hazard, "danger zone: stay clear")))
    return out


def drift(row_tokens):
    """Worst misalignment in a row, in cells."""
    return abs(sum(DRIFT.get(t, 0) for t in row_tokens))


def _place_you(rows, r, c):
    """The red dot is too big for one cell, so it takes this cell and the next (or the previous, at the edge)."""
    c = min(c, len(rows[r]) - 2)
    rows[r][c], rows[r][c + 1] = "you", "skip"


def _join(rows, labels):
    return ["".join(TOKENS[t] for t in r) + labels.get(i, "") for i, r in enumerate(rows)]


# ---------- raster layout ----------

PRIORITY = ["you", "shelter", "route", "danger", "closed", "zone", "water", "road", "blank"]


BUBBLE_PT = 255   # usable text width of an iPhone Messages bubble at 17 pt (~264 minus slack)
CELL_PT = 10.28   # one digit / figure space in SF Pro at 17 pt
AVG_CHAR_PT = 8.5  # rough width of a label character


def _fits(label, cells):
    return cells * CELL_PT + len(label) * AVG_CHAR_PT <= BUBBLE_PT


def render_raster(g, user_cell, shelter_cell, path, cols=16, min_rank=2, outline=False):
    """Downsample the routing grid to `cols` columns (2 routing cells -> 1 text cell, etc.).
    min_rank=1 also draws side streets (a focused walking map has only a few); outline=True draws only the
    zone's edge, not its whole area."""
    f = max(1, int(math.ceil(g.cols / cols)))
    R, C = int(math.ceil(g.rows / f)), int(math.ceil(g.cols / f))
    kinds = [[set() for _ in range(C)] for _ in range(R)]
    for r in range(g.rows):
        for c in range(g.cols):
            k = kinds[r // f][c // f]
            if g.blocked[r, c]:
                k.add("closed")
            elif g.terrain[r, c] == gridmod.ROAD and g.road_rank[r, c] >= min_rank:
                k.add("road")
            if g.hazard[r, c]:
                k.add("danger" if g.hazard_depth[r, c] > 0.5 else "zone")
            if g.terrain[r, c] == gridmod.WATER:
                k.add("water")
    if outline:  # keep a zone cell only where it borders a cell outside the zone
        inz = [[bool(kinds[r][c] & {"zone", "danger"}) for c in range(C)] for r in range(R)]
        for r in range(R):
            for c in range(C):
                if "zone" in kinds[r][c] and all(inz[rr][cc] for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1))
                                                 if 0 <= rr < R and 0 <= cc < C):
                    kinds[r][c].discard("zone")
    rows = [[next((p for p in PRIORITY if p in kinds[r][c]), "blank") for c in range(C)] for r in range(R)]

    # Route as arrows: collapse the path to coarse cells, arrow points to the next coarse cell.
    coarse = []
    for r, c in path:
        rc = (r // f, c // f)
        if not coarse or coarse[-1] != rc:
            coarse.append(rc)
    for (r0, c0), (r1, c1) in zip(coarse, coarse[1:]):
        if c1 != c0:
            rows[r0][c0] = "right" if c1 > c0 else "left"
        else:
            rows[r0][c0] = "down" if r1 > r0 else "up"
    # Λ is 6% wider than a cell, so a zigzag route can't have two in a row: an extra one takes the arrow of the
    # step after it (where the route turns sideways), or plain road.
    ups = {}
    for i, (r, c) in enumerate(coarse[:-1]):
        if rows[r][c] == "up":
            ups[r] = ups.get(r, 0) + 1
            if ups[r] > 1:
                nxt = rows[coarse[i + 1][0]][coarse[i + 1][1]] if i + 2 < len(coarse) else None
                rows[r][c] = nxt if nxt in ("left", "right") else "road"
    if shelter_cell:
        rows[shelter_cell[0] // f][shelter_cell[1] // f] = "shelter"
    if user_cell:
        _place_you(rows, user_cell[0] // f, user_cell[1] // f)

    # Name the main road crossing each row, at the row's end (words can't break alignment there).
    labels = {}
    for r in range(R):
        names = [g.road_name[rr][cc] for rr in range(r * f, min(g.rows, (r + 1) * f)) for cc in range(g.cols)
                 if g.road_name[rr][cc] and g.road_rank[rr, cc] >= min_rank]
        if names:
            top = max(set(names), key=names.count)
            label = " " + _short(top)
            # A label that would wrap the row breaks the whole map, so drop it instead.
            if names.count(top) >= g.cols // 3 and label not in labels.values() and _fits(label, C):
                labels[r] = label
    keys = {t for row in rows for t in row} | ({"route"} if len(coarse) > 1 else set())
    return {"rows": _join(rows, labels), "keys": keys, "drift": max(drift(r) for r in rows),
            "title": "MAP (north is up):", "footer": None}


def _short(name):
    name = re.sub(r"^(West|East|North|South) ", "", name)
    for a, b in [("Boulevard", "Blvd"), ("Avenue", "Av"), ("Street", "St"), ("Drive", "Dr"), ("Road", "Rd"),
                 ("Pacific Coast Highway", "PCH"), ("Highway", "Hwy")]:
        name = name.replace(a, b)
    return name


# ---------- Manhattan street-grid layout ----------

# West -> east, as named after norm_road(). Midtown/downtown order; Broadway is diagonal and skipped.
AVENUES = ["12 avenue", "11 avenue", "10 avenue", "9 avenue", "8 avenue", "7 avenue", "6 avenue", "5 avenue",
           "madison avenue", "park avenue", "lexington avenue", "3 avenue", "2 avenue", "1 avenue"]
ALIASES = {"avenue of americas": "6 avenue", "avenue of the americas": "6 avenue"}
AV_LABEL = {"madison avenue": "Madison", "park avenue": "Park", "lexington avenue": "Lex"}
GRID_BEARING = 29.0  # Manhattan's "north" is ~29 degrees east of true north
AV_GAP, ROW_GAP = 4, 2  # text cells between avenues / rows between streets


def _canon(name):
    n = norm_road(name)
    return ALIASES.get(n, n)


def _street_num(n):
    m = re.fullmatch(r"(\d+) street", n)
    return int(m.group(1)) if m else None


def is_grid_place(place):
    """True for 'X Av and NN St' style places inside the Manhattan grid."""
    parts = re.split(r"\s+(?:and|&|at)\s+", (place or "").split(",")[0], flags=re.I)
    if len(parts) != 2:
        return False
    a, b = _canon(parts[0]), _canon(parts[1])
    return (a in AVENUES and _street_num(b)) or (b in AVENUES and _street_num(a))


def render_grid(roads, center, user, radius, avoid=(), shelter=None, max_avenues=5, max_streets=9, halo=True, pad=2):
    """Schematic street-grid map, or None if the roads around `center` don't form the grid.
    halo=False leaves out the '+' area around the incident; pad = streets kept beyond the incident, you and closures."""
    proj = LocalProjection(center)
    th = math.radians(GRID_BEARING)

    def uv(p):  # u = across town (east), v = uptown
        x, y = proj.to_xy(p)
        return x * math.cos(th) - y * math.sin(th), x * math.sin(th) + y * math.cos(th)

    av_u, st_v = {}, {}
    for rd in roads:
        if not rd["name"]:
            continue
        n = _canon(rd["name"])
        pts = [uv(p) for p in rd["pts"]]
        pts = [q for q in pts if abs(q[0]) < 2500 and abs(q[1]) < 2500]
        if not pts:
            continue
        if n in AVENUES:
            av_u.setdefault(n, []).extend(q[0] for q in pts)
        elif _street_num(n):
            st_v.setdefault(_street_num(n), []).extend(q[1] for q in pts)
    med = lambda xs: sorted(xs)[len(xs) // 2]
    av_u = {k: med(v) for k, v in av_u.items()}
    st_v = {k: med(v) for k, v in st_v.items()}
    if len(av_u) < 2 or len(st_v) < 3:
        return None

    # Window: the incident, the user, and the closures, padded by one avenue / two streets.
    pts = [uv(center)] + ([uv(user)] if user else [])
    avs = sorted(av_u, key=av_u.get)
    near_av = lambda u: min(range(len(avs)), key=lambda i: abs(av_u[avs[i]] - u))
    lo = max(0, min(near_av(p[0]) for p in pts) - 1)
    hi = min(len(avs) - 1, max(near_av(p[0]) for p in pts) + 1)
    avs = avs[lo:hi + 1][:max_avenues]
    nums = sorted(st_v)
    near_st = lambda v: min(nums, key=lambda s: abs(st_v[s] - v))
    want = [near_st(p[1]) for p in pts]
    for seg in avoid:
        for x in (seg.get("from"), seg.get("to")):
            if x and _street_num(_canon(x)) in st_v:
                want.append(_street_num(_canon(x)))
    s_lo, s_hi = min(want) - pad, max(want) + pad
    mid = near_st(uv(center)[1])
    while s_hi - s_lo + 1 > max_streets:  # trim the end farther from the incident
        if s_hi - mid >= mid - s_lo:
            s_hi -= 1
        else:
            s_lo += 1
    streets = [s for s in range(s_hi, s_lo - 1, -1) if s in st_v]  # uptown first
    if len(streets) < 3:
        return None

    ncols = AV_GAP * (len(avs) - 1) + 3
    nrows = ROW_GAP * (len(streets) - 1) + 1
    av_col = {a: 1 + AV_GAP * i for i, a in enumerate(avs)}
    st_row = {s: ROW_GAP * i for i, s in enumerate(streets)}

    def interp(x, keys, val, pos):  # piecewise-linear position along sorted anchors
        anchors = sorted(((val[k], pos[k]) for k in keys), key=lambda a: a[0])
        if x <= anchors[0][0]:
            return anchors[0][1]
        for (x0, p0), (x1, p1) in zip(anchors, anchors[1:]):
            if x <= x1:
                return p0 + (p1 - p0) * (x - x0) / ((x1 - x0) or 1)
        return anchors[-1][1]

    def cell(p):
        u, v = uv(p)
        c = round(interp(u, avs, av_u, av_col))
        r = round(interp(v, streets, st_v, st_row))
        return min(nrows - 1, max(0, r)), min(ncols - 1, max(0, c))

    def cell_uv(r, c):  # inverse: text cell -> (u, v) metres, for the zone test
        return interp(c, avs, av_col, av_u), interp(r, streets, st_row, st_v)

    rows = [["road" if r % ROW_GAP == 0 else ("avenue" if c in av_col.values() else "blank") for c in range(ncols)]
            for r in range(nrows)]
    cu, cv = uv(center)
    for r in range(nrows):
        for c in range(ncols):
            if halo and rows[r][c] == "blank":
                u, v = cell_uv(r, c)
                if math.hypot(u - cu, v - cv) <= radius:
                    rows[r][c] = "zone"

    # Closures along an avenue (between two streets) or along a street (between two avenues).
    for seg in avoid:
        road = _canon(seg["road"])
        ends = [_canon(x) for x in (seg.get("from"), seg.get("to")) if x]
        if road in av_col:
            nums_ = [_street_num(e) for e in ends if _street_num(e)]
            r0, r1 = (0, nrows - 1)
            if len(nums_) == 2:
                rr = [interp(st_v[n], streets, st_v, st_row) if n in st_v else (0 if n > streets[0] else nrows - 1) for n in nums_]
                r0, r1 = sorted(round(x) for x in rr)
            for r in range(max(0, r0), min(nrows - 1, r1) + 1):
                rows[r][av_col[road]] = "closed"
        elif _street_num(road) in st_row:
            r = st_row[_street_num(road)]
            cs = [av_col[e] for e in ends if e in av_col]
            c0, c1 = (min(cs), max(cs)) if len(cs) == 2 else (0, ncols - 1)
            for c in range(c0, c1 + 1):
                rows[r][c] = "closed"

    r, c = cell(center)
    rows[r][c] = "danger"
    if shelter:
        sr, sc = cell(shelter[1])
        rows[sr][sc] = "shelter"
    if user:
        ur, uc = cell(user)
        _place_you(rows, ur, uc)

    labels = {st_row[s]: " %d St" % s for s in streets}
    keys = {t for row in rows for t in row} - {"avenue", "skip"}
    footer = "Avenues left to right: " + ", ".join(AV_LABEL.get(a, a.split()[0] + " Av") for a in avs)
    return {"rows": _join(rows, labels), "keys": keys, "drift": max(drift(r) for r in rows),
            "title": "MAP (uptown is up):", "footer": footer}
