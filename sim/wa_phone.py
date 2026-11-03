"""A test phone for the WhatsApp channel: it sends messages to the bridge as your WhatsApp number, plays the offline
kit for photos (it keeps the original and answers every ZOOM by cropping it), and prints the replies, so a whole
exchange runs without anyone pressing Enter.

    .venv/bin/python sim/wa_phone.py "help"
    .venv/bin/python sim/wa_phone.py "Is it going to rain here?" --at 40.6776,-74.0086
    .venv/bin/python sim/wa_phone.py --photo sim/samples/spiders/black_widow.jpg "Is this a black widow?"
    .venv/bin/python sim/wa_phone.py --via whatsapp "help"

--via sim (default): posts to the bridge what Twilio would for a message from you, signed with your Twilio token. The
    replies are real: Twilio delivers them to your WhatsApp. Your own side of the chat won't show what this sent.
--via whatsapp: fills in the chat in WhatsApp Desktop without bringing it forward, then brings it forward for about a
    second to press Return and gives the screen back to the app you were in (macOS only sends a key press to the
    front app). The app running this needs Accessibility permission. The message really comes from your account.
Either way the replies are read from the bridge's log in the local store (evac/whatsapp.py records every message).

.env: MY_WHATSAPP (your number, e.g. +15551234567; default: the last WhatsApp user the bridge saw), plus the bridge's
TWILIO_AUTH_TOKEN and PUBLIC_URL.
"""
import argparse
import base64
import hashlib
import hmac
import os
import random
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import requests  # noqa: E402
from PIL import Image  # noqa: E402

from evac import closer, codes, llm, smsimage, store  # noqa: E402

SANDBOX = "whatsapp:+14155238886"
# Wait for the filled-in chat, bring WhatsApp forward just long enough to press Return, then give the screen back.
PRESS_RETURN = """
delay 1.5
tell application "System Events" to set previous to name of first application process whose frontmost is true
tell application "WhatsApp" to activate
delay 0.6
tell application "System Events" to keystroke return
delay 0.4
if previous is not "WhatsApp" then tell application previous to activate
"""
HELPER = "/tmp/muzip-%d" % os.getuid()  # where MuZip.app (sim/make_muzip_app.sh) takes requests to press Return
llm._load_dotenv()


def me():
    n = os.environ.get("MY_WHATSAPP", "").strip()
    if n:
        return n if n.startswith("whatsapp:") else "whatsapp:+" + n.lstrip("+")
    users = store.find("wa_user", limit=1)
    if users:
        return users[0]["who"]
    raise RuntimeError("Set MY_WHATSAPP in .env (your WhatsApp number), or message the bridge once from your phone.")


def press_return():
    """Press Return in WhatsApp: through MuZip.app when it's running (it holds the Accessibility permission and stays out
    of Documents, so macOS needn't grant it the project folder), else from this process (which then needs the permission)."""
    alive = os.path.join(HELPER, "alive")
    if os.path.exists(alive) and time.time() - os.path.getmtime(alive) < 3:
        done = os.path.join(HELPER, "done")
        if os.path.exists(done):
            os.remove(done)
        open(os.path.join(HELPER, "press"), "w").close()
        for _ in range(100):
            if os.path.exists(done):
                with open(done) as f:
                    answer = f.read().strip()
                os.remove(done)
                return None if answer == "ok" else answer or "failed"
            time.sleep(0.1)
        return "MuZip.app didn't answer"
    r = subprocess.run(["osascript", "-e", PRESS_RETURN], capture_output=True, text=True)
    return r.stderr.strip() or "failed" if r.returncode else None


def send(text, who, via, bridge, at=None):
    """One message from you to the Muse number."""
    if via == "whatsapp":
        number = SANDBOX.split("+")[1]
        subprocess.run(["open", "-g", "whatsapp://send?phone=%s&text=%s" % (number, quote(text))], check=True)  # -g: stay behind
        error = press_return()
        if error:
            raise RuntimeError("Couldn't press Return in WhatsApp (%s). Open out/MuZip.app and give it Accessibility permission "
                               "(System Settings > Privacy & Security > Accessibility), or use sim." % error)
        return
    params = {"From": who, "To": SANDBOX, "Body": text, "NumMedia": "0", "MessageSid": "SMtest%08d" % random.randrange(10 ** 8)}
    if at:
        params.update(Latitude=str(at[0]), Longitude=str(at[1]))
    url = os.environ.get("PUBLIC_URL", "").rstrip("/") + "/whatsapp"
    data = url + "".join(k + params[k] for k in sorted(params))
    sig = base64.b64encode(hmac.new(os.environ.get("TWILIO_AUTH_TOKEN", "").encode(), data.encode(), hashlib.sha1).digest()).decode()
    r = requests.post(bridge + "/whatsapp", data=params, headers={"X-Twilio-Signature": sig}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError("The bridge refused it (%d %s). Is sim/whatsapp_server.py running with the same .env?" % (r.status_code, r.text[:80]))


def replies(who, since, quiet=6, timeout=120):
    """The bridge's replies to you after `since`, once it has gone quiet for `quiet` seconds (or `timeout`)."""
    start, last, got = time.time(), None, []
    while time.time() - start < timeout:
        new = sorted((m for m in store.find("wa_msg", since=since, limit=500) if m["who"] == who and m["meta"]["dir"] == "out"),
                     key=lambda m: m["created"])
        if len(new) > len(got):
            got, last = new, time.time()
        if last and time.time() - last > quiet:
            break
        time.sleep(1)
    return [m["meta"]["body"] for m in got]


def show(prefix, text):
    shown = codes.CODE.sub(lambda c: c.group(0)[:16] + "…", text)
    print("%s %s" % (prefix, shown.replace("\n", "\n" + " " * (len(prefix) + 1))), flush=True)


def converse(text, who, via, bridge, at=None):
    t = time.time() - 1
    show("you  >", text)
    send(text, who, via, bridge, at)
    out = replies(who, t)
    for r in out:
        show("muse >", r)
    if not out:
        print("muse > (no reply in time: is the bridge running, and does Twilio reach it?)")
    return out


def photo(path, question, who, via, bridge):
    """The kit: send the photo as a 128 px grey code with the question, and answer every ZOOM from the original."""
    img = Image.open(path).convert("RGB")
    pic = random.choice("abcdefghjkmnpqrstuvwxyz") + random.choice("23456789abcdefghjkmnpqrstuvwxyz")
    message = question + "\n" + codes.text(codes.parts("P", smsimage.webp(img), pic))
    print("(the photo goes out as %d texts of code; the original stays here)" % codes.sms_parts(message))
    for _ in range(closer.MAX_VIEWS + 1):
        out = converse(message, who, via, bridge)
        ask = next((codes.ZOOM.search(r) for r in reversed(out) if codes.ZOOM.search(r)), None)
        if not ask or ask.group(1) != pic:
            return
        message = codes.answer_zoom(img, ask, pic)[0]
        o = codes.zoom_options(ask)
        print("(the kit crops that part of the original%s%s: %d texts)" % (" in colour" if o["colour"] else "", " in high detail" if o["side"] else "",
                                                                         codes.sms_parts(message)))


def main():
    try:
        run()
    except RuntimeError as ex:
        sys.exit(str(ex))


def run():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("text", nargs="?", default="help")
    a.add_argument("--photo")
    a.add_argument("--at", help="share a location first: lat,lon")
    a.add_argument("--via", choices=["sim", "whatsapp"], default="sim")
    a.add_argument("--bridge", default="http://127.0.0.1:%s" % os.environ.get("WHATSAPP_PORT", 8770))
    args = a.parse_args()
    who = me()
    if args.at:
        at = tuple(float(v) for v in args.at.split(","))
        if args.via == "whatsapp":
            sys.exit("A location can only be shared from the phone itself; use --via sim for --at.")
        converse("", who, args.via, args.bridge, at)
    if args.photo:
        photo(args.photo, args.text, who, args.via, args.bridge)
    else:
        converse(args.text, who, args.via, args.bridge)


if __name__ == "__main__":
    main()
