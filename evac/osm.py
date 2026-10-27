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


def fetch_roads(south, west, north, east):
    """Return a list of roads: {"name", "kind", "pts": [(lat, lon), ...]}."""
    q = '[out:json][timeout:25];way["highway"~"^(%s)$"](%f,%f,%f,%f);out geom tags;' % (DRIVABLE, south, west, north, east)
    # Cache on disk: Overpass is flaky and a live demo shouldn't depend on it.
    cache = os.path.join(CACHE_DIR, hashlib.md5(q.encode()).hexdigest() + ".json")
    if os.path.exists(cache):
        with open(cache) as f:
            elements = json.load(f)
    else:
        err = None
        for url in OVERPASS_MIRRORS:  # public instances 429/502 often; try the next one
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=60)
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
