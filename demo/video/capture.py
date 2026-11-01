"""Run the Red Hook demo beat by beat against the simulator and screenshot the dashboard after each one.

    .venv/bin/python sim/server.py                      # in another terminal (or the "simulator" launch config)
    .venv/bin/python demo/video/capture.py              # ~3 minutes -> out/demo/redhook/shots/*.png + shots.json

Screenshots come from headless Chrome at 1920 x 1080 and device scale 2 (3840 x 2160), so the video can zoom into
the phone or the map and stay sharp. Each shot sets the map view and which neighbour's phone is open (URL
parameters of sim/dashboard.html).
"""
import json
import os
import subprocess
import sys
import time
from urllib.parse import quote

import requests

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
OUT = os.path.join(ROOT, "out", "demo", "redhook", "shots")
URL = "http://127.0.0.1:8765"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

DANI, ALVAREZ, LUISA, OKAFORS, SAM = "+15550101", "+15550110", "+15550111", "+15550113", "+15550114"
VIEW = "c=40.6752,-74.0098&z=16"             # all of Red Hook
ROUTE = "c=40.6762,-74.0062&z=17"            # the hub, the Okafors' street and Mr. Alvarez
CLOSE = "c=40.67567,-74.00608&z=18"          # the downed line


def beat(name, **kw):
    r = requests.post(URL + "/api/demo/beat", json=dict(kw, name=name), timeout=600).json()
    print(name, {k: v for k, v in r.items() if k != "kit"}, flush=True)
    return r


def state():
    return requests.get(URL + "/api/hub/state", timeout=30).json()


def report(who):
    return next((r for r in state()["reports"] if r["who"] == who), None)


def wait(test, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        if test():
            return True
        time.sleep(1)
    return False


SHOTS = {}


def shot(name, phone=None, view=VIEW, extra="", settle=9000):
    """Screenshot the dashboard as it is now."""
    q = "still=1&clean=1&%s%s%s" % (view, "&phone=" + quote(phone) if phone else "", "&" + extra if extra else "")
    requests.get(URL + "/api/hub/summary", timeout=120)  # Muse's "help first" list, ready before the page asks
    path = os.path.join(OUT, name + ".png")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--user-data-dir=" + os.path.join(OUT, "..", "chrome"),
                    "--window-size=1920,1080", "--force-device-scale-factor=2", "--virtual-time-budget=%d" % settle,
                    "--screenshot=" + path, URL + "/dashboard?" + q], capture_output=True, timeout=120)
    SHOTS[name] = {"phone": phone, "counts": state()["counts"], "at": time.time()}
    print("  shot", name, flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    beat("setup")
    shot("empty")
    beat("alert")
    shot("alert_sam", SAM, extra="top=1")
    shot("rumour_sam", SAM)
    beat("checkin", pace=1.4)
    for i, after in enumerate([1, 4, 4, 6]):  # the pins filling in as check-ins arrive (each shot takes ~10 s)
        time.sleep(after)
        shot("wave%d" % (i + 1), settle=4000)
    wait(lambda: state()["counts"]["checked_in"] >= 38)
    time.sleep(8)  # Muse's "help first" catches up
    shot("help_first", ALVAREZ)
    beat("assign")
    shot("dani_route", DANI, ROUTE)
    beat("photo")
    wait(lambda: (report(OKAFORS) or {}).get("triage"))
    shot("okafor_photo", OKAFORS, ROUTE, "focus=%s" % report(OKAFORS)["id"])
    beat("zoom")
    wait(lambda: (report(OKAFORS) or {}).get("zoom"))
    time.sleep(1)
    shot("okafor_zoom", OKAFORS, ROUTE, "focus=%s" % report(OKAFORS)["id"])
    beat("block")
    shot("blocked", DANI, CLOSE)
    beat("luisa")
    time.sleep(2)
    shot("luisa", LUISA, ROUTE)
    beat("walk")
    time.sleep(8)
    shot("end", ALVAREZ)
    json.dump(SHOTS, open(os.path.join(OUT, "shots.json"), "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
