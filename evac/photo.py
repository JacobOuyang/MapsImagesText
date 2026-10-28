"""Photos that won't load: the server (which has a connection) looks at the photo with Muse Spark and sends back
what's in it, plus the photo itself drawn in text, sized for each channel:

  low    - SMS: description + a small sketch in the iPhone-safe characters (all exactly one digit wide in SF Pro)
  medium - WhatsApp: description + a wider monospace sketch with Muse's subjects boxed and numbered
  high   - the photo itself
"""
import base64
import gzip
import io

import numpy as np
from PIL import Image, ImageOps

from . import llm

# Lightest to darkest. SF Pro 17 px: all 10.28 px wide; ink measured on a canvas (0, 24, 28, 37, 46, 52, 55).
SMS_RAMP = " +×¤0#$"
MONO_RAMP = " .:-=+*#%@"
SMS_CELL = 22 / 10.28   # iMessage line height / digit width
MONO_CELL = 16.9 / 7.53  # WhatsApp monospace line height / Menlo cell width at 12.5 px
PROMPT = ("Describe this photo for someone whose phone could not load it. Reply as JSON: "
          '{"caption": one sentence, "details": [up to 3 short facts a person would want to know, including any '
          'readable text], "subjects": [up to 4 {"name": 1-3 words, "box": [x0, y0, x1, y1] as fractions of the '
          "width and height}]}")


def describe(jpeg):
    """Muse Spark's caption, details and subject boxes. Falls back to an empty description."""
    if not llm.available():
        return {"caption": "A photo (no description: model not configured).", "details": [], "subjects": []}
    part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}
    try:
        d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": PROMPT}, part]}])
    except Exception as e:
        return {"caption": "A photo (description failed: %s)." % e, "details": [], "subjects": []}
    subs = []
    for s in d.get("subjects") or []:
        try:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in s["box"])
            if x1 > x0 and y1 > y0:
                subs.append({"name": str(s["name"]), "box": [x0, y0, x1, y1]})
        except (KeyError, TypeError, ValueError):
            continue
    return {"caption": d.get("caption") or "", "details": [str(x) for x in d.get("details") or []][:3], "subjects": subs[:4]}


def sketch(img, cols, ramp, cell_aspect):
    """Photo -> rows of characters, darker pixels -> inkier characters. Each cell keeps the photo's proportions."""
    g = ImageOps.equalize(img.convert("L"))  # spreads tones out, so misty or dark photos still show shapes
    rows = max(1, round(cols * img.height / img.width / cell_aspect))
    a = np.asarray(g.resize((cols, rows), Image.LANCZOS)) / 255.0
    idx = np.rint((1 - a) * (len(ramp) - 1)).astype(int)
    return [[ramp[i] for i in row] for row in idx]


def _box(grid, box, n):
    """Outline a subject box with line-drawing characters and number it in the top-left corner."""
    rows, cols = len(grid), len(grid[0])
    c0, c1 = int(box[0] * (cols - 1)), int(box[2] * (cols - 1))
    r0, r1 = int(box[1] * (rows - 1)), int(box[3] * (rows - 1))
    if c1 - c0 < 2 or r1 - r0 < 1:
        return
    for c in range(c0 + 1, c1):
        grid[r0][c] = grid[r1][c] = "─"
    for r in range(r0 + 1, r1):
        grid[r][c0] = grid[r][c1] = "│"
    grid[r0][c0], grid[r0][c1], grid[r1][c0], grid[r1][c1] = "┌", "┐", "└", "┘"
    grid[r0][c0 + 1] = str(n)


def _where(box):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    v = "top" if cy < 0.34 else "bottom" if cy > 0.66 else "middle"
    h = "left" if cx < 0.34 else "right" if cx > 0.66 else "centre"
    return "across the %s" % v if box[2] - box[0] > 0.8 else "%s %s" % (v, h)


def run(data, sms_cols=18, mono_cols=58):
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    d = describe(buf.getvalue())

    lines = ["Photo: " + d["caption"]] + ["- " + x for x in d["details"]]
    sms_rows = sketch(img, sms_cols, SMS_RAMP, SMS_CELL)
    sms = "\n".join(lines + [""] + ["".join(r) for r in sms_rows] + ["(the photo, drawn in text)"])

    mono_rows = sketch(img, mono_cols, MONO_RAMP, MONO_CELL)
    key, n, drawn = [], 0, []
    for s in d["subjects"]:
        x0, y0, x1, y1 = s["box"]
        # Box objects only: regions like "sky" or "water" (or boxes that overlap) would just hide the picture.
        if (x1 - x0) * (y1 - y0) < 0.5 and x1 - x0 < 0.9 and not any(
                x0 < b[2] and b[0] < x1 and y0 < b[3] and b[1] < y1 for b in drawn):
            drawn.append(s["box"])
            n += 1
            _box(mono_rows, s["box"], n)
            key.append("%d  %s (%s)" % (n, s["name"], _where(s["box"])))
        else:
            key.append("   %s (%s)" % (s["name"], _where(s["box"])))
    mono = "\n".join(["".join(r) for r in mono_rows] + [""] + key)
    return {"caption": d["caption"], "details": d["details"], "subjects": d["subjects"],
            "sms": sms, "text": "\n".join(lines), "mono": mono,
            "mono_bytes": len(mono.encode()), "mono_gzip": len(gzip.compress(mono.encode())),
            "photo_bytes": len(data), "width": img.width, "height": img.height}
