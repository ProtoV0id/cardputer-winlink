"""Exercise the reply UI and Pat adapter over loopback only; never transmit."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest

from backend import PatBackend
from tests.test_local_api import Handler
from winlink_gui import UI


class ReplyHandler(Handler):
    posted = []

    def do_GET(self):
        if self.path == "/api/mailbox/in/A123":
            self.respond({"MID": "A123", "From": {"Addr": "W1AW"},
                          "To": [{"Addr": "N0CALL"}], "Subject": "Field report", "Body": "Original"})
        else:
            super().do_GET()


class ReplyLocalAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ReplyHandler.posted = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ReplyHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_reply_reaches_outbox_only_after_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "pat.json"
            config.write_text('{"mycall":"N0CALL"}', encoding="utf-8")
            pat = PatBackend("fake-pat", config)
            pat._base = "http://127.0.0.1:" + str(self.server.server_port)
            pat._start = lambda: None
            ui = UI(pat, radio_config_path=root / "radio.json", appearance_config_path=root / "appearance.json",
                    contacts_path=root / "contacts.json", gateway_profiles_path=root / "gateways.json")

            def finish():
                for _ in range(200):
                    ui.poll()
                    if ui.mode != "busy":
                        return
                    time.sleep(.01)
                self.fail("worker did not complete")

            ui.open_message("in", {"id": "A123"})
            finish()
            ui.input("ENTER")  # message actions
            ui.input("ENTER")  # reply
            self.assertEqual(ui.draft, {"to": "W1AW", "subject": "Re: Field report", "body": ""})
            self.assertEqual(ReplyHandler.posted, [])
            ui.draft["body"] = "Reply text"
            ui.review_draft()
            ui.input("ENTER")  # review -> queue confirmation
            self.assertEqual(ReplyHandler.posted, [])
            ui.input("ENTER")  # explicitly queue
            finish()
            self.assertEqual(len(ReplyHandler.posted), 1)
            path, payload = ReplyHandler.posted[0]
            self.assertEqual(path, "/api/mailbox/out")
            self.assertEqual(payload["to"], ["W1AW"])
            self.assertEqual(payload["subject"], ["Re: Field report"])
            self.assertEqual(payload["body"], ["Reply text"])
            pat.close()


if __name__ == "__main__":
    unittest.main()
