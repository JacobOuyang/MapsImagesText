"""What the offline kit saves: every reply type sent the plain way and the kit way, measured on the repository's
samples (real runs, nothing estimated). Writes out/eval/kit/measure.json and evals/kit/RESULTS.md.

    .venv/bin/python evals/kit/measure.py            # ~1 minute (alerts, a walk, six photos, three how-to answers)
    .venv/bin/python evals/kit/measure.py --report   # rewrite the report from the last run

An SMS carries 140 bytes on the air whatever its alphabet, so bytes = SMS x 140 for every SMS row. "Data" rows are
what the same reply costs over a connection (WhatsApp text gzipped, the small 400 px WebP, the full picture).
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from PIL import Image  # noqa: E402

from evac import codes, directions, pipeline, router, smsimage  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
OUT = os.path.join(ROOT, "out", "eval", "kit")
SMS_BYTES = 140
SAMPLES = os.path.join(ROOT, "sim", "samples")
PHOTOS = ["Apple", "Bicycle", "Dog", "Mug", "Sneaker", "Teapot"]
ASK = [("Como desligo o gás no contador?", (38.7139, -9.1406), "Portuguese, Lisbon"),
       ("¿Cómo se usa un extintor?", (40.4168, -3.7038), "Spanish, Madrid"),
       ("Comment couper l'eau en cas de fuite ?", (48.8566, 2.3522), "French, Paris")]


def kit_html_bytes():
    import gzip
    import urllib.request
    try:
        body = urllib.request.urlopen("http://127.0.0.1:8765/kit.html", timeout=10).read()
        return len(body), len(gzip.compress(body, 9))
    except Exception:
        return None, None


def alerts():
    rows = []
    for name, at in [("nyc_fire", (40.75491, -73.97441)), ("nyc_gas_leak", (40.7560, -73.9740)),
                     ("palisades_fire", (34.0445, -118.5300))]:
        r = pipeline.run(open(os.path.join(ROOT, "samples", name + ".txt")).read(), user=at, laptop=58, image=True)
        if r.get("scene") is None:
            continue
        kit = router.kit_alert(r)
        rows.append({"what": "Alert: " + name, "plain_sms": codes.sms_parts(r["message"]), "kit_sms": codes.sms_parts(kit),
                     "whatsapp_gzip": r["laptop"]["gzip"], "small": r["small"]["bytes"], "picture": r["image"]["bytes"],
                     "kit": kit, "plain": r["message"]})
    return rows


def walk():
    r = directions.run((38.7139, -9.1406), "Castelo de São Jorge, Lisbon")
    trip = r["trip"]
    pts = [trip.you] + trip.route if trip.you else trip.route
    route = codes.route(pts, trip.name)
    kit = router.kit_message(r["text"], codes.map_scene(r["scene"]) + route)
    gpx_points = len(codes._simplify(pts))
    return {"what": "Walk: Rossio to Castelo de São Jorge", "plain_sms": codes.sms_parts(r["sms"]), "kit_sms": codes.sms_parts(kit),
            "whatsapp_gzip": r.get("mono_gzip"), "small": r["small"]["bytes"], "picture": r["image"]["bytes"],
            "route_points": len(pts), "gpx_points": gpx_points, "route_code_sms": codes.sms_parts(codes.text(route)),
            "plain_text_sms": codes.sms_parts(r["text"]), "kit_text_sms": codes.sms_parts(router.kit_message(r["text"])),
            "kit": kit, "plain": r["sms"]}


def photos():
    from evac import imagemap
    rows = []
    for name in PHOTOS:
        path = os.path.join(SAMPLES, name + ".jpg")
        img = Image.open(path).convert("RGB")
        grey, bw = smsimage.webp(img), smsimage.png(img)
        parts = codes.picture(img)
        ident = codes.CODE.match(parts[0]).group(2)
        # A zoom on the middle quarter of the picture, against seeing that detail without zoom: the whole picture
        # sent at 4x the size (512 px, same format).
        w, h = smsimage.fit(img).size
        words, zoom = codes.zoom("ZOOM %s %d %d %d %d" % (ident, w * 3 // 8, h * 3 // 8, w // 4, h // 4))
        buf = io.BytesIO()
        from PIL import ImageOps
        ImageOps.autocontrast(smsimage.fit(img, 512), cutoff=1).save(buf, "WEBP", quality=5, method=6)
        rows.append({"what": name, "original": os.path.getsize(path), "small": imagemap.shrink(img)["bytes"],
                     "webp": len(grey), "png": len(bw), "picture_sms": codes.sms_parts(codes.text(parts)),
                     "png_sms": codes.sms_parts(codes.text(codes.encode("P", bw))),
                     "zoom_sms": codes.sms_parts(codes.text(zoom)), "zoom_bytes": sum(len(p) for p in zoom) * 3 // 4,
                     "whole_at_4x": len(buf.getvalue()), "whole_at_4x_sms": codes.sms_parts(codes.text(codes.encode("P", buf.getvalue())))})
    return rows


def ask():
    rows = []
    for text, at, label in ASK:
        r = router.handle(text, at)
        reply = r.get("text") or ""
        rows.append({"what": label, "question": text, "reply": reply, "plain_sms": codes.sms_parts(reply),
                     "kit_sms": codes.sms_parts(router.kit_message(reply)), "kit_full": r.get("kit"),
                     "kit_full_sms": r.get("kit") and codes.sms_parts(r["kit"]), "tools": [c["tool"] for c in r.get("tools", [])]})
    return rows


def table(rows, cols):
    head = "| " + " | ".join(c for c, _ in cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "".join("| " + " | ".join(f(r) for _, f in cols) + " |\n" for r in rows)


def kb(b):
    return "—" if b is None else ("%d B" % b if b < 1000 else "%.1f KB" % (b / 1000))


def report(m):
    x = lambda a, b: "%.1f×" % (a / b) if a and b else "—"
    md = ["# What the offline kit saves (measured)", "",
          "Generated by `evals/kit/measure.py` on %s. One SMS = 140 bytes on the air. Kit page: %s (%s gzipped), "
          "downloaded once." % (m["date"], kb(m["kit_html"][0]), kb(m["kit_html"][1])), "",
          "## Alerts and walking directions", "",
          table(m["alerts"] + [m["walk"]], [
              ("Reply", lambda r: r["what"]),
              ("Plain SMS (text map)", lambda r: "%d SMS = %s" % (r["plain_sms"], kb(r["plain_sms"] * SMS_BYTES))),
              ("Kit SMS", lambda r: "%d SMS = %s" % (r["kit_sms"], kb(r["kit_sms"] * SMS_BYTES))),
              ("Gain", lambda r: x(r["plain_sms"], r["kit_sms"])),
              ("WhatsApp text (gz)", lambda r: kb(r.get("whatsapp_gzip"))),
              ("Small picture", lambda r: kb(r["small"])),
              ("Full picture", lambda r: kb(r["picture"])),
              ("Kit vs small picture", lambda r: x(r["small"], r["kit_sms"] * SMS_BYTES))]),
          "The kit versions carry more than the plain SMS: a real map the kit draws (streets, zone, closures), the "
          "alert's signature, and for the walk the whole route (%d points simplified to %d, %d SMS) that opens as a GPX "
          "file in an offline map app." % (m["walk"]["route_points"], m["walk"]["gpx_points"], m["walk"]["route_code_sms"]), "",
          "## Photos (a picture the person can zoom into)", "",
          table(m["photos"], [
              ("Photo", lambda r: r["what"]),
              ("Original", lambda r: kb(r["original"])),
              ("Small picture (400 px WebP)", lambda r: kb(r["small"])),
              ("Kit picture (128 px grey WebP)", lambda r: "%s = %d SMS" % (kb(r["webp"]), r["picture_sms"])),
              ("1-bit PNG instead", lambda r: "%s = %d SMS" % (kb(r["png"]), r["png_sms"])),
              ("One zoom (a quarter)", lambda r: "%s = %d SMS" % (kb(r["zoom_bytes"]), r["zoom_sms"])),
              ("Same detail without zoom (512 px)", lambda r: "%s = %d SMS" % (kb(r["whole_at_4x"]), r["whole_at_4x_sms"])),
              ("Zoom gain", lambda r: x(r["whole_at_4x_sms"], r["zoom_sms"])),
              ("Kit picture vs small picture", lambda r: x(r["small"], r["webp"]))]),
          "Greyscale WebP is the default for photos: about the size of a 1-bit PNG (busy photos a little more), but the "
          "1-bit Bicycle and Sneaker were unreadable (a brick wall turned to noise, a white shoe vanished). A zoom is "
          "cropped from the full-size original the server kept, so it shows detail the 128 px picture never had; the "
          "alternative is sending the whole picture big enough to show that detail.", "",
          "## Words in languages with accents", "",
          table(m["ask"], [
              ("Question", lambda r: "%s (%s)" % (r["question"], r["what"])),
              ("Reply as plain SMS", lambda r: "%d SMS (%s)" % (r["plain_sms"], "UCS-2" if any(ch not in codes.GSM7 for ch in r["reply"]) else "GSM-7")),
              ("Reply as a T code", lambda r: "%d SMS" % r["kit_sms"]),
              ("Gain", lambda r: x(r["plain_sms"], r["kit_sms"]))]),
          ]
    tot_plain = sum(r["plain_sms"] for r in m["alerts"] + [m["walk"]] + m["ask"])
    tot_kit = sum(r["kit_sms"] for r in m["alerts"] + [m["walk"]] + m["ask"])
    md += ["## Overall", "",
           "Alerts, the walk and the three answers: %d SMS plain, %d SMS with the kit (%s fewer), i.e. %s vs %s on "
           "the air. The kit page itself (%s gzipped) is paid back after about %d SMS saved." % (
               tot_plain, tot_kit, x(tot_plain, tot_kit), kb(tot_plain * SMS_BYTES), kb(tot_kit * SMS_BYTES),
               kb(m["kit_html"][1]), (m["kit_html"][1] or 0) // SMS_BYTES), ""]
    return "\n".join(md)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    if "--report" in sys.argv:  # rewrite RESULTS.md from the last run, without running anything
        md = report(json.load(open(os.path.join(OUT, "measure.json"))))
        open(os.path.join(os.path.dirname(__file__), "RESULTS.md"), "w").write(md)
        print(md)
        sys.exit()
    t = time.time()
    m = {"date": time.strftime("%Y-%m-%d"), "kit_html": kit_html_bytes(), "alerts": alerts(), "walk": walk(),
         "photos": photos(), "ask": ask()}
    json.dump(m, open(os.path.join(OUT, "measure.json"), "w"), indent=1, ensure_ascii=False, default=str)
    md = report(m)
    open(os.path.join(os.path.dirname(__file__), "RESULTS.md"), "w").write(md)
    print(md)
    print("%.0f s" % (time.time() - t))
