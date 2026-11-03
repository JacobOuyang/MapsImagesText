# The demo video: script (draft)

Three minutes, about 420 words of voice-over. Every number in it is measured: the three-way comparison is
`evals/kit/three_ways.py` (`evals/kit/THREE_WAYS.md`), the WhatsApp run and the drill are real runs.

**The idea, in one line:** every compression algorithm decides what to throw away. Classical ones keep what your eye
would notice; MuZip keeps what the question needs, and lets the AI on the other end decide, by asking for the part
of the photo it needs. The photo stays on the phone.

**How to say it:** "MuZip" is said "mew zip" (spell it `Mew-zip` for ElevenLabs if it says it wrong). The voice-over
below is ready to paste into ElevenLabs, one section per generation; the `<break>` tags work on Multilingual v2,
Turbo and Flash (Eleven v3 ignores them: drop them and keep the punctuation).

| # | Time | Section | On screen | Words |
|---|---|---|---|---|
| 1 | 0:00–0:25 | The idea at a glance | `/demo` scene 1: the extinguisher, three ways (`/compare`) | ~65 |
| 2 | 0:25–1:20 | How it works, over real WhatsApp | MuZip on this Mac sending from WhatsApp, cut with `/demo` scene 2 (Muse's rounds) | ~130 |
| 3 | 1:20–1:40 | Not one lucky photo | `/demo` scene 1, steps 4–5: the medicine label, the tick | ~45 |
| 4 | 1:40–2:40 | Where it matters most: a flood night | `/demo` scenes 3–4 (Red Hook) | ~150 |
| 5 | 2:40–3:00 | Close | title card | ~30 |

## 1. The idea at a glance (0:00–0:25)

On screen: the extinguisher's three columns. Step through: the photo itself, then classical compression ("looks
ready to use", in red), then MuZip ("EMPTY / RECHARGE", in green).

Measured: the photo is 476 KB, about 4,800 texts. WebP at MuZip's size (3.5 KB, 35 texts, its best shot of every size
and quality) gave 0 sure and right answers in 5 runs, and 3 of 5 said it was ready. MuZip: 5 of 5 sure and right,
a median of 37 texts.

```text
Is this fire extinguisher ready to use? <break time="0.4s" /> Sent the normal way, this photo is half a megabyte: almost five thousand text messages. <break time="0.4s" /> Squeeze it into thirty-five texts with classical compression, and the AI sees an extinguisher on the wall. It says it looks ready to use. <break time="0.3s" /> It isn't. <break time="0.6s" /> This is MuZip. About the same number of texts. And it reads the label: empty, recharge.
```

## 2. How it works, over real WhatsApp (0:25–1:20)

On screen: MuZip on the Mac (the original with each close-up outlined, the thread), WhatsApp beside it filling with
code texts; cut to `/demo` scene 2 for Muse's rounds (what it saw, what it asked for, SAM's outline). Speed up the
waits; skip no step.

A real run of the same photo (`/closer`): the phone's question and 128 px grey picture (12 texts); Muse asks for the
extinguisher in colour (15 texts back), then the gauge and label inside that close-up (14), then the gauge alone (11);
SAM 3.1 outlines the extinguisher in each (0.92–0.98); "No, it is not ready to use. It is marked EMPTY / RECHARGE."
59 texts both ways.

```text
Every compression algorithm decides what to throw away. Classical ones keep what your eye would notice, for anyone, for any question. MuZip keeps only what this question needs, and lets the AI decide. <break time="0.5s" /> Here's a real run, over my own WhatsApp. My phone sends the question and the whole photo, a hundred and twenty-eight pixels wide, in grey. About ten texts. <break time="0.4s" /> Muse Spark, Meta's model, finds the extinguisher, and asks for a closer look, in colour. My phone crops its own full-size photo and sends back only that piece, by itself. SAM three point one outlines the extinguisher, and tells Muse exactly where it is. <break time="0.4s" /> Muse zooms in again, inside that close-up, until it's sure. <break time="0.5s" /> Not ready to use. And the photo never left my phone.
```

## 3. Not one lucky photo (1:20–1:40)

On screen: `/demo` scene 1, steps 4 and 5 (the medicine cabinet, the tick).

Measured, 5 runs each: MuZip 15 of 15 sure and right; WebP at the same size 0 of 15 (it couldn't read the date, and
it only ever thought the tick "looks like" one); the full photo 15 of 15.

```text
It isn't one lucky photo. <break time="0.3s" /> An expiry date too small to read in any thumbnail. A tick a few pixels wide. <break time="0.4s" /> Five runs each. MuZip was right, and sure, fifteen times out of fifteen. Classical compression, at the same size: not once.
```

## 4. Where it matters most: a flood night (1:40–2:40)

On screen: `/demo` scenes 3 and 4 (the Okafors' phone beside the hub). Press R for the plain texts.

```text
Now take it to the moment it matters most. <break time="0.5s" /> When a storm hits, mobile data is the first thing to go. Text messages keep getting through, even over satellite. <break time="0.4s" /> This is a flood drill in Red Hook, Brooklyn. Forty neighbours whose phones can only text, and one hub with a connection. <break time="0.4s" /> The flood warning goes out to everyone. Their phones turn it into a map with the way out, and check that it's signed. <break time="0.4s" /> And this is all that actually arrives. Ordinary texts. <break time="0.5s" /> A forged copy starts going round, sending people the wrong way. It's caught. Not genuine. <break time="0.5s" /> Neighbours check in with one tap. Muse Spark tells the hub who to help first. <break time="0.4s" /> The Okafors send a photo of a tree on a power line. Muse rates it critical, and asks their phone for a closer look at the wire: the same loop. The hub blocks the street, reroutes every volunteer around it, and warns everyone nearby. <break time="0.6s" /> The whole night, every alert, check-in and photo, used less data than one photo from one phone.
```

## 5. Close (2:40–3:00)

On screen: a title card: MuZip, and the three models.

```text
MuZip. <break time="0.3s" /> Muse Spark decides what's worth seeing. SAM three point one finds it. Your phone keeps the rest. <break time="0.6s" /> Pictures, at the speed of a text message.
```

## Still open

- Section 2 needs the real WhatsApp recording (MuZip from your account) of the extinguisher.
- Section 4's "the hub signs it" is literal; "really from the city" would not be.
- A same-photo, two-questions shot (two different sets of close-ups) would prove the compression is semantic; not
  built yet.
