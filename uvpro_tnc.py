"""Optional UV-PRO Bluetooth KISS bridge via an externally installed tncd.

The UV-PRO's KISS stream is a frame interface, not an AX.25 connected-mode
engine. tncd supplies AX.25 and exposes a loopback-only AGWPE service for Pat.
No software is installed and no connection is started by importing this module.

Starting tncd asks BlueZ to connect to the selected paired radio. tncd may
write KISS control/parameter frames during setup or shutdown; this controller
does not ask it to transmit a packet. A running listener proves neither the
Bluetooth data link nor RF transmission. Select and verify the RF channel on
the radio, and explicitly start a Pat packet session, before expecting traffic.
"""
from __future__ import annotations

import os
import json
import http.client
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time


class TNCError(RuntimeError):
    """A Bluetooth bridge operation cannot be safely completed."""


_MAC = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z")
_CALL = re.compile(r"([A-Z0-9]{1,6})(?:-([0-9]{1,2}))?\Z")
_TNCD_VERSION = "2.1.0"
_EXPERIMENTAL_TNCD_VERSION = "2.1.0-pocketlink.1"


def normalize_mac(value):
    """Validate without permitting config-file directives or shell syntax."""
    value = str(value or "").strip().upper()
    if not _MAC.fullmatch(value) or value in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
        raise TNCError("Enter the radio Bluetooth address as AA:BB:CC:DD:EE:FF.")
    return value


def normalize_callsign(value):
    value = str(value or "").strip().upper()
    match = _CALL.fullmatch(value)
    if not match or (match.group(2) is not None and int(match.group(2)) > 15):
        raise TNCError("Set a valid AX.25 callsign and SSID (0–15) first.")
    return value


def _ensure_port_free(port):
    """Check the selected loopback port without talking to an existing server."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))
    except OSError as exc:
        raise TNCError("Local packet port %d is in use or unavailable." % port) from exc


def _ephemeral_port(exclude):
    """Select a temporary loopback status port; later verify listener ownership."""
    try:
        for _ in range(4):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            if port != exclude:
                return port
    except OSError as exc:
        raise TNCError("Cannot allocate a local packet status port.") from exc
    raise TNCError("Cannot allocate a separate packet status port.")


def _experimental_tncd_enabled():
    """Require an exact, explicit local opt-in for the modified bridge."""
    marker = Path.home() / ".config" / "cardputer-pat" / "experimental-tncd"
    try:
        with marker.open("rb") as stream:
            content = stream.read(9)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TNCError("Cannot read experimental tncd opt-in: " + str(exc)) from exc
    if content != b"enabled\n":
        raise TNCError("Invalid experimental tncd opt-in; use exactly enabled followed by a newline, or remove the marker.")
    return True


def _find_tncd(experimental=False):
    # APPLaunch does not necessarily add the user's local bin to PATH.
    if experimental:
        candidate = Path.home() / ".local" / "bin" / "pocketlink-tncd-experimental"
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise TNCError("Experimental tncd is enabled, but its executable is missing or not executable.")
        return str(candidate)
    candidate = Path.home() / ".local" / "bin" / "tncd"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which("tncd")


def _check_tncd(executable, version=_TNCD_VERSION):
    """Check the documented safe version subcommand before connecting radio."""
    if not executable:
        raise TNCError("tncd is not installed. Install tncd 2.1.0 with Bluetooth support first.")
    try:
        result = subprocess.run([executable, "version"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TNCError("Could not run tncd. Check that the selected tncd " + version + " is executable.") from exc
    if result.returncode != 0 or result.stdout.strip() != "tncd " + version:
        raise TNCError("PocketLink requires tncd " + version + " for this radio connection.")


def _owns_listener(pid, port):
    """Verify the tncd PID owns this loopback listening socket on Linux.

    Checking only whether a TCP port accepts connections could mistake an
    unrelated AGWPE service for our newly launched process after a bind race.
    Fail closed if procfs cannot establish ownership; never contact that port.
    """
    try:
        fds = Path("/proc") / str(pid) / "fd"
        inodes = set()
        for fd in fds.iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            match = re.fullmatch(r"socket:\[([0-9]+)\]", target)
            if match:
                inodes.add(match.group(1))
        if not inodes:
            return False
        entries = (Path("/proc") / str(pid) / "net" / "tcp").read_text(encoding="ascii").splitlines()
        for row in entries[1:]:
            fields = row.split()
            if len(fields) < 10 or fields[3] != "0A":  # LISTEN
                continue
            address, local_port = fields[1].split(":", 1)
            if address.upper() == "0100007F" and int(local_port, 16) == port and fields[9] in inodes:
                return True
    except (OSError, ValueError, IndexError):
        return False
    return False


class UVProTNC:
    """Manage one explicitly requested, private tncd Bluetooth bridge."""

    def __init__(self, mac, callsign, executable=None, port=8000, startup_timeout=150,
                 api_port=None, cancel_event=None):
        self.mac = normalize_mac(mac)
        self.callsign = normalize_callsign(callsign)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise TNCError("Invalid local packet port.")
        self.port = port
        if api_port is not None and (isinstance(api_port, bool) or not isinstance(api_port, int) or not 1 <= api_port <= 65535 or api_port == port):
            raise TNCError("Invalid local packet status port.")
        self._requested_api_port = api_port
        self.api_port = api_port
        experimental = executable is None and _experimental_tncd_enabled()
        self.expected_version = _EXPERIMENTAL_TNCD_VERSION if experimental else _TNCD_VERSION
        self.executable = executable if executable is not None else _find_tncd(experimental)
        self.startup_timeout = startup_timeout
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self._proc = None
        self._scan_proc = None
        self._scan_thread = None
        self._scan_event = None
        self._scan_error = None
        self._runtime = None
        self._listener_verified = False
        self._starting = False
        self.log_path = None
        self._log_thread = None
        self._lock = threading.RLock()

    def _config_text(self):
        # No rigctl, KISS TCP server, station discovery or TX command is
        # enabled. The status API is bound to loopback, with its UI disabled.
        return ("[server]\nlisten_host = 127.0.0.1\nlisten_port = %d\ncallsign = %s\n\n"
                "[client.0]\ntype = bluetooth\nbdaddr = %s\nota_baudrate = 1200\n"
                "ax25_version = 2.0\nsend_kiss_exit = false\nreconnect = false\n"
                "rx_wedge_timeout = 0\n\n"
                "[api]\nenabled = true\nlisten_host = 127.0.0.1\nlisten_port = %d\nserve_ui = false\n"
                % (self.port, self.callsign, self.mac, self.api_port))

    def _online(self, proc):
        """Check pinned tncd's per-port status after verifying API ownership."""
        if proc.poll() is not None or not _owns_listener(proc.pid, self.api_port):
            return False
        conn = http.client.HTTPConnection("127.0.0.1", self.api_port, timeout=0.5)
        try:
            conn.request("GET", "/api/status")
            response = conn.getresponse()
            raw = response.read(16385)
            if response.status != 200 or len(raw) > 16384:
                return False
            data = json.loads(raw)
            ports = data.get("ports")
            if data.get("version") != self.expected_version or not isinstance(ports, list):
                return False
            if proc.poll() is not None or not _owns_listener(proc.pid, self.api_port):
                return False
            return any(isinstance(p, dict) and p.get("port") == 0 and
                       p.get("type") == "bluetooth" and p.get("online") is True for p in ports)
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            return False
        finally:
            conn.close()

    def _open_log(self):
        state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        log_dir = state_home / "cardputer-pat"
        log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="uvpro-tncd-", suffix=".log", dir=log_dir)
        self.log_path = str(path)
        return fd

    @staticmethod
    def _drain_log(stream, fd):
        """Continuously drain child output into a private file capped at 128 KiB."""
        try:
            with os.fdopen(fd, "wb") as log:
                remaining = 128 * 1024
                while True:
                    chunk = os.read(stream.fileno(), 4096)
                    if not chunk:
                        break
                    if remaining:
                        log.write(chunk[:remaining])
                        log.flush()
                        remaining -= min(len(chunk), remaining)
        except OSError:
            # A closed pipe or filesystem error must not crash PocketLink.
            pass
        finally:
            stream.close()

    def _drain_discovery(self, stream, scan_event):
        """Observe scan startup, then discard discovery output and device data."""
        pending = b""
        discard_line = False
        try:
            while True:
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    break
                if scan_event.is_set():
                    continue
                parts = chunk.split(b"\n")
                for index, fragment in enumerate(parts):
                    if not discard_line:
                        if len(pending) + len(fragment) > 4096:
                            pending, discard_line = b"", True
                        else:
                            pending += fragment
                    if index == len(parts) - 1:
                        break
                    if not discard_line:
                        line = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", pending).strip()
                        if line.startswith(b"Failed to start discovery") or line == b"No default controller available":
                            self._scan_error = "Bluetooth discovery could not start; check the Bluetooth adapter."
                            scan_event.set()
                        elif line == b"Discovery started":
                            scan_event.set()
                    pending, discard_line = b"", False
                    if scan_event.is_set():
                        break
        except OSError:
            pass
        finally:
            if not scan_event.is_set():
                self._scan_error = "Bluetooth discovery stopped before it became ready."
                scan_event.set()
            stream.close()

    def _start_discovery(self):
        """Hold our own BlueZ discovery session while the packet bridge runs."""
        bluetoothctl = shutil.which("bluetoothctl")
        if not bluetoothctl:
            raise TNCError("Bluetooth discovery requires bluetoothctl.")
        self._scan_event = threading.Event()
        self._scan_error = None
        env = os.environ.copy()
        env.update({"LC_ALL": "C", "LANG": "C"})
        try:
            self._scan_proc = subprocess.Popen(
                [bluetoothctl, "--timeout", "600", "scan", "on"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                close_fds=True, env=env)
        except OSError as exc:
            raise TNCError("Could not start Bluetooth discovery: " + str(exc)) from exc
        self._scan_thread = threading.Thread(target=self._drain_discovery,
                                             args=(self._scan_proc.stdout, self._scan_event), daemon=True)
        self._scan_thread.start()
        deadline = time.monotonic() + 15
        while not self._scan_event.wait(0.1):
            if self.cancel_event.is_set():
                raise TNCError("Bluetooth connection canceled.")
            if self._scan_proc.poll() is not None:
                raise TNCError("Bluetooth discovery stopped before it became ready.")
            if time.monotonic() >= deadline:
                raise TNCError("Bluetooth discovery did not start; check the Bluetooth adapter.")
        if self.cancel_event.is_set():
            raise TNCError("Bluetooth connection canceled.")
        if self._scan_error:
            raise TNCError(self._scan_error)
        if self._scan_proc.poll() is not None:
            raise TNCError("Bluetooth discovery stopped before it became ready.")

    def start(self):
        """Start tncd and wait for its local listener and Bluetooth link.

        Discovery startup may take an additional 15 seconds before the bridge's
        ``startup_timeout`` begins. Call from a worker thread. A connected
        transport does not prove radio TX or gateway reachability.
        """
        with self._lock:
            if self.cancel_event.is_set():
                raise TNCError("Bluetooth connection canceled.")
            if self._proc is not None and self._proc.poll() is None:
                if self._listener_verified and _owns_listener(self._proc.pid, self.port) and self._online(self._proc):
                    return
                raise TNCError("Packet bridge is running without a verified listener.")
            self.stop()
            if sys.platform != "linux":
                raise TNCError("UV-PRO Bluetooth support requires Linux and BlueZ.")
            _check_tncd(self.executable, self.expected_version)
            if self.cancel_event.is_set():
                raise TNCError("Bluetooth connection canceled.")
            _ensure_port_free(self.port)
            self.api_port = self._requested_api_port or _ephemeral_port(self.port)
            if self._requested_api_port is not None:
                _ensure_port_free(self.api_port)
            self._starting = True
            try:
                self._start_discovery()
                self._runtime = tempfile.TemporaryDirectory(prefix="pocketlink-tncd-")
                config = Path(self._runtime.name) / "tncd.ini"
                fd = os.open(str(config), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(self._config_text())
                log_fd = self._open_log()
                try:
                    if self.cancel_event.is_set():
                        raise TNCError("Bluetooth connection canceled.")
                    self._proc = subprocess.Popen([self.executable, "-c", str(config)],
                                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                  stderr=subprocess.STDOUT, close_fds=True)
                except BaseException:
                    os.close(log_fd)
                    raise
                self._log_thread = threading.Thread(target=self._drain_log,
                                                    args=(self._proc.stdout, log_fd), daemon=True)
                self._log_thread.start()
                deadline = time.monotonic() + self.startup_timeout
                while time.monotonic() < deadline:
                    if self.cancel_event.is_set():
                        raise TNCError("Bluetooth connection canceled.")
                    if self._proc.poll() is not None:
                        raise TNCError("tncd exited before starting. Check its version and Bluetooth setup.")
                    if self._scan_proc is not None and self._scan_proc.poll() is not None:
                        raise TNCError("Bluetooth discovery stopped during setup.")
                    if _owns_listener(self._proc.pid, self.port) and self._online(self._proc):
                        if self.cancel_event.is_set():
                            raise TNCError("Bluetooth connection canceled.")
                        self._listener_verified = True
                        return
                    self.cancel_event.wait(0.1)
                if self.cancel_event.is_set():
                    raise TNCError("Bluetooth connection canceled.")
                raise TNCError("Radio Bluetooth link did not become ready; check pairing and the radio's KISS setting.")
            except TNCError as exc:
                log_path = self.log_path
                self.stop()
                if str(exc) == "Bluetooth connection canceled.":
                    raise
                if log_path:
                    raise TNCError(str(exc) + " Log: " + log_path) from exc
                raise
            except OSError as exc:
                self.stop()
                raise TNCError("Could not start the packet bridge: " + str(exc)) from exc
            except BaseException:
                self.stop()
                raise
            finally:
                self._starting = False

    def status(self):
        """Return bridge process state, never a guessed Bluetooth/RF status."""
        # Reading these individual values is safe without blocking the UI on
        # start(), which may wait for the configured startup timeout. Recheck ownership; a
        # concurrent stop can only turn a positive check into a stale snapshot.
        proc, starting, verified = self._proc, self._starting, self._listener_verified
        if starting:
            state, detail = "starting", "Starting local packet bridge."
        elif proc is None:
            state, detail = "stopped", "Packet bridge is stopped."
        elif proc.poll() is not None:
            state, detail = "exited", "Packet bridge exited."
        elif verified and _owns_listener(proc.pid, self.port) and self._online(proc):
            state, detail = "running", "Bluetooth link online; RF is unverified."
        elif verified:
            state, detail = "offline", "Bluetooth link or local packet service went offline."
        else:
            state, detail = "starting", "Packet bridge process running; Bluetooth link not ready."
        return {"state": state, "port": self.port, "detail": detail, "log_path": self.log_path}

    def stop(self):
        """Stop only the process started by this instance and remove its config."""
        with self._lock:
            proc, self._proc = self._proc, None
            scan_proc, self._scan_proc = self._scan_proc, None
            runtime, self._runtime = self._runtime, None
            log_thread, self._log_thread = self._log_thread, None
            scan_thread, self._scan_thread = self._scan_thread, None
            self._listener_verified = False
            try:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
            finally:
                try:
                    if scan_proc is not None and scan_proc.poll() is None:
                        scan_proc.terminate()
                        try:
                            scan_proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            scan_proc.kill()
                            scan_proc.wait(timeout=5)
                finally:
                    if scan_proc is not None and scan_proc.stdin is not None:
                        scan_proc.stdin.close()
                    if scan_thread is not None:
                        scan_thread.join(timeout=2)
                    if log_thread is not None:
                        log_thread.join(timeout=2)
                    if runtime is not None:
                        runtime.cleanup()

    close = stop

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
