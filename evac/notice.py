"""Evacuation SMS -> structured notice.

Primary path: Muse Spark via llm.chat_json. Fallback: regexes that handle the common
"<hazard> ... within N miles of <place> ... shelter at <place> ... avoid X between A and B" shape
so the demo runs offline.

The model copies numbers, units and street names exactly as written; unit conversion happens here,
so results are deterministic (the model used to round 2 mi to 3219 or 3218.688 between runs).
Closed roads are segments: {"road", "from", "to"}, where from/to are cross streets or None.
"""
import re

from . import llm

SCHEMA_PROMPT = """Extract the alert into JSON with exactly these keys:
{
  "hazard": "wildfire|fire|flood|hurricane|chemical|gas|police|tsunami|other",
  "order": "mandatory|warning|avoid_area|shelter_in_place|lifted",
  "zone_center_place": "place, address or intersection the incident is at, geocodable",
  "zone_radius": {"value": number, "unit": "as written: mi|km|ft|m|blocks"} or null,
  "shelters": ["geocodable place names of shelters / evacuation centers"],
  "avoid_roads": [{"road": "street name", "from": "cross street or null", "to": "cross street or null"}],
  "deadline": "string or null",
  "summary": "one sentence plain-language summary"
}
Copy numbers, units and street names exactly as written; do not convert. Use null when unknown. Output JSON only."""

UNITS = {"mile": 1609.34, "mi": 1609.34, "km": 1000.0, "kilometer": 1000.0, "ft": 0.3048, "feet": 0.3048, "foot": 0.3048,
         "m": 1.0, "meter": 1.0, "metre": 1.0, "block": 80.0}  # a Manhattan street block is ~80 m


def to_meters(value, unit):
    u = (unit or "m").lower().strip(". ")
    return float(value) * UNITS.get(u, UNITS.get(u.rstrip("s"), 1.0))


def parse(text):
    if llm.available():
        try:
            n = llm.chat_json([{"role": "system", "content": SCHEMA_PROMPT}, {"role": "user", "content": text}])
            n["_parser"] = "llm"
            return _normalize(n)
        except Exception as e:  # fall through to regex so a flaky API never blocks an evacuation
            print("[notice] LLM parse failed (%s); using regex fallback" % e)
    return _normalize(_regex(text))


def _normalize(n):
    r = n.pop("zone_radius", None)
    n["zone_radius_m"] = to_meters(r["value"], r.get("unit")) if isinstance(r, dict) and r.get("value") is not None else None
    segs = []
    for a in n.get("avoid_roads") or []:
        if isinstance(a, str):
            a = {"road": a}
        if a.get("road"):
            segs.append({"road": a["road"], "from": a.get("from") or None, "to": a.get("to") or None})
    n["avoid_roads"] = segs
    return n


def segment_text(s):
    if s["from"] and s["to"]:
        return "%s (%s to %s)" % (s["road"], s["from"], s["to"])
    return s["road"]


def _regex(text):
    t = " ".join(text.split())
    low = t.lower()
    hazard = next((h for h in ["wildfire", "fire", "flood", "hurricane", "chemical", "gas", "tsunami"] if h in low), "other")
    order = ("mandatory" if "mandatory" in low or "evacuate now" in low else "shelter_in_place" if "shelter in place" in low
             else "avoid_area" if "avoid" in low else "warning")

    radius = center = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*(miles?|mi|km|kilometers?|ft|feet|meters?|m|blocks?)\b\s+(?:radius\s+)?(?:of|around|from)\s+(.+?)(?:[.;,]| (?:should|must|are|is|go|shelter|avoid|evacuat|leave|move)\b)", t, re.I)
    if m:
        radius = {"value": float(m.group(1)), "unit": m.group(2)}
        center = m.group(3).strip()
    else:
        m = re.search(r"\b(?:at|near)\s+(.+?\s(?:and|&)\s.+?)(?:,\s*[A-Z][a-z]+)?[.;]", t)
        center = m.group(1).strip() if m else None

    shelters = [s.strip() for s in re.findall(r"(?i:shelter|evacuation cent(?:er|re)|go to|proceed to)\s*(?i:is\s+)?(?i:open\s+)?(?i:at|:)?\s+([A-Z0-9][^.;]+)", t)]
    avoid = []
    for chunk in re.findall(r"(?:avoid|closed:?)\s+([^.;]+)", t, re.I):
        seg = re.match(r"(.+?)\s+between\s+(.+?)\s+and\s+(.+)", chunk, re.I)
        if seg:
            avoid.append({"road": seg.group(1).strip(), "from": seg.group(2).strip(), "to": seg.group(3).strip()})
            continue
        avoid += [{"road": a.strip()} for a in re.split(r",| and ", chunk)
                  if a.strip() and a.strip().lower() not in ("closed", "the area")]

    return {
        "hazard": hazard, "order": order, "zone_center_place": center, "zone_radius": radius,
        "shelters": shelters, "avoid_roads": avoid, "deadline": None,
        "summary": t[:160], "_parser": "regex",
    }
