# The demo video: script (draft)

Three minutes, about 420 words of voice-over, ready to paste into ElevenLabs one section per generation ("MuZip" is
spelled `Mew-zip` in every voice-over block, so the voice says it "mew zip"). The `<break>` tags work on Multilingual v2, Turbo and
Flash; Eleven v3 ignores them (drop them and keep the punctuation). Every number is measured: the three-way comparison
is `evals/kit/three_ways.py` (`evals/kit/THREE_WAYS.md`), the scenes are live runs of `/demo` on this branch.

| # | Time | Section | On screen | Status |
|---|---|---|---|---|
| 1 | 0:00–0:14 | Intro | `/title.html`: μ and a zipper, "agentic image compression" | agreed |
| 2 | 0:10–0:50 | The side-by-side | `/demo` scene 1 (three ways), steps 1–4: the dashboard | draft |
| 3 | 0:52–1:30 | How it works, on the same photo | `/demo` scene 2 (look closer, live): the dashboard round by round | to write |
| 4 | 1:41–1:51 | Any photo, any question | the workflow chart as a recap | draft |
| 5 | 1:25–2:10 | Real world: an emergency, and a group chat | `/demo` scenes 4 (drone) and 3 (group chat) | to write |
| 6 | 2:10–2:40 | Decisions per byte, when bytes are the limit | a chart: bytes sent against right answers | to write |
| 7 | 2:40–3:00 | Try it yourself | the live modes and the one command | to write |

The sections below the agreed ones are drafts from the earlier outline, kept for their wording.

## 1. Intro (0:00–0:10)

On screen: `/title.html` (click or R replays it).

```text
A photo is only as good as the connection it's sent over. <break time="0.3s" /> On a weak one, it doesn't make it. <break time="0.5s" /> This is Mew-zip: agentic image compression. <break time="0.3s" /> Muse figures out which parts of your image actually matter, and sends only those, up to a hundred times smaller.
```

## 2. The side-by-side (0:10–0:50)

On screen: the title card dissolves into the dashboard photo, the question appears as a text bubble; then `/demo`
scene 1 (`/compare?photo=dashboard&demo=1`: one answer per panel and a verdict): the photo itself (214 KB, ✓ Right),
classical compression (WebP, 2.9 KB: "I can't tell for sure because the photo is too dark and blurry to see the
lights.", "? Muse couldn't tell". A chosen try: of 65 tries at this size, 63 named the right lights without being
sure and 2 couldn't tell; every try is kept in the dashboard's results, `more_runs`), MuZip (2.9 KB, 74× smaller than the photo, ✓ Right; 5 of 5 tries), its panel
building up what Muse saw: the blurry sketch, then the cluster, then each light on its own, sharp and in colour. To
end, a two-by-two: data sent across, right answers up; MuZip alone in the top left.

```text
Say you're on a dark road with one bar of signal. <break time="0.3s" /> Two warning lights come on: which ones are they, and what do they mean? <break time="0.6s" /> Sent the normal way, this photo is over two hundred kilobytes. <break time="0.5s" /> Mew-zip sends just three kilobytes, <break time="0.3s" /> and Muse answers, correctly and confidently: the battery light, and the check-engine light. <break time="0.6s" /> Squeeze the photo to the same three kilobytes with classical compression, and Muse can't tell which lights they are. <break time="0.6s" /> Classical compression makes you choose: a small file, or the right answer. <break time="0.3s" /> Mew-zip gets you both: as accurate as the full photo, at a fraction of the size.
```

## 3. How it works (0:52–1:30)

On screen: the same dashboard run, step by step, built like the other sections (`sim/video/`): the phone keeping its
photo; the 0.5 KB grey sketch going out with the question; Muse Spark's request and its box drawn on the sketch
("the instrument cluster ... in colour"); the colour close-up coming back, with SAM 3.1's outline on it; the red light
alone, then the amber light alone, each at the photo's own pixels; the answer; "the photo never left the phone".

```text
Here's how it works. <break time="0.3s" /> Mew-zip shrinks the photo to a tiny grey WebP and writes it out as plain text: short enough to fit in a handful of text messages. <break time="0.3s" /> It sends that to Muse Spark, with the question. <break time="0.4s" /> Muse isn't sure yet, so it asks for one part: the dashboard, in colour. <break time="0.4s" /> Mew-zip crops that part from the full photo, and sends it back, again as text. <break time="0.3s" /> SAM three point one outlines what Muse is looking for, and tells Muse exactly where it is in the photo. <break time="0.4s" /> There are two small lights, so Muse asks for each one on its own, at full resolution. <break time="0.4s" /> Muse only answers once it's sure. <break time="0.3s" /> Now it is: the battery light, and the check-engine light. <break time="0.4s" /> And the full photo never left the phone.
```

## 4. Any photo, any question (about 1:41–1:51)

On screen: the workflow chart (`/video/parts.html`) as a recap, each part lit as it's named, ending on the device:
the photo on the phone → a tiny WebP piece → Muse Spark → SAM 3.1 → back round the loop.

```text
Built on existing image compression, the same loop works for any photo and any question. <break time="0.3s" /> Muse Spark decides, SAM three point one finds, and the device keeps the rest.
```

## Possible closing tagline (undecided: at the very end, over the logo)

"A picture may be worth a thousand words. Mew-zip finds the thousand characters that matter." (Figurative, like the
saying; the dashboard run sent about 5,000 characters, so keep it away from any size on screen.)

## Earlier drafts (kept for their wording)

### Not one lucky photo (1:10–1:30)

On screen: `/demo` scene 1, steps 4 and 5. Measured: on eight questions, four on real photos, MuZip was right and
sure 30 times out of 40; WebP at the same size, never.

```text
It isn't one lucky photo. <break time="0.3s" /> A real supermarket aisle: is there almond milk? A corridor: can I use this extinguisher? <break time="0.3s" /> Classical compression says it looks ready. It's empty. <break time="0.4s" /> On eight questions, Mew-zip was right, and sure, thirty times out of forty. Classical compression, at the same size: not once.
```

### A group chat on a flood night (1:30–2:15)

On screen: `/demo` scene 3. Ana shares a photo of her flooded street (the group gets the 8-text sketch); Ben asks what
the little sign says (Muse asks Ana's phone for close-ups: NO PARKING 2AM–6AM); Chen asks which warehouse it is
(different close-ups: Warehouse 12). The whole conversation: 140 texts, about 20 KB, 28× less than one iPhone photo on
WhatsApp (543 KB, measured).

```text
Now put Muse in a group chat, on a flood night, when the network is barely holding. <break time="0.4s" /> Ana shares a photo of her street. The group gets Mew-zip's sketch: eight texts. <break time="0.4s" /> Ben asks what the little sign says. Muse asks Ana's phone for the part it needs, and her phone sends it back by itself. No parking, two to six. <break time="0.4s" /> Chen asks which warehouse it is. Same photo, different question, different close-ups. Warehouse twelve. <break time="0.5s" /> The whole conversation used less data than a twentieth of one photo, and Ana's photo never left her phone.
```

### Beyond phones (2:15–2:45)

On screen: `/demo` scene 4. A drone over a flood keeps its full photo; Muse asks it for two close-ups and reads the
sheet on a roof: NEED INSULIN 2 PEOPLE (126 texts both ways).

```text
And it isn't only phones. <break time="0.3s" /> A drone over a flood, on a thin radio link. People on a roof, with a sign. <break time="0.4s" /> The drone keeps its photo. Muse asks for two close-ups, and reads it: need insulin, two people. <break time="0.5s" /> Drones, remote cameras, satellite messengers. Anywhere the picture is big and the link is thin.
```

### Close (2:45–3:00)

On screen: a title card: MuZip, and the three models.

```text
Mew-zip. <break time="0.3s" /> Muse Spark decides what's worth seeing. SAM three point one finds it. Your device keeps the rest. <break time="0.6s" /> Agentic compression.
```

## Recording notes

- Open http://127.0.0.1:8765/demo at 1920 × 1080 and press C to hide the captions; step with →. Scenes 2–4 call the
  models live (about 20–60 s per step): record the waits and speed them up in editing, skip no step.
- Section 2's WhatsApp clip: MuZip (`sim/muzip.py` + `out/MuZip.app`) with the bridge and a tunnel running; drop
  `sim/samples/hard/medicine_cabinet.jpg` and ask the headache question.
- Known limits to avoid on camera: dense small text across a big real photo (the bus timetable, a distant price tag),
  and a few-pixel target anywhere in a wide shot (a person on one roof of twenty); see HOW_IT_WORKS.md.
