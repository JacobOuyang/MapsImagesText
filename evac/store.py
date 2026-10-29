"""One place for everything the server sends or receives, so a later message can refer back to it.

Every thing is a record with a short id: a photo, a zoom on it, a walking trip, a message's codes, a check-in. "RESEND
7k3 2", "ZOOM 7k3 2" and a GPS update on a trip are all "fetch record 7k3 and do something to it". Records live in a
SQLite file (cache/store/store.db) with their originals beside it (cache/store/files/), so they survive a restart.

    rec = store.new("photo", image=img, meta={"size": [128, 96]})
    store.get(rec)          -> {"id", "kind", "version", "parent", "box", "who", "where", "meta", "file", "sent", ...}
    store.image(rec)        -> the original, as a PIL image
    store.update(rec, meta={...}, sent=[...], obj=trip)     (bumps the version)
    store.find("report", near=(lat, lon), radius_m=2000)

Ids are a counter in base 36 that starts at "100", so the server's ids are 3+ characters and never collide with the
2-character ids a phone gives the codes it makes. Live Python objects (a Trip with its routing grids) are pickled; only
this server writes them, so loading them back is safe.
"""
import json
import os
import pickle
import sqlite3
import threading
import time

ROOT = os.path.join(os.path.dirname(__file__), "..", "cache", "store")
FIRST = 36 * 36  # "100"
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"

_lock = threading.RLock()
_db = None
_dir = ROOT
_live = {}  # id -> the live object this process made or loaded (pickling a trip on every read would be slow)


def use(directory):
    """Keep records in another directory (tests)."""
    global _db, _dir
    with _lock:
        if _db is not None:
            _db.close()
        _db, _dir = None, directory
        _live.clear()


def _conn():
    global _db
    if _db is None:
        os.makedirs(os.path.join(_dir, "files"), exist_ok=True)
        _db = sqlite3.connect(os.path.join(_dir, "store.db"), check_same_thread=False)
        _db.execute("""CREATE TABLE IF NOT EXISTS records (
            n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, kind TEXT, version INTEGER, parent TEXT, box TEXT,
            who TEXT, lat REAL, lon REAL, created REAL, updated REAL, meta TEXT, file TEXT, sent TEXT, obj BLOB)""")
        _db.execute("CREATE INDEX IF NOT EXISTS by_kind ON records (kind, created)")
    return _db


def _ident(n):
    n += FIRST - 1
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = DIGITS[r] + out
    return out


def new(kind, *, meta=None, image=None, file=None, obj=None, parent=None, box=None, who=None, where=None):
    """A new record -> its id. `image` (PIL) is saved as its original; `file` shares another record's original."""
    with _lock:
        db, now = _conn(), time.time()
        cur = db.execute("INSERT INTO records (kind, version, parent, box, who, lat, lon, created, updated, meta, file) "
                         "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (kind, parent, json.dumps(box) if box is not None else None, who,
                          where[0] if where else None, where[1] if where else None, now, now,
                          json.dumps(meta or {}, ensure_ascii=False), file))
        ident = _ident(cur.lastrowid)
        db.execute("UPDATE records SET id = ? WHERE n = ?", (ident, cur.lastrowid))
        if image is not None:
            name = ident + ".jpg"
            image.convert("RGB").save(os.path.join(_dir, "files", name), "JPEG", quality=92)
            db.execute("UPDATE records SET file = ? WHERE id = ?", (name, ident))
        if obj is not None:
            _put_obj(ident, obj)
        db.commit()
        return ident


def _put_obj(ident, obj):
    _live[ident] = obj
    try:
        blob = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # something that can't be pickled stays live in this process only
        blob = None
    _conn().execute("UPDATE records SET obj = ? WHERE id = ?", (blob, ident))


COLS = "id, kind, version, parent, box, who, lat, lon, created, updated, meta, file, sent"


def _row(r):
    if r is None:
        return None
    ident, kind, version, parent, box, who, lat, lon, created, updated, meta, file, sent = r
    return {"id": ident, "kind": kind, "version": version, "parent": parent, "box": json.loads(box) if box else None,
            "who": who, "where": (lat, lon) if lat is not None else None, "created": created, "updated": updated,
            "meta": json.loads(meta or "{}"), "file": file, "sent": json.loads(sent) if sent else None}


def get(ident):
    """The record, or None."""
    if not ident:
        return None
    with _lock:
        return _row(_conn().execute("SELECT %s FROM records WHERE id = ?" % COLS, (str(ident).lower(),)).fetchone())


def update(ident, *, meta=None, sent=None, obj=None, where=None, bump=True):
    """Change a record: `meta` is merged into its meta. The version goes up by one (bump=False: filling in a record
    just made, such as the codes it sent)."""
    with _lock:
        rec = get(ident)
        if rec is None:
            raise KeyError(ident)
        db = _conn()
        if meta is not None:
            db.execute("UPDATE records SET meta = ? WHERE id = ?", (json.dumps(dict(rec["meta"], **meta), ensure_ascii=False), rec["id"]))
        if sent is not None:
            db.execute("UPDATE records SET sent = ? WHERE id = ?", (json.dumps(sent), rec["id"]))
        if where is not None:
            db.execute("UPDATE records SET lat = ?, lon = ? WHERE id = ?", (where[0], where[1], rec["id"]))
        if obj is not None:
            _put_obj(rec["id"], obj)
        db.execute("UPDATE records SET version = version + ?, updated = ? WHERE id = ?", (int(bump), time.time(), rec["id"]))
        db.commit()
        return rec["version"] + int(bump)


def obj(ident):
    """The live object kept with a record (a Trip), or None."""
    ident = str(ident).lower()
    with _lock:
        if ident not in _live:
            r = _conn().execute("SELECT obj FROM records WHERE id = ?", (ident,)).fetchone()
            if not r or r[0] is None:
                return None
            _live[ident] = pickle.loads(r[0])
        return _live[ident]


def path(ident):
    """The file of a record's original, or None."""
    rec = get(ident)
    return os.path.join(_dir, "files", rec["file"]) if rec and rec["file"] else None


def image(ident):
    """A record's original as a PIL image, or None."""
    from PIL import Image
    p = path(ident)
    return Image.open(p).convert("RGB") if p and os.path.exists(p) else None


def find(kind=None, near=None, radius_m=None, since=None, limit=500):
    """Records, newest first: of one kind, within radius_m of `near`, created after `since` (a timestamp)."""
    from . import geo
    sql, args = "SELECT %s FROM records WHERE 1" % COLS, []
    if kind:
        sql, args = sql + " AND kind = ?", args + [kind]
    if since:
        sql, args = sql + " AND created >= ?", args + [since]
    with _lock:
        rows = [_row(r) for r in _conn().execute(sql + " ORDER BY n DESC LIMIT ?", args + [limit]).fetchall()]
    if near and radius_m:
        rows = [r for r in rows if r["where"] and geo.haversine_m(near, r["where"]) <= radius_m]
    return rows
