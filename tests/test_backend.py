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

    def test_winlink_callsign_and_full_address_are_forwarded_to_pat(self):
        # Pat handles bare Winlink callsigns as well as fully qualified
        # addresses. Do not invent an SMTP prefix or change the recipient.
        for address in ("W1AW", "W1AW@winlink.org"):
            with self.subTest(address=address):
                self.backend.queue_message(address, "Test", "Hello")
                path, data = self.backend.calls[-1]
                self.assertEqual(path, "/api/mailbox/out")
                self.assertEqual(data["to"], address)

    def test_self_address_is_blocked_with_instructions_without_queuing(self):
        for address in ("N0CALL", "n0call", "N0CALL@WINLINK.ORG",
                        "SMTP:N0CALL@winlink.org", "TEST; n0call@winlink.org"):
            with self.subTest(address=address):
                before = len(self.backend.calls)
                with self.assertRaisesRegex(PatError, "TEST@winlink.org"):
                    self.backend.queue_message(address, "Subject", "Body")
                self.assertEqual(len(self.backend.calls), before)

    def test_other_address_and_distinct_ssid_remain_allowed(self):
        # Registered SSID accounts can be separate; don't block solely because
        # their base callsign is the same as ours.
        for address in ("N0CALL-3", "N0CALL-3@winlink.org", "n0call@example.org", "TEST"):
            with self.subTest(address=address):
                self.backend.queue_message(address, "Subject", "Body")
                self.assertEqual(self.backend.calls[-1][1]["to"], address)

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

    def test_packet_session_is_explicit_and_uses_local_agwpe(self):
        config = json.loads(self.config.read_text())
        config["agwpe"] = {"addr": "example.invalid:8000", "radio_port": 2}
        self.config.write_text(json.dumps(config))
        original = self.config.read_bytes()
        self.backend.queue_message("TEST", "Subject", "Text")
        self.assertEqual(self.backend.calls[-1][0], "/api/mailbox/out")
        self.assertIsNone(self.backend._agw_port)
        self.assertIn("Received 2", self.backend.connect_packet(" w1aw-10 ", 54321))
        self.assertEqual(self.backend.calls[-1],
                         ("/api/connect", {"url": "ax25+agwpe:///W1AW-10"}))
        child = self.backend._runtime_config(23456)
        self.assertEqual(child["agwpe"], {"addr": "127.0.0.1:54321", "radio_port": 0})
        self.assertEqual(child["http_addr"], "127.0.0.1:23456")
        self.assertEqual(child["listen"], [])
        self.assertEqual(child["schedule"], {})
        self.assertEqual(self.config.read_bytes(), original)
        # Switching transport is an explicit request, not an RF-to-Telnet fallback.
        self.backend.connect_telnet()
        self.assertTrue(self.backend.calls[-1][1]["url"].startswith("telnet://"))

    def test_packet_rejects_invalid_gateways_and_ports_without_request(self):
        for gateway in ("", "W1AW-16", "TOO-LONG-CALL", "W1AW/path", "foo:123", None):
            with self.subTest(gateway=gateway):
                with self.assertRaises(PatError):
                    self.backend.connect_packet(gateway, 8000)
        for port in (0, 65536, -1, "8000", True):
            with self.subTest(port=port):
                with self.assertRaises(PatError):
                    self.backend.connect_packet("W1AW-10", port)
        self.assertEqual(self.backend.calls, [])
        self.assertIsNone(self.backend._agw_port)

    def test_packet_timeout_does_not_retry_or_fall_back(self):
        calls = []
        def timed_out(path, data=None, **kwargs):
            calls.append((path, data))
            raise PatTimeout("timeout")
        self.backend._request = timed_out
        with self.assertRaisesRegex(PatError, "delivery status may be unknown"):
            self.backend.connect_packet("W1AW-10", 8000)
        self.assertEqual(calls, [("/api/connect", {"url": "ax25+agwpe:///W1AW-10"})])


if __name__ == "__main__":
    unittest.main()
