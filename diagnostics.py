"""Read bounded, sanitized connection diagnostics for PocketLink's screen.

Only recognized connection events are displayed as fixed phrases. Raw log
lines, payloads, station addresses, credentials and arbitrary error strings
are never returned by this module. Logs remain private and are not modified.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from datetime import datetime


MAX_LOG_BYTES = 64 * 1024
MAX_EVENTS = 8
MAX_DIRECTORY_ENTRIES = 4096


def _state_dir(state_home=None):
    home = Path(state_home) if state_home is not None else Path(
        os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return home / "cardputer-pat"


def _read_tail(path, *, not_before=None):
    """Return a bounded tail of a regular file, or None if inaccessible/stale."""
    fd = None
    try:
        # Avoid following attacker-controlled symlinks or blocking on FIFOs.
        path = Path(path)
        if path.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(str(path), flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if not_before is not None and info.st_mtime < float(not_before) - 2:
            return None
        start = max(0, info.st_size - MAX_LOG_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        data = os.read(fd, MAX_LOG_BYTES)
        if start:
            # A partial leading record must not be mistaken for a complete log line.
            _, marker, data = data.partition(b"\n")
            if not marker:
                return ""
        return data.decode("utf-8", "replace")
    except (OSError, ValueError, TypeError, OverflowError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _bridge_path(log_path, directory):
    """Permit only this application's per-session bridge logs."""
    if not log_path:
        return None
    try:
        path = Path(log_path)
        if not re.fullmatch(r"uvpro-tncd-[A-Za-z0-9_-]+\.log", path.name):
            return None
        if path.is_symlink() or path.parent.resolve() != directory.resolve():
            return None
        return path
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _append(events, value):
    if not events or events[-1] != value:
        events.append(value)


def _bridge_events(text):
    events = []
    for line in text.splitlines():
        # These patterns deliberately emit fixed text rather than log content.
        if line.startswith("agwpe: listening on "):
            _append(events, "Local packet service started.")
        elif line.startswith("bluetooth: SPP profile registered at "):
            _append(events, "Bluetooth serial profile registered.")
        elif line.startswith("bluetooth: calling ConnectProfile on "):
            _append(events, "Connecting Bluetooth serial profile.")
        elif line.startswith("bluetooth: SPP socket ready "):
            _append(events, "Bluetooth serial link opened.")
        elif line.startswith("bridge: port 0 start error: "):
            if "le-connection-abort-by-local" in line:
                _append(events, "Bluetooth connection aborted locally.")
            elif "timed out" in line.lower():
                _append(events, "Bluetooth connection timed out.")
            elif "not paired" in line.lower():
                _append(events, "Radio is not paired.")
            else:
                _append(events, "Radio bridge could not start.")
        elif line.startswith("bluetooth: TX stalled"):
            _append(events, "Bluetooth data transmission stalled.")
    return events[-MAX_EVENTS:]


def _pat_events(text):
    events = []
    for line in text.splitlines():
        # Pat's logger prefixes records with the local date and time. Do not
        # display other records, which can include message data or credentials.
        match = re.match(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (.*)$", line)
        if not match:
            continue
        record = match.group(1)
        if re.fullmatch(r"Connecting to [A-Za-z0-9-]+ \(ax25\+agwpe\)\.\.\.", record):
            _append(events, "Connecting to the packet station.")
        elif record.startswith("Unable to establish connection to remote:"):
            if re.search(r"refused \(DM\)\s*$", record, re.IGNORECASE):
                _append(events, "Remote packet station refused the connection (DM).")
            elif "timed out" in record.lower():
                _append(events, "Remote connection timed out.")
            else:
                _append(events, "Remote connection could not be established.")
    return events[-MAX_EVENTS:]


def _error_hint(error):
    """Describe a known failure without echoing untrusted exception text."""
    text = str(error or "").lower()
    if "connection canceled" in text or "connection cancelled" in text:
        return "Bluetooth setup canceled."
    if "delivery status may be unknown" in text:
        return "Session timed out; check Outbox and Sent before retrying."
    if "bluetooth discovery" in text:
        return "Bluetooth discovery failed; check that the adapter is on."
    if "radio bluetooth link did not become ready" in text:
        return "Radio link not ready; check power, pairing and KISS mode."
    if "packet port" in text and ("in use" in text or "unavailable" in text):
        return "Local packet port is unavailable."
    if "invalid local packet port" in text or "local packet tnc port is invalid" in text:
        return "Local packet port setting is invalid."
    if "radio failed" in text or ("radio" in text and ("not connected" in text or "isn't connected" in text)):
        return "Radio connection failed; check power, pairing and KISS mode."
    if "tncd is not installed" in text:
        return "Packet bridge tncd is not installed."
    if "tncd exited" in text:
        return "Packet bridge exited before it was ready."
    if "requires tncd" in text:
        return "Packet bridge version does not match; check its installation."
    if "could not run tncd" in text or "could not start the packet bridge" in text:
        return "Packet bridge could not be started."
    if "packet bridge is running without a verified listener" in text:
        return "Packet bridge did not provide a verified local service."
    if "pat is not installed" in text:
        return "Pat is not installed or not on PATH."
    if "pat service unavailable" in text:
        return "Pat service is unavailable; check its installation."
    if "pat returned an unreadable" in text:
        return "Pat returned an unreadable response."
    if "cannot read packet settings" in text:
        return "Packet settings could not be read."
    if "gateway callsign" in text or "ax.25 gateway" in text or ("gateway" in text and "invalid" in text):
        return "Check the gateway callsign and SSID in Packet settings."
    if "digipeater callsign" in text:
        return "Check the digipeater callsign and SSID in Packet settings."
    if "bluetooth address" in text and ("enter" in text or "invalid" in text):
        return "Check the radio Bluetooth address in Packet settings."
    if "callsign" in text and "settings" in text:
        return "Check the callsign in Settings."
    if "callsign" in text and ("valid" in text or "invalid" in text):
        return "Check the callsign and SSID."
    if "refused (dm)" in text:
        return "Remote packet station refused the connection (DM)."
    if "le-connection-abort-by-local" in text:
        return "Bluetooth connection aborted locally."
    if "no such host" in text or "temporary failure in name resolution" in text:
        return "The host name could not be resolved."
    if "network is unreachable" in text:
        return "The network is unreachable. Check the network connection."
    if "no route to host" in text:
        return "No network route to the host."
    if any(phrase in text for phrase in ("authentication failed", "authentication failure",
                                          "authentication rejected", "login failed", "invalid password")):
        return "Authentication failed. Check the login details."
    if "connection refused" in text:
        return "The connection was refused."
    return None


def failure_summary(error):
    """Return a fixed, privacy-safe session error for immediate display."""
    return _error_hint(error) or "Session failed; check the connection and settings."


def format_report(kind, outcome, *, error=None, result=None, bridge_log=None,
                  include_pat=False, started_at=None, state_home=None):
    """Create a short report for one explicitly started session.

    ``kind`` is ``packet`` or ``telnet`` and ``outcome`` is ``completed``,
    ``failed`` or ``canceled``. Pass the exact bridge log created by the same
    attempt, and set ``include_pat`` only if Pat's session was actually started.
    ``started_at`` is the wall-clock time before the attempt (``time.time()``),
    used to reject stale Pat and bridge files. Earlier events in a reused Pat
    log are labeled as potentially older. No connection or RF operation occurs.
    """
    if kind not in ("packet", "telnet"):
        raise ValueError("Unknown session kind")
    if outcome not in ("completed", "failed", "canceled"):
        raise ValueError("Unknown session outcome")
    lines = ["Session: " + kind.upper(), "Result: " + outcome.capitalize()]
    if outcome == "completed" and result:
        match = re.fullmatch(r"(?:Telnet|Packet) session completed\. Received ([0-9]{1,8}) message\(s\)\.", str(result))
        if match:
            lines.append("Messages received: " + match.group(1))
        lines.append("Check Outbox and Sent for message status.")
    elif outcome == "failed":
        lines.append(failure_summary(error))
    elif outcome == "canceled":
        lines.append("Setup canceled before the packet session started." if kind == "packet"
                     else "Session canceled.")

    directory = _state_dir(state_home)
    if kind == "packet":
        safe_path = _bridge_path(bridge_log, directory)
        bridge_text = _read_tail(safe_path, not_before=started_at) if safe_path else None
        if bridge_text is None:
            lines.extend(("", "Radio bridge: no log available for this attempt."))
        else:
            events = _bridge_events(bridge_text)
            lines.extend(("", "Radio bridge:"))
            lines.extend(events or ["No recognized connection events."])
    if include_pat:
        pat_text = _read_tail(directory / "pat.log", not_before=started_at)
        if pat_text is None:
            lines.extend(("", "Pat: no recent log available."))
        else:
            events = _pat_events(pat_text)
            if events:
                lines.extend(("", "Recent Pat events (may include earlier activity):"))
                lines.extend(events)
    return "\n".join(lines)


def latest_bridge_report(state_home=None):
    """Summarize the last recorded bridge log without implying a session result.

    Used after restarting PocketLink, when the original in-memory session
    outcome no longer exists. Only regular, non-symlink logs in this app's
    private state directory are considered. Directory enumeration and reading
    are bounded; neither raw log text nor filesystem paths are returned.
    """
    heading = ["Historical radio bridge log.",
               "This is not a current connection or session result."]
    directory = _state_dir(state_home)
    latest = None
    try:
        with os.scandir(directory) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_DIRECTORY_ENTRIES:
                    return "\n".join(heading + ["Too many files to safely find the latest log."])
                if not re.fullmatch(r"uvpro-tncd-[A-Za-z0-9_-]+\.log", entry.name):
                    continue
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                candidate = (info.st_mtime_ns, entry.name, Path(entry.path), info.st_mtime)
                if latest is None or candidate[:2] > latest[:2]:
                    latest = candidate
    except FileNotFoundError:
        return "\n".join(heading + ["No radio bridge log is available."])
    except OSError:
        return "\n".join(heading + ["Radio bridge log is unavailable."])
    if latest is None:
        return "\n".join(heading + ["No radio bridge log is available."])
    path = _bridge_path(latest[2], directory)
    text = _read_tail(path) if path else None
    if text is None:
        return "\n".join(heading + ["Radio bridge log is unavailable."])
    try:
        modified = datetime.fromtimestamp(latest[3]).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        modified = None
    if modified:
        heading.append("Log updated: " + modified)
    heading.append("")
    events = _bridge_events(text)
    return "\n".join(heading + (events or ["No recognized connection events."]))
