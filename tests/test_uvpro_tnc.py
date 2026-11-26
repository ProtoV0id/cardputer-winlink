"""UV-PRO process controller tests: mock all Bluetooth and RF operations."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import uvpro_tnc as tnc


class FakeProc:
    def __init__(self):
        self.pid = 1234
        self.code = None
        read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15
        if self.write_fd is not None:
            os.close(self.write_fd)
            self.write_fd = None

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.code


class UVProTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_mac_and_callsign_validation(self):
        self.assertEqual(tnc.normalize_mac("aa:bb:cc:dd:ee:ff"), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(tnc.normalize_callsign("n0call-15"), "N0CALL-15")
        for mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "AA:BB:CC:DD:EE:FF\n[api]", "not-a-mac"):
            with self.assertRaises(tnc.TNCError):
                tnc.normalize_mac(mac)
        for call in ("N0CALL-16", "longcall", "N0\n[api]", "N0CALL-1/1"):
            with self.assertRaises(tnc.TNCError):
                tnc.normalize_callsign(call)

    def test_version_gate_accepts_only_pinned_release(self):
        proc = mock.Mock(returncode=0, stdout="tncd 2.1.0\n")
        with mock.patch.object(tnc.subprocess, "run", return_value=proc) as run:
            tnc._check_tncd("/local/tncd")
        self.assertEqual(run.call_args.args[0], ["/local/tncd", "version"])
        proc.stdout = "tncd 1.3.3\n"
        with mock.patch.object(tnc.subprocess, "run", return_value=proc):
            with self.assertRaisesRegex(tnc.TNCError, "2.1.0"):
                tnc._check_tncd("/local/tncd")

    def test_start_owns_loopback_bridge_and_cleans_up(self):
        process = FakeProc()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd")
        with mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc, "_owns_listener", return_value=True), \
             mock.patch.object(bridge, "_online", return_value=True), \
             mock.patch.object(tnc.subprocess, "Popen", return_value=process) as spawn:
            bridge.start()
            config = Path(spawn.call_args.args[0][2])
            text = config.read_text()
            self.assertIn("listen_host = 127.0.0.1", text)
            self.assertIn("bdaddr = AA:BB:CC:DD:EE:01", text)
            self.assertIn("listen_port = 23021", text)
            self.assertIn("reconnect = false", text)
            self.assertIn("rx_wedge_timeout = 0", text)
            self.assertIn("send_kiss_exit = false", text)
            self.assertEqual(bridge.status()["state"], "running")
            self.assertIn("RF is unverified", bridge.status()["detail"])
            with mock.patch.object(bridge, "_online", return_value=False):
                self.assertEqual(bridge.status()["state"], "offline")
            self.assertTrue(Path(bridge.log_path).is_file())
            bridge.stop()
            self.assertEqual(bridge.status()["state"], "stopped")
            self.assertTrue(process.terminated)
            self.assertFalse(config.exists())

    def test_local_listener_alone_never_means_bluetooth_ready(self):
        process = FakeProc()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake", startup_timeout=0)
        with mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc, "_owns_listener", return_value=True), \
             mock.patch.object(bridge, "_online", return_value=False), \
             mock.patch.object(tnc.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(tnc.TNCError, "Bluetooth link did not become ready"):
                bridge.start()
            self.assertTrue(process.terminated)
            self.assertEqual(bridge.status()["state"], "stopped")
            self.assertTrue(Path(bridge.log_path).exists())

    def test_used_port_stops_before_starting_child(self):
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake")
        with mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free", side_effect=tnc.TNCError("in use")), \
             mock.patch.object(tnc.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "in use"):
                bridge.start()
            spawn.assert_not_called()

    def test_status_api_requires_owned_compatible_online_port(self):
        process = FakeProc()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", api_port=23021)
        response = mock.Mock(status=200)
        response.read.return_value = json.dumps({"version": "2.1.0", "ports": [{"port": 0, "type": "bluetooth", "online": True}]}).encode()
        conn = mock.Mock()
        conn.getresponse.return_value = response
        with mock.patch.object(tnc, "_owns_listener", return_value=True), mock.patch.object(tnc.http.client, "HTTPConnection", return_value=conn):
            self.assertTrue(bridge._online(process))
            response.read.return_value = json.dumps({"version": "2.1.0", "ports": [{"port": 0, "type": "bluetooth", "online": False}]}).encode()
            self.assertFalse(bridge._online(process))
        with mock.patch.object(tnc, "_owns_listener", return_value=False), mock.patch.object(tnc.http.client, "HTTPConnection") as factory:
            self.assertFalse(bridge._online(process))
            factory.assert_not_called()
        process.terminate()
        process.stdout.close()

    def test_log_is_bounded(self):
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "rb", buffering=0)
        path = Path(self.temp.name) / "bounded.log"
        log_fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        thread = threading.Thread(target=tnc.UVProTNC._drain_log, args=(stream, log_fd))
        thread.start()
        chunk = b"x" * 4096
        for _ in range(40):
            os.write(write_fd, chunk)
        os.close(write_fd)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(path.stat().st_size, 128 * 1024)


if __name__ == "__main__":
    unittest.main()
