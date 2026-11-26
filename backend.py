"""Adapter for Pat's local HTTP API; no network session starts automatically.

Pat keeps its normal configuration and mailbox. The child HTTP service is
bound to an ephemeral IPv4 loopback port and stopped when this adapter closes.
All public operations are synchronous; call them off the UI thread.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from urllib import error, parse, request


class PatError(RuntimeError):
    """An operation could not be completed; the message is safe to show."""


class PatTimeout(PatError):
    """A Pat HTTP request timed out; its server-side result may be unknown."""


class PatBackend:
    BOXES = {"inbox": "in", "outbox": "out", "in": "in", "out": "out",
             "sent": "sent", "archive": "archive"}

    def __init__(self, executable=None, config_path=None):
        self.executable = executable or shutil.which("pat") or shutil.which("pat-winlink")
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.config_path = Path(config_path) if config_path else config_home / "pat" / "config.json"
        self._process = None
        self._base = None
        self._runtime = None
        self._lock = threading.RLock()

    def available(self):
        return bool(self.executable)

    def _config(self):
        try:
            if not self.config_path.exists():
                return {}
            with self.config_path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            return data
        except (OSError, ValueError) as exc:
            raise PatError("Cannot read Pat configuration: " + str(exc)) from exc

    def get_settings(self):
        with self._lock:
            data = self._config()
            return {"callsign": str(data.get("mycall", "")),
                    "locator": str(data.get("locator", "")),
                    "has_password": bool(data.get("secure_login_password", ""))}

    def save_settings(self, callsign, locator, password=None):
        callsign, locator = callsign.strip().upper(), locator.strip()
        if not re.fullmatch(r"[A-Z0-9]{2,12}(?:-[0-9]{1,2})?", callsign):
            raise PatError("Enter a valid callsign.")
        if locator and not re.fullmatch(r"[A-Ra-r]{2}[0-9]{2}(?:[A-Xa-x]{2}(?:[0-9]{2})?)?", locator):
            raise PatError("Enter a valid Maidenhead grid locator, or leave it empty.")
        if password is not None and ("\n" in password or "\r" in password):
            raise PatError("Password cannot contain a line break.")
        with self._lock:
            self.close()
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            before = self.config_path.read_bytes() if self.config_path.exists() else None
            data = self._config()
            if before is None:
                # A new, otherwise minimal config still retains Pat's standard
                # Telnet alias for use from Pat's own CLI.
                data["connect_aliases"] = {"telnet": "telnet://{mycall}:CMSTelnet@cms.winlink.org:8772/wl2k"}
            data["mycall"] = callsign
            data["locator"] = locator
            if password is not None:
                data["secure_login_password"] = password
            # Recheck before replacing to avoid clobbering a simultaneous edit.
            try:
                fd, temp = tempfile.mkstemp(prefix=".pat-config-", dir=self.config_path.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(data, stream, indent=2, ensure_ascii=False)
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    current = self.config_path.read_bytes() if self.config_path.exists() else None
                    if current != before:
                        raise PatError("Pat configuration changed; please try saving again.")
                    os.replace(temp, self.config_path)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
            except OSError as exc:
                raise PatError("Cannot save Pat configuration: " + str(exc)) from exc

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            return
        if not self.available():
            raise PatError("Pat is not installed or is not on PATH.")
        if not self.get_settings()["callsign"]:
            raise PatError("Set your callsign in Settings first.")
        # Never bind Pat's API to LAN. Use a runtime copy so an existing Pat
        # listener or scheduled connection is not silently started by the UI.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {key: value for key, value in os.environ.items() if not key.startswith("PAT_")}
        state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        state_dir = state_home / "cardputer-pat"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._runtime = tempfile.TemporaryDirectory(prefix="pat-", dir=state_dir)
        runtime_config = Path(self._runtime.name) / "config.json"
        config = self._config()
        config.update({"listen": [], "schedule": {}, "version_reporting_disabled": True,
                       "http_addr": "127.0.0.1:" + str(port), "hamlib_rigs": {}})
        gps = dict(config.get("gpsd") or {})
        gps.update({"update_locator": False, "enable_http": False})
        config["gpsd"] = gps
        with runtime_config.open("w", encoding="utf-8") as stream:
            json.dump(config, stream)
        try:
            runtime_config.chmod(0o600)
        except OSError:
            pass
        args = [self.executable, "--config", str(runtime_config), "--log", str(state_dir / "pat.log"),
                "--event-log", str(state_dir / "eventlog.json"), "http", "--addr", "127.0.0.1:" + str(port)]
        try:
            self._process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self._runtime.cleanup()
            self._runtime = None
            raise PatError("Could not start Pat: " + str(exc)) from exc
        self._base = "http://127.0.0.1:" + str(port)
        proc = self._process
        # Give an immediate bind failure time to surface before probing.
        time.sleep(0.15)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self._process is not proc:
                raise PatError("Pat service was stopped.")
            if proc.poll() is not None:
                self.close()
                raise PatError("Pat could not start. Check the callsign and Pat installation.")
            try:
                self._request("/api/status", timeout=0.3, ensure=False)
                if self._process is not proc or proc.poll() is not None:
                    raise PatError("Pat service was stopped.")
                return
            except PatError:
                time.sleep(0.1)
        self.close()
        raise PatError("Pat did not start its local service.")

    def close(self):
        # Do not wait for the operation lock: closing the UI must be able to
        # interrupt a blocked Telnet session running in a worker thread.
        proc, self._process = self._process, None
        runtime, self._runtime = self._runtime, None
        self._base = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if runtime is not None:
            runtime.cleanup()

    def _request(self, path, data=None, timeout=10, ensure=True):
        if ensure:
            self._start()
        encoded = None if data is None else parse.urlencode(data).encode("utf-8")
        base = self._base
        if not base:
            raise PatError("Pat service was stopped.")
        req = request.Request(base + path, data=encoded)
        opener = request.build_opener(request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace").strip()
            raise PatError("Pat: " + (detail or str(exc.code))) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise PatTimeout("Pat request timed out.") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise PatTimeout("Pat request timed out.") from exc
            raise PatError("Pat service unavailable: " + str(exc)) from exc
        except (OSError, TimeoutError) as exc:
            raise PatError("Pat service unavailable: " + str(exc)) from exc

    def _box(self, folder):
        try:
            return self.BOXES[folder.lower()]
        except (KeyError, AttributeError) as exc:
            raise PatError("Unknown mailbox folder.") from exc

    @staticmethod
    def _address(value):
        if isinstance(value, dict):
            return str(value.get("Addr", value.get("addr", "")))
        return str(value or "")

    def _message(self, value):
        recipients = value.get("To") or []
        attachments = []
        for item in value.get("Files") or []:
            if isinstance(item, dict):
                name = item.get("Name") or item.get("name") or item.get("Filename") or item.get("filename")
                attachments.append(str(name) if name else "attachment")
            else:
                attachments.append("attachment")
        return {"id": str(value.get("MID", "")), "subject": str(value.get("Subject", "")),
                "from": self._address(value.get("From")),
                "to": ", ".join(self._address(item) for item in recipients),
                "date": str(value.get("Date", "")), "unread": bool(value.get("Unread", False)),
                "body": str(value.get("Body", "")), "attachments": attachments}

    def list_messages(self, folder):
        with self._lock:
            try:
                values = json.loads(self._request("/api/mailbox/" + self._box(folder)))
                return [self._message(value) for value in values]
            except (ValueError, TypeError, AttributeError) as exc:
                raise PatError("Pat returned an unreadable mailbox.") from exc

    def read_message(self, folder, message_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", message_id):
            raise PatError("Invalid message ID.")
        with self._lock:
            try:
                value = json.loads(self._request("/api/mailbox/" + self._box(folder) + "/" + message_id))
                return self._message(value)
            except (ValueError, TypeError, AttributeError) as exc:
                raise PatError("Pat returned an unreadable message.") from exc

    def queue_message(self, to, subject, body):
        if not to.strip() or not subject.strip() or not body.strip():
            raise PatError("Enter a recipient, subject, and message.")
        with self._lock:
            own_call = str(self._config().get("mycall") or "").strip().casefold()
            if own_call:
                own_addresses = {own_call, own_call + "@winlink.org"}
                for address in re.split(r"[,;\s]+", to.strip()):
                    candidate = address.casefold()
                    if candidate.startswith("smtp:"):
                        candidate = candidate[5:]
                    if candidate in own_addresses:
                        raise PatError("Winlink cannot deliver to your own account. "
                                       "For a test, send to TEST@winlink.org.")
            date = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self._request("/api/mailbox/out", {"to": to.strip(), "subject": subject.strip(), "body": body, "date": date})

    def connect_telnet(self):
        with self._lock:
            # Explicit CMS URL avoids a user-overridden alias selecting a radio.
            call = self.get_settings()["callsign"].upper()
            if not re.fullmatch(r"[A-Z0-9]{2,12}(?:-[0-9]{1,2})?", call):
                raise PatError("Set a valid callsign in Settings first.")
            url = "telnet://" + call + ":CMSTelnet@cms.winlink.org:8772/wl2k"
            try:
                result = json.loads(self._request("/api/connect", {"url": url}, timeout=300))
            except PatTimeout as exc:
                raise PatError("Session timed out; delivery status may be unknown. Check Sent and Outbox before retrying.") from exc
            except (ValueError, TypeError) as exc:
                raise PatError("Pat returned an unreadable session result.") from exc
            count = result.get("NumReceived", 0)
            return "Telnet session completed. Received %s message(s)." % count
