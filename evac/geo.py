"""Geocoding, user location, and distance helpers."""
import math

import requests

UA = {"User-Agent": "evac-ascii-prototype/0.1"}
EARTH_R = 6371000.0


def haversine_m(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def compass(a, b):
    """8-point bearing from a to b, e.g. 'NE'."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    ang = math.degrees(math.atan2(math.sin(dlon) * math.cos(lat2),
                                  math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((ang + 22.5) // 45) % 8]


def geocode(query, near=None):
    """Place name -> (lat, lon) via OSM Nominatim. `near` biases results to a ~50km box."""
    p = place(query, near)
    return p[:2] if p else None


BUSINESS = {"tourism", "amenity", "shop", "office", "craft", "leisure"}


def place(query, near=None):
    """Place name -> (lat, lon, what was matched) via OSM Nominatim. The label says what the name matched when it
    isn't the same name ("Atlantis" matched "Atlantis Inn Roma (hotel, Via Muzio Clementi)"), so a reply never
    passes off a different or fictional place as the one asked for; None when it is."""
    params = {"q": query, "format": "json", "limit": 1}
    if near:
        d = 0.5
        params["viewbox"] = "%f,%f,%f,%f" % (near[1] - d, near[0] + d, near[1] + d, near[0] - d)
    r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=UA, timeout=20)
    r.raise_for_status()
    hits = r.json()
    if not hits:
        return None
    h = hits[0]
    parts = [x.strip() for x in (h.get("display_name") or "").split(",")]
    name, asked = (parts[0] if parts else ""), query.split(",")[0].strip()
    label = None
    if name and name.lower() != asked.lower():
        label = name  # a landmark's local name helps with signs ("Colosseo")
        if h.get("class") in BUSINESS:  # a business may not be what was meant: say what and where
            kind = (h.get("type") or "").replace("_", " ")
            street = next((x for x in parts[1:4] if not x[:1].isdigit()), "")
            label = "%s (%s)" % (name, ", ".join(x for x in (kind, street) if x))
    return float(h["lat"]), float(h["lon"]), label


# The emergency number by country (ISO code); 112 works across the EU and on most GSM phones elsewhere.
EMERGENCY = {"us": "911", "ca": "911", "mx": "911", "gb": "999", "au": "000", "nz": "111", "jp": "119", "kr": "119",
             "cn": "120", "in": "112", "br": "192", "za": "10111"}
_COUNTRY = {}


def country(latlon):
    """ISO country code for a point (Nominatim reverse lookup), or None."""
    key = (round(latlon[0], 1), round(latlon[1], 1))
    if key not in _COUNTRY:
        try:
            r = requests.get("https://nominatim.openstreetmap.org/reverse", headers=UA, timeout=10,
                             params={"lat": latlon[0], "lon": latlon[1], "format": "json", "zoom": 3})
            r.raise_for_status()
            _COUNTRY[key] = ((r.json().get("address") or {}).get("country_code") or "").lower() or None
        except Exception:
            return None
    return _COUNTRY[key]


def emergency_number(latlon):
    """The local emergency number for a point ('112' if the country is unknown or not listed)."""
    return EMERGENCY.get(country(latlon) or "", "112") if latlon else "112"


def current_location(override=None):
    """GPS stand-in. On a phone this comes from the device; on a laptop we fall back to IP geolocation."""
    if override:
        return override
    r = requests.get("https://ipapi.co/json/", headers=UA, timeout=10)
    r.raise_for_status()
    j = r.json()
    return float(j["latitude"]), float(j["longitude"])


class LocalProjection:
    """Equirectangular projection around an origin -> meters (x east, y north). Fine at city scale."""

    def __init__(self, origin):
        self.lat0, self.lon0 = origin
        self.kx = math.cos(math.radians(self.lat0)) * math.pi * EARTH_R / 180
        self.ky = math.pi * EARTH_R / 180

    def to_xy(self, latlon):
        return (latlon[1] - self.lon0) * self.kx, (latlon[0] - self.lat0) * self.ky

    def to_latlon(self, xy):
        return self.lat0 + xy[1] / self.ky, self.lon0 + xy[0] / self.kx
