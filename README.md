# MuZip

**Agentic compression for photos.** Instead of shrinking the whole photo and hoping the detail survives, MuZip sends
a tiny sketch and lets the AI on the other end ask for exactly the parts it needs to answer your question. The photo
stays on your phone; what crosses the link is under a hundredth of it, as ordinary text messages.

Built on the Meta Model API: **Muse Spark** decides what to look at, **SAM 3.1** outlines it, **Muse Image** made
the test photos.

## How it works

1. **The phone sends the question and a 128 px grey sketch of the photo** (a WebP, about 10 texts).
2. **Muse Spark looks and decides.** If it is sure, it answers. If not, it asks for one close-up: a box on any view
   so far (the whole photo, or inside an earlier close-up), in colour or grey, at normal (128 px) or high (256 px)
   detail, and says what it wants to see there.
3. **The phone crops that part of its own full-size photo** and sends it back by itself, as text.
4. **SAM 3.1 outlines the thing on the close-up,** and Muse is told exactly where it sits in the whole photo.
5. **Again, until Muse is sure,** within a budget (160 texts or 8 close-ups). It may not answer "can't tell" or
   "fairly sure" while it can still look, and a box that repeats a view it already has is refused with the reason.
   To tell two or three small symbols apart (dashboard warning lights), it closes in on each one by itself, at the
   photo's own pixels.

Classical compression keeps what your eye would notice, for anyone, for any question. MuZip keeps what this question
needs. On a cabinet of thirty look-alike bottles ("which of these is for a headache?") one high-detail close-up of the
shelves is enough; on an office corridor ("can I use this fire extinguisher?") it zooms in three times, each close-up
inside the last, until it reads "EMPTY / RECHARGE".

## The evidence: the same texts, three ways

`evals/kit/three_ways.py` asks Muse Spark the same open question three ways, five times each: from **the photo
itself** (at most 2,048 px), from **the whole photo as WebP squeezed into the texts MuZip used** (its best shot: 128,
256 and 384 px at the highest quality that fits, colour and grey), and **through MuZip**. A separate Muse call judges
each answer against a reference, and an answer counts only when it is right *and* Muse says it is sure.

| Photo | Question (answer) | The photo itself | WebP, same texts | MuZip |
|---|---|---|---|---|
| Medicine cabinet (generated) | I have a headache. Which of these can I take, and where? (the ibuprofen, bottom shelf) | 412 KB: 5/5 | 66 texts: 0/5 | 69 texts: **5/5** |
| Medicine cabinet (generated) | Anything here for allergies? Which, and where? (loratadine, middle shelf) | 412 KB: 5/5 | 102 texts: 0/5 | 103 texts: **5/5** |
| Leg after a hike (generated) | What's that on my leg? (a tick) | 581 KB: 0/5 (5 hedged) | 83 texts: 0/5 (5 hedged) | 85 texts: **5/5** |
| Office corridor (generated) | Can I use this fire extinguisher? (no: EMPTY / RECHARGE) | 476 KB: 5/5 | 52 texts: 0/5 | 53 texts: **5/5** |
| Car at night (generated) | Two warning lights came on. Which ones, and what do they mean? (battery and check-engine) | 214 KB: 5/5 | 38 texts: 0/5 | 41 texts: **5/5** |
| Bus stop timetable (real) | Saturday, 9 am: when's the next bus? (10:06) | 4.7 MB: 1/5 | 181 texts: 0/5 | 183 texts: 1/5 |
| Milk aisle (real) | How much is a gallon of whole milk? ($3.69) | 1.0 MB: 5/5 | 166 texts: 0/5 | 169 texts: 0/5 |
| Milk aisle (real) | Any almond milk? Which brand? (Almond Breeze) | 3.3 MB: 5/5 | 91 texts: 0/5 (5 hedged) | 93 texts: **5/5** |
| Old pharmacy (real) | Anything with cinnamon? What does its label say? (SIR. CINNAMOMI) | 5.8 MB: 5/5 | 86 texts: 0/5 | 87 texts: **4/5** |

- **In all:** MuZip 35/45, WebP at the same size 0/45, the photo itself 36/45. From WebP Muse also made answers up
  ("take the aspirin", "there's Benadryl", "Paracetamol 500 mg").
- **Against how a photo is normally sent:** an iPhone photo sent on WhatsApp arrived as a 2160×3840 JPEG of 543–811 KB
  (measured through the bridge). MuZip's answers took 5–18 KB: 30–100× less.
- **The weak spot:** dense small text in a big real photo (a timetable, a price tag across an aisle). MuZip runs out of
  texts before it can read it.
- **When the detail is big** (street parking signs, a handwritten label), a 5 KB WebP is enough, and MuZip answers in
  one close-up (32–40 texts).

Five photos were made with Muse Image (`sim/samples/hard/`), four are real photos from Wikimedia Commons
(`sim/samples/real/`, [credits](sim/samples/real/CREDITS.md)). Each passed a screen first: Muse is sure and right from
the full photo, never from a 3–5 KB WebP. Nine photos are a demonstration, not a benchmark. Full results:
[evals/kit/THREE_WAYS.md](evals/kit/THREE_WAYS.md).

## How Meta's models are used

| Model | What it does here |
| --- | --- |
| **Muse Spark** | Looks at the sketch and every close-up so far and decides what it still needs to see: where, in colour or not, at what detail, and why. Answers once it is sure. Judges answers in the evals. |
| **SAM 3.1** | Outlines the thing Muse named on each close-up, so Muse knows exactly where it is in the whole photo. |
| **Muse Image** | Made the generated test photos (the prompts are in `sim/samples/hard/prompts.json`). |

What we learned about them:

- **SAM needs pixels.** It found nothing under about 20 px in the 128 px sketch, however it was enlarged or restored
  first. So Muse aims the first close-up from context, and SAM works on the close-ups
  ([evals/closer/RESULTS.md](evals/closer/RESULTS.md)).
- **Muse has to be told what it already has.** Without being told how much closer each view is, it asked eight times
  for the same view of a timetable. Without the rule against answering unsure, it gave up a shelf too early.

## Run it

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

Put a Meta Model API key in `.env` (git-ignored) as `LLAMA_API_KEY=...`. The offline tests need no key and no
network (codes, the store, the loop with scripted replies, the WhatsApp bridge):

```bash
.venv/bin/python -m unittest discover tests
```

The loop's own eval, after any change to it: 10 questions × 3 runs in parallel, about 80 s, compared with a saved
baseline:

```bash
.venv/bin/python evals/loop/run.py
```

The simulator shows the loop with a simulated phone (`/closer`) and the three-way comparison (`/compare`, after
`.venv/bin/python evals/kit/three_ways.py`):

```bash
.venv/bin/python sim/server.py
```

### Over WhatsApp

MuZip runs over WhatsApp through Twilio's sandbox: you send a photo with a question to the Muse number, and Muse's
close-up requests and answers come back as messages.

1. **Twilio:** put `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and your own number as `MY_WHATSAPP` in `.env`.
2. **The bridge:** `.venv/bin/python sim/whatsapp_server.py` (port 8770). It serves only Twilio's webhook.
3. **A tunnel** to port 8770, for example `cloudflared tunnel --url http://127.0.0.1:8770`. Put its https address in
   `.env` as `PUBLIC_URL` and restart the bridge.
4. **Twilio's sandbox settings:** set "When a message comes in" to `PUBLIC_URL/whatsapp` (POST).
5. **Each phone** sends the sandbox's join message once.

Then either send a photo from your phone (the bridge plays the phone's part and crops each close-up from the photo),
or use **MuZip on a Mac**, which keeps the photo on the computer: run `.venv/bin/python sim/muzip.py`, build the
helper with `bash sim/make_muzip_app.sh`, open `out/MuZip.app`, and drop a photo at http://127.0.0.1:8780. MuZip
sends the sketch and every close-up from your own WhatsApp as text codes; the app only presses Return in WhatsApp,
which needs Accessibility permission (re-add it after every rebuild). `sim/wa_phone.py` is a test phone that sends as
your number and prints the replies.

## Code

| Area | Files |
| --- | --- |
| The loop | `evac/closer.py` (what Muse sees, decides and asks for), `evac/sam.py` (SAM 3.1) |
| Codes | `evac/codes.py` (photos and close-ups as text: `P`, `Z`, `ZOOM … C H`, `RESEND`), `evac/smsimage.py` (the sketch) |
| Router and store | `evac/router.py` (codes first, then the loop, else a plain answer), `evac/store.py` (every photo, close-up and message, in SQLite) |
| WhatsApp | `evac/whatsapp.py`, `sim/whatsapp_server.py`, `sim/muzip.py`, `sim/make_muzip_app.sh`, `sim/wa_phone.py` |
| Simulator | `sim/server.py`, `sim/closer.html`, `sim/compare.html`, `sim/closer_demo.py`, `sim/phones.py` |
| Evals | `evals/kit/three_ways.py` (three ways), `evals/loop/` (the loop's eval), `evals/kit/webp_vs_normal.py` (sizes), `evals/closer/` (SAM on small pictures) |

[HOW_IT_WORKS.md](HOW_IT_WORKS.md) has the details with real runs; `CLAUDE.md` describes how the pieces connect.

**The demo (this branch):** `.venv/bin/python sim/server.py`, then http://127.0.0.1:8765/demo: four scenes (three
ways, look closer, a simulated WhatsApp group with Muse in it, a drone), live with the models. See
[demo/README.md](demo/README.md) and the voice-over in [demo/SCRIPT.md](demo/SCRIPT.md).

**Branches:** `main` is MuZip. `demo` adds the demo environment (a simulated WhatsApp group with Muse in it, the
video's stage and script). `full-prototype` keeps everything this started as: text maps for emergency alerts, walking
directions, weather radar, photo drawings, an offline kit page and a neighbourhood hub.

## Limits

- **Dense small text in big photos** is the weak spot (above).
- **WhatsApp through Twilio's sandbox** is the live channel; plain SMS works the same way but wasn't set up.
- **Muse aims the first close-up from context alone,** because SAM can't find small things in the sketch.
- **The test set is small:** nine questions in the comparison, twelve in the loop's eval, half on generated photos.
