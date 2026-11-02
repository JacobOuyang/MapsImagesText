# MapsImagesText

**Pictures in a text message.** When a storm or a fire takes down mobile data, phones can still send and receive
SMS, and satellite texting on newer phones does the same with no towers at all. Those channels carry words, not
photos or maps. This project carries the pictures as well: a photo you can zoom into, a map with the way out, a
signed alert, a check-in with your location. Everything travels as ordinary text messages.

Two pieces make it work:

- **The kit**, one web page (62 KB, 28 KB gzipped) that a person saves on their phone before they need it. It works
  with no connection. It reads the short codes inside a text message and draws what they carry: a photo, a map, a
  route, a check-in, a signature check. It also makes those codes, so the phone can send a photo or its location by
  plain SMS.
- **A server with a good connection** that runs Meta's models through the Meta Model API. It looks at what phones
  send, decides what to answer and what to ask for, and packs the reply into as few texts as possible.

## Three things it does

### 1. Ask about a photo that never leaves the phone

*"Is this a black widow?"* The kit sends the photo as about ten texts: 128 pixels, in grey. **Muse Spark** sees only
that tiny picture. When it can't tell, it doesn't guess. It asks for a closer look at one part ("the dark shape
under the shelf, in colour"), and the kit crops that part from the phone's own full-size photo and sends it back as
text. **SAM 3.1** outlines the spider in each close-up, and Muse is told where that is in the whole photo. After two
close-ups the red hourglass settles it.

![Look closer: the phone's thread, the crops on the original, and what Muse saw](docs/closer_black_widow.jpg)

In a real run, the whole exchange was 43 texts (6 KB) against a 254 KB photo. The same loop ran from a real WhatsApp
account in 34 s. Muse may ask for at most three close-ups before it has to answer.

### 2. One signed alert for everyone, with a map

A neighbourhood hub (one laptop with the only working connection) re-sends the city's flood warning to every phone
as plain SMS. **Muse Spark** reads the alert and copies its numbers and street names exactly as written. Code then
draws the danger zone, routes each person's way out and signs the message. In the kit the codes become a map with
the zone, closures and the person's position. The signature check shows **✔ Genuine**. A forged copy that changes
where to go fails it and shows **✘ NOT genuine**.

A Manhattan fire alert with its map is 6 SMS (840 bytes) through the kit. The smallest picture map of it is 16.6 KB.

### 3. Check-ins and photo reports to the hub

One tap in the kit sends status, number of people, battery and GPS in a single text, and a photo can go with it.
The hub puts every check-in on a map. **Muse Spark** suggests who to help first and rates each photo report from the
128 px picture (urgency, hazard, advice, where to look). **SAM 3.1** confirms the part worth a closer look. The hub
then asks *that phone* for a zoom, and the phone's kit crops its own original. Marking a hazard (a power line on the
street) re-routes every volunteer and warns the neighbours nearby. Anyone who writes in another language gets
answers in it, packed so that accents don't double the SMS count.

![The hub during the Red Hook drill](docs/hub_dashboard.jpg)

In the full drill (42 phones, 39 check-ins, two photo reports, a close-up, warnings and two volunteer walks), the
whole night came to 187 texts, 69.7 KB. That is under 3% of one phone photo.

## How much data it saves

The kit sends a photo as a 128 px grey WebP, packed into SMS codes. Below, that is set against sending the same photo
the normal way: the file itself, or a 400 px WebP. These are real encodes of the demo's photos (the first four) and
six test photos (`evals/kit/webp_vs_normal.py`). One SMS carries 140 bytes on the air.

| Photo | Sent normally (the file) | Small picture (400 px WebP) | Kit: 128 px grey WebP | As SMS | Saving vs the file | + one colour close-up |
|---|---|---|---|---|---|---|
| Black widow | 253.9 KB (1024×1024) | 20.0 KB | 1.0 KB | 10 SMS = 1.4 KB | 181× | 1.4 KB = 14 SMS |
| False widow | 239.8 KB (1024×1024) | 19.6 KB | 1.1 KB | 11 SMS = 1.5 KB | 156× | 1.6 KB = 16 SMS |
| Downed power line | 336.0 KB (1024×1024) | 25.3 KB | 1.4 KB | 14 SMS = 2.0 KB | 171× | 2.8 KB = 28 SMS |
| Flooded corner | 248.4 KB (1024×1024) | 17.4 KB | 800 B | 8 SMS = 1.1 KB | 222× | 1.9 KB = 19 SMS |
| Apple | 37.8 KB (640×640) | 4.5 KB | 322 B | 4 SMS = 560 B | 67× | 1.0 KB = 11 SMS |
| Bicycle | 114.5 KB (640×640) | 25.3 KB | 1.4 KB | 14 SMS = 2.0 KB | 58× | 2.8 KB = 29 SMS |
| Dog | 88.6 KB (640×640) | 17.8 KB | 1.1 KB | 12 SMS = 1.7 KB | 53× | 1.3 KB = 14 SMS |
| Mug | 18.3 KB (640×640) | 1.7 KB | 284 B | 3 SMS = 420 B | 43× | 272 B = 3 SMS |
| Sneaker | 31.8 KB (640×640) | 3.3 KB | 386 B | 4 SMS = 560 B | 57× | 452 B = 5 SMS |
| Teapot | 76.1 KB (640×640) | 14.9 KB | 1.2 KB | 13 SMS = 1.8 KB | 42× | 956 B = 10 SMS |

When the 128 px picture isn't enough, Muse asks for a close-up of one part instead of the whole photo. The last
column is one colour close-up of a quarter of the picture. The whole black-widow exchange (the photo, two close-ups
and three replies) was 43 SMS, 6 KB, against 254 KB for the file. A real phone photo is 2–4 MB, about ten times these
samples, so the saving against a real photo is larger.

## How Meta's models are used

| Model | What it does here |
| --- | --- |
| **Muse Spark** | Looks at a 128 px photo and decides what it still needs to see, as a box, colour or grey, and why. Answers once it is sure. Reads alerts into structured data. Rates photo reports. Suggests who to help first. Translates replies. |
| **SAM 3.1** | Outlines the thing Muse named on each close-up. Checks the hub's photo reports, where its box is used only if it scores ≥ 0.6 and agrees with Muse's box. |
| **Muse Image** | Made the drill's photos: the downed line and the flooded corner. |

What we found about the models along the way:

- **SAM needs pixels.** It found nothing under about 20 px in the 128 px picture, however the picture was enlarged
  or restored first. So it runs on the close-ups, where the subject fills the frame, and Muse aims the first close-up
  from context ([evals/closer/RESULTS.md](evals/closer/RESULTS.md)).
- **The model copies, code computes.** When Muse converted units itself, the zone's radius changed from run to run.
  Now it copies numbers as written and code converts them.

## How the kit works

- **A code inside a text:** `#<type><id>.<part>/<parts>:<base64>:<crc16>#`, at most 153 characters per part, in the
  basic SMS alphabet. Parts can arrive in any order. A damaged or missing part is named, and `RESEND id n` asks for
  just that part again.
- **Types:**
  - `P`: a picture (greyscale WebP at quality 5, or a 1-bit PNG for line drawings).
  - `Z`: a close-up, with the box it covers in its parent picture, so close-ups can chain.
  - `M`: a map.
  - `G`: a route, which opens as a GPX file in an offline map app.
  - `T`: compressed words, used when text would otherwise be sent as UCS-2.
  - `L`: a check-in.
  - `A`: an Ed25519 signature over the words.
- **Zoom:** the phone keeps the originals. A `ZOOM id x y w h [C]` request (C for colour) makes the kit crop that
  part of the full-size photo and reply with a `Z` code.
- **Offline:** the kit is one self-contained file with the issuer's public key built in. A message can be passed on
  to a phone with no signal as a QR code.

Measured sizes are in [evals/kit/RESULTS.md](evals/kit/RESULTS.md). [HOW_IT_WORKS.md](HOW_IT_WORKS.md#the-offline-kit)
has examples of every code.

## The demo

```bash
.venv/bin/python sim/server.py
```

Open http://127.0.0.1:8765/demo in a 1920 × 1080 window. It plays the three use cases step by step with a caption
for each step, on the real server and with the real kit. Only the neighbours' phones are simulated.

| Key | What it does |
| --- | --- |
| → or space | Next step |
| 1, 2, 3 | Jump to a scene: look closer, the signed alert, check-in and a photo |
| R | Show what actually arrives on the phone (the plain texts) instead of what the kit shows |
| C | Hide the captions, to record clean and add a voice-over later |

Scene 1 runs the look-closer loop (about 30 s, live with Muse and SAM). Scenes 2 and 3 run the Red Hook drill: the
Okafors' phone, read by the kit, beside the hub's dashboard. Each scene can also be opened on its own:

- `/closer`: look closer.
- `/phone?who=+15550113`: one neighbour's phone.
- `/dashboard`: the hub.

[`demo/README.md`](demo/README.md) has the drill's story and how to render a video from screenshots.

## Run it

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

Put a Meta Model API key in `.env` (git-ignored) as `LLAMA_API_KEY=...`, then start the simulator:

```bash
.venv/bin/python sim/server.py
```

The simulator serves these pages:

- `/demo`: the demo above.
- `/kit.html`: the kit, as the single file a phone saves.
- `/closer`: look closer.
- `/dashboard`: the hub.
- `/`: every other mode.

Without a key, replies fall back to keyword routing, regex parsing and template text.

The offline tests need no key and no network. They cover codes, signatures against RFC 8032, the store, RESEND, ZOOM,
check-ins, routing around danger, the look-closer loop and the WhatsApp channel:

```bash
.venv/bin/python -m unittest discover tests
```

### Over WhatsApp

Without an SMS gateway, WhatsApp can stand in for the text channel. A person messages the Muse number (Twilio's
sandbox) and gets exactly what they would get by SMS.

1. **Twilio:** put `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and your own number as `MY_WHATSAPP` in `.env`.
2. **The bridge:** `.venv/bin/python sim/whatsapp_server.py` (port 8770). It serves only Twilio's webhook.
3. **A tunnel** to port 8770, for example `cloudflared tunnel --url http://127.0.0.1:8770`. Put its https address in
   `.env` as `PUBLIC_URL` and restart the bridge.
4. **Twilio's sandbox settings:** set "When a message comes in" to `PUBLIC_URL/whatsapp` (POST).
5. **Each phone** sends the sandbox's join message once.

**MuZip** sends from your own WhatsApp on a Mac. Run `.venv/bin/python sim/muzip.py`, build the helper app with
`bash sim/make_muzip_app.sh` and open `out/MuZip.app`. Then drop a photo at http://127.0.0.1:8780 and ask. MuZip does
the kit's work: the 128 px code, and a crop for every close-up Muse asks for. The app itself only presses Return in
WhatsApp, which needs Accessibility permission. Re-add it after every rebuild.

`sim/wa_phone.py` is a test phone that sends as your number and prints the replies.
[HOW_IT_WORKS.md](HOW_IT_WORKS.md#on-whatsapp) has the details.

## Also in this repo

The same server answers more than the kit's three use cases. These modes are not the focus right now, and
[HOW_IT_WORKS.md](HOW_IT_WORKS.md) has real inputs and outputs for each:

- **Emergency alerts:** "are you affected, what to avoid, which way out", with a map in characters that lines up in
  iPhone Messages.
- **Walking directions abroad:** one step at a time on street data, re-sent as you move.
- **Weather radar:** the latest radar and a 2-hour forecast as text.
- **Photos as drawings in characters**, checked by Muse reading them back blind.
- **How-to answers:** steps from cited web sources (Muse Spark's web search), with before and after pictures drawn by
  Muse Image and checked by **Muse Wonder**.

## Code

| Area | Files |
| --- | --- |
| The kit | `sim/kit.js`, `sim/kit.html`, `evac/codes.py` (codes, signatures, zoom crops), `evac/smsimage.py` (the 128 px picture) |
| Look closer | `evac/closer.py` (the loop), `evac/sam.py`, `sim/closer.html`, `sim/closer_demo.py`, `sim/phones.py` (phones that keep their originals) |
| Hub | `evac/hub.py`, `sim/dashboard.html`, `sim/redhook.py` (the drill), `sim/phone.html` (a neighbour's phone) |
| Demo | `sim/demo.html` (the three scenes), `demo/` (story and video tooling) |
| Router and store | `evac/router.py` (codes first, then Muse and its tools), `evac/store.py` (every photo, zoom, message and report, in SQLite) |
| WhatsApp | `evac/whatsapp.py`, `sim/whatsapp_server.py`, `sim/wa_phone.py`, `sim/muzip.py` |
| Other modes | `evac/pipeline.py`, `evac/directions.py`, `evac/radar.py`, `evac/photo.py`, `evac/howto.py`, the map renderers |
| Tests and evals | `tests/` (offline), `evals/` (kit sizes, SAM on small pictures, drawings) |

`CLAUDE.md` describes how the pieces connect.

## Limits

- **Not yet on real phones:** the kit hasn't been tried on a real phone with real SMS. WhatsApp through Twilio's
  sandbox is the live channel so far.
- **The drill's phones are simulated,** and so are its photos (made with Muse Image).
- **The kit must be saved ahead of time,** and pasting a message into it is a step people have to learn.
- **Small things in a 128 px picture:** Muse aims the first close-up from context alone, because SAM can't find them
  until the phone sends a close-up.

Roads and places © OpenStreetMap contributors (via Overpass); geocoding by Nominatim; aerial imagery © Esri.
