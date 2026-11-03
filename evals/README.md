# Evals

| Script | Question | Results |
| --- | --- | --- |
| `loop/run.py` | Does a change to the loop help? MuZip alone on `loop/dataset.json` (10 questions × 3 runs, all at once, ~80 s), against a saved baseline. Run it after every change to `evac/closer.py` or the ZOOM code. | printed; `loop/BASELINE.json` and `loop/RESULTS.md` with `--save` |
| `kit/three_ways.py` | Does MuZip beat classical compression at the same size? One open question per photo answered from the photo, from WebP squeezed into MuZip's texts, and through MuZip, 5 runs each, judged against a reference. | `kit/THREE_WAYS.md`; `out/eval/three_ways/` (shown by `/compare`) |
| `kit/webp_vs_normal.py` | How big is the sketch and a close-up against the photo sent normally? Encoding only, no model calls. | `kit/WEBP_VS_NORMAL.md` |
| `closer/sam_small.py` | Can SAM 3.1 find small things in the 128 px sketch if it's enlarged or restored first? (No.) | `closer/RESULTS.md` |

An answer counts as right only when a separate Muse Spark call judges it the same as the reference **and** Muse said
it was sure. The photos are in `sim/samples/hard/` (made with Muse Image) and `sim/samples/real/` (Wikimedia Commons,
`CREDITS.md`). The evals of the earlier prototype (text drawings, emergency pictures) are on the `full-prototype` branch.
