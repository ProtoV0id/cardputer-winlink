"""UV-PRO process controller tests: mock all Bluetooth and RF operations."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

import uvpro_tnc as tnc

_REAL_START_DISCOVERY = tnc.UVProTNC._start_discovery


class FakeProc:
    def __init__(self):
        self.pid = 1234
        self.code = None
        read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self.stdin = None
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
        # Existing bridge tests exercise the bridge itself; discovery has
        # dedicated lifecycle tests below, without requiring a Bluetooth adapter.
        self.discovery = mock.patch.object(tnc.UVProTNC, "_start_discovery")
        self.discovery.start()

    def tearDown(self):
        self.discovery.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_discovery_is_running_before_bridge_and_stops_with_session(self):
        scanner, process = FakeProc(), FakeProc()
        os.write(scanner.write_fd, b"[CHG] Controller ...\nDiscovery started\n")
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd")
        with mock.patch.object(tnc.UVProTNC, "_start_discovery", _REAL_START_DISCOVERY), \
             mock.patch.object(tnc.shutil, "which", side_effect=lambda value: "/usr/bin/" + value), \
             mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc, "_owns_listener", return_value=True), \
             mock.patch.object(bridge, "_online", return_value=True), \
             mock.patch.object(tnc.subprocess, "Popen", side_effect=[scanner, process]) as spawn:
            bridge.start()
            self.assertEqual(spawn.call_args_list[0].args[0], ["/usr/bin/bluetoothctl", "--timeout", "600", "scan", "on"])
            self.assertEqual(spawn.call_args_list[1].args[0][0], "fake-tncd")
            self.assertIsNone(scanner.poll())
            self.assertIsNone(process.poll())
            bridge.stop()
            self.assertTrue(scanner.terminated)
            self.assertTrue(process.terminated)

    def test_discovery_rejection_never_launches_bridge(self):
        scanner = FakeProc()
        os.write(scanner.write_fd, b"Failed to start discovery: org.bluez.Error.NotReady\n")
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd")
        with mock.patch.object(tnc.UVProTNC, "_start_discovery", _REAL_START_DISCOVERY), \
             mock.patch.object(tnc.shutil, "which", side_effect=lambda value: "/usr/bin/" + value), \
             mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc.subprocess, "Popen", return_value=scanner) as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "discovery could not start"):
                bridge.start()
            self.assertEqual(spawn.call_count, 1)
            self.assertTrue(scanner.terminated)

    def test_discovery_ack_is_a_complete_line_and_not_a_device_name(self):
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "rb", buffering=0)
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake")
        event = threading.Event()
        worker = threading.Thread(target=bridge._drain_discovery, args=(stream, event))
        worker.start()
        try:
            os.write(write_fd, b"[NEW] Device AA:BB:CC:DD:EE:FF Discovery started\n")
            self.assertFalse(event.wait(0.1))
            os.write(write_fd, b"\x1b[0mDiscovery sta")
            self.assertFalse(event.wait(0.1))
            os.write(write_fd, b"rted\x1b[0m\r\n")
            self.assertTrue(event.wait(1))
            self.assertIsNone(bridge._scan_error)
        finally:
            os.close(write_fd)
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_missing_discovery_command_fails_without_starting_bridge(self):
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd")
        with mock.patch.object(tnc.UVProTNC, "_start_discovery", _REAL_START_DISCOVERY), \
             mock.patch.object(tnc.shutil, "which", return_value=None), \
             mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "requires bluetoothctl"):
                bridge.start()
            spawn.assert_not_called()

    def test_cancellation_during_discovery_cleans_up_without_launching_bridge(self):
        scanner = FakeProc()
        cancel = threading.Event()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd", cancel_event=cancel)
        def spawn_scan(*args, **kwargs):
            cancel.set()
            return scanner
        with mock.patch.object(tnc.UVProTNC, "_start_discovery", _REAL_START_DISCOVERY), \
             mock.patch.object(tnc.shutil, "which", side_effect=lambda value: "/usr/bin/" + value), \
             mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc.subprocess, "Popen", side_effect=spawn_scan) as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "Bluetooth connection canceled"):
                bridge.start()
            self.assertEqual(spawn.call_count, 1)
            self.assertTrue(scanner.terminated)

    def test_discovery_timeout_stops_scanner_without_launching_bridge(self):
        scanner = FakeProc()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd")
        with mock.patch.object(tnc.UVProTNC, "_start_discovery", _REAL_START_DISCOVERY), \
             mock.patch.object(tnc.shutil, "which", return_value="/usr/bin/bluetoothctl"), \
             mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc.time, "monotonic", side_effect=[0, 16]), \
             mock.patch.object(tnc.subprocess, "Popen", return_value=scanner) as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "discovery did not start"):
                bridge.start()
            self.assertEqual(spawn.call_count, 1)
            self.assertTrue(scanner.terminated)

    def test_bridge_spawn_error_cleans_up_running_discovery(self):
        scanner = FakeProc()
        os.write(scanner.write_fd, b"Discovery started\n")
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd")
        with mock.patch.object(tnc.UVProTNC, "_start_discovery", _REAL_START_DISCOVERY), \
             mock.patch.object(tnc.shutil, "which", return_value="/usr/bin/bluetoothctl"), \
             mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc.subprocess, "Popen", side_effect=[scanner, OSError("missing bridge")]):
            with self.assertRaisesRegex(tnc.TNCError, "Could not start the packet bridge"):
                bridge.start()
            self.assertTrue(scanner.terminated)

    @unittest.skipUnless(sys.platform == "linux", "Linux subprocess discovery check")
    def test_real_discovery_subprocess_is_held_and_terminated(self):
        fake = Path(self.temp.name) / "fake-bluetoothctl"
        fake.write_text("#!/bin/sh\nprintf 'Discovery started\\n'\nexec sleep 300\n", encoding="utf-8")
        fake.chmod(0o755)
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake-tncd")
        with mock.patch.object(tnc.UVProTNC, "_start_discovery", _REAL_START_DISCOVERY), \
             mock.patch.object(tnc.shutil, "which", return_value=str(fake)):
            try:
                bridge._start_discovery()
                process = bridge._scan_proc
                self.assertIsNone(process.poll())
                self.assertFalse(process.stdin.closed)
            finally:
                bridge.stop()
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdin.closed)

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

    def test_default_timeout_allows_bridge_spp_and_profile_setup(self):
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL")
        self.assertEqual(bridge.startup_timeout, 150)

    def test_canceled_before_start_does_not_launch(self):
        cancel = threading.Event()
        cancel.set()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake", cancel_event=cancel)
        with mock.patch.object(tnc, "_check_tncd") as check, mock.patch.object(tnc.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "^Bluetooth connection canceled\\.$"):
                bridge.start()
            check.assert_not_called()
            spawn.assert_not_called()

    def test_canceled_during_version_check_does_not_launch(self):
        cancel = threading.Event()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake", cancel_event=cancel)
        with mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd", side_effect=lambda *_: cancel.set()), \
             mock.patch.object(tnc, "_ensure_port_free") as port_check, \
             mock.patch.object(tnc.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "^Bluetooth connection canceled\\.$"):
                bridge.start()
            port_check.assert_not_called()
            spawn.assert_not_called()

    def test_canceled_while_waiting_stops_child(self):
        cancel = threading.Event()
        process = FakeProc()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake", cancel_event=cancel)
        def not_online(_):
            cancel.set()
            return False
        with mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc, "_owns_listener", return_value=True), \
             mock.patch.object(bridge, "_online", side_effect=not_online), \
             mock.patch.object(tnc.subprocess, "Popen", return_value=process) as spawn:
            with self.assertRaisesRegex(tnc.TNCError, "^Bluetooth connection canceled\\.$"):
                bridge.start()
            self.assertTrue(process.terminated)
            self.assertFalse(Path(spawn.call_args.args[0][2]).exists())
            self.assertEqual(bridge.status()["state"], "stopped")

    def test_canceled_as_link_becomes_ready_stops_child(self):
        cancel = threading.Event()
        process = FakeProc()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake", cancel_event=cancel)
        def online(_):
            cancel.set()
            return True
        with mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc, "_check_tncd"), \
             mock.patch.object(tnc, "_ensure_port_free"), \
             mock.patch.object(tnc, "_ephemeral_port", return_value=23021), \
             mock.patch.object(tnc, "_owns_listener", return_value=True), \
             mock.patch.object(bridge, "_online", side_effect=online), \
             mock.patch.object(tnc.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(tnc.TNCError, "^Bluetooth connection canceled\\.$"):
                bridge.start()
            self.assertTrue(process.terminated)
            self.assertEqual(bridge.status()["state"], "stopped")

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

    def test_experimental_bridge_requires_exact_marker_and_existing_binary(self):
        home = Path(self.temp.name)
        marker = home / ".config" / "cardputer-pat" / "experimental-tncd"
        binary = home / ".local" / "bin" / "pocketlink-tncd-experimental"
        with mock.patch.object(tnc.Path, "home", return_value=home), \
             mock.patch.object(tnc.shutil, "which", return_value="/system/tncd"):
            bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL")
            self.assertEqual(bridge.executable, "/system/tncd")
            self.assertEqual(bridge.expected_version, "2.1.0")

            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"enabled")
            with self.assertRaisesRegex(tnc.TNCError, "Invalid experimental"):
                tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL")
            marker.write_bytes(b"enabled\n")
            with self.assertRaisesRegex(tnc.TNCError, "missing or not executable"):
                tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL")

            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"test binary")
            with mock.patch.object(tnc.os, "access", return_value=True):
                bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL")
            self.assertEqual(bridge.executable, str(binary))
            self.assertEqual(bridge.expected_version, "2.1.0-pocketlink.1")

            marker.write_bytes(b"enabled\nextra")
            with self.assertRaisesRegex(tnc.TNCError, "Invalid experimental"):
                tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL")

    def test_experimental_version_and_api_gates(self):
        proc = mock.Mock(returncode=0, stdout="tncd 2.1.0\n")
        with mock.patch.object(tnc.subprocess, "run", return_value=proc):
            with self.assertRaisesRegex(tnc.TNCError, "2.1.0-pocketlink.1"):
                tnc._check_tncd("/local/experimental", "2.1.0-pocketlink.1")
            proc.stdout = "tncd 2.1.0-pocketlink.1\n"
            tnc._check_tncd("/local/experimental", "2.1.0-pocketlink.1")
            with self.assertRaisesRegex(tnc.TNCError, "2.1.0"):
                tnc._check_tncd("/local/experimental")

        process = FakeProc()
        bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL", executable="fake", api_port=23021)
        bridge.expected_version = "2.1.0-pocketlink.1"
        response = mock.Mock(status=200)
        conn = mock.Mock()
        conn.getresponse.return_value = response
        with mock.patch.object(tnc, "_owns_listener", return_value=True), \
             mock.patch.object(tnc.http.client, "HTTPConnection", return_value=conn):
            status = {"version": "2.1.0", "ports": [{"port": 0, "type": "bluetooth", "online": True}]}
            response.read.return_value = json.dumps(status).encode()
            self.assertFalse(bridge._online(process))
            status["version"] = "2.1.0-pocketlink.1"
            response.read.return_value = json.dumps(status).encode()
            self.assertTrue(bridge._online(process))
            status["ports"][0]["online"] = False
            response.read.return_value = json.dumps(status).encode()
            self.assertFalse(bridge._online(process))
        process.terminate()
        process.stdout.close()

    def test_experimental_wrong_version_never_starts_bridge_or_falls_back(self):
        home = Path(self.temp.name)
        marker = home / ".config" / "cardputer-pat" / "experimental-tncd"
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b"enabled\n")
        binary = home / ".local" / "bin" / "pocketlink-tncd-experimental"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"test binary")
        proc = mock.Mock(returncode=0, stdout="tncd 2.1.0\n")
        with mock.patch.object(tnc.Path, "home", return_value=home), \
             mock.patch.object(tnc.os, "access", return_value=True), \
             mock.patch.object(tnc.sys, "platform", "linux"), \
             mock.patch.object(tnc.subprocess, "run", return_value=proc) as run, \
             mock.patch.object(tnc.subprocess, "Popen") as spawn, \
             mock.patch.object(tnc, "_ensure_port_free") as port_check:
            bridge = tnc.UVProTNC("AA:BB:CC:DD:EE:01", "N0CALL")
            with self.assertRaisesRegex(tnc.TNCError, "2.1.0-pocketlink.1"):
                bridge.start()
            self.assertEqual(run.call_args.args[0], [str(binary), "version"])
            spawn.assert_not_called()
            port_check.assert_not_called()

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
