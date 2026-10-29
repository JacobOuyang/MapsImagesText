"""Muse Image (muse-image-1.0) on the Meta Model API: a picture from text, or a new picture from a picture.

    museimage.generate("a person kneeling beside a mannequin ...")          # /images/generations (text only)
    museimage.generate("same person, now pushing down ...", image=frame1)   # /images/edits (keeps the look)

Answers are cached in cache/image/ by prompt and input picture, so re-running a sequence costs nothing. Output is
square (1600 x 1600 whatever size is asked).
"""
import base64
import hashlib
import io
import json
import os
import time

import requests
from PIL import Image

from . import llm

MODEL = "muse-image-1.0"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "image")


def generate(prompt, image=None):
    """-> PIL image (RGB). With `image`, the picture is the starting point (same people, style, viewpoint)."""
    src = b""
    if image is not None:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "PNG")
        src = buf.getvalue()
    key = hashlib.md5(prompt.encode() + b"\0" + src).hexdigest()
    path = os.path.join(CACHE_DIR, key + ".jpg")
    if os.path.exists(path):
        return Image.open(path).convert("RGB")
    h = {"Authorization": "Bearer " + llm._key()}
    base = llm.BASE_URL.rstrip("/")
    for wait in (4, 12, 30, None):  # several pictures at once get refused (rate limits): back off and retry
        if image is None:
            r = requests.post(base + "/images/generations", headers=dict(h, **{"Content-Type": "application/json"}), timeout=300,
                              data=json.dumps({"model": MODEL, "prompt": prompt, "n": 1, "size": "1024x1024"}))
        else:
            r = requests.post(base + "/images/edits", headers=h, timeout=300, files={"image": ("in.png", src, "image/png")},
                              data={"model": MODEL, "prompt": prompt, "size": "1024x1024"})
        if r.status_code not in (429, 500, 502, 503, 504) or wait is None:
            break
        time.sleep(wait)
    r.raise_for_status()
    im = Image.open(io.BytesIO(base64.b64decode(r.json()["data"][0]["b64_json"]))).convert("RGB")
    os.makedirs(CACHE_DIR, exist_ok=True)
    small = im.copy()
    small.thumbnail((1024, 1024))
    small.save(path, quality=90)
    return small
