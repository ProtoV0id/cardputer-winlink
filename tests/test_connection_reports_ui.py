"""On-screen reports for actual session phases, without radio or network."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from winlink_gui import UI


class PatFixture:
    def __init__(self):
        self.packet_calls = []
        self.telnet_calls = 0

    def get_settings(self):
        return {"callsign": "N0CALL", "locator": "", "has_password": False}

    def connect_packet(self, gateway, port, via):
        self.packet_calls.append((gateway, port, via))
        return "Packet session completed. Received 1 message(s)."

    def connect_telnet(self):
        self.telnet_calls += 1
        return "Telnet session completed. Received 2 message(s)."


class ConnectionReportUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state_dir = self.state / "cardputer-pat"
        self.state_dir.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.backend = PatFixture()
        self.ui = UI(self.backend, radio_config_path=self.root / "radio.json",
                     appearance_config_path=self.root / "appearance.json",
                     contacts_path=self.root / "contacts.json", gateway_profiles_path=self.root / "gateways.json")

    def finish(self):
        for _ in range(200):
            self.ui.poll()
            if self.ui.mode != "busy":
                return
            time.sleep(.01)
        self.fail("session worker did not finish")

    def test_packet_result_has_on_screen_report_without_raw_log_content(self):
        log = self.state_dir / "uvpro-tncd-current.log"
        class FakeTNC:
            def __init__(self, *args, **kwargs):
                self.log_path = str(log)
            def __enter__(self):
                log.write_text("agwpe: listening on 127.0.0.1:8000\n"
                               "bluetooth: SPP profile registered at /org/tncd/spp\n"
                               "body: secret@example.org SECRET-PASSWORD\n", encoding="utf-8")
                return self
            def __exit__(self, *_):
                pass
        self.ui.tnc_factory = FakeTNC
        self.ui.packet_connect({"mac": "AA:BB:CC:DD:EE:FF", "gateway": "KT7RUN-10", "via": "W7MOT-6"})
        self.finish()
        self.assertEqual(self.backend.packet_calls, [("KT7RUN-10", 8000, "W7MOT-6")])
        self.assertEqual(self.ui.title, "SESSION FINISHED")
        self.ui.input("ENTER")
        self.assertEqual(self.ui.title, "CONNECTION REPORT")
        self.assertIn("Session: PACKET", self.ui.text)
        self.assertIn("Messages received: 1", self.ui.text)
        self.assertIn("Bluetooth serial profile registered", self.ui.text)
        self.assertNotIn("secret@example.org", self.ui.text)
        self.assertNotIn("SECRET-PASSWORD", self.ui.text)
        self.ui.input("ESC")
        self.assertEqual(self.ui.title, "SESSION FINISHED")

    def test_failed_radio_start_reports_same_attempt_without_starting_pat(self):
        log = self.state_dir / "uvpro-tncd-failure.log"
        (self.state_dir / "pat.log").write_text(
            "2026/11/26 16:48:40 Unable to establish connection to remote: connect to OLD refused (DM)\n",
            encoding="utf-8")
        class FailingTNC:
            def __init__(self, *args, **kwargs):
                self.log_path = str(log)
            def __enter__(self):
                log.write_text("bridge: port 0 start error: bluetooth: ConnectProfile: "
                               "le-connection-abort-by-local\n", encoding="utf-8")
                raise RuntimeError("Radio Bluetooth link did not become ready")
            def __exit__(self, *_):
                pass
        self.ui.tnc_factory = FailingTNC
        self.ui.packet_connect({"mac": "AA:BB:CC:DD:EE:FF", "gateway": "KT7RUN-10", "via": ""})
        self.finish()
        self.assertEqual(self.ui.title, "SESSION ERROR")
        self.assertEqual(self.backend.packet_calls, [])
        self.ui.input("ENTER")
        self.assertIn("Bluetooth connection aborted locally", self.ui.text)
        self.assertNotIn("Remote packet station refused", self.ui.text)

    def test_radio_log_remains_accessible_and_historical_after_restart(self):
        (self.state_dir / "uvpro-tncd-previous.log").write_text(
            "bluetooth: calling ConnectProfile on /org/bluez/secret\n", encoding="utf-8")
        self.ui.connection_reports(self.ui.home)
        self.ui.input("DOWN")
        self.ui.input("ENTER")
        self.assertEqual(self.ui.title, "RADIO BRIDGE LOG")
        self.assertIn("Historical", self.ui.text)
        self.assertIn("not a current connection or session result", self.ui.text)
        self.assertIn("Connecting Bluetooth serial profile", self.ui.text)
        self.assertNotIn("/org/bluez/secret", self.ui.text)

    def test_telnet_report_does_not_claim_delivery(self):
        # A reused Pat log may contain events from a previous packet session.
        (self.state_dir / "pat.log").write_text(
            "2026/11/26 16:48:40 Unable to establish connection to remote: connect to OLD refused (DM)\n",
            encoding="utf-8")
        self.ui.connect()
        self.finish()
        self.ui.input("ENTER")
        self.assertIn("Session: TELNET", self.ui.text)
        self.assertIn("Messages received: 2", self.ui.text)
        self.assertNotIn("delivered", self.ui.text.casefold())
        self.assertNotIn("Remote packet station refused", self.ui.text)
        self.assertEqual(self.backend.telnet_calls, 1)

    def test_telnet_failure_screen_and_report_do_not_echo_raw_error(self):
        def fail():
            raise RuntimeError("no such host secret-address.example SECRET-PASSWORD")
        self.backend.connect_telnet = fail
        self.ui.connect()
        self.finish()
        self.assertEqual(self.ui.title, "SESSION ERROR")
        self.assertIn("host name could not be resolved", self.ui.text)
        self.assertNotIn("SECRET-PASSWORD", self.ui.text)
        self.ui.input("ENTER")
        self.assertNotIn("secret-address", self.ui.text)
        self.assertNotIn("SECRET-PASSWORD", self.ui.text)


if __name__ == "__main__":
    unittest.main()
