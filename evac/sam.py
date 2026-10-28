"""SAM 3.1 on the Meta Model API: a short noun phrase + an image -> every matching object's box and mask.

    sam.segment(img, "penguin") -> [{"id", "box": (x1, y1, x2, y2), "score", "mask": HxW bool or None}, ...]

SAM 3.1 is served on /v1/responses (not /chat/completions). It answers in a special-token grammar in the output
text, one line per frame (an image is always `<0f>`):

    <0f>7<|box;x1=42;y1=65;x2=243;y2=255;w=256;h=256;c=0.91|><|mask;x=0;y=0;c=0.91;data=191,202,!<payload>|>

Boxes are in the pixels of the image sent (corners inclusive); the mask is the box's rows x columns. Its payload
is compressed, and Meta's SAM License does not allow reverse-engineering it, so masks are decoded with Meta's own
`meta-sam-parser` (pip; Python 3.10+). Without it, `mask` is None and callers use the box.
Responses are cached by image and prompt in cache/sam/, like the road data.
"""
import base64
import hashlib
import io
import json
import os
import re

import numpy as np
import requests

from . import llm

MODEL = os.environ.get("SAM_MODEL", "sam-3.1")
MAX_SIDE = 1024  # images are sent at most this big; boxes come back in these pixels and are scaled back up
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "sam")
RECORD = re.compile(r"(\d+)<\|box;([^|]*)\|><\|mask;([^|]*)\|>")

try:  # Meta's decoder for the mask payload; optional
    from meta_sam_parser import decode_mask_to_raster
except ImportError:
    decode_mask_to_raster = None


def available():
    return llm.available()


def masks_available():
    return decode_mask_to_raster is not None


def _fields(s):
    return dict(kv.split("=", 1) for kv in s.split(";") if "=" in kv)


def _request(img, prompt):
    """The raw output text for one image and prompt (cached)."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    data = buf.getvalue()
    key = hashlib.md5(data + b"\0" + prompt.encode() + MODEL.encode()).hexdigest()
    cache = os.path.join(CACHE_DIR, key + ".txt")
    if os.path.exists(cache):
        with open(cache) as f:
            return f.read()
    body = {"model": MODEL, "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}]}]}
    r = requests.post(llm.BASE_URL.rstrip("/") + "/responses", timeout=120, data=json.dumps(body),
                      headers={"Authorization": "Bearer " + llm._key(), "Content-Type": "application/json"})
    r.raise_for_status()
    text = "".join(part.get("text", "") for item in r.json().get("output", []) for part in item.get("content", []))
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache, "w") as f:
        f.write(text)
    return text


def segment(img, prompt):
    """Every object matching `prompt`, best first. Boxes (and masks) are in `img`'s own pixels."""
    img = img.convert("RGB")
    scale = min(1.0, MAX_SIDE / max(img.size))
    sent = img.resize((round(img.width * scale), round(img.height * scale))) if scale < 1 else img
    out = []
    for oid, box_s, mask_s in RECORD.findall(_request(sent, prompt)):
        b, m = _fields(box_s), _fields(mask_s)
        x1, y1, x2, y2 = (int(b[k]) / scale for k in ("x1", "y1", "x2", "y2"))
        rec = {"id": int(oid), "box": (x1, y1, x2, y2), "score": float(b.get("c", 1)), "mask": None}
        if decode_mask_to_raster is not None and "data" in m:
            rec["mask"] = _place(m, b, img.size, scale)
        out.append(rec)
    return sorted(out, key=lambda r: -r["score"])


def _place(m, b, size, scale):
    """The decoded mask (box-sized, in sent pixels) pasted into a full-size bool image."""
    h, w = (int(v) for v in m["data"].split(",", 2)[:2])
    raster = np.asarray(decode_mask_to_raster(m["data"]), np.uint8).reshape(h, w)
    full = np.zeros((round(size[1] * scale), round(size[0] * scale)), bool)
    x1, y1 = int(b["x1"]), int(b["y1"])
    full[y1:y1 + h, x1:x1 + w] = raster[:full.shape[0] - y1, :full.shape[1] - x1] > 0
    if scale < 1:
        from PIL import Image
        full = np.asarray(Image.fromarray(full.astype(np.uint8) * 255).resize(size)) > 127
    return full
