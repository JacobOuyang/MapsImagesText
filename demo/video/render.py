"""Render one section of the video: a page drawn as a function of time (render(t) after it loads; ?t=seconds for a
single frame), captured frame by frame, encoded with demo/video/encode.swift (AVFoundation; compiled on first use)
and the voice-over added.

    .venv/bin/python sim/server.py                       # in another terminal (or the "simulator" launch config)
    .venv/bin/python demo/video/render.py /video/intro.html 17.2 private/voice/01_intro.mp3 private/video/01_intro.mp4

Each worker keeps one headless Chrome open and drives it over its DevTools pipe: the viewport is set to exactly
1920 x 1080, then for every frame the page's render(t) runs and a screenshot is taken. (Starting a Chrome per frame
took about 1.5 s a frame and sometimes shot with the window 63 px short, a flicker at the bottom.) Frames go to
out/demo/frames/<name>/; runs of identical frames become one frame held longer. The voice-over and the finished
video stay in private/ (git-ignored).
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
URL = "http://127.0.0.1:8765"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ENCODER = os.path.join(ROOT, "out", "demo", "encode")
FPS, W, H, WORKERS = 30, 1920, 1080, 4


class Browser:
    """One headless Chrome on a page, driven over --remote-debugging-pipe (no websocket library needed: the
    protocol's JSON messages go over fds 3 and 4, each ending in a NUL byte)."""

    def __init__(self, url):
        self.profile = tempfile.mkdtemp(prefix="chrome-")
        to_chrome, self.out = os.pipe()
        self.inp, from_chrome = os.pipe()
        os.set_inheritable(to_chrome, True)
        os.set_inheritable(from_chrome, True)

        def fds():  # in the child: Chrome reads commands from fd 3 and writes replies to fd 4
            os.dup2(to_chrome, 3)
            os.dup2(from_chrome, 4)
        self.proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-pipe",
                                      "--user-data-dir=" + self.profile, "about:blank"],
                                     preexec_fn=fds, close_fds=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_chrome)
        os.close(from_chrome)
        self.n, self.buf = 0, b""
        target = self.call("Target.createTarget", url="about:blank")["targetId"]
        self.session = self.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False, session=True)
        self.call("Page.navigate", url=url, session=True)
        for _ in range(400):  # the scene sets document.title to "ready" once its pictures are loaded
            if self.eval("document.title") == "ready":
                return
            time.sleep(0.05)
        raise RuntimeError("the page never got ready: " + url)

    def call(self, method, session=False, **params):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params}
        if session:
            msg["sessionId"] = self.session
        os.write(self.out, json.dumps(msg).encode() + b"\0")
        while True:
            while b"\0" not in self.buf:
                chunk = os.read(self.inp, 1 << 20)
                if not chunk:
                    raise RuntimeError("Chrome closed the pipe")
                self.buf += chunk
            line, self.buf = self.buf.split(b"\0", 1)
            reply = json.loads(line)
            if reply.get("id") == self.n:
                if "error" in reply:
                    raise RuntimeError("%s: %s" % (method, reply["error"]))
                return reply.get("result", {})

    def eval(self, js):
        return self.call("Runtime.evaluate", expression=js, returnByValue=True, session=True).get("result", {}).get("value")

    def frame(self, t, path):
        self.eval("render(%.5f)" % t)
        shot = self.call("Page.captureScreenshot", format="png", clip={"x": 0, "y": 0, "width": W, "height": H, "scale": 1}, session=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))

    def close(self):
        self.proc.kill()
        for fd in (self.inp, self.out):
            os.close(fd)
        shutil.rmtree(self.profile, ignore_errors=True)


def main(page, seconds, voice, out):
    name = os.path.splitext(os.path.basename(out))[0]
    frames = os.path.join(ROOT, "out", "demo", "frames", name)
    shutil.rmtree(frames, ignore_errors=True)
    os.makedirs(frames)
    n = int(round(seconds * FPS))
    paths = [os.path.join(frames, "%05d.png" % i) for i in range(n)]
    started = time.time()

    def work(k):  # worker k renders one stretch of frames: two Chromes antialias a few edge pixels differently, and
        b = Browser(URL + page)  # handing out frames in turn made the logo shimmer every 4 frames
        try:
            for i in range(k * n // WORKERS, (k + 1) * n // WORKERS):
                b.frame(i / FPS, paths[i])
        finally:
            b.close()
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(work, range(WORKERS)))
    held, last = [], None  # identical frames in a row -> one frame, held
    for p in paths:
        h = hashlib.md5(open(p, "rb").read()).hexdigest()
        if held and h == last:
            held[-1][0] += 1 / FPS
        else:
            held.append([1 / FPS, p])
        last = h
    listing = os.path.join(frames, "frames.txt")
    with open(listing, "w") as f:
        f.writelines("%.5f %s\n" % (secs, p) for secs, p in held)
    if not os.path.exists(ENCODER):
        subprocess.run(["swiftc", "-O", os.path.join(os.path.dirname(__file__), "encode.swift"), "-o", ENCODER], check=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    subprocess.run([ENCODER, listing, out, str(W), str(H)] + ([voice] if voice else []), check=True)
    print("%d frames, %d distinct, captured in %.0f s" % (n, len(held), time.time() - started))


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4])
