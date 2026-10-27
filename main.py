"""CLI: python main.py samples/palisades_fire.txt --at 34.0453,-118.5260"""
import argparse
import sys

from evac import pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("notice", help="path to a text file with the SMS, or '-' for stdin")
    ap.add_argument("--at", help="lat,lon to use instead of device/IP location")
    ap.add_argument("--cols", type=int, default=40, help="map width in characters (~40 fits a phone)")
    ap.add_argument("--seg", choices=["color", "sam3"], default="color", help="aerial segmentation backend")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    sms = sys.stdin.read() if a.notice == "-" else open(a.notice).read()
    at = tuple(float(x) for x in a.at.split(",")) if a.at else None
    res = pipeline.run(sms, user=at, cols=a.cols, seg_backend=a.seg)
    if a.verbose:
        print("\n".join(res["log"]), "\n")
    print(res["message"])


if __name__ == "__main__":
    main()
