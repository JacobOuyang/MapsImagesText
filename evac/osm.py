"""Road network from OpenStreetMap (Overpass). Used as ground truth for where you can drive/walk."""
import hashlib
import json
import os
import re

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
    """Return a list of roads: {"name", "kind", "pts": [(lat, lon), ...]}. kinds: highway regex (default DRIVABLE)."""
    q = '[out:json][timeout:25];way["highway"~"^(%s)$"](%f,%f,%f,%f);out geom tags;' % (kinds or DRIVABLE, south, west, north, east)
    elements = _overpass(q)
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


def _overpass(q, mirrors=OVERPASS_MIRRORS, timeout=60):
    # Cache on disk: Overpass is flaky and a live demo shouldn't depend on it.
    cache = os.path.join(CACHE_DIR, hashlib.md5(q.encode()).hexdigest() + ".json")
    if os.path.exists(cache):
        with open(cache) as f:
            elements = json.load(f)
    else:
        err = None
        for url in mirrors:  # public instances 429/502 often; try the next one
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=timeout)
                r.raise_for_status()
                elements = r.json().get("elements", [])
                break
            except Exception as e:
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
