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
    params = {"q": query, "format": "json", "limit": 1}
    if near:
        d = 0.5
        params["viewbox"] = "%f,%f,%f,%f" % (near[1] - d, near[0] + d, near[1] + d, near[0] - d)
    r = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=UA, timeout=20)
    r.raise_for_status()
    hits = r.json()
    if not hits:
        return None
    return float(hits[0]["lat"]), float(hits[0]["lon"])


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
