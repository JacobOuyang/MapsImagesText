"""Road network from OpenStreetMap (Overpass). Used as ground truth for where you can drive/walk."""
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .geo import UA, haversine_m

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "osm")

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
# Highway types worth routing on during an evacuation (skip footpaths/service alleys for driving).
DRIVABLE = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
# For walking directions: no motorways, plus pedestrian streets, footways and steps. Service roads and trail
# paths are left out: in a big city they multiply the download and made Overpass time out.
WALKABLE = "trunk|primary|secondary|tertiary|unclassified|residential|living_street|pedestrian|footway|steps|primary_link|secondary_link|tertiary_link"


def fetch_roads(south, west, north, east, kinds=None):
    """Return a list of roads: {"name", "kind", "pts": [(lat, lon), ...]}. kinds: highway regex (default DRIVABLE).
    An area inside one already downloaded is cut from it instead of downloaded again: an alert first fetches the
    streets around the incident (to find the intersection), then the map's area, which is usually inside that."""
    kinds = kinds or DRIVABLE
    bbox = (south, west, north, east)
    for done in _index():
        if done["kinds"] == kinds and _inside(bbox, done["bbox"]) and os.path.exists(_cache_path(done["query"])):
            return [r for r in _roads(_overpass(done["query"])) if _touches(r["pts"], bbox)]
    q = '[out:json][timeout:25];way["highway"~"^(%s)$"](%f,%f,%f,%f);out geom tags;' % (kinds, south, west, north, east)
    roads = _roads(_overpass(q))
    _remember({"kinds": kinds, "bbox": bbox, "query": q})
    return roads


INDEX = os.path.join(CACHE_DIR, "index.json")  # which areas the road cache holds, so covered areas aren't refetched


def _index():
    try:
        with open(INDEX) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _remember(entry):
    done = [d for d in _index() if d["query"] != entry["query"]] + [entry]
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(INDEX, "w") as f:
        json.dump(done, f)


def _inside(a, b):
    """bbox a (s, w, n, e) within bbox b."""
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _touches(pts, bbox):
    """The road's own bounding box overlaps bbox (so a road crossing it without a vertex inside is kept)."""
    s, w, n, e = bbox
    las, los = [la for la, _ in pts], [lo for _, lo in pts]
    return min(las) <= n and max(las) >= s and min(los) <= e and max(los) >= w


def _roads(elements):
    roads = []
    for el in elements:
        if "geometry" not in el:
            continue
        tags = el.get("tags", {})
        roads.append({
            "name": tags.get("name") or tags.get("ref") or "",
            "kind": tags.get("highway", ""),
            "pts": [(g["lat"], g["lon"]) for g in el["geometry"]],
        })
    return roads


# Things a walker can see and recognise, and how notable each kind is (3: you can't miss it).
LANDMARK_TAGS = {
    "tourism": ("attraction|museum|viewpoint", 3), "historic": (".", 3),
    "amenity": ("place_of_worship|theatre|cinema|fountain|hospital|marketplace|townhall", 3),
    "railway": ("station|subway_entrance", 3),
    "amenity ": ("pharmacy|bank|post_office|police", 2), "shop": ("supermarket|department_store|mall", 2),
    "amenity  ": ("cafe|restaurant|bar|pub|fast_food", 1),
}


def fetch_landmarks(south, west, north, east):
    """Named places a walker can spot (churches, monuments, stations, pharmacies, cafés...):
    [{"name", "kind", "rank", "lat", "lon"}]. Nice to have, so one quick try; [] if it fails."""
    bbox = "(%f,%f,%f,%f)" % (south, west, north, east)
    q = "[out:json][timeout:25];(%s);out center tags;" % "".join(
        'nwr["name"]["%s"~"^(%s)$"]%s;' % (k.strip(), v, bbox) if v != "." else 'nwr["name"]["%s"]%s;' % (k.strip(), bbox)
        for k, (v, _) in LANDMARK_TAGS.items())
    try:
        elements = _overpass(q, mirrors=OVERPASS_MIRRORS[:2], timeout=30)
    except Exception:
        return []
    out = []
    for el in elements:
        tags, c = el.get("tags", {}), el.get("center") or el
        if "lat" not in c:
            continue
        rank, kind = 0, ""
        for k, (v, r) in LANDMARK_TAGS.items():
            val = tags.get(k.strip())
            if val and (v == "." or re.fullmatch(v, val)) and r > rank:
                rank, kind = r, val
        if rank:
            out.append({"name": tags.get("name:en") or tags["name"], "kind": kind.replace("_", " "), "rank": rank,
                        "lat": c["lat"], "lon": c["lon"]})
    return out


def fetch_places(south, west, north, east):
    """Cities and towns in the bbox, biggest first: [{"name", "pop", "lat", "lon"}]. Nice-to-have labels, so
    only one quick try."""
    q = '[out:json][timeout:10];node["place"~"^(city|town)$"](%f,%f,%f,%f);out tags;' % (south, west, north, east)
    out = []
    for el in _overpass(q, mirrors=OVERPASS_MIRRORS[:1], timeout=12):
        tags = el.get("tags", {})
        try:
            pop = int(re.sub(r"[^0-9]", "", tags.get("population", "")) or 0)
        except ValueError:
            pop = 0
        if tags.get("name"):
            out.append({"name": tags.get("name:en") or tags["name"], "pop": pop + (10 ** 9 if tags.get("place") == "city" else 0),
                        "lat": el["lat"], "lon": el["lon"]})
    return sorted(out, key=lambda p: -p["pop"])


_POOL = ThreadPoolExecutor(8)  # mirror requests; a slow loser finishes in the background


def _cache_path(q):
    return os.path.join(CACHE_DIR, hashlib.md5(q.encode()).hexdigest() + ".json")


def _post(url, q, timeout):
    r = requests.post(url, data={"data": q}, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json().get("elements", [])


def _overpass(q, mirrors=OVERPASS_MIRRORS, timeout=40):
    """Cached on disk: Overpass is flaky and a live demo shouldn't depend on it. A new query goes to every mirror
    at once and the first answer wins: one at a time, with a 60 s timeout each, an alert once took over 2 minutes.
    40 s: on a busy day a big-city query took 33 s, and at 25 s all three mirrors gave up."""
    cache = _cache_path(q)
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    err = None
    for fut in as_completed([_POOL.submit(_post, url, q, timeout) for url in mirrors]):
        try:
            elements = fut.result()
            break
        except Exception as e:  # public instances 429/502/time out often; wait for the others
            err = e
    else:
        raise err
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache, "w") as f:
        json.dump(elements, f)
    return elements


ABBREV = {"blvd": "boulevard", "dr": "drive", "st": "street", "ave": "avenue", "av": "avenue", "rd": "road",
          "hwy": "highway", "pkwy": "parkway", "ln": "lane", "pl": "place", "sq": "square"}
ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6",
            "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10", "eleventh": "11", "twelfth": "12"}


def norm_road(name):
    """'W 47 St', 'West 47th Street' -> '47 street'; '5 Av', 'Fifth Avenue' -> '5 avenue'."""
    words = []
    for w in name.lower().replace(".", "").split():
        w = re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", w)
        w = ORDINALS.get(w, ABBREV.get(w, w))
        if w not in ("north", "south", "east", "west", "n", "s", "e", "w"):
            words.append(w)
    return " ".join(words)


def find_intersection(roads, a, b, max_gap_m=40):
    """(lat, lon) where roads named a and b meet, from shared or nearby vertices; None if they don't."""
    na, nb = norm_road(a), norm_road(b)
    pa = [p for r in roads if r["name"] and norm_road(r["name"]) == na for p in r["pts"]]
    pb = [p for r in roads if r["name"] and norm_road(r["name"]) == nb for p in r["pts"]]
    best = min(((haversine_m(p, q), p, q) for p in pa for q in pb), default=None)
    if not best or best[0] > max_gap_m:
        return None
    return (best[1][0] + best[2][0]) / 2, (best[1][1] + best[2][1]) / 2
