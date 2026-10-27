"""Turn an aerial image into per-pixel terrain masks.

Backends:
  sam3  - Meta SAM 3 with text prompts ("road", "water", ...). This is the intended path for the hackathon.
          Needs `pip install transformers torch` and access to the gated facebook/sam3 weights.
  color - Crude RGB heuristic. No dependencies; lets the pipeline run end-to-end offline.

Every backend returns {class_name: HxW bool numpy array}.
"""
import numpy as np

# Class -> SAM 3 text prompt. Order matters when masks overlap: later classes win.
CLASSES = {
    "vegetation": "trees",
    "building": "building roof",
    "water": "water",
    "road": "road",
    "smoke": "smoke or fire",
}


def segment(img, backend="color"):
    if backend == "sam3":
        return _sam3(img)
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


_sam = None


def _sam3(img):
    # Untested against the real weights yet; follows the HF transformers SAM 3 API.
    global _sam
    import torch
    from transformers import Sam3Model, Sam3Processor

    if _sam is None:
        dev = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        _sam = (Sam3Processor.from_pretrained("facebook/sam3"), Sam3Model.from_pretrained("facebook/sam3").to(dev), dev)
    proc, model, dev = _sam
    img = img.convert("RGB")
    out = {}
    for cls, prompt in CLASSES.items():
        inputs = proc(images=img, text=prompt, return_tensors="pt").to(dev)
        with torch.no_grad():
            res = model(**inputs)
        inst = proc.post_process_instance_segmentation(
            res, threshold=0.4, mask_threshold=0.5, target_sizes=inputs["original_sizes"].tolist()
        )[0]
        m = np.zeros((img.height, img.width), bool)
        for mask in inst["masks"]:
            m |= mask.cpu().numpy().astype(bool)
        out[cls] = m
    return out
