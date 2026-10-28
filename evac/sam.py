"""SAM 3.1 on the Meta Model API: a short noun phrase + an image -> every matching object's box and mask.

    sam.segment(img, "penguin") -> [{"id", "box": (x1, y1, x2, y2), "score", "mask": HxW bool or None}, ...]

SAM 3.1 is served on /v1/responses (not /chat/completions). It answers in a special-token grammar in the output
text, one line per frame (an image is always `<0f>`):

    <0f>7<|box;x1=42;y1=65;x2=243;y2=255;w=256;h=256;c=0.91|><|mask;x=0;y=0;c=0.91;data=191,202,!<payload>|>

Boxes are in the pixels of the image sent; the mask covers the box. Its payload is compressed, and Meta's SAM
License does not allow reverse-engineering it, so responses are parsed and masks decoded with Meta's own
`meta-sam-parser` (pip; Python 3.10+, installed in the project's .venv). Without it, a minimal reader of the box
tokens keeps the boxes, `mask` is None and callers use the box.
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
from PIL import Image

from . import llm

MODEL = os.environ.get("SAM_MODEL", "sam-3.1")
MAX_SIDE = 1024  # images are sent at most this big; boxes come back in these pixels and are scaled back up
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "sam")
RECORD = re.compile(r"(\d+)<\|box;([^|]*)\|><\|mask;([^|]*)\|>")

try:  # Meta's parser and mask decoder; optional
    import meta_sam_parser
except ImportError:
    meta_sam_parser = None


def available():
    return llm.available()


def masks_available():
    return meta_sam_parser is not None


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
    text = _request(sent, prompt)
    out = _parse(text, sent.size) if meta_sam_parser else _boxes_only(text)
    for rec in out:
        rec["box"] = tuple(v / scale for v in rec["box"])
        if rec["mask"] is not None and scale < 1:
            rec["mask"] = np.asarray(Image.fromarray(rec["mask"].astype(np.uint8) * 255).resize(img.size)) > 127
    return sorted(out, key=lambda r: -r["score"])


def _parse(text, size):
    """Meta's parser: box and mask records keyed by object id; each mask pasted into a full-size bool image."""
    parser = meta_sam_parser.image_segmentation_format().create_parser()
    parser.push(text)
    result = parser.finish(meta_sam_parser.CompletedOutcome()).result
    out = {}
    for r in result.records:
        rec = out.setdefault(r.object_id, {"id": r.object_id, "box": None, "score": 1.0, "mask": None})
        if r.confidence is not None:
            rec["score"] = float(r.confidence)
        if isinstance(r, meta_sam_parser.SegmentationBoxRecord):
            rec["box"] = (r.left, r.top, r.right, r.bottom)
        elif isinstance(r, meta_sam_parser.SegmentationMaskRecord):
            b, m = r.bounds, r.mask
            raster = np.frombuffer(meta_sam_parser.decode_mask_to_raster(m), np.uint8).reshape(m.height, m.width)
            full = np.zeros((size[1], size[0]), bool)
            full[b.top:b.top + m.height, b.left:b.left + m.width] = raster[:size[1] - b.top, :size[0] - b.left] > 0
            rec["mask"] = full
            rec["box"] = rec["box"] or (b.left, b.top, b.right, b.bottom)
    return [r for r in out.values() if r["box"]]


def _boxes_only(text):
    """Without Meta's parser: just the box tokens."""
    out = []
    for oid, box_s, _ in RECORD.findall(text):
        b = _fields(box_s)
        out.append({"id": int(oid), "box": tuple(int(b[k]) for k in ("x1", "y1", "x2", "y2")),
                    "score": float(b.get("c", 1)), "mask": None})
    return out
