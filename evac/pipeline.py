"""SMS in -> (am I affected?) -> aerial map -> ASCII map + directions out."""
import os
import re

from . import geo, grid, imagemap, imagery, laptopmap, llm, notice, osm, segment, textmap

ALERT_BUFFER_M = 3000   # outside the zone but this close still gets a map
DEFAULT_RADIUS_M = 150  # no radius given: treat it as a point incident (a building, an intersection)


def locate(place, near, log):
    """Geocode the incident. Nominatim misplaces intersections ('5 Av and W 47 St' lands ~550 m off),
    so for 'A and B' find where the two streets meet in the OSM road data around the rough hit."""
    # Look the place up as written first: biasing toward the reader turned "5 Av and W 47 St, Manhattan"
    # into a 5th Ave in Los Angeles for a reader in LA. The bias is only a fallback.
    streets = re.split(r"\s+(?:and|&|at)\s+", place.split(",")[0], flags=re.I)
    area = place.split(",", 1)[1] if "," in place else ""
    rough = (geo.geocode(place)
             or (len(streets) == 2 and geo.geocode(streets[0] + ("," + area if area else "")))  # "Lexington Av, Manhattan"
             or geo.geocode(place, near=near))
    if len(streets) == 2:
        lat, lon = rough or near
        try:
            roads = osm.fetch_roads(lat - 0.01, lon - 0.013, lat + 0.01, lon + 0.013)
            p = osm.find_intersection(roads, streets[0], streets[1])
            if p:
                log.append("intersection %s & %s found in road data at %s (geocoder said %s)" % (streets[0], streets[1], p, rough))
                return p
        except Exception as ex:
            log.append("intersection lookup failed (%s)" % ex)
    return rough


def fmt_dist(m):
    return "%d m" % round(m, -1) if m < 1000 else "%.1f km" % (m / 1000)


def run(sms, user=None, cols=40, seg_backend="color", out_dir="out", laptop=False, image=False):
    n = notice.parse(sms)
    user = geo.current_location(user)
    log = ["notice (%s): %s" % (n.get("_parser"), {k: v for k, v in n.items() if not k.startswith("_")})]

    center = locate(n["zone_center_place"], user, log) if n.get("zone_center_place") else None
    if not center:
        return {"affected": None, "message": "Couldn't locate the evacuation zone in this notice. Follow official instructions.", "log": log}
    radius = n.get("zone_radius_m") or DEFAULT_RADIUS_M
    d = geo.haversine_m(user, center)
    inside = d < radius
    log.append("user %s, zone center %s r=%dm, distance %dm, inside=%s" % (user, center, radius, d, inside))
    if d > radius + ALERT_BUFFER_M:
        return {"affected": False, "message": "You are %s outside the %s zone. No action needed." % (fmt_dist(d - radius), n.get("hazard")), "log": log}

    shelters = [(s, geo.geocode(s, near=user)) for s in n.get("shelters") or []]
    shelters = [(s, p) for s, p in shelters if p]

    # User-centric map: big enough to reach the zone edge, and the nearest shelter if it's close.
    # Scales with the zone so a one-building incident gets a block-level map (~25 m per character).
    margin = min(1500, max(300, radius / 2))
    half = max(min(1500, 3 * radius), (radius - d) + margin) if inside else max(400, min(2000, 1.5 * d))
    half = min(half, 8000)
    shelter = min(shelters, key=lambda sp: geo.haversine_m(user, sp[1])) if shelters else None
    if shelter and geo.haversine_m(user, shelter[1]) < half * 1.5:
        half = max(half, geo.haversine_m(user, shelter[1]) * 1.15)
    g = grid.Grid(user, half, half, cols)
    s, w, nn, e = g.bbox()

    img = imagery.fetch_aerial(s, w, nn, e)
    os.makedirs(out_dir, exist_ok=True)
    img.save(os.path.join(out_dir, "aerial.png"))
    masks = segment.segment(img, seg_backend)
    g.add_terrain(masks)
    roads = []
    try:
        roads = osm.fetch_roads(s, w, nn, e)
        g.add_roads(roads, n.get("avoid_roads") or [])
        for road, spans in g.closure_spans.items():
            log.append("closure %r: %s" % (road, "segment between cross streets" if all(spans) else "whole road in view (no cross streets given, or not found)"))
    except Exception as ex:
        log.append("roads unavailable (%s); routing on imagery only" % ex)
    g.add_hazard_circle(center, radius)

    ucell = g.cell_of(user)
    scell = g.cell_of(shelter[1]) if shelter else None
    goals = {scell} if scell else (g.safe_cells(n.get("hazard")) if inside else set())
    path = g.route(ucell, goals, n.get("hazard"))
    steps = g.directions(path)
    if shelter and not scell:
        km = geo.haversine_m(user, shelter[1]) / 1000
        steps.append("Continue to %s (%.1f km %s)" % (shelter[0], km, geo.compass(user, shelter[1])))
    tm = None
    if roads and textmap.is_grid_place(n.get("zone_center_place")):
        tm = textmap.render_grid(roads, center, user, radius, n.get("avoid_roads") or [], shelter)
    if not tm:
        tm = textmap.render_raster(g, ucell, scell, path)
    log.append("text map: %d rows, worst row drift %.2f cells" % (len(tm["rows"]), tm["drift"]))

    text = instructions(n, inside, d, radius, shelter, steps)
    msg = compose(text, tm, n, shelter)
    wide = None
    if laptop:  # same view and roads, drawn wide for a monospace screen; laptop=True or a column count
        try:
            wide = laptopmap.render(n, user, center, radius, roads, shelter, half,
                                    cols=laptop if laptop is not True else laptopmap.COLS)
            log.append("laptop map: %dx%d, %d m per char, %.1f KB (%.1f KB gzipped) vs %.0f KB aerial JPEG"
                       % (wide["cols"], wide["rows"], wide["m_per_char"], wide["bytes"] / 1e3, wide["gzip"] / 1e3, wide["aerial_bytes"] / 1e3))
        except Exception as ex:
            log.append("laptop map failed (%s)" % ex)
    picture = None
    if image:  # the high-data version: an ordinary picture map of the same view
        try:
            picture = imagemap.render(n, g, img, masks, roads, center, radius, user, shelter, path)
            log.append("image map: %dx%d JPEG, %.0f KB" % (picture["width"], picture["height"], picture["bytes"] / 1e3))
        except Exception as ex:
            log.append("image map failed (%s)" % ex)
    return {"affected": True, "inside": inside, "map": tm, "steps": steps, "text": text, "message": msg,
            "laptop": wide, "image": picture, "log": log}


def instructions(n, inside, d, radius, shelter, steps):
    head = ("EVACUATE NOW - you are INSIDE the %s zone." if inside and n.get("order") == "mandatory"
            # Big zones: how far to the zone's edge. Point incidents: how far to the incident itself.
            else "You are inside the %s zone." if inside else "Heads up: %s incident %s from you." % ("%s", fmt_dist(d - radius if radius >= 1000 else d)))
    head = head % n.get("hazard", "hazard")
    lines = [head]
    if shelter:
        lines.append("Shelter: " + shelter[0])
    lines += ["%d. %s" % (i + 1, s) for i, s in enumerate(steps[:6])]
    if n.get("avoid_roads"):
        lines.append("Avoid: " + ", ".join(notice.segment_text(s) for s in n["avoid_roads"]))
    text = "\n".join(lines)
    if llm.available():
        try:
            text = llm.chat([
                {"role": "system", "content": "You write emergency evacuation SMS messages. Be calm and direct, under 400 characters, "
                 "one short sentence per line. Keep road names, directions and every distance (including how far the "
                 "incident is from the reader) exactly as given. Do not invent roads or shelters. Plain text only."},
                {"role": "user", "content": "Notice summary: %s\nDraft:\n%s" % (n.get("summary"), text)},
            ]).strip()
        except Exception as e:  # keep the template text so a flaky API never blocks the alert
            print("[compose] LLM rewrite failed (%s); using template" % e)
    return text


def compose(text, tm, n, shelter):
    lines = [text, "", tm["title"]] + tm["rows"] + [""] + textmap.legend(tm["keys"], n.get("hazard"), shelter and shelter[0])
    if tm["footer"]:
        lines.append(tm["footer"])
    return "\n".join(lines)
