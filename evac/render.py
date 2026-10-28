"""Scene -> every version of a reply. Modes build a Scene (evac/scene.py); nothing here fetches data or routes.

  sms(scene)              low data: message text + a map aligned for iPhone Messages (SF Pro)
  mono(scene, cols)       medium data: monospace map for WhatsApp / laptops (laptopmap.py)
  picture(scene)          high data: JPEG picture map (imagemap.py)
  small(scene)            slow connection: the picture map drawn at 400 px, WebP
"""
from . import imagemap, laptopmap, textmap


def sms(scene):
    """Returns (message, text map dict). Manhattan intersections use the avenue/street grid, everything else the
    routing grid shrunk to a phone-width raster."""
    tm, focused = None, scene.focus is not None
    if scene.street_grid and scene.roads and scene.zone:
        tm = textmap.render_grid(scene.roads, scene.zone[0], scene.you, scene.zone[1], scene.closures, scene.goal,
                                 **({"halo": False, "pad": 1} if focused else {}))
    if not tm:
        g = scene.frame()
        goal = g.cell_of(scene.goal[1]) if scene.goal else None
        tm = textmap.render_raster(g, g.cell_of(scene.you), goal, g.cells_along(scene.route, interpolate=False),
                                   min_rank=1 if focused and scene.walk else 2, outline=focused)
    legend = textmap.legend(tm["keys"], scene.hazard, scene.goal and scene.goal[0])
    if not scene.alert:  # directions: a destination and a route, not a shelter and a way out
        legend = [l.replace("way out", "route").replace("shelter:", "destination:") for l in legend]
    lines = [scene.text, "", tm["title"]] + tm["rows"] + [""] + legend
    if tm["footer"]:
        lines.append(tm["footer"])
    return "\n".join(lines), tm


def mono(scene, cols=laptopmap.COLS):
    return laptopmap.render(scene, cols)


def picture(scene, **kw):
    return imagemap.render(scene, **kw)


def small(scene):
    return imagemap.render(scene, **imagemap.SMALL)
