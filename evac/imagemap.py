"""High-data version: an ordinary picture map, styled like the maps emergency agencies post (street basemap,
red zone, closed roads, route out, you and the shelter). Drawn from the same roads, zone and route as the text
maps, so the three versions can be compared side by side. Returns a JPEG, the format a chat app would send."""
import base64
import io
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import geo, grid
from .osm import norm_road

WIDTH = 800
SS = 2  # draw at 2x and shrink, for smooth lines
FONT = "/System/Library/Fonts/Helvetica.ttc"
# rank -> (casing width, fill width, fill colour) at SS scale
ROAD_STYLE = {1: (7, 5, "#ffffff"), 2: (10, 7, "#ffffff"), 3: (13, 10, "#fcd68a"), 4: (13, 10, "#fcd68a"),
              5: (15, 12, "#f9b26b"), 6: (15, 12, "#f9b26b")}


def _font(size, bold=False):
    try:
        return ImageFont.truetype(FONT, size * SS, index=1 if bold else 0)
    except Exception:
        return ImageFont.load_default()


def render(scene, width=WIDTH, fmt="JPEG", quality=82):
    """Scene -> a picture map. A scene with no zone is plain directions (goal = destination).
    A small `width` draws the map for that size (same text and line sizes in pixels, so fewer but readable labels)
    instead of shrinking a big one, which turns labels into mush."""
    g = grid.Grid(scene.center or scene.you, scene.half_m, scene.half_m, scene.cols)  # the frame the aerial masks cover
    g.set_closures(scene.roads, scene.closures)
    masks, roads, user, shelter = scene.masks or {}, scene.shown, scene.you, scene.goal
    center, radius = scene.zone if scene.zone else (None, 0)
    w_m, h_m = g.cols * g.cw, g.rows * g.ch
    W = width * SS
    H = int(W * h_m / w_m)
    s = W / w_m
    m_per_px = 1 / (s / SS)

    def px(latlon):
        x, y = g.proj.to_xy(latlon)
        return (x - g.x0) * s, (g.y1 - y) * s

    im = Image.new("RGB", (W, H), "#f2efe9")
    for cls, colour in (("vegetation", "#d6e7c4"), ("water", "#aad3df")):
        # At street scale the colour heuristic calls rooftops trees; only trust it zoomed out.
        # A focused street-scale map skips water too: blue rooftops read as specks of lake.
        if cls in masks and ((cls == "water" and scene.focus is None) or m_per_px > 4):
            m = Image.fromarray(masks[cls].astype(np.uint8) * 255).resize((W, H)).filter(ImageFilter.MedianFilter(9))
            im.paste(colour, (0, 0), m)
    d = ImageDraw.Draw(im)

    # A focused scene: the other roads only as faint hairlines, so the route still has a city around it.
    if scene.focus is not None:
        drawn = {id(rd) for rd in roads}
        for rd in scene.roads:
            if id(rd) not in drawn:
                d.line([px(p) for p in rd["pts"]], fill="#dcd6cc", width=2 * SS if m_per_px < 3 else SS, joint="curve")

    # Roads, minor first, casing under fill.
    spans = getattr(g, "closure_spans", {})
    styled = []
    for rd in roads:
        rank = grid.ROAD_RANK.get(rd["kind"].replace("_link", ""), 1)
        styled.append((rank, rd, [px(p) for p in rd["pts"]]))
    styled.sort(key=lambda t: t[0])
    thin = m_per_px > 5  # zoomed out: side streets as hairlines
    for layer in (0, 1):
        for rank, rd, pts in styled:
            cw, fw, colour = ROAD_STYLE[rank]
            if thin and rank == 1:
                cw, fw = 3, 2
            d.line(pts, fill="#c9c2b8" if layer == 0 else colour, width=cw if layer == 0 else fw, joint="curve")

    # Closed stretches in red, on top of the roads.
    for rank, rd, pts in styled:
        sp = spans.get(norm_road(rd["name"])) if rd["name"] else None
        if not sp:
            continue
        xy = [g.proj.to_xy(p) for p in rd["pts"]]
        run = []
        for (x, y), p in zip(xy, pts):
            if any(grid._within(x, y, span) for span in sp):
                run.append(p)
            else:
                if len(run) > 1:
                    d.line(run, fill="#d70015", width=12, joint="curve")
                run = []
        if len(run) > 1:
            d.line(run, fill="#d70015", width=12, joint="curve")

    # Danger zone: translucent red disc.
    if center:
        zone = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        zd = ImageDraw.Draw(zone)
        cx, cy = px(center)
        r = radius * s
        zd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 30, 30, 60), outline=(200, 0, 0, 230), width=5)
        im.paste(zone, (0, 0), zone)
    d = ImageDraw.Draw(im)

    # Route.
    if len(scene.route) > 1:
        rpts = [px(p) for p in scene.route]
        d.line(rpts, fill="#ffffff", width=20, joint="curve")
        d.line(rpts, fill="#1a73e8", width=12, joint="curve")
        (x0, y0), (x1, y1) = rpts[-2], rpts[-1]
        a = math.atan2(y1 - y0, x1 - x0)
        d.polygon([(x1 + 22 * math.cos(a), y1 + 22 * math.sin(a)),
                   (x1 + 16 * math.cos(a + 2.4), y1 + 16 * math.sin(a + 2.4)),
                   (x1 + 16 * math.cos(a - 2.4), y1 + 16 * math.sin(a - 2.4))], fill="#1a73e8")

    # Where each numbered step starts.
    for n, p in scene.marks:
        x, y = px(p)
        if 0 <= x < W and 0 <= y < H:
            d.ellipse([x - 15, y - 15, x + 15, y + 15], fill="#ffffff", outline="#1a73e8", width=5)
            d.text((x, y), str(n), font=_font(10, bold=True), fill="#1a73e8", anchor="mm")

    # Road names, biggest roads first, skipping any that would overlap.
    boxes = []
    f_lab = _font(11, bold=True)
    by_name = {}
    for rank, rd, pts in styled:
        if rd["name"] and (rank >= 2 or not thin or scene.focus is not None):
            inside = [p for p in pts if 0 <= p[0] < W and 0 <= p[1] < H]
            key = norm_road(rd["name"])
            if inside and len(inside) > len(by_name.get(key, (0, [], ""))[1]):
                by_name[key] = (rank, inside, rd["name"])
    for rank, inside, name in sorted(by_name.values(), key=lambda t: (-t[0], -len(t[1])))[:16]:
        x, y = inside[len(inside) // 2]
        tw, th = d.textbbox((0, 0), name, font=f_lab)[2:]
        box = (x - tw / 2 - 6, y - th / 2 - 6, x + tw / 2 + 6, y + th / 2 + 6)
        if box[0] < 0 or box[2] > W or box[1] < 70 * SS or box[3] > H or any(
                not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in boxes):
            continue
        boxes.append(box)
        d.text((x - tw / 2, y - th / 2), name, font=f_lab, fill="#333333", stroke_width=4, stroke_fill="#ffffff")

    # Incident, shelter, you.
    if center:
        d.regular_polygon((cx, cy, 20), 4, fill="#d70015", outline="#ffffff")
        d.text((cx, cy), "!", font=_font(14, bold=True), fill="#ffffff", anchor="mm")
    f_pin = _font(12, bold=True)
    if shelter:
        sx, sy = px(shelter[1])
        if 0 <= sx < W and 0 <= sy < H:
            d.ellipse([sx - 18, sy - 18, sx + 18, sy + 18], fill="#1e8e3e", outline="#ffffff", width=5)
            d.text((sx, sy), "S", font=f_pin, fill="#ffffff", anchor="mm")
            tw = d.textbbox((0, 0), shelter[0], font=f_pin)[2]
            right = sx + 26 + tw < W - 10  # label on whichever side stays inside the frame
            d.text((sx + 26, sy) if right else (sx - 26, sy), shelter[0], font=f_pin, fill="#1e8e3e",
                   anchor="lm" if right else "rm", stroke_width=4, stroke_fill="#ffffff")
        else:  # off the map: a tag on the edge pointing at it
            ux, uy = px(user)
            a = math.atan2(sy - uy, sx - ux)
            t = min(((W - 40) / 2) / abs(math.cos(a) or 1e-9), ((H - 140 * SS) / 2) / abs(math.sin(a) or 1e-9))
            ex, ey = W / 2 + t * math.cos(a), H / 2 + 30 * SS + t * math.sin(a)
            label = "%s  %.1f km %s" % (shelter[0], geo.haversine_m(user, shelter[1]) / 1000, geo.compass(user, shelter[1]))
            tw, th = d.textbbox((0, 0), label, font=f_pin)[2:]
            bx = min(max(ex - tw / 2, 10), W - tw - 30)
            d.rounded_rectangle([bx - 12, ey - th / 2 - 10, bx + tw + 12, ey + th / 2 + 10], 12, fill="#1e8e3e")
            d.text((bx, ey), label, font=f_pin, fill="#ffffff", anchor="lm")
    ux, uy = px(user)
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse([ux - 44, uy - 44, ux + 44, uy + 44], fill=(26, 115, 232, 50))
    im.paste(halo, (0, 0), halo)
    d = ImageDraw.Draw(im)
    d.ellipse([ux - 16, uy - 16, ux + 16, uy + 16], fill="#1a73e8", outline="#ffffff", width=6)
    d.text((ux + 24, uy), "You", font=f_pin, fill="#1a73e8", anchor="lm", stroke_width=4, stroke_fill="#ffffff")

    # Banner, north arrow, scale, legend, credits.
    title = scene.title
    d.rectangle([0, 0, W, 62 * SS], fill="#c62828")
    f_title = _font(20, bold=True)
    while title and d.textbbox((0, 0), title, font=f_title)[2] > W - 32 * SS:  # fit the banner at any width
        title = title[:-2].rstrip() + "…"
    d.text((16 * SS, 10 * SS), title, font=f_title, fill="#ffffff")
    sub = (scene.summary or "")[:int(width / 7.2)]
    d.text((16 * SS, 38 * SS), sub, font=_font(11), fill="#ffe1e1")
    d.polygon([(W - 30 * SS, 76 * SS), (W - 38 * SS, 98 * SS), (W - 22 * SS, 98 * SS)], fill="#333333")
    d.text((W - 30 * SS, 106 * SS), "N", font=_font(11, bold=True), fill="#333333", anchor="mm")
    nice = next((m for m in (50, 100, 200, 250, 500, 1000, 2000, 5000) if m * s >= 80 * SS), 5000)
    sx0, sy0 = W - 20 * SS - nice * s, H - 22 * SS
    d.rectangle([sx0 - 8 * SS, sy0 - 18 * SS, W - 12 * SS, H - 10 * SS], fill=(255, 255, 255))
    d.line([(sx0, sy0), (sx0 + nice * s, sy0)], fill="#333333", width=4)
    d.text((sx0 + nice * s / 2, sy0 - 4 * SS), "%d m" % nice if nice < 1000 else "%g km" % (nice / 1000),
           font=_font(10), fill="#333333", anchor="md")
    items = [("you", "You")] + ([("zone", "Danger zone")] if center else []) + ([("closed", "Road closed")] if spans else [])
    if len(scene.route) > 1:
        items.append(("route", "Way out" if center else "Route"))
    if shelter:
        items.append(("shelter", "Shelter" if center else "Destination"))
    lx, ly, f_leg = 12 * SS, H - (16 + 18 * len(items)) * SS, _font(11)
    d.rectangle([lx - 6 * SS, ly - 8 * SS, lx + 118 * SS, H - 10 * SS], fill="#ffffff", outline="#cccccc")
    for i, (k, text) in enumerate(items):
        y = ly + i * 18 * SS + 6 * SS
        if k == "you":
            d.ellipse([lx, y - 6 * SS, lx + 12 * SS, y + 6 * SS], fill="#1a73e8", outline="#ffffff", width=3)
        elif k == "zone":
            d.rectangle([lx, y - 6 * SS, lx + 12 * SS, y + 6 * SS], fill="#f3b8b8", outline="#c80000", width=3)
        elif k == "closed":
            d.line([(lx, y), (lx + 12 * SS, y)], fill="#d70015", width=10)
        elif k == "route":
            d.line([(lx, y), (lx + 12 * SS, y)], fill="#1a73e8", width=10)
        else:
            d.ellipse([lx, y - 6 * SS, lx + 12 * SS, y + 6 * SS], fill="#1e8e3e")
        d.text((lx + 20 * SS, y), text, font=f_leg, fill="#333333", anchor="lm")
    d.text((W - 12 * SS, H - 4 * SS), "Roads © OpenStreetMap contributors · imagery Esri", font=_font(8),
           fill="#777777", anchor="rd")

    out = im.resize((width, H // SS), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, fmt, quality=quality)
    data = buf.getvalue()
    return {"data_url": "data:image/%s;base64,%s" % (fmt.lower(), base64.b64encode(data).decode()), "bytes": len(data),
            "width": width, "height": H // SS, "format": fmt}


SMALL = {"width": 400, "fmt": "WEBP", "quality": 50}  # the "small picture" tier: ~5-15 KB


def shrink(data_or_img, width=400, quality=50):
    """For pictures that can't be redrawn (a photo, the radar): resize to `width` and save as WebP."""
    im = data_or_img if isinstance(data_or_img, Image.Image) else Image.open(io.BytesIO(data_or_img))
    im = im.convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality)
    data = buf.getvalue()
    return {"data_url": "data:image/webp;base64," + base64.b64encode(data).decode(), "bytes": len(data),
            "width": im.width, "height": im.height, "format": "WEBP"}
