# MapsImagesText

**Pictures for people who can only receive text.** A server with a good connection does the heavy work: reading an emergency alert, looking at a photo, reading the weather radar, routing on street data. It sends back text that still works as a picture, a map or sketch drawn in characters. Each reply comes in several sizes, and the person gets the richest one their connection can carry.

| Version | Channel | What arrives | Typical size |
| --- | --- | --- | --- |
| **Low data** | SMS on an iPhone | the message + a small map in characters that line up in iPhone Messages | 6 to 12 SMS |
| **Medium data** | WhatsApp (monospace text) | the message + a 58-column map or drawing | 0.4 to 1.1 KB compressed |
| **Small picture** | a slow connection | a 400 px WebP drawn for that size | 4.5 to 23 KB |
| **High data** | a working connection | a picture map, or the photo itself | 50 to 180 KB |
| **Kit** | plain SMS + a page saved on the phone beforehand | the words + codes the page turns into a real map, a zoomable photo, a GPX route | 2 to 16 SMS |

Built on the Meta Model API:
- **Muse Spark** plans, writes and searches the web.
- **SAM 3.1** finds objects in photos.
- **Muse Image** draws the step pictures (and made the test pictures).
- **Muse Wonder** checks the pictures.

## What you can ask

Send one message (plus GPS, optionally a photo). Muse Spark picks the tools:

| Message | What comes back |
| --- | --- |
| an emergency alert (fire, gas leak, wildfire, flood) | the fixed, tested reply: are you affected, what to avoid, which way out, with a map |
| "Walk me to the Colosseum" | one step at a time on real street data, re-sent as you move |
| "Is it going to rain?" | the latest radar and a 2-hour forecast as text |
| a photo that won't download + "what is this?" | a description and the main object's outline in characters |
| "How do I shut off the gas?" | numbered steps from cited web sources, then checked before/after pictures where they help |

Every map shows **only what the reply is about**: the route, the streets it uses, closures and the danger zone's edge. Everything else is left blank, so the way to go is the first thing you see.

## How it works, briefly

Muse Spark reads the message and picks tools (alerts, routing, radar, photos, how-to search), which run in parallel.
Each tool gives the model a short summary and keeps its bulky result (streets, a route, a radar grid) for drawing.
Every result becomes one Scene, and every version (SMS, WhatsApp, pictures, kit codes) is drawn from it, so they
all show the same route. The model plans and writes the words; code computes distances, routes and maps. Anything
slow or flaky is cached or falls back (no key: keyword rules and templates).

**[HOW_IT_WORKS.md](HOW_IT_WORKS.md)** has the details, with real example inputs and outputs for every mode: emergency
alerts, walking directions, photos and picture compression, how-to answers, radar, the offline kit and its codes,
the look-closer loop, WhatsApp, the community hub, and why the text maps line up.

## Try it

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

Put a Meta Model API key in `.env` (git-ignored) as `LLAMA_API_KEY=...`. Without one, everything falls back to keyword routing, regex parsing and template text.

```bash
.venv/bin/python sim/server.py
```

The simulator runs at http://127.0.0.1:8765. It has four pages, linked from the top of the first:

- **`/`**: tabs for emergencies (three people with their own GPS), photos, radar, walking (with simulated GPS moves) and "ask anything". Every reply is shown in each of its versions with real sizes, including how the kit draws it.
- **`/kit.html`**: the offline kit, as the single file a phone saves.
- **`/dashboard`**: the community hub; the buttons at the bottom left run the Red Hook drill beat by beat. It is laid out for a 1920 × 1080 window.
- **`/closer`**: look closer; pick a spider photo and ask.

Offline tests (no key or network needed):

```bash
.venv/bin/python -m unittest discover tests
```

From the command line:

```bash
.venv/bin/python main.py --ask "Is it going to rain on my walk to the Colosseum?" --at 41.9010,12.5016
```

```bash
.venv/bin/python main.py samples/nyc_fire.txt --at 40.75491,-73.97441 --laptop 58 -v
```

```bash
.venv/bin/python main.py --walk "Castelo de São Jorge, Lisbon" --at 38.7139,-9.1406
```

`--photo some.jpg` and `--radar LAT,LON` run the other modes.

## On WhatsApp

For a demo without an SMS number, WhatsApp stands in for the text channel: a person messages the Muse WhatsApp
number (Twilio's sandbox) and gets exactly the SMS reply. A photo runs the look-closer loop, and the photo itself
never leaves the computer ([details](HOW_IT_WORKS.md#on-whatsapp)).

To set it up:

1. **Twilio:** put `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and your own number as `MY_WHATSAPP` (for the test phone) in `.env`.
2. **The bridge:** `.venv/bin/python sim/whatsapp_server.py` (port 8770). It serves only Twilio's webhook; the
   simulator and its data stay local.
3. **A tunnel** to port 8770, for example `cloudflared tunnel --url http://127.0.0.1:8770`. Put its https address
   in `.env` as `PUBLIC_URL` (Twilio signs requests with it) and restart the bridge.
4. **Twilio's sandbox settings:** set "When a message comes in" to `PUBLIC_URL/whatsapp` (POST).
5. **Each phone** sends the sandbox's join message to its number once; sandbox sessions end after 72 hours without a
   message.

**MuZip, the app for a demo.** On a Mac with WhatsApp Desktop: start the bridge and MuZip's server
(`.venv/bin/python sim/whatsapp_server.py` and `.venv/bin/python sim/muzip.py`), build the app with
`bash sim/make_muzip_app.sh` and double-click `out/MuZip.app`, which opens MuZip's page at http://127.0.0.1:8780.
Drop a photo, type a question and press Send. MuZip turns the photo into the 128 px code, sends it from your WhatsApp,
answers each closer look Muse asks for by cropping the original, and shows the conversation, the original with every
crop outlined, and what Muse saw. The photo never leaves the computer. A real run from a WhatsApp account (the
black widow in [HOW_IT_WORKS.md](HOW_IT_WORKS.md#on-whatsapp)) took 34 s: three messages out, three answers back.

To send from your account, something has to press Return in WhatsApp, which macOS only allows an app with
Accessibility permission. That is all MuZip.app does: it waits for requests in a private folder in `/tmp` and
brings WhatsApp forward for about a second to press Return. It never opens the project folder, so it needs no
Documents permission (the first version ran the servers itself and stalled on macOS's per-app Documents check). Give
it Accessibility (System Settings > Privacy & Security > Accessibility > + > MuZip) and allow the "control System
Events" prompt; after every rebuild remove it from that list and add it again, because the grant is tied to the
app's signature. MuZip's "simulated" switch sends straight to the bridge instead; Muse's replies still arrive in
your WhatsApp.

**Testing without a phone in hand:** `sim/wa_phone.py` is a test phone. It sends messages to the bridge as your
WhatsApp number, plays the kit for photos, and prints Muse's replies, which Twilio also delivers to your WhatsApp.
A real run took 19 s:

```bash
.venv/bin/python sim/wa_phone.py --photo sim/samples/spiders/black_widow.jpg "Is this a black widow?"
```

`--via whatsapp` sends from WhatsApp Desktop itself: it fills in the chat and has MuZip.app press Return when it's
running (otherwise it presses Return itself, which needs Accessibility permission for the app running it).

## A community hub (demo)

The coordinator's side for a neighbourhood whose phones can only text: one signed alert for everyone, check-ins on a
map, triaged photo reports, volunteer walks around the danger. Open http://127.0.0.1:8765/dashboard and run the Red
Hook drill with the buttons at the bottom left. [HOW_IT_WORKS.md](HOW_IT_WORKS.md#a-community-hub-demo) describes it;
[`demo/README.md`](demo/README.md) has the story and how to make the video.

## Code

| Area | Files |
| --- | --- |
| Router and tools | `evac/router.py`, `evac/tools.py`, `evac/llm.py` |
| Scene and drawing | `evac/scene.py`, `evac/focus.py`, `evac/render.py`, `evac/textmap.py` (SMS), `evac/laptopmap.py` (WhatsApp), `evac/imagemap.py` (pictures) |
| Emergency | `evac/pipeline.py`, `evac/notice.py`, `evac/grid.py`, `evac/segment.py`, `evac/imagery.py` |
| Walking | `evac/directions.py`, `evac/osm.py`, `evac/geo.py` |
| Photos | `evac/photo.py`, `evac/sam.py`, `evac/sketch.py`, `evac/tonal.py`, `evac/zooms.py` |
| How-to | `evac/howto.py`, `evac/illustrate.py`, `evac/verify.py`, `evac/museimage.py` |
| Radar | `evac/radar.py` |
| Kit | `evac/codes.py`, `evac/smsimage.py`, `sim/kit.js`, `sim/kit.html`, `sim/pack.json` |
| Store | `evac/store.py` (every photo, zoom, trip, message and report, in SQLite) |
| Community hub | `evac/hub.py`, `sim/dashboard.html`, `sim/redhook.py` (the Red Hook drill), `demo/` (story and video) |
| Look closer | `evac/closer.py` (the loop), `sim/closer.html` + `sim/closer_demo.py` (the demo), `sim/phones.py` (simulated phones that keep their originals) |
| WhatsApp | `evac/whatsapp.py` (the channel), `sim/whatsapp_server.py` (Twilio's webhook, the only part on the internet) |
| Simulator | `sim/server.py`, `sim/index.html` |
| Tests | `tests/` (codes, signatures, the store, RESEND/ZOOM/check-ins, routing around danger, the look-closer loop, the WhatsApp channel; offline) |
| Evals | `evals/` (photo drawings, emergency pictures, kit sizes) |

[HOW_IT_WORKS.md](HOW_IT_WORKS.md) explains each part; `CLAUDE.md` has the details of how the pieces connect.

## Data sources and limits

Roads and places © OpenStreetMap contributors (via Overpass); geocoding by Nominatim; aerial imagery © Esri; radar © RainViewer; forecasts by Open-Meteo. How-to steps name the sites they came from.

- **Not checked on real phones:** the maps have not been checked on a real iPhone or in WhatsApp, and the GPX files not in a map app on a phone.
- **Overpass:** the free servers are slow, 30 s to 2 minutes for a new area. Alerts wait at most 15 s for streets, then route on aerial imagery alone.
- **SMS length:** replies are 6 to 12 parts, and a photo with tones in the kit is 3–14 SMS.
- **The kit must be saved ahead of time,** and pasting a message into it is a step people have to learn.
- **The how-to pictures are experimental:** few steps qualify, the checks are noisy, and the test pictures are renders, not real photos.
