"""Compact codes inside ordinary text messages, for a small offline kit on the phone (sim/kit.html) to turn into
pictures, maps and forms, and back.

Every code looks like  #P3f.1/2:iVBORw0KGgo...:9c1e#  : a type letter, an id, part i of n, a chunk of base64, and a
CRC-16 of the chunk. Only characters of the basic SMS alphabet are used (base64, # . / :), so an SMS carries 160 of
them, and a part never exceeds one SMS (PART_CHARS). Parts can arrive out of order or not at all: the kit reassembles
them, says which are missing and writes the "RESEND <id> <part>" reply; the server keeps what it sent in OUTBOX.

Types (payloads before base64):
  P picture       a 128 px picture: a greyscale WebP for photos, a 1-bit PNG for line drawings (picture(); every
                  browser decodes both, so the kit needs no decoder of its own). The
                  server keeps the full-size original, so the person can ask for a closer look (Z). Sending an
                  "after" as only its difference from "before" was tried and was no smaller (the two redrawn
                  pictures differ in scattered pixels); the kit flips between the two and zooms on what changed
  Z zoom          a closer look at part of an earlier picture, cropped from the kept original: the parent's id, the
                  box in the parent's pixels (4 x uint16, 1/64 px), then a greyscale WebP. The kit pastes it over the
                  parent, so the picture gets sharper where the person looked. Asked for with "ZOOM <id> x y w h"
                  (a box in that picture's pixels) or "ZOOM <id> <n>" (a numbered part from its key)
  K picture key   JSON {"i": picture id, "k": [[name, x, y, w, h]]}: the named parts of a picture (SAM 3.1 boxes),
                  which the kit outlines and numbers so a tap asks for that part
  M map           deflated JSON: you, the danger zone, closed stretches, the route, a few named streets, the goal and
                  landmarks, in metres from you (rounded); the kit draws it as a real map
  G route         deflated "destination\nencoded polyline" (Google's polyline format, 1e-5 degrees): the kit rebuilds
                  a GPX file that an offline map app (Organic Maps, OsmAnd) opens for turn-by-turn navigation
  T text          deflated UTF-8 text. One accented letter outside the GSM alphabet (á, ã, ç, ...) makes a whole SMS
                  UCS-2 (70 characters, codes included); compressed and base64'd it stays in the basic alphabet
  L location      JSON: GPS, accuracy, status (ok / help / hurt), people, battery, time (phone -> server)
  S phrasebook    "<entry id>": a checked how-to entry already in the kit (steps and pictures), so only the id travels
  A signature     Ed25519 signature of the message's text (outside the codes, plus any T text): the kit checks the
                  alert is genuine
"""
import base64
import hashlib
import json
import os
import re
import zlib

PART_CHARS = 153            # one SMS part in the basic alphabet
CODE = re.compile(r"#([PZKMGTLSA])([0-9a-z]{2})\.(\d+)/(\d+):([A-Za-z0-9+/=]*):([0-9a-f]{4})#")
OUTBOX = {}                 # id -> [parts]: what was sent, for RESEND
KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "keys", "ed25519.json")
KEPT_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "kit")  # originals of the pictures sent, for ZOOM
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
           "abcdefghijklmnopqrstuvwxyzäöñüà")


def crc16(s):
    """CRC-16/CCITT-FALSE of an ASCII string, as 4 hex digits."""
    c = 0xFFFF
    for ch in s.encode():
        c ^= ch << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return "%04x" % c


def sms_parts(text):
    """SMS a text takes: the basic (GSM-7) alphabet if every character is in it (160 alone, 153 per part), else
    UCS-2 (70 alone, 67 per part)."""
    if all(ch in GSM7 for ch in text):
        return 1 if len(text) <= 160 else -(-len(text) // 153)
    units = len(text.encode("utf-16-le")) // 2
    return 1 if units <= 70 else -(-units // 67)


def _new_id(data):
    return "".join("0123456789abcdefghijklmnopqrstuvwxyz"[b % 36] for b in hashlib.md5(data).digest()[:2])


def encode(kind, data):
    """bytes -> the code's parts (each fits one SMS). Remembered in OUTBOX for RESEND."""
    b64 = base64.b64encode(data).decode()
    ident = _new_id(kind.encode() + data)
    head = len("#%s%s.%d/%d:" % (kind, ident, 9, 9)) + len(":xxxx#")
    size = PART_CHARS - head - 2  # room for two-digit part numbers
    chunks = [b64[i:i + size] for i in range(0, len(b64), size)] or [""]
    parts = ["#%s%s.%d/%d:%s:%s#" % (kind, ident, i, len(chunks), c, crc16(c)) for i, c in enumerate(chunks, 1)]
    OUTBOX[ident] = parts
    return parts


def text(parts):
    return "\n".join(parts)


def decode(message):
    """Every code in a message. -> {"complete": [{"kind", "id", "data"}], "missing": {id: [part numbers]},
    "bad": [(id, part)]}: parts with a wrong checksum count as missing."""
    got, bad = {}, []
    for kind, ident, i, n, chunk, crc in CODE.findall(message or ""):
        if crc16(chunk) != crc:
            bad.append((ident, int(i)))
            continue
        g = got.setdefault(ident, {"kind": kind, "n": int(n), "parts": {}})
        g["parts"][int(i)] = chunk
    complete, missing = [], {}
    for ident, g in got.items():
        lost = [k for k in range(1, g["n"] + 1) if k not in g["parts"]]
        if lost:
            missing[ident] = lost
            continue
        data = base64.b64decode("".join(g["parts"][k] for k in range(1, g["n"] + 1)))
        complete.append({"kind": g["kind"], "id": ident, "data": data})
    for ident, i in bad:
        if i not in missing.get(ident, []):
            missing.setdefault(ident, []).append(i)
    return {"complete": complete, "missing": missing, "bad": bad}


RESEND = re.compile(r"\bRESEND\s+([0-9a-z]{2})((?:\s+\d+)+)", re.I)


def resend(message):
    """"RESEND 7k 2 3" -> those parts again (from OUTBOX), or None."""
    m = RESEND.search(message or "")
    if not m or m.group(1).lower() not in OUTBOX:
        return None
    parts = OUTBOX[m.group(1).lower()]
    want = [int(x) for x in m.group(2).split()]
    return [parts[k - 1] for k in want if 1 <= k <= len(parts)]


def plain(message):
    """The message's text outside its codes, whitespace collapsed (what a signature covers)."""
    return " ".join(CODE.sub(" ", message or "").split())


# ---------- pictures and zooms ----------

def _kept():
    path = os.path.join(KEPT_DIR, "kept.json")
    return json.load(open(path)) if os.path.exists(path) else {}


def _keep(ident, file, region, size, parts=None):
    """Remember what a picture code shows: the original (a file in KEPT_DIR), the region of it, the size sent."""
    kept = _kept()
    kept[ident] = {"file": file, "region": [round(v, 1) for v in region], "size": list(size), "parts": parts or []}
    json.dump(kept, open(os.path.join(KEPT_DIR, "kept.json"), "w"))


def picture(img, tones=True, parts=None):
    """A picture -> code parts: a greyscale WebP for a photo (tones=True), a 1-bit PNG for a line drawing. The
    original is kept so the person can zoom; `parts` [(name, (x0, y0, x1, y1) in img's pixels)] adds a K code naming
    them, so a tap on one asks for it."""
    from . import smsimage
    data = smsimage.webp(img) if tones else smsimage.png(img)
    out = encode("P", data)
    ident = CODE.match(out[0]).group(2)
    os.makedirs(KEPT_DIR, exist_ok=True)
    file = hashlib.md5(img.tobytes()).hexdigest()[:16] + ".jpg"
    if not os.path.exists(os.path.join(KEPT_DIR, file)):
        img.convert("RGB").save(os.path.join(KEPT_DIR, file), "JPEG", quality=92)
    size = smsimage.fit(img).size
    k = size[0] / img.width
    named = [[str(n)[:20]] + [round(b[0] * k), round(b[1] * k), max(1, round((b[2] - b[0]) * k)), max(1, round((b[3] - b[1]) * k))]
             for n, b in (parts or [])]
    _keep(ident, file, (0, 0, img.width, img.height), size, named)
    if named:
        out += encode("K", json.dumps({"i": ident, "k": named}, separators=(",", ":"), ensure_ascii=False).encode())
    return out


ZOOM = re.compile(r"\bZOOM\s+([0-9a-z]{2})((?:\s+\d+){1,4})\b", re.I)


def zoom(message):
    """"ZOOM 3f 40 22 30 30" (a box in picture 3f's pixels) or "ZOOM 3f 2" (part 2 of its key) -> (words, code
    parts) for a closer look cropped from the kept original, or None if the message asks for no zoom."""
    from . import smsimage
    from PIL import Image
    m = ZOOM.search(message or "")
    if not m:
        return None
    ident, nums = m.group(1).lower(), [int(x) for x in m.group(2).split()]
    src = _kept().get(ident)
    if not src:
        return "Nothing to zoom: picture %s is no longer kept." % ident, []
    (x0, y0, x1, y1), (w, h) = src["region"], src["size"]
    if len(nums) == 1:
        if not 1 <= nums[0] <= len(src["parts"]):
            return "Picture %s has no part %d." % (ident, nums[0]), []
        name, bx, by, bw, bh = src["parts"][nums[0] - 1]
        what = "%s (part %d)" % (name, nums[0])
    elif len(nums) == 4:
        bx, by, bw, bh = nums
        what = "the area you marked"
    else:
        return "Send ZOOM, the picture's id, then a part number or a box: x y width height.", []
    sx, sy = (x1 - x0) / w, (y1 - y0) / h
    img = Image.open(os.path.join(KEPT_DIR, src["file"]))
    # The crop in the original's pixels: a little margin, square-ish, and never smaller than a picture's own 128 px
    # (past the original's resolution a zoom shows nothing new).
    cx, cy = x0 + (bx + bw / 2) * sx, y0 + (by + bh / 2) * sy
    half = max(bw * sx, bh * sy) * 0.6
    half = max(half, smsimage.SIDE / 2)
    rx0, ry0 = max(0, cx - half), max(0, cy - half)
    rx1, ry1 = min(img.width, cx + half), min(img.height, cy + half)
    crop = img.crop((round(rx0), round(ry0), round(rx1), round(ry1)))
    data = smsimage.webp(crop)
    box = [(rx0 - x0) / sx, (ry0 - y0) / sy, (rx1 - rx0) / sx, (ry1 - ry0) / sy]  # in the parent's pixels
    head = ident.encode() + b"".join(int(max(0, min(65535, round(v * 64)))).to_bytes(2, "big") for v in box)
    out = encode("Z", head + data)
    _keep(CODE.match(out[0]).group(2), src["file"], (rx0, ry0, rx1, ry1), smsimage.fit(crop).size)
    return "Closer look at %s in picture %s:" % (what, ident), out


# ---------- maps ----------

def map_scene(scene, max_roads=8, step_m=5):
    """A Scene (an alert or a walking step) -> map code parts. Everything in metres from you, rounded to step_m."""
    from . import geo, osm
    proj = geo.LocalProjection(scene.you)
    q = lambda p: [int(round(v / step_m)) for v in proj.to_xy(p)]
    simplify = lambda pts, k=24: pts if len(pts) <= k else [pts[round(i * (len(pts) - 1) / (k - 1))] for i in range(k)]
    m = {"v": 1, "s": step_m, "h": int(scene.half_m)}
    if scene.zone:
        (c, r), m["k"] = scene.zone, scene.hazard or "danger"
        m["z"] = q(c) + [int(round(r / step_m))]
    if scene.route:
        m["r"] = [q(p) for p in simplify(scene.route)]
    if scene.goal:
        m["g"] = [str(scene.goal[0])[:40]] + q(scene.goal[1])
    roads = []
    for road in (scene.shown or [])[:60]:
        if road.get("name") and len(road["pts"]) >= 2:
            roads.append([road["name"][:30], [q(p) for p in simplify(road["pts"], 12)]])
    named, seen = [], set()
    for name, pts in roads:  # one line per street name, the longest in view first
        if name not in seen:
            seen.add(name)
            named.append([name, pts])
    m["n"] = named[:max_roads]
    closed = []
    for seg in getattr(scene, "closures", []) or []:
        ends = [osm.find_intersection(scene.roads, seg["road"], x) for x in (seg.get("from"), seg.get("to")) if x]
        if len(ends) == 2 and all(ends):
            closed.append([seg["road"][:30], q(ends[0]), q(ends[1])])
    if closed:
        m["c"] = closed
    if scene.landmarks:
        m["l"] = [[str(n)[:24]] + q(p) for n, p in scene.landmarks[:4]]
    raw = json.dumps(m, separators=(",", ":"), ensure_ascii=False).encode()
    return encode("M", zlib.compress(raw, 9)[2:-4])  # raw deflate: the browser's DecompressionStream("deflate-raw")


# ---------- routes (GPX for offline map apps) ----------

def _simplify(pts, tol_m=4.0):
    """Douglas-Peucker in local metres: drops points within tol_m of the line through their neighbours."""
    from . import geo
    if len(pts) < 3:
        return list(pts)
    proj = geo.LocalProjection(pts[0])
    xy = [proj.to_xy(p) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        L = max(1e-9, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
        far, d = None, tol_m
        for i in range(a + 1, b):
            px, py = xy[i]
            dist = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L
            if dist > d:
                far, d = i, dist
        if far is not None:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [p for p, k in zip(pts, keep) if k]


def polyline(pts):
    """Google's encoded polyline (precision 5)."""
    out, last = [], (0, 0)
    for lat, lon in pts:
        cur = (round(lat * 1e5), round(lon * 1e5))
        for d in (cur[0] - last[0], cur[1] - last[1]):
            v = ~(d << 1) if d < 0 else d << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        last = cur
    return "".join(out)


def route(pts, name):
    """A walking route -> code parts the kit turns into a GPX file (and draws)."""
    raw = ("%s\n%s" % (str(name)[:60], polyline(_simplify(pts)))).encode()
    return encode("G", zlib.compress(raw, 9)[2:-4])


# ---------- text ----------

def words(text):
    """Text for a kit message: as it is in the basic SMS alphabet; otherwise a T code (deflated UTF-8), which keeps
    it and every code beside it out of UCS-2. -> (plain text, code parts)."""
    if all(ch in GSM7 for ch in text):
        return text, []
    return "", encode("T", zlib.compress(text.encode(), 9)[2:-4])


def read_text(data):
    return zlib.decompress(data, -15).decode()


def signed_text(message):
    """What a signature covers: the plain text outside the codes, then the text of every complete T code."""
    found = [read_text(c["data"]) for c in decode(message)["complete"] if c["kind"] == "T"]
    return " ".join(" ".join([plain(message)] + found).split())


# ---------- location (phone -> server) ----------

def read_location(data):
    try:
        d = json.loads(data)
        return {"lat": float(d["la"]), "lon": float(d["lo"]), "accuracy_m": d.get("ac"), "status": d.get("st", "ok"),
                "people": d.get("p"), "battery": d.get("b"), "time": d.get("t"), "note": d.get("n", "")}
    except Exception:
        return None


# ---------- phrasebook ----------

PACK_PATH = os.path.join(os.path.dirname(__file__), "..", "sim", "pack.json")


def pack():
    return json.load(open(PACK_PATH)) if os.path.exists(PACK_PATH) else {"entries": []}


def pack_match(task):
    """The phrasebook entry for a how-to task, if one fits (most of its words in common), else None."""
    words = lambda s: {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in {"the", "and", "how", "use"}}
    want = words(task)
    best = max(pack()["entries"], key=lambda e: len(want & words(e["task"])) / max(1, len(words(e["task"]))), default=None)
    if best and len(want & words(best["task"])) / max(1, len(words(best["task"]))) >= 0.6:
        return best
    return None


def phrase(entry):
    return encode("S", entry["id"].encode())


# ---------- signatures (Ed25519, RFC 8032; small pure-Python version, for signing a few alerts) ----------

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    return _P - x if x % 2 else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_xrecover(_BY), _BY, 1, _xrecover(_BY) * _BY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p, s = _add(p, p), s >> 1
    return q


def _enc_point(p):
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _key():
    if not os.path.exists(KEY_PATH):
        os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
        json.dump({"seed": os.urandom(32).hex()}, open(KEY_PATH, "w"))
    seed = bytes.fromhex(json.load(open(KEY_PATH))["seed"])
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a = (a & ((1 << 254) - 8)) | (1 << 254)
    return a, h[32:], _enc_point(_mul(a, _B))


def public_key():
    """The issuer's public key (32 bytes, base64) the kit checks signatures with."""
    return base64.b64encode(_key()[2]).decode()


def sign(message_text):
    """A signature code for a message's text (signed_text(): codes removed, T codes' text added, whitespace
    collapsed)."""
    a, prefix, pub = _key()
    m = signed_text(message_text).encode()
    r = int.from_bytes(hashlib.sha512(prefix + m).digest(), "little") % _L
    R = _enc_point(_mul(r, _B))
    k = int.from_bytes(hashlib.sha512(R + pub + m).digest(), "little") % _L
    s = (r + k * a) % _L
    return encode("A", R + s.to_bytes(32, "little"))
