"""The Red Hook demo (a fictional drill): a coastal flood at night, the phones of ~40 neighbours that can only text,
and a community hub at Coffey Park with the one working connection. The beats drive the real hub (evac/hub.py);
only the neighbours' phones are simulated: they check in, send photos, and answer the hub's requests from their kit.

    beat("setup")   a fresh store: the incident, the cast, where everyone is
    beat("alert")   the city's flood alert, re-sent signed to everyone; a forged copy goes round too
    beat("checkin") the wellness check: everyone checks in over ~20 s (two with photos)
    beat("assign")  Dani walks to Mr. Alvarez (14th floor, no power), around the flood
    beat("photo")   the Okafors report a tree down on a power line, on Dani's way
    beat("zoom")    the hub asks their phone for a closer look at the wire
    beat("block")   the hub marks it: Dani is re-routed, neighbours nearby warned
    beat("luisa")   Luísa gets the hub's answer in Portuguese; June goes to her
    beat("walk")    the volunteers arrive; Mr. Alvarez checks in safe
"""
import json
import os
import random
import threading
import time

from PIL import Image

from evac import codes, directions, geo, hub, osm, store
from phones import Phone

ROOT = os.path.dirname(__file__)
PHOTOS = os.path.join(ROOT, "samples", "redhook")
HUB = (40.67755, -74.00860)                 # Coffey Park
ZONE = ((40.67444, -74.01630), 500)         # around Van Brunt St and Beard St, as the alert says
BOX = (40.6705, -74.0180, 40.6815, -73.9985)
ALERT = open(os.path.join(ROOT, "..", "samples", "redhook_flood.txt")).read().strip()
CLOCK = "Tuesday 9:40 PM · hour 3 of the storm"

# The people the story follows. Everyone else is generated (seeded) on Red Hook's streets.
CAST = [
    dict(who="+15550100", name="Rosa", role="coordinator", lang="en", at=HUB),
    dict(who="+15550101", name="Dani", role="volunteer", lang="en", at=HUB, note="17, knows every building"),
    dict(who="+15550102", name="June", role="volunteer", lang="en", at=HUB),
    dict(who="+15550103", name="Marcus", role="volunteer", lang="en", at=HUB),
    dict(who="+15550110", name="Mr. Alvarez", role="resident", lang="en", at=(40.67380, -74.00300),
         checkin=dict(st="help", p=1, b=18, n="14th floor, no power, no elevator. Can't do the stairs. Out of water.")),
    dict(who="+15550111", name="Luísa", role="resident", lang="pt", at=(40.67615, -74.00720),
         checkin=dict(st="help", p=2, b=35, n="Sem luz. A minha mãe tem 82 anos e não consegue descer.")),
    dict(who="+15550112", name="Mr. Chen", role="resident", lang="en", at=(40.67500, -74.00600),
         checkin=dict(st="hurt", p=2, b=52, n="My wife fell on the dark stairs, her ankle is swollen, she can't walk.")),
    dict(who="+15550113", name="The Okafors", role="resident", lang="en", at=None,   # placed on Dani's way at setup
         checkin=dict(st="ok", p=4, b=64, n="We're fine.")),
    dict(who="+15550114", name="Sam", role="resident", lang="en", at=(40.67650, -74.01580),
         checkin=dict(st="help", p=3, b=41, n="Water coming in downstairs on Beard St, we're on the 2nd floor."), photo="flooded_corner"),
]
FIRST = ["Ana", "Mo", "Kim", "Jo", "Lee", "Ray", "Ade", "Nia", "Tom", "Eli", "Ivy", "Raj", "Bea", "Cal", "Dee", "Gus", "Hal",
         "Ida", "Jay", "Kai", "Liv", "Max", "Ned", "Oz", "Pia", "Rob", "Sol", "Tia", "Uma", "Vic", "Wes", "Zoe"]
NOTES_OK = ["All good, no power.", "Fine, staying in.", "OK, phone low.", "We're fine.", "Safe on 6th floor.", ""]
NOTES_HELP = ["Out of water.", "Need a phone charger.", "No heat, two kids.", "Neighbor next door not answering."]


# ---------- the phones ----------

PHONES = {}


def _deliver(who, text):
    if who in PHONES:
        PHONES[who].got(text)


hub.deliver[:] = [_deliver]


def checkin(who, st, p, b, n="", photo=None):
    """What a phone sends from the kit's Send tab: a check-in code, and maybe a photo code."""
    at = store.get(_person_id(who))["where"]
    loc = {"la": round(at[0], 5), "lo": round(at[1], 5), "ac": 10, "st": st, "p": p, "b": b, "t": int(time.time())}
    if n:
        loc["n"] = n
    msg = codes.text(codes.parts("L", json.dumps(loc, separators=(",", ":"), ensure_ascii=False).encode(), random.choice("abcdef") + "1"))
    if photo:
        msg += "\n" + codes.text(PHONES[who].picture(Image.open(os.path.join(PHOTOS, photo + ".jpg")).convert("RGB")))
    return hub.receive(who, msg)


def _person_id(who):
    return next(p["id"] for p in store.find("person") if p["who"] == who)


# ---------- the cast ----------

def _in_red_hook(p):
    """West of the Gowanus Expressway / Hamilton Ave, which cuts Red Hook off from Carroll Gardens."""
    (a_lat, a_lon), (b_lat, b_lon) = (40.6840, -74.0070), (40.6680, -73.9960)
    return (b_lon - a_lon) * (p[0] - a_lat) - (b_lat - a_lat) * (p[1] - a_lon) < 0


def _streets():
    roads = osm.fetch_roads(*BOX, kinds=osm.WALKABLE)
    return [p for r in roads if r["kind"] in ("residential", "footway", "living_street", "pedestrian") for p in r["pts"]
            if BOX[0] <= p[0] <= BOX[2] and BOX[1] <= p[1] <= BOX[3] and _in_red_hook(p)]


def setup(n_others=34, seed=7):
    """A fresh store with the incident, the cast and ~34 more neighbours on Red Hook's streets."""
    store.use(os.path.join(ROOT, "..", "cache", "store-redhook", time.strftime("%Y%m%d-%H%M%S")))
    geo.KNOWN["coffey park"] = HUB  # the hub knows its own address (the alert names it as the place to go)
    PHONES.clear()
    rnd = random.Random(seed)
    store.new("incident", where=HUB, meta={"name": "Coastal flood, Red Hook (drill)", "hazard": "flood", "zone": [list(ZONE[0]), ZONE[1]],
                                          "hub_name": "Neighbors Hub, Coffey Park", "alert": ALERT, "clock": CLOCK})
    # The Okafors live on the street Dani will take to Mr. Alvarez, 40% of the way: their tree falls on it.
    way = directions.run(HUB, ("Mr. Alvarez", CAST[4]["at"]), avoid={"kind": "flood", "zones": [ZONE]})
    route = way["trip"].route
    okafor = route[int(len(route) * 0.4)]
    for c in CAST:
        at = okafor if c["name"] == "The Okafors" else c["at"]
        store.new("person", who=c["who"], where=at, meta={"name": c["name"], "role": c["role"], "lang": c["lang"], "note": c.get("note", "")})
        PHONES[c["who"]] = Phone(c["who"], hub.receive)
    streets = _streets()
    for i in range(n_others):
        while True:
            at = rnd.choice(streets)
            if geo.haversine_m(at, HUB) > 60 and all(geo.haversine_m(at, c["at"] or okafor) > 40 for c in CAST[4:]):
                break
        who = "+1555020%02d" % i
        store.new("person", who=who, where=(at[0] + rnd.uniform(-5e-5, 5e-5), at[1] + rnd.uniform(-5e-5, 5e-5)),
                  meta={"name": "%s %s." % (FIRST[i % len(FIRST)], "ABCDEFGHJKLMNPRSTW"[i % 18]), "role": "resident", "lang": "en"})
        PHONES[who] = Phone(who, hub.receive)
    return {"people": len(store.find("person")), "okafors": okafor, "dani_route_m": round(way["distance_m"])}


# ---------- the beats ----------

def _forged(kit):
    """The rumour: the hub's own signed message with the destination changed. The signature no longer matches."""
    return kit.replace("Coffey Park", "the Navy Yard", 1) if "Coffey Park" in kit else kit.replace("\n", "\nGo to the Navy Yard now.\n", 1)


def beat(name, pace=0.4):
    """One beat of the story (`pace`: seconds between check-ins in the wave; the video slows it down)."""
    rnd = random.Random(name)
    if name == "setup":
        return setup()
    if name == "alert":
        out = hub.broadcast_alert(ALERT)
        rumour = _forged(out["kit"])
        for p in hub.people("resident"):  # forwarded neighbour to neighbour; it never went through the hub
            store.new("sms", who=p["who"], meta={"dir": "rumour", "text": rumour, "parts": codes.sms_parts(rumour), "at": time.time()})
        return {k: out[k] for k in ("sent", "personal")}
    if name == "checkin":
        msg = "Neighbors Hub at Coffey Park: please check in. In your kit tap Send > Check in (safe / need help / someone hurt)."
        for p in hub.people("resident"):
            hub.send(p["who"], msg)

        def wave():
            others = [p for p in hub.people("resident") if p["who"] not in {c["who"] for c in CAST}]
            order = [c for c in CAST if c.get("checkin") and c["name"] != "The Okafors"] + others
            rnd.shuffle(order)
            for x in order:
                c = next((c for c in CAST if c["who"] == x["who"]), None)
                if c:
                    ci = c["checkin"]
                    checkin(c["who"], ci["st"], ci["p"], ci["b"], ci.get("n", ""), c.get("photo"))
                else:
                    st = rnd.choices(["ok", "help"], [0.85, 0.15])[0]
                    checkin(x["who"], st, rnd.randint(1, 5), rnd.randint(8, 95), rnd.choice(NOTES_OK if st == "ok" else NOTES_HELP))
                time.sleep(pace)
        threading.Thread(target=wave, daemon=True).start()
        return {"sending": len(hub.people("resident"))}
    if name == "assign":
        rep = _report_of("Mr. Alvarez")
        out = hub.assign("+15550101", rep["id"])
        hub.walk(out["trip"], 90)
        return {"trip": out["trip"], "distance_m": round(out["distance_m"])}
    if name == "photo":
        return {"report": checkin("+15550113", "ok", 4, 64, "Tree came down on the power line outside our house. The wire is on the street.",
                                  photo="downed_line")["report"]}
    if name == "zoom":
        rep = _report_of("The Okafors")
        for _ in range(40):  # triage runs in the background: wait for what it found worth a closer look
            if store.get(rep["id"])["meta"].get("triage"):
                break
            time.sleep(0.5)
        box = store.get(rep["id"])["meta"].get("look_box")
        hub.ask_closer(rep["id"], box or [40, 70, 60, 40])
        return {"asked": True, "box": box}
    if name == "block":
        rep = _report_of("The Okafors")
        street = _street_at(rep["where"])
        return hub.block(tuple(rep["where"]), 30, "Downed power line on %s" % street if street else "Downed power line")
    if name == "luisa":
        rep = _report_of("Luísa")
        hub.send(rep["who"], "Stay where you are with your mother. June from the hub is bringing water, a lantern and a power bank, about 10 minutes.")
        out = hub.assign("+15550102", rep["id"])
        return {"trip": out["trip"]}
    if name == "walk":
        for t in store.find("trip"):
            if t["meta"].get("volunteer") and not t["meta"].get("done"):
                for _ in range(30):
                    if hub.walk(t["id"], 80)["state"] == "arrived":
                        break
        out = checkin("+15550110", "ok", 1, 15, "Dani is here with water and a power bank. Thank you.")
        store.update(out["report"], meta={"reached": time.time(), "assigned": "+15550101"})
        return hub.counts()
    raise KeyError(name)


def _report_of(name):
    who = next(p["who"] for p in store.find("person") if p["meta"]["name"] == name)
    return next(r for r in hub.latest_reports() if r["who"] == who)


def _street_at(where):
    roads = osm.fetch_roads(*BOX, kinds=osm.WALKABLE)
    best = min(((geo.haversine_m(where, p), r["name"]) for r in roads if r["name"] for p in r["pts"]), default=(0, None))
    return best[1]
