"""Private, opt-in packet gateway presets for PocketLink.

This store never changes the currently selected route or radio settings and
does not connect to, tune, or transmit with a radio. A frequency is a note for
the operator, not a radio-control command.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import uuid


_CALLSIGN = re.compile(r"[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


def _text(value, label, limit, *, required=False):
    if not isinstance(value, str):
        raise ValueError(label + " must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError("Enter " + label.lower() + ".")
    if len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(label + " must be a single line of at most " + str(limit) + " characters.")
    return value


def _fields(name, gateway, via="", frequency=""):
    name = _text(name, "Profile name", 48, required=True)
    gateway = _text(gateway, "Gateway callsign", 9, required=True).upper()
    via = _text(via, "Digipeater", 9).upper()
    frequency = _text(frequency, "Frequency note", 40)
    if not _CALLSIGN.fullmatch(gateway):
        raise ValueError("Enter a gateway callsign and optional SSID 0-15.")
    if via and not _CALLSIGN.fullmatch(via):
        raise ValueError("Enter one digipeater callsign and optional SSID 0-15, or leave it blank.")
    return {"name": name, "gateway": gateway, "via": via, "frequency": frequency}


class GatewayProfiles:
    """Manage saved profiles without implicitly migrating or selecting one."""

    def __init__(self, path=None):
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.path = Path(path) if path is not None else config_home / "pocketlink" / "gateways.json"

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None, {"version": 1, "profiles": []}
        except OSError as exc:
            raise RuntimeError("Cannot read gateway profiles: " + str(exc)) from exc
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("version", 1) != 1:
                raise ValueError("unsupported profile file")
            entries = data.get("profiles")
            if not isinstance(entries, list):
                raise ValueError("expected a profile list")
            ids = set()
            names = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("invalid profile entry")
                entry_id = entry.get("id")
                if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id) or entry_id in ids:
                    raise ValueError("invalid or duplicate profile id")
                clean = _fields(entry.get("name"), entry.get("gateway"), entry.get("via", ""),
                                entry.get("frequency", ""))
                # Reject values that would be silently normalized when selected.
                if any(entry.get(key, "") != value for key, value in clean.items()):
                    raise ValueError("profile fields are not normalized")
                name_key = clean["name"].casefold()
                if name_key in names:
                    raise ValueError("duplicate profile name")
                ids.add(entry_id)
                names.add(name_key)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise RuntimeError("Cannot read gateway profiles: " + str(exc)) from exc
        return raw, data

    @staticmethod
    def _find(data, profile_id):
        for profile in data["profiles"]:
            if profile["id"] == profile_id:
                return profile
        raise ValueError("Gateway profile was not found.")

    @staticmethod
    def _check_name(data, name, except_id=None):
        for profile in data["profiles"]:
            if profile["id"] != except_id and profile["name"].casefold() == name.casefold():
                raise ValueError("A gateway profile already has that name.")

    def list(self):
        """Return independent copies, in saved order."""
        _, data = self._read()
        return [dict(profile) for profile in data["profiles"]]

    def get(self, profile_id):
        """Return a profile copy; selection is left to the caller."""
        _, data = self._read()
        return dict(self._find(data, profile_id))

    def _write(self, before, data):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".gateways-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(data, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    current = self.path.read_bytes()
                except FileNotFoundError:
                    current = None
                if current != before:
                    raise RuntimeError("Gateway profiles changed; please retry.")
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        except OSError as exc:
            raise RuntimeError("Cannot save gateway profiles: " + str(exc)) from exc

    def create(self, name, gateway, via="", frequency=""):
        fields = _fields(name, gateway, via, frequency)
        before, data = self._read()
        self._check_name(data, fields["name"])
        profile = {"id": uuid.uuid4().hex, **fields}
        data["profiles"].append(profile)
        self._write(before, data)
        return dict(profile)

    def update(self, profile_id, name, gateway, via="", frequency="", *, expected=None):
        fields = _fields(name, gateway, via, frequency)
        before, data = self._read()
        profile = self._find(data, profile_id)
        if expected is not None and profile != expected:
            raise RuntimeError("Gateway profile changed; reload it and retry.")
        self._check_name(data, fields["name"], profile_id)
        profile.update(fields)
        self._write(before, data)
        return dict(profile)

    def delete(self, profile_id, *, expected=None):
        """Delete a saved preset only; UI must confirm before calling."""
        before, data = self._read()
        target = self._find(data, profile_id)
        if expected is not None and target != expected:
            raise RuntimeError("Gateway profile changed; reload it and retry.")
        data["profiles"].remove(target)
        self._write(before, data)
