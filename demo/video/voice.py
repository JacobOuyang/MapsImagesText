"""The voice-over for one section of the video, from ElevenLabs: the section's ```text block in demo/SCRIPT.md, read
by one of your ElevenLabs voices, saved to private/voice/ (git-ignored). Only for making the video: MuZip itself
uses only the Meta Model API.

    .venv/bin/python demo/video/voice.py 2                      # section 2 -> private/voice/02_<name>.mp3
    .venv/bin/python demo/video/voice.py 2 --model eleven_v3    # another model (v3 ignores <break> tags: they're dropped)
    .venv/bin/python demo/video/voice.py --list                 # your voices and the models your key can use

Needs ELEVENLABS_API_KEY in .env. The voice is found by name (VOICE, "Jeremy" unless --voice says otherwise).
"""
import argparse
import os
import re
import sys

import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from evac import llm  # noqa: E402  (loads .env)

API = "https://api.elevenlabs.io"
VOICE, MODEL = "Jeremy", "eleven_multilingual_v2"


def key():
    llm._load_dotenv()
    k = os.environ.get("ELEVENLABS_API_KEY")
    if not k:
        sys.exit("Put ELEVENLABS_API_KEY in .env first.")
    return {"xi-api-key": k}


def voices(h):
    r = requests.get(API + "/v1/voices", headers=h, timeout=30)
    r.raise_for_status()
    return r.json()["voices"]


def section(n):
    """(slug, voice-over text) of section n of demo/SCRIPT.md."""
    s = open(os.path.join(ROOT, "demo", "SCRIPT.md")).read()
    m = re.search(r"^## %d\. ([^\n(]+).*?```text\n(.*?)```" % n, s, re.M | re.S)
    if not m:
        sys.exit("No voice-over for section %d in demo/SCRIPT.md." % n)
    slug = re.sub(r"[^a-z0-9]+", "_", m.group(1).strip().lower()).strip("_")
    return slug, m.group(2).strip()


def main():
    a = argparse.ArgumentParser()
    a.add_argument("section", nargs="?", type=int)
    a.add_argument("--voice", default=VOICE)
    a.add_argument("--model", default=MODEL)
    a.add_argument("--list", action="store_true")
    args = a.parse_args()
    h = key()
    if args.list:
        for v in voices(h):
            print("voice:", v["name"], v["voice_id"])
        r = requests.get(API + "/v1/models", headers=h, timeout=30)
        for m in r.json() if r.ok else []:
            print("model:", m["model_id"], "-", m.get("name", ""))
        return
    if args.section is None:
        sys.exit(__doc__)
    match = [v for v in voices(h) if v["name"].lower().startswith(args.voice.lower())]
    if not match:
        sys.exit("No voice named %r in your ElevenLabs voices (--list shows them)." % args.voice)
    slug, text = section(args.section)
    if args.model == "eleven_v3":
        text = re.sub(r"\s*<break[^>]*/>\s*", " ", text)
    r = requests.post(API + "/v1/text-to-speech/%s?output_format=mp3_44100_128" % match[0]["voice_id"], headers=h, timeout=180,
                      json={"text": text, "model_id": args.model})
    if not r.ok:
        sys.exit("ElevenLabs refused it: %s %s" % (r.status_code, r.text[:300]))
    out = os.path.join(ROOT, "private", "voice", "%02d_%s.mp3" % (args.section, slug))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(r.content)
    print("%s: %s, %s, %d KB" % (out, match[0]["name"], args.model, len(r.content) // 1000))


if __name__ == "__main__":
    main()
