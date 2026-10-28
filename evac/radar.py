"""Weather radar over a thin connection. The server reads the latest radar frame (RainViewer, no key), turns the
colours back into rain strength, and sends it three ways:

  low    - SMS: what's happening at your spot and where the nearest rain is, plus a small shaded grid
  medium - WhatsApp: a wider monospace radar with towns, you, and a 2-hour rain timeline for your spot
  high   - the radar picture itself over a basemap

The 2-hour timeline for your exact spot comes from Open-Meteo's 15-minute forecast (also no key), because the
free radar has no forecast frames.
"""
import base64
import datetime
import gzip
import io
import math
import time

import numpy as np
import requests
from PIL import Image, ImageDraw

from . import geo, imagemap, imagery, llm, osm

FRAMES_URL = "https://api.rainviewer.com/public/weather-maps.json"
RADAR_Z = 7  # the most detailed zoom RainViewer's free tiles serve (~1 km per pixel)
BASEMAP = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
LABELS = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
FORECAST = "https://api.open-meteo.com/v1/forecast"
# RainViewer's tile colours ("Universal Blue", rainviewer_api_colors_table.csv) -> dBZ, to read rain strength back out.
PALETTE = [(10, "cec087"), (15, "88ddee"), (20, "00a3e0"), (25, "0077aa"), (30, "005588"), (34, "004768"),
           (35, "ffee00"), (40, "ffaa00"), (44, "ff8100"), (45, "ff4400"), (50, "c10000"), (54, "5d0000"),
           (55, "ffaaff"), (60, "ff77ff"), (64, "ff4eff"), (65, "ffffff")]
LEVEL_DBZ = [15, 30, 40, 50]  # light, moderate, heavy, storm
LEVEL_NAME = ["none", "light", "moderate", "heavy", "storm"]
MONO_SHADE = " ░▒▓█"
SMS_SHADE = " +×0#"  # iPhone-safe, lightest to darkest
YOU = "\U0001F534"
SMS_YOU = " \U0001F534 "  # padded to two SF Pro digit widths, as in the emergency map
MONO_CELL, SMS_CELL = 16.9 / 7.53, 22 / 10.28

_frames = {"t": 0, "data": None}
_no_places = {}


def frames():
    """RainViewer frame list, cached for two minutes."""
    if time.time() - _frames["t"] > 120:
        _frames["data"] = requests.get(FRAMES_URL, headers=geo.UA, timeout=20).json()
        _frames["t"] = time.time()
    return _frames["data"]


def _tile_url(meta, frame):
    return meta["host"] + frame["path"] + "/256/{z}/{x}/{y}/2/0_0.png"


def to_dbz(rgba):
    """RGBA radar pixels -> dBZ (0 where there is no echo), by nearest palette colour."""
    a = np.asarray(rgba).astype(np.int32)
    pal = np.array([[int(h[i:i + 2], 16) for i in (0, 2, 4)] for _, h in PALETTE])
    d = ((a[..., None, :3] - pal[None, None]) ** 2).sum(-1)
    dbz = np.array([v for v, _ in PALETTE])[d.argmin(-1)]
    return np.where(a[..., 3] > 40, dbz, 0)


def _levels(dbz):
    return np.searchsorted(LEVEL_DBZ, dbz, side="right")


def _bbox(user, half_w_km, half_h_km):
    p = geo.LocalProjection(user)
    s, w = p.to_latlon((-half_w_km * 1000, -half_h_km * 1000))
    n, e = p.to_latlon((half_w_km * 1000, half_h_km * 1000))
    return s, w, n, e


def _cells(lv, cols, cell_aspect, w_km, h_km):
    """Max rain level in each text cell (a cell is cell_aspect times taller than wide)."""
    rows = max(4, round(h_km / (w_km / cols * cell_aspect)))
    H, W = lv.shape
    out = np.zeros((rows, cols), int)
    for r in range(rows):
        for c in range(cols):
            win = lv[r * H // rows: max(r * H // rows + 1, (r + 1) * H // rows),
                     c * W // cols: max(c * W // cols + 1, (c + 1) * W // cols)]
            out[r, c] = win.max()
    return out


def _forecast(user):
    """Open-Meteo 15-minute precipitation for the next 2 hours at the user's spot: (times, mm), utc offset."""
    r = requests.get(FORECAST, params={"latitude": user[0], "longitude": user[1], "minutely_15": "precipitation",
                                       "forecast_minutely_15": 8, "timezone": "auto"}, headers=geo.UA, timeout=20).json()
    m = r["minutely_15"]
    return [t[11:16] for t in m["time"]], [float(x or 0) for x in m["precipitation"]], r.get("utc_offset_seconds", 0)


def _motion(meta, s, w, n, e, w_km, h_km):
    """Where the rain in view moved over the last ~40 minutes: (bearing deg, km/h) or None."""
    past = meta["radar"]["past"]
    if len(past) < 5:
        return None
    cents = []
    for f in (past[-5], past[-1]):
        lv = _levels(to_dbz(imagery.fetch_tiles(s, w, n, e, _tile_url(meta, f), z=RADAR_Z, mode="RGBA")))
        ys, xs = np.nonzero(lv >= 1)
        if len(xs) < 40:
            return None
        cents.append((xs.mean() / lv.shape[1] * w_km, ys.mean() / lv.shape[0] * h_km, f["time"]))
    (x0, y0, t0), (x1, y1, t1) = cents
    dx, dy = x1 - x0, -(y1 - y0)
    km = math.hypot(dx, dy)
    if km < 3:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360, km / ((t1 - t0) / 3600)


def _compass(deg):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg + 22.5) // 45) % 8]


def run(user, w_km=160, h_km=110, sms_cols=18, mono_cols=58):
    meta = frames()
    frame = meta["radar"]["past"][-1]
    s, w, n, e = _bbox(user, w_km / 2, h_km / 2)
    rgba = imagery.fetch_tiles(s, w, n, e, _tile_url(meta, frame), z=RADAR_Z, mode="RGBA")
    lv = _levels(to_dbz(rgba))
    times, mm, utc_off = _forecast(user)
    clock = datetime.datetime.utcfromtimestamp(frame["time"] + utc_off).strftime("%H:%M")

    # Facts for the message.
    H, W = lv.shape
    uy, ux = H // 2, W // 2
    here = int(lv[max(0, uy - 2): uy + 3, max(0, ux - 2): ux + 3].max())
    ys, xs = np.nonzero(lv >= 1)
    nearest = None
    if len(xs):
        dkm = np.hypot((xs - ux) / W * w_km, (ys - uy) / H * h_km)
        i = dkm.argmin()
        bearing = math.degrees(math.atan2(xs[i] - ux, -(ys[i] - uy))) % 360
        nearest = (dkm[i], _compass(bearing), LEVEL_NAME[lv[ys[i], xs[i]]])
    try:
        motion = _motion(meta, s, w, n, e, w_km, h_km)
    except Exception:
        motion = None
    facts = ["Radar at %s." % clock]
    facts.append("Rain over you now: %s." % LEVEL_NAME[here] if here else
                 "No rain over you now. Nearest rain: %.0f km %s (%s)." % nearest if nearest else
                 "No rain on radar within %d km." % (w_km // 2))
    if motion:
        facts.append("The rain is moving toward the %s at about %.0f km/h." % (_compass(motion[0]), motion[1]))
    wet = [i for i, x in enumerate(mm) if x >= 0.1]
    if wet:
        peak = max(range(len(mm)), key=lambda i: mm[i])
        facts.append("Forecast for your spot: rain from %s to about %s, heaviest around %s (%.1f mm in 15 min)."
                     % (times[wet[0]], times[min(len(times) - 1, wet[-1] + 1)], times[peak], mm[peak]))
    else:
        facts.append("Forecast for your spot: no rain in the next 2 hours.")
    text = "\n".join(facts)
    if llm.available():
        try:
            text = llm.chat([{"role": "system", "content": "Rewrite this weather update as a short, friendly text message. "
                              "Keep every time, distance, direction and number exactly. Plain text, under 300 characters."},
                             {"role": "user", "content": text}]).strip()
        except Exception:
            pass

    # Places to label (cached Overpass; the map still works without them).
    key = (round(s, 1), round(w, 1))
    places = []
    if time.time() - _no_places.get(key, 0) > 600:  # don't wait on a slow Overpass twice in ten minutes
        try:
            places = osm.fetch_places(s, w, n, e)[:12]
        except Exception:
            _no_places[key] = time.time()
    proj = geo.LocalProjection(user)

    def cell_of(lat, lon, cols, rows):
        x, y = proj.to_xy((lat, lon))
        return int((h_km / 2 - y / 1000) / h_km * rows), int((x / 1000 + w_km / 2) / w_km * cols)

    # Medium: monospace map.
    g = _cells(lv, mono_cols, MONO_CELL, w_km, h_km)
    rows = len(g)
    grid = [[MONO_SHADE[v] for v in row] for row in g]
    taken = [[False] * mono_cols for _ in range(rows)]
    ur, uc = rows // 2, mono_cols // 2 - 1
    grid[ur][uc], grid[ur][uc + 1] = YOU, ""
    taken[ur][uc] = taken[ur][uc + 1] = True
    for p in places:
        r, c = cell_of(p["lat"], p["lon"], mono_cols, rows)
        label = "·" + p["name"][:14]
        if 0 <= r < rows and 0 <= c and c + len(label) < mono_cols and all(
                not taken[r][c + i] and grid[r][c + i] in " ░" for i in range(-1, len(label) + 1) if 0 <= c + i < mono_cols):
            for i, ch in enumerate(label):
                grid[r][c + i] = ch
                taken[r][c + i] = True
    km_per_col = w_km / mono_cols
    bar_km = next(k for k in (5, 10, 20, 25, 50, 100) if k / km_per_col >= 8)
    bar = "├" + "─" * max(1, round(bar_km / km_per_col) - 2) + "┤ %d km" % bar_km
    head = " ↑ N  radar %s " % clock
    lines = ["┌─" + head + "─" * (mono_cols - 2 - len(head)) + "─┐"]
    lines += ["│" + "".join(r) + "│" for r in grid]
    lines.append("└─ " + bar + " " + "─" * (mono_cols - 3 - len(bar)) + "┘")
    spark = "".join(" ▁▂▃▄▅▆▇█"[min(8, int(math.ceil(x / 0.5)))] if x >= 0.1 else "·" for x in mm)
    lines += ["", "Your spot, next 2 h (15 min steps): %s" % spark, "%s now  ...  %s" % (times[0], times[-1]),
              "", "░ light  ▒ moderate  ▓ heavy  █ storm   %s you" % YOU]
    mono = "\n".join(lines)

    # Low: small SMS grid in iPhone-safe characters.
    gs = _cells(lv, sms_cols, SMS_CELL, w_km, h_km)
    srows = [[SMS_SHADE[v] for v in row] for row in gs]
    r0, c0 = len(srows) // 2, sms_cols // 2 - 1
    srows[r0][c0], srows[r0][c0 + 1] = SMS_YOU, ""
    sms = "\n".join([text, "", "RADAR (north is up, %d km across):" % w_km] + ["".join(r) for r in srows] +
                    ["", "+ light  × moderate", "0 heavy  # storm", "\U0001F534 you"])

    image = _picture(user, (s, w, n, e), rgba, clock, w_km)
    return {"text": text, "sms": sms, "mono": mono, "mono_bytes": len(mono.encode()),
            "mono_gzip": len(gzip.compress(mono.encode())), "image": image, "frame_time": frame["time"],
            "here": LEVEL_NAME[here], "nearest": nearest, "motion": motion, "forecast": list(zip(times, mm))}


def _picture(user, bbox, rgba, clock, w_km):
    """High data: radar over a light basemap with place names, like a weather app."""
    s, w, n, e = bbox
    base = imagery.fetch_tiles(s, w, n, e, BASEMAP, max_px=1100, mode="RGB")
    labels = imagery.fetch_tiles(s, w, n, e, LABELS, max_px=1100, mode="RGBA")
    radar = rgba.resize(base.size, Image.BILINEAR)
    alpha = radar.getchannel("A").point(lambda a: int(a * 0.8))
    base.paste(radar.convert("RGB"), (0, 0), alpha)
    base.paste(labels, (0, 0), labels)
    d = ImageDraw.Draw(base)
    W, H = base.size
    d.ellipse([W / 2 - 8, H / 2 - 8, W / 2 + 8, H / 2 + 8], fill="#d70015", outline="#ffffff", width=3)
    f, fb = imagemap._font(6), imagemap._font(8, bold=True)  # _font sizes are at 2x
    d.rectangle([0, 0, W, 30], fill="#1f3b57")
    d.text((10, 7), "RAIN RADAR  ·  %s" % clock, font=fb, fill="#ffffff")
    for i, (dbz, hexc) in enumerate([(20, "00a3e0"), (30, "005588"), (40, "ffaa00"), (50, "c10000"), (60, "ff77ff")]):
        x = W - 300 + i * 58
        d.rectangle([x, 9, x + 12, 21], fill="#" + hexc)
        d.text((x + 15, 9), ["light", "mod.", "heavy", "storm", "hail"][i], font=f, fill="#ffffff")
    d.text((W - 6, H - 4), "Radar © RainViewer · basemap © Esri · %d km across" % w_km, font=f, fill="#555555", anchor="rd")
    buf = io.BytesIO()
    base.save(buf, "JPEG", quality=82)
    data = buf.getvalue()
    return {"data_url": "data:image/jpeg;base64," + base64.b64encode(data).decode(), "bytes": len(data),
            "width": W, "height": H}


def find_rain():
    """Somewhere it is raining hard right now (for demos): the strongest patch of radar echo worldwide."""
    meta = frames()
    url = _tile_url(meta, meta["radar"]["past"][-1])
    world = imagery.fetch_tiles(-85, -180, 85, 179.99, url, z=2, mode="RGBA")
    lv = _levels(to_dbz(world)).astype(float)
    k = 5
    pad = np.pad(lv >= 3, k).astype(float).cumsum(0).cumsum(1)
    score = pad[2 * k:, 2 * k:] - pad[:-2 * k, 2 * k:] - pad[2 * k:, :-2 * k] + pad[:-2 * k, :-2 * k]
    y, x = np.unravel_index(score.argmax(), score.shape)
    x0, y0 = imagery._lonlat_to_px(85, -180, 2)
    gx, gy = x0 + x * world.width / lv.shape[1], y0 + y
    size = imagery.TILE * 4
    lon = gx / size * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / size))))
    return round(lat, 4), round(lon, 4)
