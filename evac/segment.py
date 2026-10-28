"""Turn an aerial image into per-pixel terrain masks.

Backends:
  sam   - SAM 3.1 on the Meta Model API (evac/sam.py) for water, the colour heuristic for the rest. Tried on
          aerial tiles, SAM outlined the ocean cleanly but found almost no trees or buildings (45 of Manhattan's
          buildings covering 16% of a 1 km view; one in Lisbon; nothing for "forest" or "town" over the Palisades),
          while the colour heuristic's water includes blue rooftops and dark shadows. One call, ~1-2 s.
          Needs a model key and Meta's mask decoder (meta-sam-parser, in the project's .venv).
  color - Crude RGB heuristic. No dependencies.
  auto  - sam when it is available, else color (and color if the SAM call fails).

Every backend returns {class_name: HxW bool numpy array}. Roads come from OpenStreetMap, not from the image.
"""
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import sam

# Class -> SAM 3.1 text prompt for an aerial photo: only the classes SAM finds reliably from above.
CLASSES = {"water": "water"}


def segment(img, backend="auto"):
    if backend in ("auto", "sam") and sam.available() and sam.masks_available():
        try:
            return _sam(img)
        except Exception:
            if backend == "sam":
                raise
    return _color(img)


def _color(img):
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    bright = (r + g + b) / 3
    sat = a.max(axis=2) - a.min(axis=2)
    return {
        "water": (b > r + 10) & (b > g) & (bright < 150),
        "vegetation": (g > r + 5) & (g >= b) & (bright < 140),
        # Pale low-saturation pixels are mostly pavement / roofs; buildings are told apart by OSM roads later.
        "building": (sat < 25) & (bright > 150),
        "road": np.zeros(r.shape, bool),
        "smoke": np.zeros(r.shape, bool),
    }


def _sam(img):
    """The colour heuristic, with SAM's masks for the classes in CLASSES."""
    img = img.convert("RGB")

    def one(prompt):
        m = np.zeros((img.height, img.width), bool)
        for rec in sam.segment(img, prompt):
            if rec["mask"] is not None:
                m |= rec["mask"]
        return m

    with ThreadPoolExecutor(len(CLASSES)) as pool:
        found = dict(zip(CLASSES, pool.map(one, CLASSES.values())))
    return dict(_color(img), **found)
