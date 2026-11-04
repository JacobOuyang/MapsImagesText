# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MuZip: agentic compression for photos, on the Meta Model API. A phone sends a question and a 128 px grey sketch of a
photo as text codes; Muse Spark looks and either answers or asks the phone for one close-up (a box, colour or grey,
normal or high detail); the phone crops its own original and sends it back; SAM 3.1 outlines the thing on each
close-up; repeat until Muse is sure. The photo never leaves the phone. WhatsApp (Twilio's sandbox) is the live channel.
Branches: `dev` (this: work here; it is `main` plus this file, which `main` doesn't track and ignores, so merge to
`main` through a PR without CLAUDE.md), `main` (the loop, the demo environment, the video's pages and tools), `demo`
(merged into `main`, no longer used), `full-prototype` (everything this started as: text maps for alerts, walking directions, radar, photo drawings, the
offline kit page, a neighbourhood hub).

## Commands

No build or lint step. Python 3.12 in `.venv` (`python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`;
git-ignored): `requests`, `pillow`, `numpy`, `meta-sam-parser` (Meta's SAM 3.1 parser, Python ≥ 3.10). Run everything
with `.venv/bin/python`.

```bash
.venv/bin/python -m unittest discover tests      # offline: codes, store, the loop with scripted replies, the bridge
.venv/bin/python evals/loop/run.py               # the loop's eval: run after ANY change to the loop (below)
.venv/bin/python evals/kit/three_ways.py         # the photo vs WebP at MuZip's size vs MuZip (slow; --report re-renders)
.venv/bin/python sim/server.py                   # simulator on :8765: /closer, /compare (launch config "simulator")
.venv/bin/python sim/whatsapp_server.py          # the WhatsApp bridge on :8770 (needs a tunnel; launch config "whatsapp")
.venv/bin/python sim/muzip.py                    # MuZip on a Mac, :8780 (launch config "muzip"; helper: sim/make_muzip_app.sh)
```

`.env` (git-ignored, loaded by `evac/llm.py`): `LLAMA_API_KEY` (a Meta Model API key; `MODEL_API_KEY` also works),
optional `LLM_BASE_URL` (default `https://api.meta.ai/v1`), `LLM_MODEL` (default `muse-spark-1.3`),
`LLM_REASONING_EFFORT` (default `minimal`); for WhatsApp `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `PUBLIC_URL` (the
tunnel's https address), `MY_WHATSAPP`.

## Architecture

**The loop (`evac/closer.py`).** `start(who, picture, pic_id, question, texts)` makes an `inquiry` record (the sketch as
received, the question, the phone's picture id and size, `views`, `decisions`) and returns the first reply. `_decide`
shows Muse every view so far (enlarged to `SHOWN` 512 px), each labelled with its place in the photo as percentages,
colour/grey, normal/high detail, how many times closer than image 1, its cost in texts, `full` when it already shows
the photo's full resolution, and SAM's outline if any. Muse replies JSON: an answer + `sure`, or `need` {`in` (which
image the box is drawn on: 1 = the photo, 2… = close-ups; `in_photo` converts to photo fractions), `box`, `colour`,
`detail` normal/high, `look_at`, `to_see`, `object`}, or a list of up to `AT_ONCE` (3) of those (two warning lights: one
round trip); a decision keeps the first as `need` and all as `needs`. `_snap`: for a small box (narrower than `SNAP`
40% of the close-up it is drawn on) with a named `object`, SAM 3.1 runs on that close-up and the box moves onto the
outline nearest Muse's, plus `SNAP_MARGIN` 30% (Muse's own boxes on the lights were 25-40 photo px off). Up to two notes per decision re-ask Muse: `repeated` refuses a
box overlapping a view it has (IoU ≥ `SAME` 0.6) at no more detail, and an unsure answer while looks remain is sent back
once. Budget: `BUDGET` (160) texts from the phone or `MAX_VIEWS` (8) close-ups; out of looks with no answer, it is asked once
to say what it sees and how sure (it may say it can't tell: forcing an answer made misreads). An empty question is a
share (`SHARE`: one close-up, forced colour + high, then one sentence); `router.SHOW` ("@Mew-zip show me …") counts as
empty. `start` carries close-ups this phone already sent of the same pic id into `views` as `known` (free, outside the
budget), so a second question in a group pays only for what is new. `_reply` turns a
`need` (or each of `needs`) into words + one `ZOOM <pic id> x y w h[ C][ H]` line each (box in the 128 px sketch's pixels). `receive(who, parent, box,
data)` takes the phone's `Z` code (one per close-up, in the order asked; the router feeds every Z in a message, and
Muse decides again only after the last): a `zoom` record, `full` when it came back `FULL` (1.3)× wider than asked or smaller than the size it
would be sent at (either way, the photo's own pixels), its texts, and `_outline`: SAM 3.1 on the close-up for `need.object` (a match ≥ `SAM_MIN` 0.5 and ≥
`SAM_SIZE` 8% of the side), mapped into the whole photo (`sam.all` also keeps matches down to `SAM_SIZE`/2, for showing only). The prompt's rules each fixed a measured failure: answer only
when sure; colour when colour helps find the thing (grey runs picked the wrong amber bottle); high detail for small
text; no repeats (eight identical timetable views); tight boxes; one symbol per close-up for two or three
warning lights or icons, all asked for at once (naming labels too made it read shelves jar by jar); name the thing that
decides the answer, not the area around it (SAM outlined a speedometer for "dashboard dials"). **After any change to the loop run
`.venv/bin/python evals/loop/run.py`** (results keyed by a hash of the loop's code in `out/eval/loop/`; `--save`
updates `evals/loop/BASELINE.json`).

**Codes (`evac/codes.py`, `evac/smsimage.py`).** `#<type><id>.<i>/<n>:<base64>:<crc16>#`, parts ≤ 153 characters in
the basic SMS alphabet; `decode` reassembles any order and names missing parts; `resend` answers `RESEND id n…` from
the parts a record `sent`. `P`: the sketch (`smsimage.webp`: 128 px grey, contrast-stretched, WebP q5). `Z`: a close-up
(`zoom_head`: length byte + parent id + box as 4 × uint16 /64, then WebP). `ZOOM` regex group 3 holds the flags;
`zoom_options` → `crop_zoom(colour=, side=)`: colour is q30, `H` is `HIGH` 256 px (grey q20); the crop keeps the box's
shape with a 20% margin, each side at least `MIN_CROP` (48) of the original's pixels, and is never enlarged (a smaller
crop goes at its own size). A message may ask for several close-ups (one ZOOM line each); `zooms` finds them and
`answer_zooms` answers them all in one message, each Z code with its own id (`<pic>z`, `<pic>y`, ...: a shared id merged
them). Every phone answers a ZOOM with
`answer_zoom(img, match, pic_id)` → (Z message, WebP bytes): MuZip, the bridge, `sim/wa_phone.py`, `sim/phones.py`;
`sim/kit.js` (the browser reader the simulator's pages use) has the same arithmetic in `cropBox`. Other types (`T`,
`L`, `S`, `A`, `K`) are older kit codes the reader still understands.

**Router (`evac/router.py`).** `handle(message, you=None, image=None, who=None)`: RESEND; a `Z` code goes to
`closer.receive` (or "doesn't answer any open question"); a `P` code with a question starts `closer.start`; anything
else is one Muse Spark chat answer (`SYSTEM`). Returns `{"text", "sms", "log", "seconds", "inquiry"?}`.

**WhatsApp (`evac/whatsapp.py`, `sim/whatsapp_server.py`).** Twilio posts each message to `POST /whatsapp` on the
bridge (port 8770; only it goes through a tunnel); `signature_ok` checks X-Twilio-Signature against `PUBLIC_URL` +
"/whatsapp" (no token: accept, for tests). It answers with empty TwiML at once and works in a thread, replying with
Twilio's REST API (`send`, split at 1,500 characters; without credentials replies go to `OUTBOX`, which tests read).
A photo attachment runs `look_closer`: the bridge plays the phone (keeps the original, sends the sketch with the
question, answers each ZOOM with `answer_zoom`, tells the person each step and its texts); `_download` records the
delivered photo's size (`wa_media`). Codes go straight to the router; other text gets a plain answer; "help" the help.
Every message in and out is a `wa_msg` record. MuZip (`sim/muzip.py` + `muzip.html`) runs the loop from your own
WhatsApp on a Mac: `sim/wa_phone.py` fills WhatsApp Desktop via `whatsapp://send` and `out/MuZip.app`
(`sim/make_muzip_app.sh`) presses Return when `wa_phone.press_return` drops a `press` file in `/tmp/muzip-<uid>/` (it
holds the Accessibility grant and never touches ~/Documents: macOS's per-app Documents check stalled an earlier app).

**The store (`evac/store.py`).** Every record in `cache/store/store.db` (SQLite, originals in `cache/store/files/`):
`id` (base-36 from "100"), `kind` (`inquiry`, `zoom`, `photo`, `wa_msg`, `wa_media`, …), `version`, `parent` + `box`,
`who`, `where`, `meta` (JSON, merged on update), `file`, `sent` (code parts, for RESEND). `new/get/update/image/find`;
tests call `store.use(tmpdir)`.

**SAM 3.1 (`evac/sam.py`):** model `sam-3.1` is served on `/v1/responses` (chat completions refuses it). Send one user message with `input_text` (a noun phrase) and `input_image`, `metadata` `mask_encoding: one_bit`, `include_confidence: "true"` (strings). Output text: `<0f>` then records `id<|box;x1;y1;x2;y2;w;h;c|><|mask;x=0;y=0;c;data=H,W,<enc><payload>|>`; boxes are in the sent image's pixels, corners inclusive; the mask is box-sized. The payload is compressed and the SAM License forbids reverse-engineering it: parse and decode only with Meta's `meta-sam-parser` (`image_segmentation_format().create_parser()`, `push(text)`, `finish(CompletedOutcome()).result.records`: box and mask records by `object_id`; `decode_mask_to_raster(record.mask)` gives row-major 0/1 bytes for the record's half-open `bounds`). Responses are cached in `cache/sam/` by image and prompt.

**The demo environment:** `sim/demo.html` (`/demo` and `/`: four scenes in frames, stepped with →; captions from
`demo/SCRIPT.md`), `sim/group.html` + `sim/group_demo.py` (`/group`: a simulated WhatsApp group; Ana's simulated phone
answers ZOOMs, `closer.start` runs on the group's sketch with `texts=0`; `?auto=1` plays it), `?device=drone` on
`/closer`, `sim/samples/field/` and two "field" questions in the loop eval, and `demo/three_ways/` (the comparison's
data, used by `/compare` when `out/eval/three_ways/` is missing).

**The video.** Each section is a page in `sim/video/` drawn as a function of time: `render(t)` after it
loads (`?t=` for one frame, `?play=1` to watch): `intro.html` (the street photo, `sim/video/street/`), `side.html`
(the dashboard row of the comparison; `?demo=1` options in `/compare` are the same data), `how.html` and `parts.html`
(the recorded run in `sim/video/dashboard/run.json`, from `demo/video/record_run.py`, which keeps the first run with
the shape asked for: `--shape big,small,small --together --sam-first --colour-first`). `demo/video/render.py <page>
<seconds> <voice.mp3> <out.mp4>` keeps one headless Chrome per worker, driven over `--remote-debugging-pipe` (JSON
messages on fds 3/4, NUL-terminated; the viewport set to exactly 1920x1080), each worker one continuous stretch of
frames (handed out in turn, the logo shimmered: Chromes antialias a few edge pixels differently); ~1 minute a section.
`demo/video/encode.swift` (AVFoundation) encodes at a steady 30 fps, a keyframe a second (long held frames made
players stall and drift from the voice), and adds the voice; `speed.swift` makes the 1.1× versions, `join.py`
(+ `join.swift`) joins them with one continuous sound track (gaps in a track drifted in some players). `close.html` is 6 (try it yourself, the end card); `ivy.html` is 5b (a hiker, run `sim/video/ivy`); `tenants.html` is section 5 (runs `sim/video/{tree,street_ben,street_chen,puppy}`, the street pair from
`demo/video/record_group.py`). Times in each page are where the lines fall in its
voice-over (found from the pauses). Scenes are their own 1920x1080 frame (`body { position:relative }`); full-screen
pictures keep their own shape and are drawn at whole pixels (an `<img>` 5x enlarged shook by 2 px between frames).
Voice-overs and finished videos live in `private/` (git-ignored); `demo/video/voice.py` can make them with ElevenLabs
(`ELEVENLABS_API_KEY`; library voices need a paid plan).

**Simulator (`sim/server.py`).** `/closer` (`closer.html` + `closer_demo.py`: a simulated phone, `sim/phones.py`, asks
about a sample photo through the real router; samples from `sim/samples/real|hard|spiders`), `/compare`
(`compare.html`: `out/eval/three_ways/results.json`), `/kit.js`, `/store/<id>.jpg`.

**Evals (`evals/`, see its README).** `loop/run.py` and `kit/three_ways.py` share `three_ways.muzip_run` (a phone per run,
unique numbers since an inquiry is found by number) and `verdict` (a text-only Muse call judging against a reference);
right = verdict right and `sure` "yes". `three_ways` caches each question in `out/eval/three_ways/<name>.json` and runs
every photo at once; WebP gets the loop's median bytes; the full photo is a ≤ 2,048 px JPEG.

Things that are easy to break:

- **Muse Spark reasons before answering** and those tokens count toward `max_completion_tokens`; at default effort a
  small decision took 5–10 s, `minimal` gives the same answers in 1–2 s. An empty `content` with
  `finish_reason=length` means reasoning ate the limit.
- **Pillow images are loaded lazily:** threads sharing one decoded-on-demand image crashed the eval ("image file is
  truncated"); decode once before sharing.
- **The ZOOM line is parsed by every phone** (Python and `kit.js`): change `codes.ZOOM`, `zoom_options` and `cropBox`
  together.

## Conventions

- Every commit updates the docs: `README.md` (overview and setup) and `HOW_IT_WORKS.md` (how each part works, with
  example inputs/outputs copied from real runs; re-run, don't hand-write outputs). The commit message describes the same.
- Model and network failures fall back (a plain reply, "can't tell") instead of blocking the reply.
- Only Meta Model API models at runtime.
