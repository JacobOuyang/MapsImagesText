"""Walking directions abroad on a capped roaming plan. Asked directly, Muse Spark gives directions from memory: they
read well but it can't know where you are standing, some turns are wrong, and its sketch isn't to scale. Here
the route is worked out on real street and footpath data, and Muse only rewords it.

A walk is a Trip: the streets are downloaded once (`plan`), then every new GPS fix re-routes on them (`move`).
Each message carries the bare minimum: the current step, only as far as the next intersection (where the route
changes street), and which way to turn there. Its map shows just that stretch: the route to the intersection and a
little past it, the street you are on, the one you turn into and anything you cross (evac/focus.py). While the
person is still on the same street with a way to go, the update is one line and no map."""
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field

from . import focus, geo, grid, imagery, llm, osm, render, segment
from .scene import Scene

WALK_M_PER_MIN = 75  # ~4.5 km/h
MAX_WALK_M = 4000    # the street download grows with the square of the distance
PAST_TURN = (0.35, 15, 80)  # the map shows this share of the step past the intersection (15-80 m): the turn
ON_ROUTE_M = 25      # this close to the last route sent: still on it
ARRIVED_M = 30
KEEP_GOING_M = 80    # still on the same street with more than this to go: a text line, no map
LEG = re.compile(r"Head (\w+) on (.+) ~(.+)$")
FINE_CELL_M = 8      # target cell width of the grid the drawn route and the steps come from
LANDMARK_WAIT_S = 5  # after the streets arrive, wait at most this long for landmarks
_BACKGROUND = ThreadPoolExecutor(2)


@dataclass
class Trip:
    name: str                                  # the destination as written ("Castelo de São Jorge")
    dest: tuple
    roads: list
    g: object                                  # routing grid for the steps
    gi: object                                 # finer grid (~8 m cells): the route that gets drawn, and the steps
    you: tuple = None                          # the last position
    route: list = field(default_factory=list)  # the last route sent, [(lat, lon)]
    street: str = ""                           # the street the last step was on
    leg_grid: object = None                    # the grid the last steps came from (gi, or g if gi found no route)
    landmarks: list = field(default_factory=list)  # osm.fetch_landmarks over the walk's area
    sms_cols: int = 40
    mono_cols: int = 58


def plan(origin, destination, log, sms_cols=40, mono_cols=58):
    """Geocode, download the streets once, build the routing grids. Returns a Trip or {"error"}."""
    found = geo.place(destination, near=origin)
    if not found:
        return {"error": "Couldn't find %r." % destination}
    dest, matched = found[:2], found[2]
    if matched:
        log.append("%r matched %s" % (destination, matched))
    d = geo.haversine_m(origin, dest)
    log.append("destination %r at %s, %d m away" % (destination, dest, d))
    if d > MAX_WALK_M:
        return {"error": "%s is %.1f km away, too far to walk. Try transit." % (destination, d / 1000)}
    half = d * 1.2 + 150  # room to walk around the destination; later fixes must stay inside it
    g = grid.Grid(origin, half, half, sms_cols)
    marks = _BACKGROUND.submit(osm.fetch_landmarks, *g.bbox())  # nice to have: fetched alongside the streets
    roads = osm.fetch_roads(*g.bbox(), kinds=osm.WALKABLE)
    try:  # a slow first download keeps going in the background and is cached for the next request
        landmarks = marks.result(timeout=LANDMARK_WAIT_S)
    except TimeoutError:
        landmarks = []
        log.append("landmarks still downloading; this reply goes without them")
    log.append("%d streets and paths, %d landmarks downloaded" % (len(roads), len(landmarks)))
    # Cells ~8 m wide (16 m tall, like a character): a step map zoomed to 100 m needs a route that sits on the
    # street, not 25 m off it. 480 columns route in ~2 s; a walk under ~600 m gets the 160-column minimum.
    gi = grid.Grid(origin, half, half, min(480, max(160, round(2 * half / FINE_CELL_M))))
    for gr in (g, gi):
        gr.terrain[:] = grid.BUILDING  # walk along streets and paths, not through blocks
        gr.add_roads(roads)
    return Trip(matched or destination.split(",")[0].strip(), dest, roads, g, gi, origin, sms_cols=sms_cols, mono_cols=mono_cols,
                landmarks=landmarks)


def _route(trip, you):
    """-> (legs [(text, start cell, metres, end cell, heading)], route [(lat, lon)], metres) from `you`."""
    g, gi = trip.g, trip.gi
    u, dc = g.cell_of(you), g.cell_of(trip.dest)
    path = g.route(u, {dc}, None) if u and dc else []
    ui, di = gi.cell_of(you), gi.cell_of(trip.dest)
    fine = gi.route(ui, {di}, None) if ui and di else []
    route = [gi.cell_center(*c) for c in fine] or [g.cell_center(*c) for c in path]
    walked = sum(geo.haversine_m(g.cell_center(*a), g.cell_center(*b)) for a, b in zip(path, path[1:]))
    # Steps from the fine grid: on the coarse one a 1.5 km walk has ~90 m cells, street names get lost and
    # several turns blur into one "unnamed road ~910 m".
    trip.leg_grid = gi if fine else g
    return trip.leg_grid.legs(fine or path), route, walked or geo.haversine_m(you, trip.dest)


def _turn(h0, h1):
    d = (h1 - h0 + 540) % 360 - 180  # clockwise is to the right
    return ("turn sharp right" if d > 120 else "turn right" if d > 30 else "turn sharp left" if d < -120
            else "turn left" if d < -30 else "keep straight")


def _turn_point(trip, legs, route):
    """Index in `route` of the intersection that ends the current step."""
    end = trip.leg_grid.cell_center(*legs[0][3])
    return min(range(len(route)), key=lambda k: geo.haversine_m(route[k], end))


def landmarks(trip, legs, route):
    """-> (at the turn, along the way): the most notable named place near the intersection (within a quarter of the
    step, 15-40 m), and a very notable one beside the step itself. Either can be None."""
    if not trip.landmarks:
        return None, None
    i = _turn_point(trip, legs, route)
    turn, reach = route[i], min(40, max(15, 0.25 * legs[0][2]))
    streets = [_words(LEG.match(leg[0]).group(2)) for leg in legs[:2]]
    usable = [l for l in trip.landmarks if not any(len(_words(l["name"]) & w) >= 2 for w in streets)]
    near = [(l, geo.haversine_m(turn, (l["lat"], l["lon"]))) for l in usable]
    at = max(((l, d) for l, d in near if d <= reach), key=lambda ld: (ld[0]["rank"], -ld[1]), default=(None, 0))[0]
    side = [l for l in usable if l["rank"] == 3 and l is not at and l["name"] != (at or {}).get("name")
            and min(geo.haversine_m((l["lat"], l["lon"]), p) for p in route[:i + 1:2] or route[:1]) <= 20]
    along = min(side, key=lambda l: geo.haversine_m(route[i // 2], (l["lat"], l["lon"])), default=None)
    return at, along


def _words(name):
    """Significant words, to tell that 'Dom Pedro IV Square' is the 'Praça Dom Pedro IV' you're already on."""
    return {w for w in re.findall(r"\w+", name.lower()) if len(w) > 2 or w.isdigit() or w in ("iv", "vi", "ix", "xi")}


def _called(l):
    """'Farmácia Estácio (pharmacy)'; a name that already says what it is stays as it is."""
    return l["name"] if l["kind"].split()[0].lower() in l["name"].lower() else "%s (%s)" % (l["name"], l["kind"])


def step(trip, legs, route=None):
    """-> (now, then): the current step as far as the next intersection, and what to do there."""
    head, street, dist = LEG.match(legs[0][0]).groups()
    if len(legs) == 1:
        return "Head %s on %s for %s, to %s." % (head, street, dist, trip.name), "Then you're there."
    nxt = LEG.match(legs[1][0]).group(2)
    at, along = landmarks(trip, legs, route) if route else (None, None)
    where = "the next corner" if nxt == "unnamed road" else nxt
    onto = "onto the path ahead" if nxt == "unnamed road" else "onto " + nxt
    now = "Head %s on %s for %s%s, to %s." % (head, street, dist, ", past " + _called(along) if along else "", where)
    return now, "Then %s %s%s." % (_turn(legs[0][4], legs[1][4]), onto, " at " + _called(at) if at else "")


def _scene(trip, you, text, legs, route, log, maps=True):
    """The map of the current step: the route to the intersection and a little past it, nothing else of it."""
    i = _turn_point(trip, legs, route)
    share, lo, hi = PAST_TURN
    drawn = [you] + route[:i + 1] + focus.ahead(route[i:], min(hi, max(lo, share * legs[0][2])))[1:]
    marks = [l for l in landmarks(trip, legs, route) if l]
    # The zoom follows what has to fit: you, this step, a bit past the turn and its landmarks.
    center, half = focus.fit([you] + drawn + [(l["lat"], l["lon"]) for l in marks])
    scene = Scene(you=you, half_m=half, center=center, text=text, roads=trip.roads, goal=(trip.name, trip.dest),
                  cols=trip.sms_cols, walk=True, route=drawn, landmarks=[(l["name"], (l["lat"], l["lon"])) for l in marks],
                  title="WALKING  ·  to %s" % trip.name[:28], summary=step(trip, legs, route)[0])
    scene.focus = focus.select(scene, minimal=True)
    log.append("view %.0f m across, %d of %d roads drawn" % (2 * half, len(scene.focus), len(trip.roads)))
    scene.aerial = imagery.fetch_aerial(*scene.bbox())
    scene.masks = segment.segment(scene.aerial, "color")  # walking maps don't draw terrain; no SAM call per step
    out = {"text": text, "image": render.picture(scene), "small": render.small(scene), "scene": scene, "map": maps}
    if maps:
        sms, tm = render.sms(scene)
        log.append("sms map: %d rows, worst row drift %.2f cells" % (len(tm["rows"]), tm["drift"]))
        wide = render.mono(scene, trip.mono_cols)
        out.update(sms=sms, mono=wide["text"], mono_bytes=wide["bytes"], mono_gzip=wide["gzip"])
    else:
        out.update(sms=text, mono=text, mono_bytes=len(text.encode()), mono_gzip=None)
    return out


def _left(trip, walked):
    return "%s: %.1f km, about %d min." % (trip.name, walked / 1000, round(walked / WALK_M_PER_MIN))


def run(user, destination, start=None, sms_cols=40, mono_cols=58):
    """The first message of a walk, from `start` (a place name) or else from the person's GPS. The result's
    "trip" goes to `move` with each new GPS fix."""
    log = []
    origin = user
    if start:
        origin = geo.geocode(start, near=user) or geo.geocode(start)
        if not origin:
            return {"error": "Couldn't find %r." % start, "log": log}
        log.append("starting from %r at %s instead of GPS" % (start, origin))
    trip = plan(origin, destination, log, sms_cols, mono_cols)
    if isinstance(trip, dict):
        return dict(trip, log=log)
    legs, route, walked = _route(trip, origin)
    if not route or not legs:
        return {"error": "No walking route found to %s." % trip.name, "log": log}
    trip.route, trip.street = route, LEG.match(legs[0][0]).group(2)
    now, then = step(trip, legs, route)
    head = "From %s to %s" % (start.split(",")[0].strip(), _left(trip, walked)) if start else _left(trip, walked)
    draft = "\n".join([head, "Now: " + now[0].lower() + now[1:], then])
    text = draft
    if llm.available():
        try:
            text = llm.chat([{"role": "system", "content": "Reword this walking instruction as a short, plain text "
                              "message, with no greeting or encouragement (every character costs SMS). Keep all three lines (the total, the current step, and the turn after it), one "
                              "per line, with every street name, direction, distance and time exactly as given. Don't "
                              "add steps, streets, landmarks or advice. Plain text."},
                             {"role": "user", "content": draft}]).strip()
        except Exception as ex:
            log.append("rewrite failed (%s); using the draft" % ex)
    out = _scene(trip, origin, text, legs, route, log)
    out.update(distance_m=walked, now=now, then=then, steps=[leg[0] for leg in legs], trip=trip, log=log)
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
    if not route or not legs:
        text = "You're outside the area I have streets for. Ask for directions again from here."
        return {"text": text, "sms": text, "mono": text, "image": None, "small": None, "map": False, "state": "lost",
                "distance_m": to_go, "steps": [], "log": log}
    head, street, dist = LEG.match(legs[0][0]).groups()
    now, then = step(trip, legs, route)
    if off <= ON_ROUTE_M and street == trip.street and legs[0][2] > KEEP_GOING_M:
        state = "on route"
        text = "Keep going %s on %s: %s more, %s\n%s" % (head, street, dist, now.split(", ", 1)[1], _left(trip, walked))
        out = _scene(trip, you, text, legs, route, log, maps=False)
    else:
        state = "next step" if off <= ON_ROUTE_M else "off route, re-routed"
        text = "\n".join((["Off route, new way:"] if off > ON_ROUTE_M else [])
                         + ["Now: " + now[0].lower() + now[1:], then, _left(trip, walked)])
        out = _scene(trip, you, text, legs, route, log)
    trip.route, trip.street = route, street
    log.append("%s: %.0f m from the last route" % (state, off))
    out.update(state=state, distance_m=walked, now=now, then=then, steps=[leg[0] for leg in legs], log=log)
    return out


def along(trip, metres):
    """The point `metres` along the last route from where the person is (to simulate walking)."""
    pts = [trip.you] + trip.route
    for a, b in zip(pts, pts[1:]):
        hop = geo.haversine_m(a, b)
        if hop >= metres:
            t = metres / (hop or 1)
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        metres -= hop
    return trip.route[-1]


def astray(trip, metres=80):
    """A street point about `metres` from the person, off the route and not back the way they came (to simulate a
    wrong turn)."""
    gi, (r0, c0) = trip.gi, trip.gi.cell_of(trip.you)
    ahead = trip.route[min(len(trip.route) - 1, 6)]
    reach = int(metres * 1.6 / gi.cw) + 1
    best = None
    for r in range(max(0, r0 - reach), min(gi.rows, r0 + reach + 1)):
        for c in range(max(0, c0 - reach), min(gi.cols, c0 + reach + 1)):
            if gi.terrain[r, c] != grid.ROAD:
                continue
            p = gi.cell_center(r, c)
            d = geo.haversine_m(trip.you, p)
            if not metres * 0.6 <= d <= metres * 1.6 or min(geo.haversine_m(p, q) for q in trip.route[::3]) <= 2 * ON_ROUTE_M:
                continue
            behind = geo.haversine_m(p, ahead) > geo.haversine_m(trip.you, ahead) + metres * 0.5
            score = abs(d - metres) + (1000 if behind else 0)
            if best is None or score < best[0]:
                best = (score, p)
    return best[1] if best else along(trip, metres)
