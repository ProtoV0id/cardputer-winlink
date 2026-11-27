import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from diagnostics import MAX_LOG_BYTES, failure_summary, format_report, latest_bridge_report


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_home = Path(self.tmp.name)
        self.root = self.state_home / "cardputer-pat"
        self.root.mkdir()
        self.bridge = self.root / "uvpro-tncd-fixture.log"
        self.pat = self.root / "pat.log"

    def report(self, kind="packet", outcome="failed", **kwargs):
        return format_report(kind, outcome, state_home=self.state_home, **kwargs)

    def test_packet_success_reports_real_events_without_private_fields(self):
        self.bridge.write_text("\n".join([
            "agwpe: listening on 127.0.0.1:8000",
            "bluetooth: SPP profile registered at /org/tncd/spp",
            "bluetooth: calling ConnectProfile on /org/bluez/hci0/dev_38_D2_00_01_09_D3",
            "bluetooth: SPP socket ready (fd=5) for 38:D2:00:01:09:D3",
            "message body secret ; password Hunter2!",
        ]), encoding="utf-8")
        report = self.report(outcome="completed", bridge_log=self.bridge,
                             result="Packet session completed. Received 2 message(s).")
        self.assertIn("Result: Completed", report)
        self.assertIn("Messages received: 2", report)
        self.assertIn("Bluetooth serial link opened.", report)
        for private in ("Hunter2", "38:D2", "message body", "/org/bluez"):
            self.assertNotIn(private, report)
        self.assertNotIn("delivered", report.lower())

    def test_abort_and_dm_are_sanitized_and_distinguished(self):
        self.bridge.write_text("bridge: port 0 start error: bluetooth: ConnectProfile: le-connection-abort-by-local password=secret\n", encoding="utf-8")
        self.pat.write_text("2026/11/26 15:48:40 Unable to establish connection to remote: *** connect to W7MOT-6 refused (DM)\n", encoding="utf-8")
        report = self.report(error="secret user text", bridge_log=self.bridge, include_pat=True)
        self.assertIn("Bluetooth connection aborted locally.", report)
        self.assertIn("Remote packet station refused the connection (DM).", report)
        self.assertIn("may include earlier activity", report)
        self.assertNotIn("secret", report)
        self.assertNotIn("W7MOT", report)

    def test_session_failure_does_not_read_stale_pat_log_without_session(self):
        self.pat.write_text("2026/11/26 15:48:40 Unable to establish connection to remote: refused (DM)\n", encoding="utf-8")
        report = self.report(error="Radio Bluetooth link did not become ready", include_pat=False)
        self.assertIn("Radio link not ready", report)
        self.assertIn("no log available for this attempt", report)
        self.assertNotIn("refused", report)

    def test_stale_logs_are_not_assigned_to_new_session(self):
        self.bridge.write_text("bluetooth: SPP socket ready (fd=1) for RADIO\n", encoding="utf-8")
        self.pat.write_text("2026/11/26 15:48:40 Unable to establish connection to remote: refused (DM)\n", encoding="utf-8")
        past = time.time() - 60
        os.utime(self.bridge, (past, past))
        os.utime(self.pat, (past, past))
        report = self.report(started_at=time.time(), bridge_log=self.bridge, include_pat=True)
        self.assertIn("no log available", report)
        self.assertIn("no recent log available", report)
        self.assertNotIn("link opened", report)
        self.assertNotIn("refused", report)

    def test_unknown_error_and_lines_are_never_echoed(self):
        self.bridge.write_text("secret=top-secret\nbluetooth: SPP socket ready (fd=1) for top-secret\n", encoding="utf-8")
        report = self.report(error="secret top-secret user input", bridge_log=self.bridge)
        self.assertIn("Session failed", report)
        self.assertNotIn("secret", report)

    def test_bridge_path_is_constrained_and_symlinks_are_not_read(self):
        other = self.state_home / "uvpro-tncd-outside.log"
        other.write_text("bluetooth: SPP socket ready (fd=1) for RADIO\n", encoding="utf-8")
        self.assertIn("no log available", self.report(bridge_log=other))
        link = self.root / "uvpro-tncd-link.log"
        try:
            link.symlink_to(other)
        except OSError:
            return  # Windows may not permit creating symbolic links.
        self.assertIn("no log available", self.report(bridge_log=link))

    def test_large_log_read_is_bounded_and_old_record_is_excluded(self):
        self.bridge.write_text("bluetooth: SPP socket ready (fd=1) for RADIO\n" +
                               ("unrecognized x\n" * (MAX_LOG_BYTES // 4)), encoding="utf-8")
        report = self.report(bridge_log=self.bridge)
        self.assertNotIn("link opened", report)
        self.assertIn("No recognized connection events", report)

    def test_unknown_kind_or_outcome_is_rejected(self):
        with self.assertRaises(ValueError):
            self.report(kind="other")
        with self.assertRaises(ValueError):
            self.report(outcome="connected")

    def test_untrusted_result_is_not_displayed(self):
        report = self.report(kind="telnet", outcome="completed", result="secret message body")
        self.assertNotIn("secret", report)
        self.assertIn("Result: Completed", report)

    def test_safe_failure_summaries_cover_common_errors(self):
        cases = {
            "radio failed password=secret": "Radio connection failed; check power, pairing and KISS mode.",
            "tncd is not installed: secret": "Packet bridge tncd is not installed.",
            "tncd exited before starting: secret": "Packet bridge exited before it was ready.",
            "PocketLink requires tncd 2.1.0 secret": "Packet bridge version does not match; check its installation.",
            "Enter an AX.25 gateway callsign: secret": "Check the gateway callsign and SSID in Packet settings.",
            "Enter one digipeater callsign and SSID: secret": "Check the digipeater callsign and SSID in Packet settings.",
            "Set a valid callsign in Settings first. secret": "Check the callsign in Settings.",
            "Local packet port 8000 is in use. secret": "Local packet port is unavailable.",
            "lookup cms.example: no such host secret": "The host name could not be resolved.",
            "temporary failure in name resolution secret": "The host name could not be resolved.",
            "network is unreachable secret": "The network is unreachable. Check the network connection.",
            "no route to host secret": "No network route to the host.",
            "connection refused secret": "The connection was refused.",
            "authentication failed: secret": "Authentication failed. Check the login details.",
            "authentication rejected: secret": "Authentication failed. Check the login details.",
            "login failed: secret": "Authentication failed. Check the login details.",
            "connection refused (DM)": "Remote packet station refused the connection (DM).",
            "unexpected secret": "Session failed; check the connection and settings.",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                summary = failure_summary(source)
                self.assertEqual(summary, expected)
                self.assertNotIn("secret", summary)

    def test_historical_report_selects_latest_valid_bridge_log(self):
        old = self.root / "uvpro-tncd-old.log"
        new = self.root / "uvpro-tncd-new.log"
        old.write_text("bluetooth: SPP socket ready (fd=1) for RADIO\n", encoding="utf-8")
        new.write_text("bridge: port 0 start error: bluetooth: connection to SECRET timed out (30s)\n", encoding="utf-8")
        (self.root / "pat.log").write_text("secret\n", encoding="utf-8")
        now = time.time()
        os.utime(old, (now - 120, now - 120))
        os.utime(new, (now - 60, now - 60))
        report = latest_bridge_report(self.state_home)
        self.assertIn("Historical radio bridge log", report)
        self.assertIn("not a current connection or session result", report)
        self.assertIn("Bluetooth connection timed out.", report)
        self.assertNotIn("link opened", report)
        self.assertNotIn("SECRET", report)
        self.assertNotIn("Result: Completed", report)
        self.assertNotIn(str(self.state_home), report)

    def test_historical_report_skips_symlinks_and_unrelated_files(self):
        self.bridge.write_text("bluetooth: SPP socket ready (fd=1) for RADIO\n", encoding="utf-8")
        outside = self.state_home / "outside.log"
        outside.write_text("bridge: port 0 start error: not paired\n", encoding="utf-8")
        (self.root / "uvpro-tncd-unrelated.log.txt").write_text("bad data", encoding="utf-8")
        link = self.root / "uvpro-tncd-link.log"
        try:
            link.symlink_to(outside)
        except OSError:
            pass  # Windows may not permit creating symbolic links.
        report = latest_bridge_report(self.state_home)
        self.assertIn("Bluetooth serial link opened.", report)
        self.assertNotIn("not paired", report)
        self.assertNotIn("bad data", report)

    def test_historical_report_handles_absent_directory_and_no_matches(self):
        self.assertIn("No radio bridge log", latest_bridge_report(self.state_home))
        self.assertIn("No radio bridge log", latest_bridge_report(self.state_home / "missing"))

    def test_historical_report_does_not_guess_when_directory_is_too_large(self):
        self.bridge.write_text("bluetooth: SPP socket ready (fd=1) for RADIO\n", encoding="utf-8")
        (self.root / "unrelated").write_text("x", encoding="utf-8")
        with mock.patch("diagnostics.MAX_DIRECTORY_ENTRIES", 1):
            report = latest_bridge_report(self.state_home)
        self.assertIn("Too many files", report)
        self.assertNotIn("link opened", report)


if __name__ == "__main__":
    unittest.main()
