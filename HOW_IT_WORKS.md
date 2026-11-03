# How it works

The details behind [README.md](README.md): the loop, how a photo travels as text, real runs, the evidence and how it
was measured, and the WhatsApp channel.

## The loop: a question about a photo that stays on the phone

The phone sends a photo as a 128 px grey sketch. That is often enough, but sometimes the detail that decides the
answer is a few pixels, or it's a colour. So the server doesn't guess: it asks the phone for just that part
(`evac/closer.py`).

1. **The phone sends the question and the cheapest useful view:** the whole photo at 128 px, grey (about 10 texts).
2. **Muse Spark looks** (every view enlarged to 512 px) and either answers, saying how sure it is, or asks for **one
   closer look**: a tight box around the detail, drawn on any view so far (the whole photo, or an earlier close-up to
   zoom in further), whether it needs colour, at what detail, and what it wants to see there.
3. **The phone crops that box from its original**, in colour if asked, and sends it back by itself.
4. **SAM 3.1 outlines the thing** Muse named ("spider", "fire extinguisher") on the close-up, and Muse gets its exact
   place in the whole photo before deciding again.
5. **Again, with every view so far,** each labelled with where it sits in the photo, its colour and detail, how many
   times closer it is than the whole photo, what it cost in texts, and whether it already shows that part at the
   photo's full resolution (a crop is never smaller than the picture it is sent as, so zooming further would show
   nothing new). It ends when Muse answers, or when the phone has sent 160 texts or eight close-ups; then Muse must
   answer with what it has. The full photo never leaves the phone.

**Rules that made it work,** each added after a failure in the evals:

- **Answer only when sure.** While it can still look, Muse may not answer "can't tell" or "fairly sure". If it does,
  it is asked once more and told how many looks it has left. Before this, runs gave up a shelf too early or hedged
  after reading "EMPTY / RECHARGE".
- **Colour when colour helps find the thing.** In grey an amber bottle looks like any other; runs zoomed in grey onto
  the wrong bottle.
- **Detail, not only place.** A close-up normally comes back at 128 px; for small text or many small things (a
  timetable, a shelf of labels) Muse asks for high detail: 256 px, grey at WebP quality 20 instead of 5 (at 5 a 6
  blurred into an 8), about 3–4× the texts. On the medicine cabinet one high-detail close-up of the shelves now reads
  every label.
- **No repeats.** A box that overlaps a view Muse already has (IoU ≥ 0.6) at no more detail is refused, and Muse is
  told why. On a bus timetable runs had asked eight times for the same 128 px view of the whole table, which never got
  sharper.
- **Tight boxes.** A box much bigger than the detail costs texts and shows it no sharper.

**A real run** on an office corridor (`/closer`, `hard/extinguisher`, a 1600 px photo made with Muse Image), zooming in
three times, each close-up inside the last:

```
You (12 SMS)   Is the fire extinguisher in this corridor ready to use?  + the photo at 128 px, grey
Muse (2 SMS)   Not sure yet: I need a closer look at the fire extinguisher on the wall to see the gauge and pin and
               seal, in colour. Your kit crops it from your photo and sends it back.
               ZOOM v2 81 47 26 32 C
You (15 SMS)   the closer look, in colour (1.5 KB)            [SAM 3.1 outlines "fire extinguisher", 0.98]
Muse (2 SMS)   Not sure yet: I need a closer look at the extinguisher gauge and label to see the gauge needle
               position and label wording, in colour. ...      ZOOM v2 88 55 19 27 C
You (14 SMS)   the closer look, in colour (1.4 KB)            [0.98]
Muse (2 SMS)   Not sure yet: I need a closer look at the pressure gauge on top to see the needle position, in
               colour. ...                                     ZOOM v2 93 53 10 8 C
You (11 SMS)   the closer look, in colour (1.0 KB)            [0.92]
Muse (1 SMS)   No, it is not ready to use. It is marked EMPTY / RECHARGE.
               Do not use it - report it for recharge or replacement.
```

59 texts both ways, for a 476 KB photo.

**Where SAM 3.1 fits.** SAM outlines things by name, but it needs enough pixels. In the 128 px sketch a spider is
about 14 pixels, and no enlarging (4×, 8× sharpened, tiled) made SAM find it. Neither did asking Muse Image to restore
the picture, which erased the spider altogether. It found things from about 25 px up (`evals/closer/RESULTS.md`). So
Muse aims the first close-up from context ("the dark spot under the shelf"), and SAM works on the close-ups, where the
thing fills the frame: on the black widow it scored 0.82–0.89, its box within 1% of SAM's on the full original. A
match counts from confidence 0.5 and when it is at least 15% of the close-up's side.

## A photo as text

A code is `#<type><id>.<part>/<parts>:<base64>:<crc16>#` (`evac/codes.py`): at most 153 characters per part, in the
basic SMS alphabet, so a part fits one SMS and any text channel carries it. Parts can arrive in any order; a damaged or
missing part is named, and `RESEND <id> <part>` gets just that part again (the server keeps what it sent in its store).

```
#P7k.2/14:3DQwdsDX9WFdO8yf…:df2e#     type P (picture), id 7k, part 2 of 14, base64, CRC-16
```

- **`P`, the sketch:** a 128 px greyscale WebP at quality 5, contrast-stretched (`evac/smsimage.py`). A photo takes
  700 B–1.4 KB, 8–15 texts. The phone picks a two-character id for it.
- **`ZOOM <id> x y w h [C] [H]`, Muse's request:** a box in the sketch's pixels, whichever view Muse drew it on (even
  the deepest useful zoom is several of those pixels wide, so the format didn't need to change); `C` for colour (WebP
  quality 30), `H` for high detail (256 px).
- **`Z`, a close-up:** the parent's id, the box it covers in the parent's pixels (4 × uint16 in 1/64 px), then the WebP.
  `codes.answer_zoom` is what every phone runs: crop the original around the box with a little margin, square, never
  smaller than the size it is sent at, and encode it.

Sizes against sending a photo the normal way are in [evals/kit/WEBP_VS_NORMAL.md](evals/kit/WEBP_VS_NORMAL.md)
(`evals/kit/webp_vs_normal.py`): a 3–6 MB phone photo becomes an 8–15 text sketch, and one colour close-up 11–22 texts.

## The evidence: the same texts, three ways

`evals/kit/three_ways.py` (report in [evals/kit/THREE_WAYS.md](evals/kit/THREE_WAYS.md), shown by `/compare`). Muse
Spark answers one open question about one photo three ways, five times each:

- **The photo itself,** a JPEG of at most 2,048 px (phone originals are too big to send whole).
- **The whole photo as WebP squeezed into MuZip's median texts:** WebP's best shot, 128, 256 and 384 px at the highest
  quality that fits, colour and grey (192 and 512 px were never best), the candidate with the most right answers
  reported.
- **MuZip,** through the router with a simulated phone that keeps the original (`sim/phones.py`).

A separate text-only Muse call judges each answer against a reference (right, wrong, not sure), and an answer counts
only when it is right and Muse said it was sure.

**The photos.** Each hides the detail that decides the answer, and each passed a screen first: sure and right from the
full photo, never from a 3–5 KB WebP. Four were made with Muse Image at 1600 px (`sim/samples/hard/`, prompts in
`prompts.json`), four are real (`sim/samples/real/`, Wikimedia Commons, 2,600–4,000 px). Dropped by the screen:

- **The spiders:** from the full black widow Muse said "a fake plastic toy spider", and a 3 KB colour WebP shows its
  red mark.
- **Muse Image's snakes and brown recluse:** the band order and the violin mark weren't drawn clearly enough to be
  ground truth.
- **Two real photos whose detail is big** (street parking signs, a handwritten label on a box): a 5 KB WebP reads
  them, and MuZip answers them in one close-up. They stay in the loop's eval as controls.

| Photo | Question (answer) | The photo itself | WebP, same texts | MuZip |
|---|---|---|---|---|
| Medicine cabinet (generated) | I have a headache. Which of these can I take, and where? (the ibuprofen, bottom shelf) | 412 KB: 5/5 | 66 texts: 0/5 | 69 texts: **5/5** |
| Medicine cabinet (generated) | Anything here for allergies? Which, and where? (loratadine, middle shelf) | 412 KB: 5/5 | 102 texts: 0/5 | 103 texts: **5/5** |
| Leg after a hike (generated) | What's that on my leg? (a tick) | 581 KB: 0/5 (5 hedged) | 83 texts: 0/5 (5 hedged) | 85 texts: **5/5** |
| Office corridor (generated) | Can I use this fire extinguisher? (no: EMPTY / RECHARGE) | 476 KB: 5/5 | 52 texts: 0/5 | 53 texts: **5/5** |
| Bus stop timetable (real) | Saturday, 9 am: when's the next bus? (10:06) | 4.7 MB: 1/5 | 181 texts: 0/5 | 183 texts: 1/5 |
| Milk aisle (real) | How much is a gallon of whole milk? ($3.69) | 1.0 MB: 5/5 | 166 texts: 0/5 | 169 texts: 0/5 |
| Milk aisle (real) | Any almond milk? Which brand? (Almond Breeze) | 3.3 MB: 5/5 | 91 texts: 0/5 (5 hedged) | 93 texts: **5/5** |
| Old pharmacy (real) | Anything with cinnamon? What does its label say? (SIR. CINNAMOMI) | 5.8 MB: 5/5 | 86 texts: 0/5 | 87 texts: **4/5** |

MuZip 30/40, WebP 0/40, the photo itself 36/40. From WebP Muse made answers up: "take the aspirin", "there's
Benadryl", "Paracetamol 500 mg" (it was propranolol), "no almond milk visible". Before high detail and the no-repeat
rule, MuZip scored 17/20 on the generated photos and 7/20 on the real ones; after, 20/20 and 10/20.

**How a photo is normally sent.** An iPhone photo sent on WhatsApp arrived at the bridge as a 2160×3840 JPEG of 543 KB
and 811 KB (Twilio keeps the media; measured, not estimated). The report's own estimates (a 1,600 px JPEG for WhatsApp,
`sips` HEIC for the iPhone's file) are lower than that, so its "MuZip vs WhatsApp" column is conservative.

## The loop's own eval

`evals/loop/run.py` runs only MuZip on `evals/loop/dataset.json` (the eight questions plus the two easy controls),
three runs each, all at once, in about 80 s, and prints right-and-sure, hedged and wrong per question with the median
texts, close-ups and seconds, against `BASELINE.json`. Results are kept by a hash of the loop's code (`out/eval/loop/`),
so an unchanged loop isn't re-run; `--save` makes a run the baseline and writes `RESULTS.md`.

The baseline: generated 12/12, real 6/12, easy 6/6, each easy one in a single close-up (median 36 texts). A budget of
240 texts instead of 160 got the timetable to 2/3 but cost 45 more texts and didn't help the price tag, so it wasn't
kept.

## On WhatsApp

WhatsApp is the text channel (`evac/whatsapp.py`, `sim/whatsapp_server.py`): a person messages the Muse number
(Twilio's sandbox).

- **A photo with a question** runs the loop. Muse sees only the 128 px sketch, and the bridge plays the phone's part,
  cropping each close-up from the photo it received. The person sees every step and what it cost.
- **A message with codes** goes straight to the router: what MuZip on a Mac sends, so the photo never leaves the
  computer (below).
- **Any other message** gets a plain answer from Muse Spark.

The bridge answers Twilio at once (Twilio waits at most 15 s; Muse can take longer) and replies through Twilio's REST
API, split at 1,500 characters. It checks Twilio's signature against `PUBLIC_URL/whatsapp`, records every message in
and out (`wa_msg` records), and records the size of every photo WhatsApp delivers (`wa_media`).

A real run (no Twilio, replies collected; recorded before high detail existed):

```
You            [photo] Is this a black widow?
Muse           Sending your photo as a 128 px grey picture: 11 texts. Muse only sees that.
Muse           Not sure yet: I need a closer look at the dark spot under the shelf to see the shape and legs.
               (Your phone sends that part of the photo: 7 texts.)
Muse           Not sure yet: I need a closer look at the spider's round belly to see the colour of the hourglass
               marking, in colour.
               (Your phone sends that part of the photo in colour: 10 texts.)
Muse           Yes, this is a black widow. The red hourglass mark on its black belly is clearly visible.
               Do not touch it and keep people and pets away.
```

**MuZip on a Mac** (`sim/muzip.py`, page on port 8780) sends from your own WhatsApp account: drop a photo, ask, and it
sends the question with the sketch as codes, reads Muse's replies from the bridge's log, and answers every ZOOM by
cropping the photo on the Mac. WhatsApp Desktop is filled in with `whatsapp://send?…` and `out/MuZip.app`
(`sim/make_muzip_app.sh`) presses Return: a small helper that holds the Accessibility permission and takes requests
through files in `/tmp/muzip-<uid>/`, so it never touches ~/Documents (macOS's per-app Documents check stalled an
earlier version). A real exchange from a WhatsApp account took about 34 s for a spider photo with two close-ups.

To set it up (Twilio, the bridge, a tunnel, MuZip and the test phone), see [README.md](README.md#over-whatsapp).
