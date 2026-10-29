"""Pictures for the steps that need one, made with Muse Image, sent as tiny black-and-white images (and as text when
that reads).

  1. Which steps: Muse Spark picks at most MAX_PICTURES steps where the change to an object is hard to say in words
     (which way a handle ends up, a pin pulled out, a cap off), possibly none. Objects only: never a person, a body or
     a body part, so steps done to a person (CPR, choking, injecting) get words only. Drawn bodies were noise (a CPR
     chest came out as a ribcage, then as a box with a blob), Muse Image refused some, and pictures stay PG. Never
     "call 911", checks, waiting or repeats either.
  2. What it looks like: GUIDE, the style rules learned from reviewing every picture by eye (one whole object,
     black and white, few lines, no people or anatomy, no arrows or text, a big change), sent with every prompt.
  3. Motion and continuity: two frames per step, before and after; the change between them is the motion (played
     in place as a flip-book). Every frame is a Muse Image edit of the one before while it is the same object, so it
     carries through; a different object starts a fresh picture.
  4. Check: first without a model, before and after must differ (MIN_CHANGE of the pixels as sent); then a different
     model (Muse Wonder) is asked concrete questions about the two frames as they will be seen (128 px, black and
     white): is this kind of object the same on most makes, and is the change visible at a glance
     (verify.picture_checks; tuned on 19 pairs a person labelled: it agrees with them on 15, "could it be" on 14 by
     passing everything); the text frames get "could this be it?" (verify.could_be), told they are meant to be
     blurry, and
     the text frames, which it is told are meant to be blurry (verify.could_be); "it" is the item's change as part of
     the step, not the whole step (a pin picture was rejected for not also showing the aiming). An "after" that
     couldn't be is redrawn once with the reason. The change is made clearly visible at thumbnail size (a removed
     cap with a clear gap: an EpiPen "after" with the cap still on passed the lenient check).
  5. Text: outlined shapes are filled into silhouettes before turning into characters, so a pose reads as a shape
     at 40 columns; thin outlines alone broke into scattered ░▒.
Pictures are sent as tiny 2-colour PNGs (about 2 KB each); text frames only if they passed too.
"""
import base64
import io
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import llm, museimage, verify
from .photo import BLANK, MONO_CELL, SMS_CELL

MONO_COLS, MONO_ROWS = 40, 20
SMS_COLS, SMS_ROWS = 18, 9
TRIES = 2
MAX_PICTURES = 3
# The style guide: every lesson from reviewing the pictures by eye, used by every prompt below.
GUIDE = [
    "one object only, drawn whole so it is recognisable at a glance (the whole extinguisher, not a floating handle)",
    "seen from the angle that shows the change best, large and centred, cropped close",
    "black and white only, as it will arrive: thick bold black outlines and solid black shapes on pure white; no grey, "
    "shading, colour, texture or hatching (red highlights turned brown, then black anyway)",
    "few lines: no finger detail, wrinkles, flames or sparks drawn line by line; it must read at 128 pixels",
    "no people, bodies, faces or anatomy (a CPR chest came out as a ribcage); a hand only if the object cannot show "
    "the change by itself, as one plain solid silhouette",
    "no arrows, motion lines or text: the change between the two frames is the motion (arrows were noise everywhere)",
    "the change is big: a moved part ends clearly elsewhere, a removed part sits apart with a clear gap (an EpiPen "
    "\"after\" kept its cap on)",
]
RULES = "Style rules: " + "; ".join(GUIDE) + "."
CHOOSE = ("These are the steps for %r:\n%s\nSome steps may get a tiny black-and-white picture of the OBJECT the step acts "
          "on (a tool, valve, device, switch or part: never a person, a body or a body part), shown in two frames: before "
          "and after the step, so the change between them shows the motion. Pick at most %d steps where that change to "
          "an object is something words can't easily show (which way a handle ends up, a pin pulled out, a cap off, a "
          "switch flipped). Only objects that look and work about the same everywhere (pipe valve handles, breaker "
          "toggles, plugs, extinguishers, epinephrine pens, AEDs); never devices that differ by make (stove knobs, car "
          "controls, appliance panels: a picture of one model can mislead someone with another). Never pick a step "
          "that acts on a person (pressing a chest, thrusts, injecting), calling for help, checking, waiting or "
          "repeating; it is fine to pick none. For each pick give: the whole object, what it "
          "looks like before, what it looks like after (a change big enough to see in a tiny picture), and whether a "
          "hand must be shown (only if the object cannot show the change by itself). Also give \"object\": one or two "
          "words naming the physical object, the same words whenever two picks show the same object. Reply as JSON: "
          '{"pictures": [{"step": number, "object": "", "item": "", "before": "", "after": "", "hand": true or false}]}')
STYLE = "Instructional illustration of %s. %s %s It shows: %s"
EDIT = ("The same illustration: the same object, same angle, same framing and size, same style. Change only this: it "
        "now shows %s. %s%s")
HAND = "If a hand is needed, draw one simple solid black hand silhouette, nothing more of the person."
NO_HAND = "No hands."


def tiny(img, w=192):
    """The picture as a 2-colour PNG: about 1.5-3 KB for bold line art."""
    s = img.convert("L").resize((w, w), Image.LANCZOS).point(lambda v: 255 if v > 140 else 0).convert("1")
    buf = io.BytesIO()
    s.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    return {"data_url": "data:image/png;base64," + base64.b64encode(data).decode(), "bytes": len(data),
            "width": w, "height": w, "format": "PNG"}


def _shapes(img, side=512):
    """-> (ink lines, filled shapes): the picture's black lines, slightly thickened, and everything a line encloses
    (not reachable from the border), so outlined people become solid silhouettes."""
    g = img.convert("L")
    g.thumbnail((side, side))
    ink = g.point(lambda v: 0 if v < 120 else 255).filter(ImageFilter.MinFilter(3))
    fill = ink.convert("RGB")
    w, h = fill.size
    border = [(x, 0) for x in range(0, w, 8)] + [(x, h - 1) for x in range(0, w, 8)] + \
             [(0, y) for y in range(0, h, 8)] + [(w - 1, y) for y in range(0, h, 8)]
    for p in border:
        if fill.getpixel(p) == (255, 255, 255):
            ImageDraw.floodfill(fill, p, (255, 0, 0))
    a = np.asarray(fill)
    return np.asarray(ink) < 128, ~((a[..., 0] == 255) & (a[..., 1] == 0))


def to_text(img):
    """-> (WhatsApp rows, SMS rows). Outlined shapes are filled into silhouettes, so a pose reads as a shape at 40
    columns (thin outlines alone broke into scattered ░▒): lines █ ▓, filled areas ▒ ░ (WhatsApp); lines # and
    filled areas + in characters that keep their width in iPhone Messages (SMS). Cropped to the drawing."""
    lines, solid = _shapes(img)
    ys, xs = np.nonzero(solid)
    if not len(ys):
        return [], []
    pad = 4
    y0, y1, x0, x1 = max(0, ys.min() - pad), ys.max() + pad, max(0, xs.min() - pad), xs.max() + pad
    L, S = lines[y0:y1, x0:x1], solid[y0:y1, x0:x1]
    H, W = L.shape

    def cover(m, cols, rows):
        return np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((cols, rows), Image.BOX), float) / 255

    cols = max(16, min(MONO_COLS, round(MONO_ROWS * W / H * MONO_CELL)))
    rows = max(4, round(cols * H / W / MONO_CELL))
    l, f = cover(L, cols, rows), cover(S, cols, rows)
    mono = ["".join("█" if a >= 0.35 else "▓" if a >= 0.15 else "▒" if b >= 0.5 else "░" if b >= 0.2 else " "
                    for a, b in zip(lr, fr)).rstrip() for lr, fr in zip(l, f)]
    scols = max(6, min(SMS_COLS, round(SMS_ROWS * W / H * SMS_CELL)))
    srows = max(3, round(scols * H / W / SMS_CELL))
    l, f = cover(L, scols, srows), cover(S, scols, srows)
    sms = ["".join("#" if a >= 0.3 else "+" if b >= 0.45 else BLANK for a, b in zip(lr, fr)).rstrip(BLANK) or BLANK
           for lr, fr in zip(l, f)]
    return mono, sms


def choose(task, steps):
    """Muse's picks: [{"step": n, "item", "before", "after", "hand"}], at most MAX_PICTURES, in step order."""
    try:
        d = llm.chat_json([{"role": "user", "content": CHOOSE % (task, "\n".join("%d. %s" % (i, s) for i, s in enumerate(steps, 1)),
                                                                 MAX_PICTURES)}], max_tokens=1500)
    except Exception:
        return []
    out, seen = [], set()
    for p in d.get("pictures") or []:
        try:
            n = int(p.get("step"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= n <= len(steps) and n not in seen and p.get("item") and p.get("after"):
            seen.add(n)
            out.append({"step": n, "item": str(p["item"]), "before": str(p.get("before") or ""), "after": str(p["after"]),
                        "hand": bool(p.get("hand")), "object": str(p.get("object") or p["item"]).strip().lower()})
    return sorted(out, key=lambda p: p["step"])[:MAX_PICTURES]


def _render(img):
    from . import codes
    mono, sms = to_text(img)
    return {"image": img, "small": tiny(img), "mono": mono, "sms": sms, "kit": codes.text(codes.picture(img, tones=False))}


def _change(step, p):
    """What the two frames claim to show: the item's change, as part of the step (not the whole step)."""
    return "part of the step %r, shown as two frames, before then after: the %s goes from %s to %s" % (
        step, p["item"], p["before"] or "as it was", p["after"])


MIN_CHANGE = 0.015  # share of pixels that must differ between before and after (at 128 px, black and white)


def _as_sent(img, side=128):
    """The picture as the person gets it: 128 px, black and white (smsimage.png), shown back at full size."""
    return img.convert("L").resize((side, side), Image.LANCZOS).point(lambda v: 255 if v > 140 else 0) \
        .resize((512, 512), Image.NEAREST).convert("RGB")


def _changed(a, b, side=128):
    """Share of pixels that differ between the two frames as sent."""
    x = np.asarray(a.convert("L").resize((side, side), Image.LANCZOS)) > 140
    y = np.asarray(b.convert("L").resize((side, side), Image.LANCZOS)) > 140
    return float((x != y).mean())


def _pair(a, b):
    """Before and after side by side, for the checker."""
    a, b = a.convert("RGB"), b.convert("RGB").resize(a.size)
    out = Image.new("RGB", (a.width * 2 + 40, a.height), "white")
    out.paste(a, (0, 0))
    out.paste(b, (a.width + 40, 0))
    return out


def sequence(task, steps):
    """Before/after frames of the item for each picked step, all one chain of edits (each step's "before" is an
    edit of the previous step's "after"), so the same item carries through. Each step's "after" frame is checked
    ("could this be the step?") as a picture and as text; redrawn once with the reason if the picture couldn't be.
    -> [{"n", "step", "item", "frames": [before, after], "picture_ok", "picture_why", "text_ok", "text_why"}]."""
    picks = choose(task, steps)
    out, prev, prev_object = [], None, None
    for p in picks:
        n, hand = p["step"], " " + (HAND if p["hand"] else NO_HAND)
        if p["object"] != prev_object:  # a different object starts a fresh picture (a stove knob was once drawn
            prev = None                 # as an edit of the fire blanket picture, under the blanket)
        try:
            before = museimage.generate(STYLE % (p["item"], RULES, hand.strip(), p["before"] or p["item"])) if prev is None \
                else museimage.generate(EDIT % (p["before"] or p["item"], RULES, hand), prev)
            note, after, ok, why = "", None, False, ""
            for _ in range(TRIES):
                after = museimage.generate(EDIT % (p["after"], RULES, hand) + (" Fix this: %s" % note if note else ""), before)
                changed = _changed(before, after)
                if changed < MIN_CHANGE:  # no model needed: the two frames are (nearly) the same picture
                    ok, why = False, "before and after look the same (%.1f%% of the picture changed)" % (100 * changed)
                else:  # checked as it will be seen (128 px, black and white), with the questions people's labels taught
                    c = verify.picture_checks(_pair(_as_sent(before), _as_sent(after)), p["item"], p["before"], p["after"])
                    ok, why = c["ok"], ("" if c["ok"] else "not %s: " % " and not ".join(
                        k for k in ("universal", "clear") if not c[k])) + c["why"]
                if ok:
                    break
                note = why
        except Exception as ex:
            out.append({"n": n, "step": steps[n - 1], "item": p["item"], "error": type(ex).__name__})
            break
        frames = [_render(before), _render(after)]
        txt_ok, txt_why = verify.could_be(_change(steps[n - 1], p), frames=[f["mono"] for f in frames])
        out.append({"n": n, "step": steps[n - 1], "item": p["item"], "before": p["before"], "after": p["after"],
                    "frames": frames, "picture_ok": ok, "picture_why": why, "text_ok": txt_ok, "text_why": txt_why})
        prev, prev_object = after, p["object"]
    return out
