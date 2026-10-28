"""Walking directions abroad on a capped roaming plan. Asked directly, Muse Spark gives directions from memory: they
read well but it can't know where you are standing, some turns are wrong, and its sketch isn't to scale. Here
the route is worked out on real street and footpath data from the person's GPS, and Muse only rewords the steps.
Same three versions as an alert: SMS text map, WhatsApp monospace map, picture map."""
import gzip

from . import geo, grid, imagemap, imagery, laptopmap, llm, osm, segment, textmap

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

    tm = textmap.render_raster(g, ucell, dcell, path)
    legend = [l.replace("way out", "route").replace("shelter:", "destination:")
              for l in textmap.legend(tm["keys"], None, name)]
    sms = "\n".join([text, "", tm["title"]] + tm["rows"] + [""] + legend)
    log.append("sms map: %d rows, worst row drift %.2f cells" % (len(tm["rows"]), tm["drift"]))

    n_ = {"zone_center_place": "", "summary": "From your location to %s" % name}
    wide = laptopmap.render(n_, user, None, 0, roads, goal, half, cols=mono_cols, walk=True)
    # The picture can show a much finer route than the 40-column SMS grid.
    gi = grid.Grid(user, half, half, 160)
    gi.terrain[:] = grid.BUILDING
    gi.add_roads(roads)
    ui, di = gi.cell_of(user), gi.cell_of(dest)
    fine = gi.route(ui, {di}, None) if ui and di else []
    img = imagery.fetch_aerial(*gi.bbox())
    pic = imagemap.render(n_, gi, img, segment.segment(img), roads, None, 0, user, goal, fine,
                          title="WALKING  ·  to %s" % name[:28])
    return {"text": text, "sms": sms, "mono": wide["text"], "mono_bytes": wide["bytes"], "mono_gzip": wide["gzip"],
            "image": pic, "distance_m": walked, "steps": steps, "log": log}
