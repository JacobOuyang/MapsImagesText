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
        p = parts[1]
        damaged = p[:20] + ("A" if p[20] != "A" else "B") + p[21:]  # one character of the chunk (the header is shorter)
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

    def test_verify(self):
        sig = codes.decode(codes.text(codes.sign("")))["complete"][0]["data"]
        self.assertTrue(codes.verify("", sig))
        self.assertFalse(codes.verify("changed", sig))
        self.assertFalse(codes.verify("", sig[:-1] + bytes([sig[-1] ^ 1])))
        msg = "Go to Coffey Park.\n" + codes.text(codes.sign("Go to Coffey Park."))
        a = codes.decode(msg)["complete"][0]["data"]
        self.assertTrue(codes.verify(msg, a))
        self.assertFalse(codes.verify(msg.replace("Coffey Park", "the Navy Yard"), a))

    def test_signature_covers_packed_words(self):
        a = codes.decode(codes.text(codes.sign("Saia já para o Largo.")))["complete"][0]["data"]
        packed = codes.text(codes.words("Saia já para o Largo.")[1])
        self.assertEqual(codes.signed_text(packed), "Saia já para o Largo.")
        self.assertEqual(a, codes.decode(codes.text(codes.sign(packed)))["complete"][0]["data"])


if __name__ == "__main__":
    unittest.main()
