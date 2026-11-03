# The demo video: script (draft)

Three minutes, about 420 words of voice-over, ready to paste into ElevenLabs one section per generation ("MuZip" is
said "mew zip"; spell it `Mew-zip` if the voice gets it wrong). The `<break>` tags work on Multilingual v2, Turbo and
Flash; Eleven v3 ignores them (drop them and keep the punctuation). Every number is measured: the three-way comparison
is `evals/kit/three_ways.py` (`evals/kit/THREE_WAYS.md`), the scenes are live runs of `/demo` on this branch.

| # | Time | Section | On screen | Words |
|---|---|---|---|---|
| 1 | 0:00–0:30 | What MuZip is, and the proof | `/demo` scene 1 (three ways), steps 1–3: the medicine cabinet | ~75 |
| 2 | 0:30–1:10 | How it works | the real WhatsApp run (MuZip on the Mac), cut with `/demo` scene 2 (the cabinet, live) | ~95 |
| 3 | 1:10–1:30 | Not one lucky photo | `/demo` scene 1, steps 4–5: the milk aisle (real), the extinguisher | ~45 |
| 4 | 1:30–2:15 | A group chat on a flood night | `/demo` scene 3 (group chat, live) | ~100 |
| 5 | 2:15–2:45 | Beyond phones | `/demo` scene 4 (drone, live) | ~60 |
| 6 | 2:45–3:00 | Close | a title card | ~30 |

## 1. What MuZip is, and the proof (0:00–0:30)

On screen: the cabinet's three columns: the photo (412 KB, about 4,200 texts), classical compression (66 texts: "You
can take Tylenol", there is no Tylenol), MuZip (69 texts: the ibuprofen, bottom shelf). Measured over five runs each:
the photo 5/5, WebP 0/5, MuZip 5/5.

```text
This is MuZip: agentic compression. <break time="0.4s" /> It sends only the information that matters, at under a hundredth of the size. <break time="0.4s" /> Meta's Muse Spark looks at a tiny sketch of your photo, and asks for just the parts it needs. <break time="0.6s" /> Which of these can I take for a headache? <break time="0.3s" /> Squeezed the classical way into the same few texts, Muse can't read a single label, and guesses. <break time="0.3s" /> With MuZip: the ibuprofen, bottom shelf. Five times out of five.
```

## 2. How it works (0:30–1:10)

On screen: MuZip on the Mac sending from WhatsApp (record this from your account: drop the cabinet photo, ask the
question), cut with `/demo` scene 2, which shows Muse's rounds: the sketch, the box it asked for, the close-up, SAM's
outline, the answer.

```text
Here's a real run, over my own WhatsApp. <break time="0.3s" /> My phone sends the question and the whole photo, a hundred and twenty-eight pixels wide, in grey. About ten texts. <break time="0.4s" /> Muse finds the shelves, and asks for a closer look, in high detail. My phone crops its own full-size photo and sends back only that piece, by itself. SAM three point one outlines what Muse asked about, and tells it where that sits in the whole picture. <break time="0.4s" /> If one look isn't enough, Muse zooms in again, inside its own close-up, until it's sure. <break time="0.5s" /> And the photo never leaves my phone.
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
