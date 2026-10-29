"""The kit's codes (evac/codes.py), offline: no model, no network.

    .venv/bin/python -m unittest discover tests
"""
import base64
import json
import os
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evac import codes, store  # noqa: E402
from evac.scene import Scene  # noqa: E402


class Envelope(unittest.TestCase):
    def setUp(self):
        store.use(tempfile.mkdtemp())

    def test_round_trip_any_order(self):
        data = os.urandom(700)
        parts = codes.encode("P", data)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= codes.PART_CHARS for p in parts))
        got = codes.decode("hello\n" + "\n".join(reversed(parts)) + "\nbye")
        self.assertEqual(got["missing"], {})
        self.assertEqual([c["data"] for c in got["complete"]], [data])

    def test_basic_alphabet_only(self):
        msg = codes.text(codes.encode("M", os.urandom(400)))
        self.assertTrue(all(ch in codes.GSM7 for ch in msg))
        self.assertEqual(codes.sms_parts(msg), -(-len(msg) // 153))

    def test_missing_and_damaged_parts(self):
        parts = codes.encode("P", os.urandom(600))
        ident = codes.CODE.match(parts[0]).group(2)
        damaged = parts[1].replace(parts[1][20], "A" if parts[1][20] != "A" else "B", 1)
        got = codes.decode("\n".join([parts[0], damaged] + parts[3:]))
        self.assertEqual(got["complete"], [])
        self.assertEqual(sorted(got["missing"][ident]), [2, 3])

    def test_resend(self):
        parts = codes.encode("P", os.urandom(600))
        ident = codes.CODE.match(parts[0]).group(2)
        self.assertEqual(codes.resend("RESEND %s 2 3" % ident), parts[1:3])
        self.assertEqual(codes.resend("resend %s 99" % ident), [])
        self.assertIsNone(codes.resend("RESEND zzzz 1"))
        self.assertIsNone(codes.resend("no request here"))

    def test_plain_drops_codes(self):
        msg = "Go east now.\n" + codes.text(codes.encode("A", b"x" * 64)) + "\n  Stay clear."
        self.assertEqual(codes.plain(msg), "Go east now. Stay clear.")


class Payloads(unittest.TestCase):
    def setUp(self):
        store.use(tempfile.mkdtemp())

    def test_words_in_basic_alphabet_stay_plain(self):
        self.assertEqual(codes.words("Head E on 5 Av for 120 m."), ("Head E on 5 Av for 120 m.", []))

    def test_accented_words_are_packed(self):
        text = "Vérifier le cadran arrêté, robinet sans eau. Ouvrir le couvercle noir au tournevis. " * 3
        plain, parts = codes.words(text)
        self.assertEqual(plain, "")
        self.assertLess(codes.sms_parts(codes.text(parts)), codes.sms_parts(text))
        got = codes.decode(codes.text(parts))["complete"][0]
        self.assertEqual((got["kind"], codes.read_text(got["data"])), ("T", text))

    def test_polyline_matches_google_example(self):
        self.assertEqual(codes.polyline([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]), "_p~iF~ps|U_ulLnnqC_mqNvxq`@")

    def test_route_simplified_and_named(self):
        pts = [(38.7139 + i * 1e-5, -9.1406) for i in range(50)] + [(38.7144, -9.1406 + i * 1e-5) for i in range(50)]
        got = codes.decode(codes.text(codes.route(pts, "Castelo")))["complete"][0]
        name, line = zlib.decompress(got["data"], -15).decode().split("\n")
        self.assertEqual(name, "Castelo")
        self.assertLessEqual(len(line), 20)  # two straight legs: a handful of points, not 100

    def test_map_code(self):
        you = (40.7549, -73.9744)
        scene = Scene(you=you, half_m=300, zone=((40.7570, -73.9790), 150), hazard="fire",
                      roads=[{"name": "East 47th Street", "kind": "residential", "pts": [(40.7550, -73.9800), (40.7540, -73.9700)]}],
                      route=[you, (40.7545, -73.9730)])
        got = codes.decode(codes.text(codes.map_scene(scene)))["complete"][0]
        m = json.loads(zlib.decompress(got["data"], -15))
        self.assertEqual(m["k"], "fire")
        self.assertEqual(m["n"][0][0], "East 47th Street")
        self.assertEqual(m["r"][0], [0, 0])  # in metres from you


class Signature(unittest.TestCase):
    """RFC 8032 test 1: this secret key signing the empty message gives exactly this signature."""

    def setUp(self):
        self.saved = codes.KEY_PATH
        codes.KEY_PATH = os.path.join(tempfile.mkdtemp(), "key.json")
        with open(codes.KEY_PATH, "w") as f:
            json.dump({"seed": "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"}, f)
        store.use(tempfile.mkdtemp())

    def tearDown(self):
        codes.KEY_PATH = self.saved

    def test_rfc8032_vector(self):
        self.assertEqual(base64.b64decode(codes.public_key()).hex(),
                         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = codes.decode(codes.text(codes.sign("")))["complete"][0]["data"]
        self.assertEqual(sig.hex(), "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bac"
                                    "c61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")

    def test_signature_covers_packed_words(self):
        a = codes.decode(codes.text(codes.sign("Saia já para o Largo.")))["complete"][0]["data"]
        packed = codes.text(codes.words("Saia já para o Largo.")[1])
        self.assertEqual(codes.signed_text(packed), "Saia já para o Largo.")
        self.assertEqual(a, codes.decode(codes.text(codes.sign(packed)))["complete"][0]["data"])


if __name__ == "__main__":
    unittest.main()
