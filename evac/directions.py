"""Walking directions abroad on a capped roaming plan. Asked directly, Muse Spark gives directions from memory: they
read well but it can't know where you are standing, some turns are wrong, and its sketch isn't to scale. Here
the route is worked out on real street and footpath data from the person's GPS, and Muse only rewords the steps.
Same three versions as an alert: SMS text map, WhatsApp monospace map, picture map."""
from . import geo, grid, imagery, llm, osm, render, segment
from .scene import Scene

WALK_M_PER_MIN = 75  # ~4.5 km/h
MAX_WALK_M = 4000  # the street download grows with the square of the distance


def run(user, destination, sms_cols=40, mono_cols=58):
    log = []
    dest = geo.geocode(destination, near=user)
    if not dest:
        return {"error": "Couldn't find %r." % destination, "log": log}
    d = geo.haversine_m(user, dest)
    log.append("destination %r at %s, %d m away" % (destination, dest, d))
    if d > MAX_WALK_M:
        return {"error": "%s is %.1f km away, too far to walk. Try transit." % (destination, d / 1000), "log": log}
    name = destination.split(",")[0].strip()
    goal = (name, dest)

    half = d * 1.2 + 150  # user-centred, like the alert maps, with the destination well inside
    g = grid.Grid(user, half, half, sms_cols)
    s, w, n, e = g.bbox()
    roads = osm.fetch_roads(s, w, n, e, kinds=osm.WALKABLE)
    log.append("%d streets and paths in view" % len(roads))
    g.terrain[:] = grid.BUILDING  # walk along streets and paths, not through blocks
    g.add_roads(roads)
    ucell, dcell = g.cell_of(user), g.cell_of(dest)
    path = g.route(ucell, {dcell}, None) if ucell and dcell else []
    steps = g.directions(path)
    walked = sum(geo.haversine_m(g.cell_center(*a), g.cell_center(*b)) for a, b in zip(path, path[1:])) or d

    draft = "\n".join(["Walk to %s: about %.1f km, %d min." % (name, walked / 1000, round(walked / WALK_M_PER_MIN))]
                      + ["%d. %s" % (i + 1, st) for i, st in enumerate(steps[:6])])
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

    # The maps get a finer route than the 40-column grid the steps came from, so it follows the alleys.
    gi = grid.Grid(user, half, half, 160)
    gi.terrain[:] = grid.BUILDING
    gi.add_roads(roads)
    ui, di = gi.cell_of(user), gi.cell_of(dest)
    fine = gi.route(ui, {di}, None) if ui and di else []
    scene = Scene(you=user, half_m=half, text=text, roads=roads, goal=goal, cols=sms_cols, walk=True,
                  route=[gi.cell_center(*c) for c in fine] or [g.cell_center(*c) for c in path],
                  title="WALKING  ·  to %s" % name[:28], summary="From your location to %s" % name)
    scene.aerial = imagery.fetch_aerial(*scene.bbox())
    scene.masks = segment.segment(scene.aerial)
    sms, tm = render.sms(scene)
    log.append("sms map: %d rows, worst row drift %.2f cells" % (len(tm["rows"]), tm["drift"]))
    wide = render.mono(scene, mono_cols)
    return {"text": text, "sms": sms, "mono": wide["text"], "mono_bytes": wide["bytes"], "mono_gzip": wide["gzip"],
            "image": render.picture(scene), "small": render.small(scene), "distance_m": walked, "steps": steps,
            "scene": scene, "log": log}
