"""The WhatsApp bridge's own small server: the only thing a tunnel should expose to the internet.

    .venv/bin/python sim/whatsapp_server.py          # http://127.0.0.1:8770 (.claude/launch.json: "whatsapp")

POST /whatsapp            Twilio's webhook for incoming messages (checked against X-Twilio-Signature)
GET  /                    "ok", to check the tunnel reaches it

Everything else (the simulator, the store) stays on 127.0.0.1:8765. See evac/whatsapp.py for what the
bridge does and the .env settings it needs.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from evac import whatsapp  # noqa: E402

PORT = int(os.environ.get("WHATSAPP_PORT", 8770))
EMPTY = b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


class Handler(BaseHTTPRequestHandler):
    def send(self, data, kind, code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            return self.send(b"ok", "text/plain")
        self.send(b"not found", "text/plain", 404)

    def do_POST(self):
        if self.path != "/whatsapp":
            return self.send(b"not found", "text/plain", 404)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        params = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        url = os.environ.get("PUBLIC_URL", "").rstrip("/") + "/whatsapp"  # the address Twilio signed
        if not whatsapp.signature_ok(url, params, self.headers.get("X-Twilio-Signature")):
            return self.send(b"bad signature", "text/plain", 403)
        whatsapp.incoming(params)
        self.send(EMPTY, "text/xml")

    def log_message(self, fmt, *args):  # one line per request, without the query (phone numbers stay out of logs)
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))


if __name__ == "__main__":
    print("WhatsApp bridge on http://127.0.0.1:%d (expose it with a tunnel; set PUBLIC_URL to its https address)" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
