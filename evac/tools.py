"""The data sources as tools Muse Spark can call (OpenAI-style function calling).

Each tool takes a per-request Context and keyword arguments, and returns a short JSON-able summary for the
model. Bulky results (street networks, routes, radar grids, a whole emergency reply) never go to the model:
they are kept in `ctx.store` under the tool's name, for building the Scene that gets drawn.

    ctx = Context(you=(41.9010, 12.5016))
    tools.call(ctx, "walking_route", {"destination": "Colosseum, Rome"})
    tools.schemas()  # -> the `tools` list for a chat completion
"""
import io
import traceback
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image

from . import geo, grid, notice, osm, photo, pipeline, radar

REGISTRY = {}


@dataclass
class Context:
    you: tuple                      # the person's GPS (lat, lon)
    image: Optional[bytes] = None   # a photo they were sent, if any
    store: dict = field(default_factory=dict)  # tool name -> full result, for the scene builders
    log: list = field(default_factory=list)


def tool(description, **params):
    """Register a function as a tool. params: name=(json type, description, required)."""
    def deco(fn):
        REGISTRY[fn.__name__] = {"fn": fn, "schema": {"type": "function", "function": {
            "name": fn.__name__, "description": description,
            "parameters": {"type": "object",
                           "properties": {k: {"type": t, "description": d} for k, (t, d, _) in params.items()},
                           "required": [k for k, (_, _, req) in params.items() if req]}}}}
        return fn
    return deco


def schemas(names=None):
    return [REGISTRY[n]["schema"] for n in (names or REGISTRY)]


def call(ctx, name, args):
    """Run a tool by name. Never raises: failures come back as {"error": ...} so the model can adapt."""
    if name not in REGISTRY:
        return {"error": "no tool %r" % name}
    missing = [k for k in REGISTRY[name]["schema"]["function"]["parameters"]["required"] if k not in args]
    if missing:
        return {"error": "missing %s" % ", ".join(missing)}
    try:
        out = REGISTRY[name]["fn"](ctx, **args)
    except Exception as e:
        traceback.print_exc()
        out = {"error": "%s: %s" % (type(e).__name__, e)}
    ctx.log.append("tool %s(%s) -> %s" % (name, ", ".join("%s=%r" % kv for kv in args.items()), str(out)[:160]))
    return out


def _where(ctx, latlon):
    return {"lat": round(latlon[0], 6), "lon": round(latlon[1], 6), "distance_m": round(geo.haversine_m(ctx.you, latlon)),
            "direction": geo.compass(ctx.you, latlon)}


# ---------- places ----------

@tool("Find a named place (address, landmark, station) near the person and say how far away it is.",
      query=("string", "What to look for, e.g. 'Colosseum, Rome' or 'Pasadena High School'", True))
def geocode(ctx, query):
    p = geo.geocode(query, near=ctx.you) or geo.geocode(query)
    return _where(ctx, p) if p else {"error": "not found: %s" % query}


@tool("Cities and towns around the person, biggest first.",
      radius_km=("number", "How far to look, in km (default 50)", False))
def nearby_towns(ctx, radius_km=50):
    p = geo.LocalProjection(ctx.you)
    (s, w), (n, e) = p.to_latlon((-radius_km * 1000, -radius_km * 1000)), p.to_latlon((radius_km * 1000, radius_km * 1000))
    towns = osm.fetch_places(s, w, n, e)[:10]
    ctx.store["nearby_towns"] = towns
    return {"towns": [dict(name=t["name"], **_where(ctx, (t["lat"], t["lon"]))) for t in towns]}


# ---------- emergencies ----------

@tool("Read an emergency alert text into fields: hazard, order, where, radius, shelters, closed road segments.",
      text=("string", "The alert exactly as received", True))
def read_alert(ctx, text):
    n = notice.parse(text)
    ctx.store["read_alert"] = n
    return {k: v for k, v in n.items() if not k.startswith("_")}


@tool("Pin down where an incident is, e.g. an intersection like '5 Av and W 47 St, Manhattan'. Uses street data, "
      "which is more precise than geocoding for intersections.",
      place=("string", "The incident location as written in the alert", True))
def locate_incident(ctx, place):
    p = pipeline.locate(place, ctx.you, ctx.log)
    return _where(ctx, p) if p else {"error": "could not locate %s" % place}


@tool("The full, tested emergency reply for an alert: whether the person is affected, what to avoid, the way out, "
      "and a map. Use this for any emergency alert instead of composing one from other tools.",
      text=("string", "The alert exactly as received", True))
def emergency_reply(ctx, text):
    res = pipeline.run(text, user=ctx.you, laptop=58, image=True)
    ctx.store["emergency_reply"] = res
    return {"affected": res["affected"], "inside_zone": res.get("inside"), "message": res.get("text") or res["message"],
            "steps": res.get("steps", [])}


# ---------- getting around ----------

@tool("Streets around the person: how many and the main ones, within a radius.",
      radius_m=("number", "Half-width of the area in metres (default 800)", False),
      walking=("boolean", "Include footpaths and steps, skip motorways (default true)", False))
def streets(ctx, radius_m=800, walking=True):
    g = grid.Grid(ctx.you, radius_m, radius_m, 40)
    roads = osm.fetch_roads(*g.bbox(), kinds=osm.WALKABLE if walking else None)
    ctx.store["streets"] = {"roads": roads, "half_m": radius_m, "walking": walking}
    ranked = sorted({r["name"]: grid.ROAD_RANK.get(r["kind"].replace("_link", ""), 1) for r in roads if r["name"]}.items(),
                    key=lambda kv: -kv[1])
    return {"count": len(roads), "main_streets": [n for n, _ in ranked[:10]]}


@tool("Walking route along real streets and footpaths, from the person's GPS (or from `start` if they name where "
      "they start) to a destination. Gives only the current step, as far as the next intersection, and the turn "
      "there: the person gets the next step as they walk.",
      destination=("string", "Where to, e.g. 'Colosseum, Rome'", True),
      start=("string", "Where the walk starts, only if the person names a place other than where they are, "
             "e.g. 'Pantheon, Rome'", False))
def walking_route(ctx, destination, start=None):
    from . import directions  # directions builds the whole reply; the tool keeps it for the scene
    res = directions.run(ctx.you, destination, start=start)
    if "error" in res:
        return {"error": res["error"]}
    ctx.store["walking_route"] = res
    return {"distance_m": round(res["distance_m"]), "minutes": round(res["distance_m"] / directions.WALK_M_PER_MIN),
            "from": start or "the person's GPS", "now": res["now"], "then": res["then"],
            "destination": _where(ctx, res["scene"].goal[1])}


# ---------- weather ----------

@tool("Rain forecast for one spot: millimetres in each 15 minutes for the next 2 hours. rain_radar already includes "
      "this for the person's own spot, so only use this for somewhere else or when no map is wanted.",
      lat=("number", "Latitude (default: the person)", False), lon=("number", "Longitude (default: the person)", False))
def rain_forecast(ctx, lat=None, lon=None):
    times, mm, _ = radar._forecast((lat, lon) if lat is not None and lon is not None else ctx.you)
    ctx.store["rain_forecast"] = list(zip(times, mm))
    wet = [t for t, x in zip(times, mm) if x >= 0.1]
    return {"every_15_min": [{"time": t, "mm": x} for t, x in zip(times, mm)],
            "summary": "rain from %s" % wet[0] if wet else "no rain in the next 2 hours"}


@tool("Latest weather radar around the person (160 km across): rain over them now, the nearest rain, which way "
      "it is moving, and the 2-hour forecast for their spot.")
def rain_radar(ctx):
    res = radar.run(ctx.you)
    ctx.store["rain_radar"] = res
    near = res["nearest"]
    return {"rain_here_now": res["here"],
            "nearest_rain": near and {"km": round(near[0]), "direction": near[1], "strength": near[2]},
            "moving": res["motion"] and {"toward": radar._compass(res["motion"][0]), "km_per_h": round(res["motion"][1])},
            "forecast_here": next(("rain from %s, %.1f mm in the wettest 15 min" % (t, max(x for _, x in res["forecast"]))
                                   for t, x in res["forecast"] if x >= 0.1), "no rain in the next 2 hours")}


# ---------- photos ----------

@tool("Look at the photo the person was sent (it would not load on their phone): caption, details, the main object.")
def describe_photo(ctx):
    if not ctx.image:
        return {"error": "no photo attached"}
    img = Image.open(io.BytesIO(ctx.image)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    d = photo.describe(buf.getvalue())
    ctx.store["describe_photo"] = d
    return d


def _image(ctx):
    img = Image.open(io.BytesIO(ctx.image)).convert("RGB")
    img.thumbnail((1600, 1600))
    return img


@tool("The WhatsApp drawing of the attached photo, complete and checked: the details that tell what it is or what to "
      "do are carried into the drawing and get close-ups, each with one sentence on what to look for (a marking or "
      "pattern, the shape of a part, a symbol, which way a handle or arrow points). Nothing goes to WhatsApp until it "
      "is understood. Use when identifying the thing or checking a detail matters. Pass the person's question if they "
      "asked about something specific.",
      question=("string", "What the person asked about the photo, if anything, e.g. 'is this a black widow?'", False))
def photo_details(ctx, question=None):
    from . import zooms
    if not ctx.image:
        return {"error": "no photo attached"}
    subject = (ctx.store.get("describe_photo") or {}).get("object", "")
    res = zooms.complete(_image(ctx), question, subject=subject)
    ctx.store["photo_details"] = res
    return {"close_ups": [{"part": z["name"], "look_for": z["look"]} for z in res["close_ups"]],
            "drawing_understood": res["understood"], "seen_in_drawing": res["seen"]}


@tool("Show how to do something with your hands, step by step, as a short flip-book of text drawings (a video is "
      "far too big for a text connection): CPR, a tourniquet, the recovery position, shutting off gas or water, using "
      "a fire extinguisher or an EpiPen. Use whenever the person has to perform a physical action, with or without a "
      "photo, including when an emergency alert tells them to do one.",
      task=("string", "The action, e.g. 'CPR on an adult' or 'shut off the gas at the meter'", True),
      from_photo=("boolean", "True if the attached photo shows this action or the object it is done to", False))
def show_steps(ctx, task, from_photo=False):
    from . import steps
    photo_img = _image(ctx) if (from_photo and ctx.image) else None
    caption = (ctx.store.get("describe_photo") or {}).get("caption", "")
    res = steps.run(task, photo=photo_img, caption=caption if photo_img is not None else "")
    if "error" in res:
        return {"error": res["error"]}
    ctx.store["show_steps"] = res
    return {"title": res["title"], "steps": [s["say"] for s in res["steps"]], "repeat": res["repeat"]}
