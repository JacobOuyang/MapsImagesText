"""One entry point for a message from a phone. Codes come first: a close-up coming back (a Z code answering Muse's
ZOOM) goes on to its open question (evac/closer.py), and a photo (a P code: the 128 px picture) with a question starts
one. RESEND re-sends the parts of a message the phone lost. Anything else is a plain question for Muse Spark, answered
in a short text.

    router.handle("Is this a black widow?\\n#Pab.1/10:...#", who="+15550140")
"""
import io
import time

from PIL import Image

from . import closer, codes, llm

SYSTEM = ("You answer questions by text message: plain words, under 500 characters, no greeting or sign-off. To ask "
          "about something they can see, they can send a photo with their question.")


def _quick(text):
    return {"text": text, "sms": text, "log": [], "seconds": 0.0}


def handle(message, you=None, image=None, who=None):
    """One message from a person (`who`: their number, when known). `you` and `image` are kept for older callers and
    ignored: photos arrive as codes."""
    t = time.time()
    message = (message or "").strip()
    if not message:
        return _quick("Send a question, or a photo with your question.")
    again = codes.resend(message)
    if again is not None:
        return _quick(codes.text(again) if again else "Nothing to resend: that message is no longer kept.")
    found = codes.decode(message)
    log = ["incomplete codes: %s" % found["missing"]] if found["missing"] else []
    z = next((c for c in found["complete"] if c["kind"] == "Z"), None)
    if z is not None:
        parent, box, data = codes.read_zoom(z["data"])
        went_on = closer.receive(who, parent, box, data)
        if went_on:
            return dict(_quick(went_on[1]), inquiry=went_on[0], log=log + ["close-up of %s for question %s (%d bytes)" % (parent, went_on[0], len(data))],
                        seconds=round(time.time() - t, 1))
        return dict(_quick("That close-up doesn't answer any open question."), log=log)
    pic = next((c for c in found["complete"] if c["kind"] == "P"), None)
    if pic and not llm.available():
        return dict(_quick("I can't look at photos right now (no model key)."), log=log)
    if pic:  # a question about a photo the phone keeps: look closer as needed
        inq, text = closer.start(who, Image.open(io.BytesIO(pic["data"])), pic["id"], codes.plain(message) or "What is this?",
                                 texts=codes.sms_parts(message))
        return dict(_quick(text), inquiry=inq, log=log + ["question %s about photo %s" % (inq, pic["id"])], seconds=round(time.time() - t, 1))
    if found["missing"]:
        return dict(_quick("Some parts of that photo didn't arrive (%s); send RESEND and the picture's id." % found["missing"]), log=log)
    if not llm.available():
        return _quick("Send a photo with your question.")
    try:
        text = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": message}])
    except Exception as ex:
        text, log = "I couldn't answer that right now.", log + ["model error: %s" % str(ex)[:120]]
    return dict(_quick(text.strip()), log=log, seconds=round(time.time() - t, 1))
