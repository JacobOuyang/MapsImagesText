"""The store (evac/store.py) and what is built on it: RESEND, ZOOM, trips, reports. Offline.

    .venv/bin/python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PIL import Image, ImageDraw  # noqa: E402

from evac import codes, router, store  # noqa: E402


def photo():
    """A 640 x 480 test picture: a dark square on light grey."""
    img = Image.new("RGB", (640, 480), (200, 200, 200))
    ImageDraw.Draw(img).rectangle((300, 200, 420, 300), fill=(30, 30, 30))
    return img


class Records(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        store.use(self.dir)

    def test_ids_are_short_unique_and_never_two_characters(self):
        ids = [store.new("code") for _ in range(50)]
        self.assertEqual(len(set(ids)), 50)
        self.assertEqual(ids[0], "100")
        self.assertTrue(all(len(i) >= 3 for i in ids))

    def test_update_merges_and_versions(self):
        r = store.new("report", meta={"status": "help"}, where=(40.75, -73.98), who="+15550100")
        self.assertEqual(store.update(r, meta={"people": 2}), 2)
        rec = store.get(r)
        self.assertEqual(rec["meta"], {"status": "help", "people": 2})
        self.assertEqual((rec["where"], rec["who"], rec["version"]), ((40.75, -73.98), "+15550100", 2))

    def test_survives_a_restart(self):
        r = store.new("photo", image=photo(), meta={"size": [128, 96]})
        t = store.new("trip", obj={"route": [(1, 2), (3, 4)]})
        store.use(self.dir)  # a new process opening the same files
        self.assertEqual(store.get(r)["meta"], {"size": [128, 96]})
        self.assertEqual(store.image(r).size, (640, 480))
        self.assertEqual(store.obj(t), {"route": [(1, 2), (3, 4)]})

    def test_find_near_and_by_kind(self):
        store.new("report", where=(40.7550, -73.9750))
        store.new("report", where=(34.05, -118.25))
        store.new("photo")
        near = store.find("report", near=(40.7549, -73.9744), radius_m=1000)
        self.assertEqual(len(near), 1)
        self.assertEqual(len(store.find("report")), 2)


class Kit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        store.use(self.dir)

    def test_picture_zoom_chain_after_restart(self):
        parts = codes.picture(photo(), parts=[("square", (300, 200, 420, 300))])
        pid = codes.CODE.match(parts[0]).group(2)
        self.assertEqual(store.get(pid)["kind"], "photo")
        key = [c for c in codes.decode(codes.text(parts))["complete"] if c["kind"] == "K"][0]
        self.assertIn(b'"square"', key["data"])
        store.use(self.dir)
        words, z = codes.zoom("ZOOM %s 1" % pid)
        self.assertIn("square (part 1)", words)
        zc = codes.decode(codes.text(z))["complete"][0]
        n = zc["data"][0]
        self.assertEqual(zc["data"][1:1 + n].decode(), pid)  # the parent's id, length-prefixed
        zid = zc["id"]
        self.assertEqual((store.get(zid)["kind"], store.get(zid)["parent"]), ("zoom", pid))
        words, zz = codes.zoom("ZOOM %s 10 10 20 20" % zid)  # a zoom of the zoom
        self.assertEqual(store.get(codes.CODE.match(zz[0]).group(2))["parent"], zid)

    def test_zoom_errors(self):
        pid = codes.CODE.match(codes.picture(photo())[0]).group(2)
        self.assertIn("no part 1", codes.zoom("ZOOM %s 1" % pid)[0])
        self.assertIn("no longer kept", codes.zoom("ZOOM zzz 1")[0])
        self.assertIsNone(codes.zoom("zoom in please"))

    def test_resend_after_restart(self):
        parts = codes.encode("M", os.urandom(500))
        ident = codes.CODE.match(parts[0]).group(2)
        store.use(self.dir)
        self.assertEqual(codes.resend("RESEND %s 1" % ident), parts[:1])

    def test_router_answers_resend_zoom_and_checkins(self):
        pid = codes.CODE.match(codes.picture(photo(), parts=[("square", (300, 200, 420, 300))])[0]).group(2)
        out = router.handle("ZOOM %s 1" % pid, (40.75, -73.98))
        self.assertTrue(out["text"].startswith("Closer look at square"))
        loc = codes.text(codes.encode("L", b'{"la":40.7531,"lo":-73.9822,"ac":12,"st":"help","p":2,"b":41,"n":"3rd floor"}'))
        out = router.handle(loc, (40.75, -73.98), who="+15550100")
        self.assertIn("needs help, 2 people", out["text"])
        rep = store.get(out["report"])
        self.assertEqual((rep["kind"], rep["who"], rep["meta"]["status"], rep["where"]), ("report", "+15550100", "help", (40.7531, -73.9822)))


if __name__ == "__main__":
    unittest.main()
