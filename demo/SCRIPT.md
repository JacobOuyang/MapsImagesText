# The demo video: script (draft)

Three minutes, about 420 words of voice-over, ready to paste into ElevenLabs one section per generation ("MuZip" is
said "mew zip"; spell it `Mew-zip` if the voice gets it wrong). The `<break>` tags work on Multilingual v2, Turbo and
Flash; Eleven v3 ignores them (drop them and keep the punctuation). Every number is measured: the three-way comparison
is `evals/kit/three_ways.py` (`evals/kit/THREE_WAYS.md`), the scenes are live runs of `/demo` on this branch.

| # | Time | Section | On screen | Status |
|---|---|---|---|---|
| 1 | 0:00–0:10 | Intro | `/title.html`: μ and a zipper, "agentic image compression" | agreed |
| 2 | 0:10–0:50 | The side-by-side | `/demo` scene 1 (three ways), steps 1–4: the dashboard | draft |
| 3 | 0:50–1:25 | How it works, on the same photo | `/demo` scene 2 (look closer, live): the dashboard round by round | to write |
| 4 | 1:20–1:25 | Tagline (a play on "a picture is worth a thousand words") | title card | to write |
| 5 | 1:25–2:10 | Real world: an emergency, and a group chat | `/demo` scenes 4 (drone) and 3 (group chat) | to write |
| 6 | 2:10–2:40 | Decisions per byte, when bytes are the limit | a chart: bytes sent against right answers | to write |
| 7 | 2:40–3:00 | Try it yourself | the live modes and the one command | to write |

The sections below the agreed ones are drafts from the earlier outline, kept for their wording.

## 1. Intro (0:00–0:10)

On screen: `/title.html` (click or R replays it).

```text
This is MuZip: agentic image compression. <break time="0.4s" /> MuZip uses Muse to figure out which parts of your image actually matter, <break time="0.2s" /> and compresses it by up to a hundred times.
```

## 2. The side-by-side (0:10–0:50)

On screen: the title card dissolves into the dashboard photo, the question appears as a text bubble; then `/demo`
scene 1 (`/compare?photo=dashboard`): the photo itself (214 KB), classical compression (WebP, 3.9 KB: "The photo is
too dark and blurry to tell which warning lights are on."), MuZip (3.9 KB, 55× smaller than the photo), then MuZip's
views one by one: the grey sketch, the cluster in colour, the red light alone, the amber light alone. Under each
column its size and its five runs as dots: the photo 5/5, WebP 0/5, MuZip 5/5. To end, a two-by-two: data sent
across, right answers up; MuZip alone in the top left.

```text
Say you're on a dark road with one bar of signal. <break time="0.3s" /> Two warning lights come on: which ones are they, and what do they mean? <break time="0.6s" /> Sent the normal way, this photo is over two hundred kilobytes: a lot to push through one bar. <break time="0.5s" /> Squeeze it under four kilobytes with classical compression, and it gets through, but Muse can't tell which lights they are. <break time="0.6s" /> MuZip sends the same four kilobytes. Muse looks at a tiny sketch, asks for the dashboard in colour, then for each light on its own. <break time="0.4s" /> The battery light, and the check-engine light. <break time="0.6s" /> Classical compression makes you choose: the right answer, or a small message. <break time="0.3s" /> MuZip gets you both: as accurate as the full photo, at a fiftieth of the size.
```

## 3. Not one lucky photo (1:10–1:30)

On screen: `/demo` scene 1, steps 4 and 5. Measured: on eight questions, four on real photos, MuZip was right and
sure 30 times out of 40; WebP at the same size, never.

```text
It isn't one lucky photo. <break time="0.3s" /> A real supermarket aisle: is there almond milk? A corridor: can I use this extinguisher? <break time="0.3s" /> Classical compression says it looks ready. It's empty. <break time="0.4s" /> On eight questions, MuZip was right, and sure, thirty times out of forty. Classical compression, at the same size: not once.
```

## 4. A group chat on a flood night (1:30–2:15)

On screen: `/demo` scene 3. Ana shares a photo of her flooded street (the group gets the 8-text sketch); Ben asks what
the little sign says (Muse asks Ana's phone for close-ups: NO PARKING 2AM–6AM); Chen asks which warehouse it is
(different close-ups: Warehouse 12). The whole conversation: 140 texts, about 20 KB, 28× less than one iPhone photo on
WhatsApp (543 KB, measured).

```text
Now put Muse in a group chat, on a flood night, when the network is barely holding. <break time="0.4s" /> Ana shares a photo of her street. The group gets MuZip's sketch: eight texts. <break time="0.4s" /> Ben asks what the little sign says. Muse asks Ana's phone for the part it needs, and her phone sends it back by itself. No parking, two to six. <break time="0.4s" /> Chen asks which warehouse it is. Same photo, different question, different close-ups. Warehouse twelve. <break time="0.5s" /> The whole conversation used less data than a twentieth of one photo, and Ana's photo never left her phone.
```

## 5. Beyond phones (2:15–2:45)

On screen: `/demo` scene 4. A drone over a flood keeps its full photo; Muse asks it for two close-ups and reads the
sheet on a roof: NEED INSULIN 2 PEOPLE (126 texts both ways).

```text
And it isn't only phones. <break time="0.3s" /> A drone over a flood, on a thin radio link. People on a roof, with a sign. <break time="0.4s" /> The drone keeps its photo. Muse asks for two close-ups, and reads it: need insulin, two people. <break time="0.5s" /> Drones, remote cameras, satellite messengers. Anywhere the picture is big and the link is thin.
```

## 6. Close (2:45–3:00)

On screen: a title card: MuZip, and the three models.

```text
MuZip. <break time="0.3s" /> Muse Spark decides what's worth seeing. SAM three point one finds it. Your device keeps the rest. <break time="0.6s" /> Agentic compression.
```

## Recording notes

- Open http://127.0.0.1:8765/demo at 1920 × 1080 and press C to hide the captions; step with →. Scenes 2–4 call the
  models live (about 20–60 s per step): record the waits and speed them up in editing, skip no step.
- Section 2's WhatsApp clip: MuZip (`sim/muzip.py` + `out/MuZip.app`) with the bridge and a tunnel running; drop
  `sim/samples/hard/medicine_cabinet.jpg` and ask the headache question.
- Known limits to avoid on camera: dense small text across a big real photo (the bus timetable, a distant price tag),
  and a few-pixel target anywhere in a wide shot (a person on one roof of twenty); see HOW_IT_WORKS.md.
