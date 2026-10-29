"""Tiny pictures for plain SMS, which evac/codes.py sends as #P and #Z codes and the offline kit (sim/kit.html) draws:
a 1-bit PNG (line drawings; a step picture is 300-500 bytes, 3-5 SMS) or a greyscale WebP (photos and zooms). Both fit in 128 px with their aspect kept."""
import io
import math

from PIL import Image, ImageOps

SIDE = 128


def fit(img, side=SIDE):
    """Greyscale, shrunk to fit in side x side (aspect kept)."""
    g = img.convert("L")
    g.thumbnail((side, side), Image.LANCZOS)
    return g


def png(img, side=SIDE):
    """The picture as a 1-bit PNG fitting in `side` pixels (black ink on white): outlines and line drawings."""
    s = fit(img, side).point(lambda v: 255 if v > 140 else 0).convert("1")
    buf = io.BytesIO()
    s.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def webp(img, side=SIDE, quality=5):
    """The picture as a small greyscale WebP (contrast stretched first): photos and zooms. On the six sample photos
    quality 5 took 284-1386 bytes, about what a 1-bit PNG takes, but a busy or dark photo stays readable where the
    1-bit one turns to noise (the bicycle against a brick wall) or vanishes (a white sneaker). Browsers decode it."""
    buf = io.BytesIO()
    ImageOps.autocontrast(fit(img, side), cutoff=1).save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def sms_count(text):
    """SMS parts for text in the basic alphabet (base64 is): 160 in one message, 153 per part when split."""
    return 1 if len(text) <= 160 else math.ceil(len(text) / 153)
