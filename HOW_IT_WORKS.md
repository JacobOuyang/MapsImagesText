# How it works

The details behind [README.md](README.md): the loop, how a photo travels as text, real runs, the evidence and how it
was measured, and the WhatsApp channel.

## The loop: a question about a photo that stays on the phone

The phone sends a photo as a 128 px grey sketch. That is often enough, but sometimes the detail that decides the
answer is a few pixels, or it's a colour. So the server doesn't guess: it asks the phone for just that part
(`evac/closer.py`).

1. **The phone sends the question and the cheapest useful view:** the whole photo at 128 px, grey (about 10 texts).
2. **Muse Spark looks** (every view enlarged to 512 px) and either answers, saying how sure it is, or asks for **a
   closer look** (or up to three at once, for a few separate small things): a tight box around the detail, drawn on
   any view so far (the whole photo, or an earlier close-up to zoom in further), whether it needs colour, at what
   detail, what it wants to see there and what the thing is.
3. **The phone crops each box from its original**, in its own shape plus a margin, in colour if asked, and sends them
   back by itself, all in one message (one Z code each).
4. **SAM 3.1 outlines the thing** Muse named ("spider", "fire extinguisher") on the close-up, and Muse gets its exact
   place in the whole photo before deciding again.
5. **Again, with every view so far,** each labelled with where it sits in the photo, its colour and detail, how many
   times closer it is than the whole photo, what it cost in texts, and whether it already shows that part at the
   photo's full resolution (a crop is never enlarged: a part smaller than the picture it would be sent as comes back
   at its own pixels, down to 48 of them, so zooming further would show nothing new). It ends when Muse answers, or when the phone has sent 160 texts or eight close-ups; then Muse must
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
- **One symbol at a time, all asked for at once.** To tell apart two or three small symbols (warning lights, icons),
  Muse closes in on each one by itself, and asks for all of them in one reply: one `ZOOM` line each, and the phone
  answers them in one message, so two lights cost one round trip, not two. A close-up used to be at least 128 of the
  photo's pixels, so on a dashboard where each light is about 40 px wide, every close-up held both lights; now a crop
  can be as small as 48 px and goes at that size. The first wording also named labels, and Muse then read a pharmacy
  shelf one jar at a time and ran out of texts (cinnamon 0/3, 87 more texts); naming only symbols brought it back to
  its baseline.
- **Crops in the box's own shape.** A close-up used to be square: a wide box (a dashboard's cluster, two street signs)
  became a square that took in half the photo, 2.9 KB for the cluster. Now it keeps the box's shape plus a 20% margin
  (0.8–2 KB); the parking signs went from 62 texts to 28.
- **Name the thing that decides the answer.** Muse names what it is looking for ("warning light"), not the area around
  it ("dashboard dials"): SAM outlines what is named, and it outlined a speedometer dial.
- **SAM places small boxes.** Muse draws boxes by eye, and on small things it misses: both warning-light boxes were
  2-3 sketch pixels (25-40 of the photo's) left of the lights, cutting the red one off. When Muse asks for a small
  named thing inside a close-up, SAM 3.1 finds it on that close-up and the box moves onto the outline nearest the one
  Muse drew, with a 30% margin (`closer._snap`). Muse decides what to look at and roughly where; SAM says exactly
  where. The loop eval: generated 12/12, real 6/12 (one more each than before), easy and field within noise.

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

**A second real run,** on a car at night (`hard/dashboard`, a 1600×980 photo made with Muse Image; recorded with
`demo/video/record_run.py`, kept in `sim/video/dashboard/run.json`): the cluster, then both lights
in one request, each box placed by SAM:

```
You (7 SMS)    Two warning lights came on on my dashboard. Which ones are they, and what do they mean?  + the sketch
Muse (2 SMS)   Not sure yet: I need a closer look at the instrument cluster behind the steering wheel to see the
               shapes and colours of the warning lights in it, in colour, in high detail. ...
               ZOOM mx 6 31 51 23 C H
You (20 SMS)   the cluster, in colour, a 256x116 strip (1.9 KB)                  [SAM 3.1 "warning light", 0.91]
Muse (3 SMS)   Not sure yet: I need closer looks at the left red warning light to see its symbol shape clearly, in
               colour, in high detail; and the right amber warning light to see its symbol shape clearly, ...
               ZOOM mx 34 41 5 4 C H                                               [box placed by SAM, 0.91]
               ZOOM mx 40 42 4 3 C H                                               [0.89]
You (7 SMS)    both lights, one message: 75x60 and 60x48 px, the photo's own pixels (338 + 266 B)   [0.66, 0.80]
Muse (2 SMS)   They are the red battery / charging-system light, meaning the battery is not charging, and the amber
               check-engine light, meaning an engine or emissions fault.
               Stop somewhere safe and get help soon - a charging fault can stall the car.
```

34 texts from the phone (3.2 KB) for a 214 KB photo.

**Where SAM 3.1 fits.** SAM outlines things by name, but it needs enough pixels. In the 128 px sketch a spider is
about 14 pixels, and no enlarging (4×, 8× sharpened, tiled) made SAM find it. Neither did asking Muse Image to restore
the picture, which erased the spider altogether. It found things from about 25 px up (`evals/closer/RESULTS.md`). So
Muse aims the first close-up from context ("the dark spot under the shelf"), and SAM works on the close-ups, where the
thing fills the frame: on the black widow it scored 0.82–0.89, its box within 1% of SAM's on the full original. A
match counts from confidence 0.5 and when it is at least 8% of the close-up's side (15% threw away a warning light on
a close-up of the whole cluster, about 11-14% of its width). Muse is told about the best match; the view also keeps
every match down to half that size (`sam.all`), for showing: on the dashboard's cluster SAM found both lights, the red
one at 0.91 and the amber one at 0.89, but the amber one is 7.8% of the close-up's height.

## Shares and group chats

**A photo with no question is a share** (`closer.SHARE`, `share` in the inquiry): Muse asks for exactly one close-up,
always in colour at high detail, of the part a group most needs to see, then says in one sentence what it shows. The
group gets the sketch with that part sharp. "@Mew-zip show me Alex's photo" or "what's in it?" with a photo counts as
no question (`router.SHOW`). A real run on a real photo (`real/tree_on_car`, 6.8 MB; WhatsApp sent it as 659 KB):

```
Phone (10 SMS)  [the sketch, 1.0 KB]
Muse (2 SMS)    Not sure yet: I need a closer look at the crashed car to see the damage and what it hit, in colour,
                in high detail. ...                            ZOOM ck 13 34 90 43 C H
Phone (54 SMS)  [the close-up, 5.5 KB]
Muse (1 SMS)    A fallen tree is lying on top of a grey SUV.
```

6.5 KB from the phone in all.

**A second question about the same photo starts from what the phone already sent.** `closer.start` finds the
close-ups this phone sent of the same picture for earlier questions and gives them to Muse as `known` views, labelled
"Their phone sent it for an earlier question about this photo; you have it at no cost", outside the budget. Two
neighbours asking about one street photo (`real/storm_street`, recorded with `demo/video/record_group.py`):

```
Ben             My pickup's on the left side, right behind the first two cars. Is it buried, or can I drive it out?
                5 close-ups, 4.9 KB from the phone          -> Your pickup is not buried. You can drive it out.
Chen            Is the silver SUV at the far end, on the right side of the road, stuck in the sand?
                Ben's 5 close-ups free, 7 new ones, 4.6 KB  -> No. The silver SUV is sitting on clear pavement, with a
                                                               pile of sand beside it on the left.
```

**Out of looks, Muse may still say it can't tell.** When it has no closer look left and no answer, it is asked once
more: "Reply in one or two short sentences with what you can see and how sure you are; if you can't tell, say so and
what you would need to see." Pressing it to answer instead turned the field questions' "can't tell"s into confident
misreads (4 of 5 wrong).

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
| Car at night (generated) | Two warning lights came on. Which ones, and what do they mean? (battery and check-engine) | 214 KB: 4/5 | 29 texts: 0/5 (5 hedged) | 31 texts: **5/5** |
| Bus stop timetable (real) | Saturday, 9 am: when's the next bus? (10:06) | 4.7 MB: 1/5 | 181 texts: 0/5 | 183 texts: 1/5 |
| Milk aisle (real) | How much is a gallon of whole milk? ($3.69) | 1.0 MB: 5/5 | 166 texts: 0/5 | 169 texts: 0/5 |
| Milk aisle (real) | Any almond milk? Which brand? (Almond Breeze) | 3.3 MB: 5/5 | 91 texts: 0/5 (5 hedged) | 93 texts: **5/5** |
| Old pharmacy (real) | Anything with cinnamon? What does its label say? (SIR. CINNAMOMI) | 5.8 MB: 5/5 | 86 texts: 0/5 | 87 texts: **4/5** |

MuZip 35/45, WebP 0/45, the photo itself 35/45 (the dashboard was measured with the one-symbol rule, the others
before it). From WebP Muse made answers up: "take the aspirin", "there's
Benadryl", "Paracetamol 500 mg" (it was propranolol), "no almond milk visible". Before high detail and the no-repeat
rule, MuZip scored 17/20 on the generated photos and 7/20 on the real ones; after, 20/20 and 10/20.

On `/compare` MuZip's panel is what Muse saw, put back together: the 128 px sketch enlarged (as blurry as Muse got
it), each close-up pasted over the part of the photo it came from at its own detail and colour, one per second, with a
camera that follows them in (into a close-up that holds the next one; around small ones together) and back out. The
picture is drawn on a hidden canvas sized so the sharpest close-up keeps its own pixels. The run shown is the right
run closest to WebP's size (the median of the five), so the two panels' sizes match; under each panel its five runs are
five dots: green right (and sure), amber right but not sure, grey couldn't tell, red wrong.

**How a photo is normally sent.** An iPhone photo sent on WhatsApp arrived at the bridge as a 2160×3840 JPEG of 543 KB
and 811 KB (Twilio keeps the media; measured, not estimated). The report's own estimates (a 1,600 px JPEG for WhatsApp,
`sips` HEIC for the iPhone's file) are lower than that, so its "MuZip vs WhatsApp" column is conservative.

## The loop's own eval

`evals/loop/run.py` runs only MuZip on `evals/loop/dataset.json` (the comparison's questions except the dashboard, plus two easy controls and two field questions),
three runs each, all at once, in about 80 s, and prints right-and-sure, hedged and wrong per question with the median
texts, close-ups and seconds, against `BASELINE.json`. Results are kept by a hash of the loop's code (`out/eval/loop/`),
so an unchanged loop isn't re-run; `--save` makes a run the baseline and writes `RESULTS.md`.

The baseline: generated 11/12, real 7/12, easy 6/6 (median 36 texts), field 5/6; the latest run, after keeping every SAM match: 11/12, 6/12, 5/6, 5/6. The real photos
swing by about one per question between 3-run evals; check a change there with `--runs 5`. A budget of
240 texts instead of 160 got the timetable to 2/3 but cost 45 more texts and didn't help the price tag, so it wasn't
kept.

## Beyond phones: the field questions

The device that keeps the original can be anything on a thin link. `evals/loop/dataset.json` adds two
"field" questions on Muse Image photos (`sim/samples/field/`):

- **A drone over a flood:** "There are people on a roof. What does the sign next to them say?" 3/3, two high-detail
  close-ups (116 texts): NEED INSULIN 2 PEOPLE. The sheet shows as a white patch in the sketch, so Muse knows where to
  look.
- **A security camera in a pump station:** "Which pump station and line is this camera watching?" 1/3: one run read the
  cabinet's "LINE 3" as "Line 1" and was sure. A confident misread, the failure the "only when sure" rule can't catch.

**Where it stops working: search.** Two earlier field photos asked Muse to find a target of a few pixels anywhere in a
wide shot: two people on one roof among twenty (from a drone at 80 m), and a wolf by a distant fence (a trail camera).
0/6 each: the sketch gives no hint where to look, and close-ups of whole quadrants still show the target as a speck.
Sending the sketch in colour (WebP q5 colour costs about the same as grey) didn't help either (0/6; the red cloth the
person waved didn't survive). These photos were dropped from the set; the limit stays: MuZip needs the sketch to show
*something* worth a closer look.

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

## The demo video

Every section of the video is a page in `sim/video/` drawn as a function of time, so each frame is exact and the voice
decides the timing: the times in each page are where its lines fall in the voice-over, found from the pauses in the
audio. Every picture, size and answer on screen comes from a measured run: the comparison's dashboard row
(`/api/compare`), and one recorded dashboard run (`demo/video/record_run.py` keeps the first of several runs with the
shape the voice-over describes, and every message, ZOOM line, box and SAM outline of it in
`sim/video/dashboard/run.json`).

`demo/video/render.py` keeps one headless Chrome per worker and drives it over its DevTools pipe: the viewport is set to
exactly 1920x1080, then for each frame the page's `render(t)` runs and a screenshot is taken. A 38-second section takes
about a minute (a Chrome started per frame took 15 minutes, and sometimes shot with its window 63 px short: a flicker
along the bottom). Identical frames in a row become one held frame, and `demo/video/encode.swift` (AVFoundation, no
ffmpeg) encodes them and adds the voice-over.

```
.venv/bin/python demo/video/render.py /video/side.html 38.0 private/voice/02_the_side_by_side.mp3 private/video/02_the_side_by_side.mp4
1140 frames, 114 distinct, captured in 73 s
```

`encode.swift` writes a frame every 1/30 s with a keyframe each second, however long a picture is held: frames held
for seconds made players stall and lose sync with the voice, worst where sections were joined (section 2 opened on
a 3.5 s frame). `demo/video/speed.swift` plays a section faster with the voice at its own pitch (every section runs at
1.1×) and `demo/video/join.swift` puts the sections one after another:

```
out/demo/speed private/video/05_real_use_cases.mp4 private/video/05_real_use_cases_1.1x.mp4 1.1
wrote private/video/05_real_use_cases_1.1x.mp4: 35.1 s
out/demo/join private/video/all_1.1x.mp4 private/video/0{1_intro,2_the_side_by_side,3_how_it_works,4_any_photo,5_real_use_cases}_1.1x.mp4
wrote private/video/all_1.1x.mp4: 137.3 s
```

Section 5 (`sim/video/tenants.html`) is a tenants' WhatsApp group after a hurricane, then a chat on a plane. Mew-zip is
called by name, the way @Meta AI is: Maria shares a photo with "@Mew-zip" (the share above), Ben and Chen each ask
"@Mew-zip ..." about her street photo, and on a plane "@Mew-zip can you show me Alex's photo?" gets the puppy
(`hard/puppy_home`, 6.6 KB against 338 KB). Each answer shows what Mew-zip sent: the sketch with the close-ups in place,
the asker's car boxed. What WhatsApp would have sent for each photo was measured through the bridge
(`sim/video/normal_sizes.json`).

A third use case, a hiker on one bar of signal (`sim/video/ivy.html`, section 5b), is the recorded run `sim/video/ivy` ( a real photo, `real/leaf_ivy`, 3072×2304,
1.7 MB): Muse asked for one close-up, grey at high detail, of the leaf group, and SAM 3.1 outlined "leaves" (0.75):

```
Phone (8 SMS)   I'm on a hike and about to step off the trail through this. Is this poison ivy?  + the sketch
Muse (2 SMS)    Not sure yet: I need a closer look at the two pale leaves in the center to see stem join and leaf
                edges to check for three leaflets, in high detail. ...
Phone (45 SMS)  the close-up (4.5 KB)
Muse (3 SMS)    Yes, that is poison ivy - three leaflets meeting at one point. Do not step through it.
                Stay on the trail and do not touch it; wash skin and clothes if you do.
```

5.3 KB from the phone. Screened first: Muse was right and sure from the full photo 2 of 2 times, and through MuZip 3
of 3. Four other poison ivy photos were dropped: in a dense patch Muse couldn't make out the groups of three (1 of 3
each), and a wide marsh view went wrong every time.

Voice-overs and finished videos stay in `private/`, which git ignores.
