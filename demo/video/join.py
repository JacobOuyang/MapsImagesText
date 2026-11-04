"""The finished video: every section's 1.1x version one after another, with one continuous sound track.

    .venv/bin/python demo/video/join.py            # -> private/video/all_1.1x.mp4

Each section's voice is read out of its video (afconvert), padded with silence to that section's picture length, and
the pieces are written as one track (WAV, then AAC); demo/video/join.swift then joins the pictures and lays that WAV under them. (Left
as gaps in the track, the quiet stretches were skipped by some players and the voice drifted ahead of the picture.)
"""
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
SECTIONS = ["01_intro", "02_the_side_by_side", "03_how_it_works", "04_any_photo", "05_real_use_cases", "05b_hiker", "06_close"]
RATE = 44100
JOIN = os.path.join(ROOT, "out", "demo", "join")


def picture_seconds(path):
    """The video track's length, from the frames the section was rendered with, sped up 1.1x."""
    name = os.path.basename(path).replace("_1.1x.mp4", "")
    frames = os.path.join(ROOT, "out", "demo", "frames", name, "frames.txt")
    return sum(float(line.split(" ", 1)[0]) for line in open(frames) if line.strip()) / 1.1


def main():
    vids = [os.path.join(ROOT, "private", "video", s + "_1.1x.mp4") for s in SECTIONS]
    pieces = []
    d = os.path.join(ROOT, "out", "demo", "join_sound")  # kept (out/ is git-ignored)
    os.makedirs(d, exist_ok=True)
    if True:
        for v in vids:
            w = os.path.join(d, "a.wav")
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@%d" % RATE, "-c", "1", v, w], check=True)
            with wave.open(w) as f:
                pcm = np.frombuffer(f.readframes(f.getnframes()), np.int16)
            n = int(round(picture_seconds(v) * RATE))
            pieces.append(np.concatenate([pcm[:n], np.zeros(max(0, n - len(pcm)), np.int16)]))
            print("%-22s picture %6.2f s, voice %6.2f s" % (os.path.basename(v), n / RATE, len(pcm) / RATE))
        out_wav = os.path.join(d, "all.wav")
        with wave.open(out_wav, "wb") as f:
            f.setnchannels(1), f.setsampwidth(2), f.setframerate(RATE)
            f.writeframes(np.concatenate(pieces).tobytes())
        m4a = os.path.join(d, "all.m4a")  # AAC: the joiner's composition refused a WAV track (-12780)
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "192000", out_wav, m4a], check=True)
        if not os.path.exists(JOIN):
            subprocess.run(["swiftc", "-O", os.path.join(os.path.dirname(__file__), "join.swift"), "-o", JOIN], check=True)
        subprocess.run([JOIN, os.path.join(ROOT, "private", "video", "all_1.1x.mp4")] + vids + ["--audio", m4a], check=True)


if __name__ == "__main__":
    sys.exit(main())
