import json
from pathlib import Path
import tempfile
import unittest

from backend import PatBackend, PatError, PatTimeout


class SimulatedPat(PatBackend):
    def __init__(self, path):
        super().__init__(executable="fake-pat", config_path=path)
        self.calls = []

    def _request(self, path, data=None, timeout=10, ensure=True):
        self.calls.append((path, data))
        if path == "/api/mailbox/in":
            return json.dumps([{"MID": "ABC123", "From": {"Addr": "TEST"}, "To": [{"Addr": "N0CALL"}], "Subject": "Hello", "Unread": True}])
        if path == "/api/mailbox/in/ABC123":
            return json.dumps({"MID": "ABC123", "Body": "first\nsecond", "Files": [{"Name": "file"}]})
        if path == "/api/connect":
            return '{"NumReceived":2}'
        return "Message posted"


class PatBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / "config.json"
        self.config.write_text(json.dumps({"mycall": "N0CALL", "locator": "DM33", "secure_login_password": "secret", "listen": ["ardop"], "custom": {"keep": 1}}))
        self.backend = SimulatedPat(self.config)

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def test_settings_preserve_other_fields_and_password(self):
        self.backend.save_settings("n0call", "DM34", None)
        result = json.loads(self.config.read_text())
        self.assertEqual(result["secure_login_password"], "secret")
        self.assertEqual(result["custom"], {"keep": 1})
        self.assertEqual(result["listen"], ["ardop"])
        self.assertEqual(self.backend.get_settings(), {"callsign": "N0CALL", "locator": "DM34", "has_password": True})
        self.backend.save_settings("n0call", "DM34", "")
        self.assertFalse(self.backend.get_settings()["has_password"])

    def test_mailbox_body_and_attachments(self):
        message = self.backend.list_messages("inbox")[0]
        self.assertEqual((message["id"], message["from"], message["to"]), ("ABC123", "TEST", "N0CALL"))
        message = self.backend.read_message("in", "ABC123")
        self.assertEqual(message["body"], "first\nsecond")
        self.assertEqual(len(message["attachments"]), 1)
        with self.assertRaises(PatError):
            self.backend.read_message("in", "../invalid")

    def test_queue_and_session_use_pats_correct_endpoints(self):
        self.backend.queue_message("someone@example.org", "Test", "Hello")
        path, payload = self.backend.calls[-1]
        self.assertEqual(path, "/api/mailbox/out")
        self.assertEqual(payload["body"], "Hello")
        self.assertTrue(payload["date"].endswith("Z"))
        self.assertIn("2 message", self.backend.connect_telnet())
        self.assertTrue(self.backend.calls[-1][1]["url"].startswith("telnet://"))

    def test_invalid_settings_leave_file_untouched(self):
        before = self.config.read_bytes()
        with self.assertRaises(PatError):
            self.backend.save_settings("../BAD", "", "")
        self.assertEqual(self.config.read_bytes(), before)

    def test_session_timeout_does_not_claim_delivery_or_retry(self):
        calls = []
        def timed_out(path, *args, **kwargs):
            calls.append(path)
            raise PatTimeout("timeout")
        self.backend._request = timed_out
        with self.assertRaisesRegex(PatError, "delivery status may be unknown"):
            self.backend.connect_telnet()
        self.assertEqual(calls, ["/api/connect"])


if __name__ == "__main__":
    unittest.main()
