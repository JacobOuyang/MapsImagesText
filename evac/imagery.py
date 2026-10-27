"""Fetch and stitch aerial imagery (Esri World Imagery XYZ tiles, no key) for a lat/lon bbox."""
import io
import math

import requests
from PIL import Image

from .geo import UA

TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE = 256


def _lonlat_to_px(lat, lon, z):
    n = TILE * 2 ** z
    x = (lon + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def fetch_aerial(south, west, north, east, max_px=1024):
    """Return a PIL image exactly covering the bbox, at the highest zoom that fits in max_px."""
    for z in range(19, 8, -1):
        x0, y0 = _lonlat_to_px(north, west, z)
        x1, y1 = _lonlat_to_px(south, east, z)
        if max(x1 - x0, y1 - y0) <= max_px:
            break
    tx0, ty0, tx1, ty1 = int(x0 // TILE), int(y0 // TILE), int(x1 // TILE), int(y1 // TILE)
    canvas = Image.new("RGB", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            r = requests.get(TILE_URL.format(z=z, x=tx, y=ty), headers=UA, timeout=20)
            r.raise_for_status()
            canvas.paste(Image.open(io.BytesIO(r.content)).convert("RGB"), ((tx - tx0) * TILE, (ty - ty0) * TILE))
    ox, oy = tx0 * TILE, ty0 * TILE
    return canvas.crop((int(x0 - ox), int(y0 - oy), int(x1 - ox), int(y1 - oy)))
