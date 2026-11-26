"""Exercise the adapter against a local Pat-shaped HTTP server, never CMS."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from backend import PatBackend, PatError


class Handler(BaseHTTPRequestHandler):
    posted = []

    def log_message(self, *args):
        pass

    def respond(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/mailbox/in":
            self.respond([{"MID": "A123", "Subject": "Welcome", "From": {"Addr": "SYSTEM"}}])
        elif self.path == "/api/mailbox/in/A123":
            self.respond({"MID": "A123", "Subject": "Welcome", "Body": "Example only"})
        else:
            self.respond({"error": "not found"}, 404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        self.posted.append((self.path, parse_qs(raw.decode())))
        self.respond({"NumReceived": 1} if self.path == "/api/connect" else "OK")


class LocalAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.posted = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_full_local_http_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"mycall":"N0CALL"}', encoding="utf-8")
            pat = PatBackend("fake-pat", config)
            pat._base = "http://127.0.0.1:" + str(self.server.server_port)
            pat._start = lambda: None
            self.assertEqual(pat.list_messages("in")[0]["subject"], "Welcome")
            self.assertEqual(pat.read_message("in", "A123")["body"], "Example only")
            pat.queue_message("test@example.org", "Subject", "Body with & symbols")
            self.assertIn("Received 1", pat.connect_telnet())
            self.assertEqual(Handler.posted[-2][1]["body"], ["Body with & symbols"])
            self.assertEqual(Handler.posted[-1][1]["url"], ["telnet://N0CALL:CMSTelnet@cms.winlink.org:8772/wl2k"])
            with self.assertRaises(PatError):
                pat.read_message("in", "MISSING")


if __name__ == "__main__":
    unittest.main()
