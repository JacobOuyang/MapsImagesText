"""The look-closer loop (evac/closer.py) through the router, with Muse replaced by scripted replies. Offline.

    .venv/bin/python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sim"))
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from evac import closer, codes, llm, router, store  # noqa: E402
from phones import Phone  # noqa: E402

HERE = (40.6776, -74.0086)


def spider():
    """A 1024 px photo: grey wall, and a small black blob with a red mark at (700, 200)."""
    img = Image.new("RGB", (1024, 1024), (150, 140, 120))
    d = ImageDraw.Draw(img)
    d.ellipse((680, 180, 720, 230), fill=(10, 10, 10))
    d.polygon([(695, 200), (705, 200), (700, 215)], fill=(220, 20, 20))
    return img


class Loop(unittest.TestCase):
    def setUp(self):
        store.use(tempfile.mkdtemp())
        self.saved = (llm.available, llm.chat_json)
        llm.available = lambda: True
        self.seen = []  # what the model was shown each round: how many pictures

    def tearDown(self):
        llm.available, llm.chat_json = self.saved

    def script(self, *replies):
        replies = list(replies)

        def chat_json(messages, **kw):
            self.seen.append(sum(1 for part in messages[0]["content"] if part.get("type") == "image_url"))
            return replies.pop(0)
        llm.chat_json = chat_json

    def run_phone(self, question="Is this a black widow?"):
        texts = []

        def send(who, text):
            out = router.handle(text, HERE, who=who)
            texts.append(out["text"])
            phone.got(out["text"])
            return out
        phone = Phone("+15550140", send, delay=0)
        phone.text(question + "\n" + codes.text(phone.picture(spider())))
        return texts

    def test_asks_for_colour_then_answers(self):
        self.script({"answer": "", "sure": "no", "need": {"box": [0.66, 0.17, 0.71, 0.23], "colour": True,
                                                         "look_at": "the spider", "to_see": "the mark under its body"}},
                    {"answer": "Yes, a black widow: red hourglass.", "sure": "yes", "need": None, "safety": "Don't touch it."})
        texts = self.run_phone()
        self.assertEqual(len(texts), 2)
        self.assertIn("I need a closer look at the spider to see the mark under its body, in colour", texts[0])
        m = codes.ZOOM.search(texts[0])
        self.assertTrue(m.group(3))  # " C": in colour
        self.assertEqual(texts[1], "Yes, a black widow: red hourglass.\nDon't touch it.")
        self.assertEqual(self.seen, [1, 2])  # round 1: the 128 px photo; round 2: plus the close-up
        inq = store.find("inquiry")[0]
        self.assertEqual(inq["meta"]["status"], "answered")
        view = inq["meta"]["views"][0]
        zoom = store.get(view["zoom"])
        self.assertEqual((zoom["parent"], zoom["meta"]["colour"]), (inq["id"], True))
        # The close-up was cut from the 1024 px original around the box asked for: the red mark is in it.
        px = np.asarray(store.image(view["zoom"]).convert("RGB")).astype(int)
        self.assertTrue(((px[..., 0] > 150) & (px[..., 1] < 90) & (px[..., 2] < 90)).any())

    def test_budget_forces_an_answer(self):
        ask = {"answer": "", "sure": "no", "need": {"box": [0.6, 0.1, 0.8, 0.3], "colour": False, "look_at": "the spider"}}
        self.script(*[ask] * closer.MAX_VIEWS, dict(ask))  # it would keep asking
        texts = self.run_phone()
        self.assertEqual(len(texts), closer.MAX_VIEWS + 1)
        self.assertNotIn("ZOOM", texts[-1])
        self.assertIn("can't tell", texts[-1])
        self.assertEqual(self.seen[-1], closer.MAX_VIEWS + 1)

    def test_sam_outlines_the_thing_on_the_close_up(self):
        prompts = []
        saved = (closer.sam.available, closer.sam.segment)
        closer.sam.available = lambda: True
        closer.sam.segment = lambda img, phrase: [{"box": (40, 30, 90, 100), "score": 0.88, "mask": None}] if phrase == "spider" else []
        try:
            self.script({"answer": "", "sure": "no", "need": {"box": [0.6, 0.1, 0.8, 0.3], "colour": True, "look_at": "the dark spot",
                                                             "object": "spider"}},
                        {"answer": "Yes.", "sure": "yes", "need": None})
            inner = llm.chat_json
            llm.chat_json = lambda messages, **kw: (prompts.append(messages[0]["content"][0]["text"]), inner(messages))[1]
            self.run_phone()
        finally:
            closer.sam.available, closer.sam.segment = saved
        view = store.find("inquiry")[0]["meta"]["views"][0]
        self.assertEqual((view["sam"]["score"], view["sam"]["phrase"]), (0.88, "spider"))
        self.assertIn("SAM 3.1) outlines a spider", prompts[1])  # Muse's second look knows where SAM found it
        l, t, r, b = view["sam"]["in_photo"]  # inside the close-up's place in the photo
        x, y, w, h = view["got_box"]
        self.assertTrue(x / 128 <= l < r <= (x + w) / 128 and y / 128 <= t < b <= (y + h) / 128)

    def test_a_zoom_nobody_asked_for_is_not_a_question(self):
        self.assertIsNone(closer.receive("+15550140", "zz", [0, 0, 10, 10], b""))


if __name__ == "__main__":
    unittest.main()
