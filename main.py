"""CLI.

  python main.py samples/palisades_fire.txt --at 34.0445,-118.5300 [-v] [--laptop]   # alert -> SMS reply
  python main.py --photo some.jpg                                                     # photo -> text versions
  python main.py --radar 40.7128,-74.0060                                             # weather radar as text
  python main.py --walk "Castelo de São Jorge, Lisbon" --at 38.7139,-9.1406           # walking directions
  python main.py --ask "Is it going to rain on my walk to the Colosseum?" --at 41.9010,12.5016   # router
"""
import argparse
import sys

from evac import directions, photo, pipeline, radar, router


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("notice", nargs="?", help="path to a text file with the alert SMS, or '-' for stdin")
    ap.add_argument("--at", help="lat,lon to use instead of device/IP location")
    ap.add_argument("--cols", type=int, default=40, help="map width in characters (~40 fits a phone)")
    ap.add_argument("--seg", choices=["color", "sam3"], default="color", help="aerial segmentation backend")
    ap.add_argument("--laptop", nargs="?", type=int, const=100, help="also print the wide monospace map (default 100 "
                    "columns; the simulator's WhatsApp view uses 58)")
    ap.add_argument("--photo", help="photo file: print the SMS and WhatsApp text versions")
    ap.add_argument("--radar", help="lat,lon: print the weather radar as text")
    ap.add_argument("--walk", help="destination: print walking directions from --at")
    ap.add_argument("--ask", help="any message: Muse picks the tools (add --photo to attach a photo)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    at = tuple(float(x) for x in a.at.split(",")) if a.at else None

    if a.ask:
        r = router.handle(a.ask, at, image=open(a.photo, "rb").read() if a.photo else None)
        print("tools called:", ", ".join("%s(%s)" % (c["tool"], ", ".join(map(str, c["args"].values()))) for c in r["tools"]) or "none")
        if a.verbose:
            print("\n".join(r["log"]))
        print("\n" + r["sms"] + ("\n\n" + r["mono"] if r["mono"] else ""))
        return print("\n%.1f s; map: %s" % (r["seconds"], r["map"]))
    if a.photo:
        r = photo.run(open(a.photo, "rb").read())
        print(r["sms"], "\n\n" + r["text"] + "\n\n" + r["mono"])
        return print("\nWhatsApp text %d bytes (%d gzipped), photo %d bytes" % (r["mono_bytes"], r["mono_gzip"], r["photo_bytes"]))
    if a.radar:
        r = radar.run(tuple(float(x) for x in a.radar.split(",")))
        print(r["sms"], "\n\n" + r["mono"])
        return print("\nWhatsApp text %d bytes (%d gzipped), radar picture %d bytes" % (r["mono_bytes"], r["mono_gzip"], r["image"]["bytes"]))
    if a.walk:
        r = directions.run(at, a.walk)
        if a.verbose:
            print("\n".join(r["log"]), "\n")
        if "error" in r:
            return print(r["error"])
        print(r["sms"], "\n\n" + r["mono"])
        return print("\nWhatsApp text %d bytes (%d gzipped), picture map %d bytes" % (r["mono_bytes"], r["mono_gzip"], r["image"]["bytes"]))
    if not a.notice:
        ap.error("give an alert file, or --photo / --radar / --walk")

    sms = sys.stdin.read() if a.notice == "-" else open(a.notice).read()
    res = pipeline.run(sms, user=at, cols=a.cols, seg_backend=a.seg, laptop=a.laptop or False)
    if a.verbose:
        print("\n".join(res["log"]), "\n")
    print(res["message"])
    if res.get("laptop"):
        print("\n" + res["text"] + "\n\n" + res["laptop"]["text"])


if __name__ == "__main__":
    main()
