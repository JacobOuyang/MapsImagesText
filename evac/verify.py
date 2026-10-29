"""Is a drawing worth sending? Two checks by Muse, both marked right or wrong, no judging of quality.

  blind(frames, meaning)            the checker sees only the drawings and says what they show; a text-only call
                                    says whether that is `meaning`. A traced CPR drawing read as "a maze" fails here.
  answers(words, frames, question)  the checker gets the instruction's words and the drawings and answers a
                                    multiple-choice question the words don't answer (where something is, which way it
                                    points); right or wrong against the answer the question came with.

A drawing is sent only if it passes both: useful (it shows something on its own) and verifiable (it shows the
right thing). The checks run on a different model from the one that made the drawing's plan (Muse Spark), so a
model doesn't grade its own work: Muse Wonder (`muse-wonder-1.0`, also on the Meta Model API; VERIFY_MODEL in .env).
"""
import base64
import io
import os

from PIL import Image

from . import llm, sketch

MODEL = os.environ.get("VERIFY_MODEL", "muse-wonder-1.0")

SEE = ("These are drawings made of text characters (if there are several, they are frames shown one after the other). "
       'Looking only at the drawings, what do they show? Reply as JSON: {"shows": "", "confidence": "low, medium or high"}')
SAME = ("Drawings were meant to show: %r. A viewer, seeing only the drawings, said they show: %r. Is that the same "
        'thing or action (a broader or plainer description counts)? Reply as JSON: {"same": true or false}')
ASK = ("Instruction: %s\nThe drawings below illustrate it (made of text characters; frames shown one after the other). "
       "Answer from the drawings: %s\n%s\nReply as JSON: {\"answer\": one letter}")


def image_url(img, side=1024):
    """A picture as a JPEG data URL for a model."""
    im = img.convert("RGB").copy()
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def stack(frames):
    """Frames (lists of rows) rendered in Menlo one under the other, as a PNG data URL."""
    ims = [sketch.picture_image(r) for r in frames if r]
    if not ims:
        ims = [sketch.picture_image([" "])]
    w = max(i.width for i in ims)
    out = Image.new("RGB", (w, sum(i.height for i in ims) + 24 * len(ims)), "white")
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height + 24
    out.thumbnail((2000, 4000))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def blind(frames, meaning):
    """-> (what Muse saw, whether it is `meaning`)."""
    try:
        seen = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": SEE},
                                                           {"type": "image_url", "image_url": {"url": stack(frames)}}]}],
                             max_tokens=1500, model=MODEL)
        said = str(seen.get("shows") or "")
        same = bool(llm.chat_json([{"role": "user", "content": SAME % (meaning, said)}], max_tokens=800, model=MODEL).get("same"))
        return said, same
    except Exception as ex:
        return "(check failed: %s)" % type(ex).__name__, False


def answers(words, frames, question):
    """question: {"q", "choices", "answer" (index)}. -> (the choice Muse picked or None, right)."""
    q = question or {}
    choices = [str(c) for c in q.get("choices") or []][:4]
    try:
        right = int(q.get("answer"))
    except (TypeError, ValueError):
        return None, False
    if not q.get("q") or len(choices) < 2 or not 0 <= right < len(choices):
        return None, False
    letters = "ABCD"
    try:
        got = llm.chat_json([{"role": "user", "content": [
            {"type": "text", "text": ASK % (words, q["q"], "\n".join("%s) %s" % (letters[i], c) for i, c in enumerate(choices)))},
            {"type": "image_url", "image_url": {"url": stack(frames)}}]}], max_tokens=1500, model=MODEL)
    except Exception:
        return None, False
    g = str(got.get("answer") or "").strip().upper()[:1]
    pick = letters.index(g) if g and g in letters[:len(choices)] else None
    return (choices[pick] if pick is not None else None), pick == right


COULD = ("%s It is meant to illustrate this instruction: %r. %sCould it be that? Judge whether it plausibly shows it, not "
         "whether it is detailed or pretty. Reply as JSON: {\"could_be\": true or false, \"why\": \"a few words\"}")


def could_be(instruction, image=None, frames=None):
    """The lenient check the user settled on: could this picture (or this deliberately blurry text drawing) be the
    instruction? -> (yes, why). Run on the checker model, not the one that made the picture."""
    if frames is not None:
        what, url = "This is a small drawing made of text characters, so it is meant to be rough and blurry.", stack(frames)
        hint = "Read it as a rough silhouette: darker characters are ink, ▓ marks the key part. "
    else:
        what, url, hint = "This is a simple instruction picture.", image_url(image), ""
    try:
        d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": COULD % (what, instruction, hint)},
                                                        {"type": "image_url", "image_url": {"url": url}}]}],
                          max_tokens=1200, model=MODEL)
        return bool(d.get("could_be")), str(d.get("why") or "")
    except Exception as ex:
        return False, "check failed: %s" % type(ex).__name__


PICTURE = ("These two small black-and-white pictures (before on the left, after on the right) are meant to show a step: "
           "the %s goes from %s to %s. Answer three questions about them, strictly:\n"
           "1. recognisable: without being told, could you tell what the object is?\n"
           "2. universal: does this kind of object look about the same and work the same way on most makes and models "
           "(a pipe valve handle, a breaker toggle, a plug, and standardised safety devices such as a fire extinguisher, "
           "an epinephrine pen or an AED: yes; a stove knob, a car's controls, an appliance panel: no)?\n"
           "3. clear: is the change from before to after visible at a glance?\n"
           'Reply as JSON: {"recognisable": true or false, "universal": true or false, "clear": true or false, '
           '"why": "a few words"}')


def picture_checks(pair, item, before, after):
    """The checks learned from people labelling 19 step pictures (they rejected stove knobs and car controls:
    "off position isn't universal", "different cars, different mechanisms"; and objects they couldn't name).
    -> {"recognisable", "universal", "clear", "ok", "why"}. Run on the checker model."""
    try:
        d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": PICTURE % (item, before, after)},
                                                        {"type": "image_url", "image_url": {"url": image_url(pair)}}]}],
                          max_tokens=1200, model=MODEL)
    except Exception as ex:
        return {"recognisable": False, "universal": False, "clear": False, "ok": False, "why": "check failed: %s" % type(ex).__name__}
    out = {k: bool(d.get(k)) for k in ("recognisable", "universal", "clear")}
    # "recognisable" is reported but not required: at 128 px it rejected a breaker, a toilet valve and an EpiPen that
    # people found useful. "universal" matched them (stove knobs, car controls rejected) and "clear" caught no-change.
    out["ok"] = out["universal"] and out["clear"]
    out["why"] = str(d.get("why") or "")
    return out
