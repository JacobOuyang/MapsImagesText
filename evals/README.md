# Photo drawing evals

How good is the high-detail (tonal) photo drawing, and how much of a photo can be rebuilt from text drawings
alone? `photo_eval.py` runs every simulator sample photo through the real pipeline and scores the result;
`report.py` turns the scores into `out/eval/report.html` and `out/eval/summary.json`.

```bash
.venv/bin/python evals/photo_eval.py                    # all samples, 3 at a time (~30 min, ~2.5 min per photo)
.venv/bin/python evals/photo_eval.py --only Dog,Teapot  # a few
.venv/bin/python evals/photo_eval.py --no-generate      # skip Muse Image
.venv/bin/python evals/photo_eval.py --report           # rebuild the report from saved results
```

Results per photo are cached in `out/eval/<photo>/` (delete a folder to redo it). Model calls: Muse Spark
(descriptions, blind reading, judging), SAM 3.1 (segmentation; responses cached in `cache/sam/`), Muse Image
(recomposition).

## Part 1: the drawing, against the photo

Each metric is computed at the drawing's own grid: 58 columns, rows as tall as WhatsApp's monospace cells.

| Metric | How | Why |
|---|---|---|
| Tone r, tone SSIM | Every glyph's ink is measured in Menlo (share of the cell it darkens); the drawing becomes a grey image and is compared with the photo's lightness in the same cells | Does dark in the photo read as dark in the drawing? |
| Edge F1 | Photo edges: the 15% of cells where lightness changes most. Drawing edges: cells where ink changes by more than 0.12 or an outline glyph is drawn. One cell of tolerance | Are the shapes' borders in the right places? |
| Subject IoU | The drawing's subject cells against SAM's subject mask on the same grid | Does the subject keep its shape at this size? |
| Subject contrast | Ink of the subject cells minus ink of the background within 3 cells of it | Does the subject stand out? |
| Background ink | Mean ink outside the subject | Is the background light? |
| Muse reads it | Muse sees only the rendered drawing (no caption, no key) and names the subject and the scene; a second Muse call judges that against Muse's description of the photo: subject match yes/no, scene 0-10 | Can someone tell what the photo was? |
| Bytes | UTF-8 and gzip | What it costs to send |

Three drawings are scored: **low** (the outline that `photo.run` sends first), **high as sent** (tonal, the
subject emphasised and the background light) and **high faithful** (the same segmentation, one tone scale for
everything, the way it was drawn before emphasis).

## Part 2: recomposition

The question: if the receiver only ever gets text drawings, how much of the photo can be put back together, and
how does that grow with the zooms they ask for?

**Protocol.** Every drawing sets its own tone scale (its lightest half is blank), so the same `░` stands for a
different grey in different zooms. Each drawing therefore carries a one-line tone legend, `tones ·82 ░64 ▒41 ▓20`
(percent lightness of the photo's cells drawn with each glyph), about 40 bytes, counted in its size. A zoom says
which box of the photo it shows.

**D0, no model.** Decode every drawing with its legend (outline cells take the mean of their neighbours), paste
zooms over their boxes, and compare with the photo at 232 px wide (4 × 58 cells): SSIM and mean absolute error.

Zoom strategies:
- **base**: the 58-column drawing only;
- **parts**: plus `ZOOM n` on the first three numbered parts (what the app offers);
- **tiles2 / tiles4**: plus a full 2×2 tiling, then also a 4×4 tiling (every zoom you could ask for; drawn from
  brightness alone, no model calls);
- **greedy**: the server, which has the photo, sends next whichever zoom (parts or tiles) removes the most squared
  error per byte, until nothing helps. This gives the quality-against-bytes curve.

Reference points:
- **flat grey**: the photo's mean lightness everywhere (0%);
- **ceilings**: the photo itself averaged into a 58-, 116- or 232-column grid, with any grey or snapped to the
  5 shading glyphs' ink; "recovered" is the share of the distance from flat grey to the 232-column ceiling.

**D1, Muse Image.** Recreate the photo with `muse-image-1.0` from:
- the caption only (no drawing): how much words alone carry;
- the drawing only (`/images/edits`): how much the drawing carries;
- the drawing and its key;
- the D0 rebuild from all the zooms, and the key.

Each recreation is scored on:
- **layout:** the correlation of 16-column grey thumbnails with the photo, against the chance level between
  unrelated photos;
- **the subject's position:** SAM's box for the subject in both images (IoU), and whether SAM finds it at all;
- **a Muse judge:** shown the photo and the recreation, scoring 0-10 for subject, layout and overall.

## Results

See `RESULTS.md` (written from the last full run) and `out/eval/report.html` (with every photo).
