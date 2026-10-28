"""Walking directions abroad on a capped roaming plan. Asked directly, Muse Spark gives directions from memory: they
read well but it can't know where you are standing, some turns are wrong, and its sketch isn't to scale. Here
the route is worked out on real street and footpath data from the person's GPS, and Muse only rewords the steps.

A walk is a Trip: the streets are downloaded once (`plan`), then every new GPS fix re-routes on them (`move`).
Each map shows only the next stretch (the next two steps, at least AHEAD_M), fitted to it, with only the streets
the route uses, the cross streets at its turns and the main roads it crosses (evac/focus.py). While the person is
still on the route with a long way to go on the current street, the update is one line of text and no map."""
import math
from dataclasses import dataclass, field

from . import focus, geo, grid, imagery, llm, osm, render, segment
from .scene import Scene

WALK_M_PER_MIN = 75  # ~4.5 km/h
MAX_WALK_M = 4000    # the street download grows with the square of the distance
AHEAD_M = 400        # each map shows at least this much of the route ahead
WHOLE_M = 1000       # walks up to this long fit one map
ON_ROUTE_M = 25      # this close to the last route sent: still on it
ARRIVED_M = 30
KEEP_GOING_M = 80    # still on route with more than this left on the current street: a text line, no map


@dataclass
class Trip:
    name: str
    dest: tuple
    roads: list
    g: object                                  # routing grid for the turn-by-turn steps
    gi: object                                 # 4x finer grid for the route that gets drawn
    you: tuple = None                          # the last position
    route: list = field(default_factory=list)  # the last route sent, [(lat, lon)]
    sms_cols: int = 40
    mono_cols: int = 58


def plan(user, destination, log, sms_cols=40, mono_cols=58):
    """Geocode, download the streets once, build the routing grids. Returns a Trip or {"error"}."""
    dest = geo.geocode(destination, near=user)
    if not dest:
        return {"error": "Couldn't find %r." % destination}
    d = geo.haversine_m(user, dest)
    log.append("destination %r at %s, %d m away" % (destination, dest, d))
    if d > MAX_WALK_M:
        return {"error": "%s is %.1f km away, too far to walk. Try transit." % (destination, d / 1000)}
    half = d * 1.2 + 150  # room to walk around the destination; later fixes must stay inside it
    g = grid.Grid(user, half, half, sms_cols)
    roads = osm.fetch_roads(*g.bbox(), kinds=osm.WALKABLE)
    log.append("%d streets and paths downloaded" % len(roads))
    gi = grid.Grid(user, half, half, 160)
    for gr in (g, gi):
        gr.terrain[:] = grid.BUILDING  # walk along streets and paths, not through blocks
        gr.add_roads(roads)
    return Trip(destination.split(",")[0].strip(), dest, roads, g, gi, user, sms_cols=sms_cols, mono_cols=mono_cols)


def _route(trip, you):
    """-> (legs [(text, start cell, metres)], route [(lat, lon)], metres) from `you`."""
    g, gi = trip.g, trip.gi
    u, dc = g.cell_of(you), g.cell_of(trip.dest)
    path = g.route(u, {dc}, None) if u and dc else []
    ui, di = gi.cell_of(you), gi.cell_of(trip.dest)
    fine = gi.route(ui, {di}, None) if ui and di else []
    route = [gi.cell_center(*c) for c in fine] or [g.cell_center(*c) for c in path]
    walked = sum(geo.haversine_m(g.cell_center(*a), g.cell_center(*b)) for a, b in zip(path, path[1:]))
    return g.legs(path), route, walked or geo.haversine_m(you, trip.dest)


def _versions(trip, you, text, legs, route, log, maps=True):
    """Build the Scene for the next stretch and draw it. maps=False: SMS and WhatsApp get the text alone."""
    ahead_m = max(AHEAD_M, sum(m for _, _, m in legs[:2]))
    total = sum(geo.haversine_m(a, b) for a, b in zip(route, route[1:]))
    if total <= WHOLE_M:  # a short walk: show the whole way
        ahead_m = total
    seen = focus.ahead(route, ahead_m + 1)
    pts = [you] + seen + ([trip.dest] if len(seen) == len(route) else [])
    center, half = focus.fit(pts)
    snap = lambda p: min(route, key=lambda q: geo.haversine_m(p, q))  # step starts sit on the drawn line
    marks = [(i + 1, snap(trip.g.cell_center(*start))) for i, (_, start, _) in enumerate(legs[:9]) if i]
    scene = Scene(you=you, half_m=half, center=center, text=text, roads=trip.roads, goal=(trip.name, trip.dest),
                  cols=trip.sms_cols, walk=True, route=route, marks=marks,
                  title="WALKING  ·  to %s" % trip.name[:28], summary="Next %d m of your walk to %s" % (round(ahead_m, -1), trip.name))
    scene.focus = focus.select(scene)
    log.append("view %.0f m across, %d of %d roads drawn" % (2 * half, len(scene.focus), len(trip.roads)))
    scene.aerial = imagery.fetch_aerial(*scene.bbox())
    scene.masks = segment.segment(scene.aerial)
    out = {"text": text, "image": render.picture(scene), "small": render.small(scene), "scene": scene, "map": maps}
    if maps:
        sms, tm = render.sms(scene)
        log.append("sms map: %d rows, worst row drift %.2f cells" % (len(tm["rows"]), tm["drift"]))
        wide = render.mono(scene, trip.mono_cols)
        out.update(sms=sms, mono=wide["text"], mono_bytes=wide["bytes"], mono_gzip=wide["gzip"])
    else:
        out.update(sms=text, mono=text, mono_bytes=len(text.encode()), mono_gzip=None)
    return out


def _numbered(legs):
    return ["%d. %s" % (i + 1, t) for i, (t, _, _) in enumerate(legs[:6])]


def run(user, destination, sms_cols=40, mono_cols=58):
    """The first message of a walk. The result's "trip" goes to `move` with each new GPS fix."""
    log = []
    trip = plan(user, destination, log, sms_cols, mono_cols)
    if isinstance(trip, dict):
        return dict(trip, log=log)
    legs, route, walked = _route(trip, user)
    if not route:
        return {"error": "No walking route found to %s." % trip.name, "log": log}
    trip.route = route
    draft = "\n".join(["Walk to %s: about %.1f km, %d min." % (trip.name, walked / 1000, round(walked / WALK_M_PER_MIN))]
                      + _numbered(legs))
    text = draft
    if llm.available():
        try:
            text = llm.chat([{"role": "system", "content": "Rewrite these walking directions as a short, friendly text "
                              "message. Keep every street name, direction, distance and time exactly as given and in "
                              "order, one numbered step per line. Don't add streets, landmarks or advice that isn't "
                              "in the draft. Plain text."},
                             {"role": "user", "content": draft}]).strip()
        except Exception as ex:
            log.append("rewrite failed (%s); using the draft" % ex)
    out = _versions(trip, user, text, legs, route, log)
    out.update(distance_m=walked, steps=[t for t, _, _ in legs], trip=trip, log=log)
    return out


def move(trip, you):
    """A new GPS fix during the walk -> the update to send. Templates only (no model): updates must be quick."""
    log = []
    trip.you = you
    to_go = geo.haversine_m(you, trip.dest)
    if to_go <= ARRIVED_M:
        text = "You've arrived at %s." % trip.name
        return {"text": text, "sms": text, "mono": text, "image": None, "small": None, "map": False, "state": "arrived",
                "distance_m": to_go, "steps": [], "log": log}
    off = min(geo.haversine_m(you, p) for p in trip.route) if trip.route else 1e9
    legs, route, walked = _route(trip, you)
    if not route:
        text = "You're outside the area I have streets for. Ask for directions again from here."
        return {"text": text, "sms": text, "mono": text, "image": None, "small": None, "map": False, "state": "lost",
                "distance_m": to_go, "steps": [], "log": log}
    trip.route = route
    left = "%.1f km, %d min to %s." % (walked / 1000, round(walked / WALK_M_PER_MIN), trip.name)
    if off <= ON_ROUTE_M and legs and legs[0][2] > KEEP_GOING_M:
        state = "on route"
        text = "\n".join(["On route. Keep going: " + legs[0][0][0].lower() + legs[0][0][1:] + "."]
                         + (["Then: " + legs[1][0] + "."] if len(legs) > 1 else []) + [left])
        out = _versions(trip, you, text, legs, route, log, maps=False)
    else:
        state = "on route, next turn" if off <= ON_ROUTE_M else "off route, re-routed"
        head = "Next turn coming up." if off <= ON_ROUTE_M else "Off route, new directions."
        text = "\n".join([head + " " + left] + _numbered(legs))
        out = _versions(trip, you, text, legs, route, log)
    log.append("%s: %.0f m from the last route" % (state, off))
    out.update(state=state, distance_m=walked, steps=[t for t, _, _ in legs], log=log)
    return out


def along(trip, metres):
    """The point `metres` along the last route from where the person is (to simulate walking)."""
    pts = [trip.you] + trip.route
    for a, b in zip(pts, pts[1:]):
        step = geo.haversine_m(a, b)
        if step >= metres:
            t = metres / (step or 1)
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        metres -= step
    return trip.route[-1]


def astray(trip, metres=80):
    """A street point about `metres` from the person and well off the route (to simulate a wrong turn)."""
    gi, (r0, c0) = trip.gi, trip.gi.cell_of(trip.you)
    reach = int(metres * 1.6 / gi.cw) + 1
    best = None
    for r in range(max(0, r0 - reach), min(gi.rows, r0 + reach + 1)):
        for c in range(max(0, c0 - reach), min(gi.cols, c0 + reach + 1)):
            if gi.terrain[r, c] != grid.ROAD:
                continue
            p = gi.cell_center(r, c)
            d = geo.haversine_m(trip.you, p)
            if metres * 0.6 <= d <= metres * 1.6 and min(geo.haversine_m(p, q) for q in trip.route[::3]) > 2 * ON_ROUTE_M:
                if best is None or abs(d - metres) < best[0]:
                    best = (abs(d - metres), p)
    return best[1] if best else along(trip, metres)
