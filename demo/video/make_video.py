"""The Red Hook demo video, first cut: the screenshots from capture.py, captions, title cards, slow zooms and
crossfades, encoded with demo/video/encode.swift (AVFoundation, no ffmpeg).

    .venv/bin/python demo/video/make_video.py        # -> out/demo/redhook/redhook.mp4

Scenes are data (SCENES below): change a caption or a duration and run it again; nothing is re-captured.
"""
import json
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BASE = os.path.join(ROOT, "out", "demo", "redhook")
SHOTS, FRAMES = os.path.join(BASE, "shots"), os.path.join(BASE, "frames")
ENCODER = os.path.join(ROOT, "out", "demo", "encode")
W, H, FPS, FADE = 1920, 1080, 24, 0.5
BG = (11, 16, 21)

FONTS = ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"]


def font(size, bold=False):
    for f in FONTS:
        try:
            ft = ImageFont.truetype(f, size)
            if bold and f.endswith("SFNS.ttf"):
                try:
                    ft.set_variation_by_name("Bold")
                except Exception:
                    pass
            return ft
        except OSError:
            continue
    return ImageFont.load_default()


def wrap(draw, text, ft, width):
    lines = []
    for para in text.split("\n"):
        words, line = para.split(), ""
        for w in words:
            t = (line + " " + w).strip()
            if draw.textlength(t, font=ft) <= width:
                line = t
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return lines


def shot(name):
    return Image.open(os.path.join(SHOTS, name + ".png")).convert("RGB")


# Rectangles are in the dashboard's CSS pixels (1920 x 1080); the shots are twice that.
FULL = (0, 0, 1920, 1080)
MAP = (0, 64, 1030, 1080)
PANEL = (1030, 64, 1530, 1080)
PHONE = (1548, 112, 1904, 1032)
CARD = (1040, 282, 1520, 762)    # the focused report card, scrolled to the top of the feed


def crop(img, rect, size):
    k = img.width / 1920
    return img.crop(tuple(round(v * k) for v in rect)).resize(size, Image.LANCZOS)


def caption_bar(frame, text, step=None):
    """The story's words across the bottom of a dashboard frame, with the beat's name above them."""
    d = ImageDraw.Draw(frame, "RGBA")
    ft = font(38)
    lines = wrap(d, text, ft, W - 180)
    h = 46 * len(lines) + (86 if step else 50)
    d.rectangle((0, H - h, W, H), fill=(8, 12, 16, 228))
    y = H - h + 25
    if step:
        d.text((90, y), step, font=font(26, True), fill=(77, 171, 247))
        y += 38
    for ln in lines:
        d.text((90, y), ln, font=ft, fill=(240, 244, 248))
        y += 46
    return frame


def title_card(title, sub="", foot=""):
    f = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(f)
    ft = font(84, True)
    tw = d.textlength(title, font=ft)
    d.text(((W - tw) / 2, 400), title, font=ft, fill=(240, 244, 248))
    if sub:
        for i, ln in enumerate(wrap(d, sub, font(40), 1400)):
            sw = d.textlength(ln, font=font(40))
            d.text(((W - sw) / 2, 530 + i * 54), ln, font=font(40), fill=(139, 152, 165))
    if foot:
        for i, ln in enumerate(wrap(d, foot, font(28), 1600)):
            fw = d.textlength(ln, font=font(28))
            d.text(((W - fw) / 2, 900 + i * 40), ln, font=font(28), fill=(110, 122, 134))
    return f


def side(img, rect, step, title, body):
    """A tall part of the dashboard (the phone, the report panel) large on the right, the story on the left."""
    f = Image.new("RGB", (W, H), BG)
    x0, y0, x1, y1 = rect
    hgt = 1000
    wid = round((x1 - x0) * hgt / (y1 - y0))
    f.paste(crop(img, rect, (wid, hgt)), (W - wid - 140, 40))
    d = ImageDraw.Draw(f)
    d.text((120, 300), step, font=font(30, True), fill=(77, 171, 247))
    y = 350
    for ln in wrap(d, title, font(64, True), W - wid - 420):
        d.text((120, y), ln, font=font(64, True), fill=(240, 244, 248))
        y += 78
    y += 24
    for ln in wrap(d, body, font(36), W - wid - 420):
        d.text((120, y), ln, font=font(36), fill=(190, 200, 210))
        y += 50
    return f


def ease(t):
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


# Each scene: (kind, args, seconds). "zoom" moves from one rectangle to another (16:9) over the scene.
def scenes(counts):
    c = counts
    return [
        ("card", dict(title="The night the data went out", sub="A community hub in Red Hook, Brooklyn, during a coastal flood",
                      foot="A drill: the storm, the alerts and every person in it are fictional."), 4.5),
        ("card", dict(title="Neighbours go first", sub="When a storm cuts mobile data, text messages usually still get through. "
                      "For the first hours, the people who check on the 80-year-old on the 14th floor are their neighbours."), 7),
        ("full", dict(shot="empty", text="Rosa runs the Neighbors Hub at Coffey Park. Her laptop has the only working connection. "
                      "The 42 phones around her can only text.", step="9:40 PM · HOUR 3 OF THE STORM"), 7),
        ("side", dict(shot="alert_sam", rect=PHONE, step="1 · THE ALERT", title="A map in a text message",
                      body="The city's flood warning reaches the hub and goes back out to every phone as ordinary texts: the words, "
                      "a map the offline kit draws, and a signature. Sam is inside the flood zone, so his shows his way out."), 8),
        ("side", dict(shot="rumour_sam", rect=PHONE, step="2 · A RUMOUR", title="One phrase changed",
                      body="A forwarded copy tells people to go to the Navy Yard. It looks the same, but the signature no longer "
                      "matches: the kit says it's not genuine."), 7),
        ("full", dict(shot="wave1", text="Rosa asks everyone to check in. One tap in the kit sends status, people, battery and GPS, as one text.",
                      step="3 · CHECK-IN"), 2.4),
        ("full", dict(shot="wave2", text="Rosa asks everyone to check in. One tap in the kit sends status, people, battery and GPS, as one text.",
                      step="3 · CHECK-IN"), 2.4),
        ("full", dict(shot="wave3", text="Rosa asks everyone to check in. One tap in the kit sends status, people, battery and GPS, as one text.",
                      step="3 · CHECK-IN"), 2.4),
        ("full", dict(shot="wave4", text="Rosa asks everyone to check in. One tap in the kit sends status, people, battery and GPS, as one text.",
                      step="3 · CHECK-IN"), 2.8),
        ("zoom", dict(shot="help_first", start=FULL, end=(1030, 64, 1530, 345), text="Muse reads every check-in and suggests who to help "
                      "first: an injury, a neighbour on the 14th floor at 18% battery, water coming in downstairs.", step="HELP FIRST"), 7.5),
        ("side", dict(shot="dani_route", rect=PHONE, step="4 · A VOLUNTEER", title="Dani goes to Mr. Alvarez",
                      body="The walk keeps out of the floodwater. Dani's phone gets the steps, a map and the whole route, "
                      "which opens in an offline map app."), 8),
        ("side", dict(shot="okafor_photo", rect=CARD, step="5 · A PHOTO BY TEXT", title="A tree on a power line",
                      body="The Okafors text a photo: 128 pixels, a dozen texts. Muse and SAM rate it critical, a downed line, "
                      "and it's on Dani's way."), 8),
        ("side", dict(shot="okafor_zoom", rect=PHONE, step="5 · A CLOSER LOOK", title="Their phone sends the wire",
                      body="Rosa asks for a closer look. The Okafors' phone crops its own full-size photo and sends back just "
                      "that part, pasted over the picture."), 8),
        ("zoom", dict(shot="blocked", start=FULL, end=(170, 330, 860, 718), text="One click marks the danger. Dani's route bends around "
                      "it, and 17 neighbours nearby get a warning.", step="6 · MARK THE DANGER"), 8),
        ("side", dict(shot="luisa", rect=PHONE, step="7 · IN HER LANGUAGE", title="Luísa writes in Portuguese",
                      body="The hub answers in Portuguese, packed so the accents don't cost double. June is on her way "
                      "with water, a lantern and a power bank."), 8),
        ("full", dict(shot="end", text="By the end of the night: %d of %d neighbours checked in, %d visited. Everything the hub sent and "
                      "received: %d texts, %.0f KB, %.0f%% of one phone photo." % (c["checked_in"], c["residents"], c["reached"], c["sms"],
                                                                             c["bytes"] / 1000, c["bytes"] / 2.5e6 * 100),
                      step="MIDNIGHT"), 8),
        ("card", dict(title="One connection is enough", sub="Every phone already speaks SMS. A hub with one good connection turns it into "
                      "maps, photos and coordination for a whole neighbourhood.",
                      foot="Muse Spark reads the alert, triages photos, suggests priorities and translates · SAM 3.1 finds what to "
                      "look at · Muse Image made the drill's photos · all on the Meta Model API"), 8),
    ]


def render(kind, a, secs):
    """-> a list of (frame, seconds): stills are one frame, zooms are many."""
    if kind == "card":
        return [(title_card(a["title"], a.get("sub", ""), a.get("foot", "")), secs)]
    img = shot(a["shot"])
    if kind == "full":
        return [(caption_bar(crop(img, FULL, (W, H)), a["text"], a.get("step")), secs)]
    if kind == "side":
        return [(side(img, a["rect"], a["step"], a["title"], a["body"]), secs)]
    if kind == "zoom":  # hold the whole dashboard, move in, hold the detail
        out, hold, move = [], 1.6, 1.6
        start, end = a["start"], a["end"]
        ew, eh = end[2] - end[0], end[3] - end[1]  # keep 16:9: widen the end box around its centre
        cx, cy = (end[0] + end[2]) / 2, (end[1] + end[3]) / 2
        if ew / eh < 16 / 9:
            ew = eh * 16 / 9
        else:
            eh = ew * 9 / 16
        end = (max(0, cx - ew / 2), max(0, cy - eh / 2), min(1920, cx + ew / 2), min(1080, cy + eh / 2))
        out.append((caption_bar(crop(img, start, (W, H)), a["text"], a.get("step")), hold))
        n = int(move * FPS)
        for i in range(1, n + 1):
            r = lerp(start, end, ease(i / n))
            out.append((caption_bar(crop(img, r, (W, H)), a["text"], a.get("step")), 1 / FPS))
        out.append((out[-1][0], max(0.5, secs - hold - move)))
        return out
    raise KeyError(kind)


def main():
    counts = json.load(open(os.path.join(SHOTS, "shots.json")))["end"]["counts"]
    shutil.rmtree(FRAMES, ignore_errors=True)
    os.makedirs(FRAMES)
    manifest, k, prev = [], 0, None

    def put(img, secs):
        nonlocal k
        path = os.path.join(FRAMES, "%05d.jpg" % k)
        img.save(path, quality=90)
        manifest.append("%.4f %s" % (secs, os.path.abspath(path)))
        k += 1

    for kind, a, secs in scenes(counts):
        frames = render(kind, a, secs)
        if prev is not None:  # crossfade from the last frame of the previous scene
            n = int(FADE * FPS)
            for i in range(1, n + 1):
                put(Image.blend(prev, frames[0][0], i / (n + 1)), 1 / FPS)
        for img, s in frames:
            put(img, s)
        prev = frames[-1][0]
        print(kind, a.get("shot") or a.get("title"), "%.1f s" % secs, flush=True)
    with open(os.path.join(BASE, "frames.txt"), "w") as f:
        f.write("\n".join(manifest) + "\n")
    out = os.path.join(BASE, "redhook.mp4")
    print(subprocess.run([ENCODER, os.path.join(BASE, "frames.txt"), out, str(W), str(H)], capture_output=True, text=True).stdout)


if __name__ == "__main__":
    main()
