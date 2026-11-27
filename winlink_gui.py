#!/usr/bin/env python3
"""PocketLink: a keyboard-first Pat Winlink interface for Cardputer Zero."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time

from PIL import Image, ImageDraw, ImageFont
from appearance import AppearanceSettings
from backend import PatBackend
from dashboard import draw_dashboard, draw_radio_home
from device import W, H, default_keyboard, run_device

# Signal Blue: dark navy, light text and cyan accents, matching the selected
# preview. Colors are decorative and do not imply a connection is active.
BG, PANEL, CARD = "#0a1925", "#102a3b", "#164051"
TEXT, MUTED, ACCENT = "#eef8fc", "#a5c4d1", "#7be4ee"
EDGE, DIVIDER, BADGE = "#315264", "#315264", "#1b4352"


def font(size, bold=False):
    names = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]
             if bold else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


TITLE, BODY, SMALL = font(15, True), font(15), font(11)
ROWS = 5
_PACKET_CANCELLED = object()
# The message is inset on both sides, leaving space for the scroll indicator.
VIEW_LEFT = 12
VIEW_WIDTH = W - VIEW_LEFT - 12


def _fits(text, width, face):
    """Include ink overhangs as well as the font's horizontal advance."""
    if not text:
        return True
    return max(face.getlength(text), face.getbbox(text)[2]) <= width


def _wrap_words(paragraph, width, face):
    """Wrap at whitespace, breaking a word only if it cannot fit by itself.

    Spaces within lines and initial indentation are preserved where they fit;
    whitespace at a soft line break is represented by the break itself. This
    affects rendering only. A single glyph wider than the screen must be
    emitted on its own line; the view clips it to the content area.
    """
    lines, line, spaces = [], "", ""
    for token in re.findall(r" +|[^ ]+", paragraph):
        if token.isspace():
            spaces += token
            continue
        candidate = line + spaces + token
        if _fits(candidate, width, face):
            line = candidate
        else:
            if line:
                lines.append(line)
                line = ""
                spaces = ""
            # Drop initial indentation only when it would split a word that
            # otherwise fits on a fresh line.
            if _fits(spaces + token, width, face):
                line = spaces + token
            elif _fits(token, width, face):
                line = token
            else:
                # An unbroken URL or other overlong token needs hard breaks.
                for char in token:
                    if line and not _fits(line + char, width, face):
                        lines.append(line)
                        line = ""
                    line += char
        spaces = ""
    if line or not lines:
        lines.append(line)
    return lines


def fit(value, width, face=BODY):
    value = str(value)
    while value and face.getlength(value) > width:
        value = value[:-1]
    return value


def wrap(text, width=300, face=BODY, prefer_words=False):
    """Wrap display text without drawing mail line endings as box glyphs.

    Winlink messages commonly use CRLF.  Treat it as a single line break and
    lone CR as a line break too; retain empty paragraphs. Tabs expand to spaces
    and nonprinting control characters are suppressed only for rendering.
    The message returned by Pat remains unchanged. Message views can prefer
    word boundaries; the editor retains character wrapping for cursor layout.
    """
    text = str(text).replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    text = "".join(ch for ch in text if ch == "\n" or (ord(ch) >= 32 and not 127 <= ord(ch) < 160))
    result = []
    for paragraph in text.split("\n"):
        if not paragraph:
            result.append("")
            continue
        if prefer_words:
            result.extend(_wrap_words(paragraph, width, face))
            continue
        line = ""
        for ch in paragraph:
            if line and face.getlength(line + ch) > width:
                result.append(line)
                line = ""
            line += ch
        result.append(line)
    return result or [""]


class RadioSettings:
    """PocketLink-only packet settings, independent of Pat's configuration."""
    def __init__(self, path=None):
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.path = Path(path) if path else config_home / "pocketlink" / "radio.json"

    def load(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data
        except (OSError, ValueError) as exc:
            raise RuntimeError("Cannot read packet settings: " + str(exc)) from exc

    def update(self, field, value):
        value = value.strip().upper()
        if field == "mac":
            if not re.fullmatch(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}", value):
                raise ValueError("Enter the Bluetooth address as AA:BB:CC:DD:EE:FF.")
        elif field in ("gateway", "via"):
            if (field == "gateway" or value) and not re.fullmatch(r"[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?", value):
                raise ValueError("Enter a valid callsign with optional SSID 0-15." if field == "gateway"
                                 else "Enter one digipeater callsign with optional SSID 0-15, or leave blank for direct.")
        else:
            raise ValueError("Unknown packet setting.")
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            before = self.path.read_bytes() if self.path.exists() else None
            data = self.load()
            data[field] = value
            fd, temp = tempfile.mkstemp(prefix=".radio-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                current = self.path.read_bytes() if self.path.exists() else None
                if current != before:
                    raise RuntimeError("Packet settings changed; please retry.")
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        except OSError as exc:
            raise RuntimeError("Cannot save packet settings: " + str(exc)) from exc


class UI:
    def __init__(self, backend=None, radio_config_path=None, tnc_factory=None, appearance_config_path=None):
        self.backend = backend or PatBackend()
        self.radio = RadioSettings(radio_config_path)
        self.appearance = AppearanceSettings(appearance_config_path)
        try:
            self.layout = self.appearance.load()
        except RuntimeError:
            # A damaged appearance file must not prevent access to messages.
            # A later attempt to change it will display the actual error.
            self.layout = "classic"
        self.tnc_factory = tnc_factory
        self._packet_control = None
        self._session_progress = None
        self._session_started = None
        # Radio-style home navigation remembers the selected action in each
        # column while the operator switches sides or visits a submenu.
        self._radio_focus = 0
        self._radio_selected = [0, 4]
        self._radio_right_top = 0
        self.results = queue.Queue()
        self.draft = {"to": "", "subject": "", "body": ""}
        self.settings = {}
        self.home()

    def menu(self, title, items, back=None):
        self.mode, self.title, self.items = "menu", title, items
        self.sel = self.top = 0
        self.back = back or self.home

    def home(self):
        self.menu("POCKETLINK", [
            ("Inbox", lambda: self.folder("in")),
            ("Compose message", self.compose),
            ("Outbox", lambda: self.folder("out")),
            ("Sent messages", lambda: self.folder("sent")),
            ("Connect via Telnet", self.connect_confirm),
            ("Connect via packet", self.packet_intro),
            ("Packet radio settings", self.packet_settings),
            ("Settings", self.load_settings),
            ("About / help", self.help),
            ("Exit", self.quit),
        ], self.quit)
        if self.layout == "radio":
            self.sel = self._radio_selected[self._radio_focus]

    def quit(self):
        # Do not release the framebuffer or stop Pat while a worker is active.
        if self.mode == "busy":
            return
        self.mode = "quit"

    def view(self, title, text, back=None):
        self.mode, self.title, self.text = "view", title, str(text)
        # Message text is immutable for the lifetime of the view. Wrap once
        # to keep keyboard scrolling responsive even for long messages.
        self.view_lines = wrap(self.text, width=VIEW_WIDTH, prefer_words=True)
        self.top = 0
        self.back = back or self.home
        self.accept = None
        self.accept_hint = "F/X SCROLL   ENTER NEXT   ESC BACK"

    def edit(self, title, value, done, back, multiline=False, secret=False):
        self.mode, self.title, self.text = "edit", title, str(value)
        self.cursor = len(self.text)
        self.done, self.back = done, back
        self.multiline, self.secret = multiline, secret

    def task(self, title, work, done, back=None, session=None):
        self.mode, self.title = "busy", title
        self.text = "Please wait..."
        self.back = back or self.home
        self._session_progress = session
        self._session_started = time.monotonic() if session else None
        def run():
            try:
                self.results.put((True, work(), done, self.back))
            except Exception as exc:
                self.results.put((False, str(exc), done, self.back))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            ok, value, done, back = self.results.get_nowait()
        except queue.Empty:
            return
        self._packet_control = None
        self._session_progress = None
        self._session_started = None
        if ok:
            try:
                done(value)
            except Exception as exc:
                self.view("DISPLAY ERROR", str(exc), back)
        else:
            self.view("PAT ERROR", value, back)

    def folder(self, folder):
        names = {"in": "INBOX", "out": "OUTBOX", "sent": "SENT"}
        def loaded(messages):
            if not messages:
                self.view(names[folder], "No messages in this folder.", self.home)
                return
            items = []
            for message in messages:
                marker = "* " if message.get("unread") else "  "
                label = marker + (message.get("subject") or "(no subject)")
                items.append((label, lambda m=message: self.open_message(folder, m)))
            items.append(("Back", self.home))
            self.menu(names[folder], items, self.home)
        self.task("LOADING " + names[folder], lambda: self.backend.list_messages(folder), loaded)

    def open_message(self, folder, message):
        def loaded(m):
            attachments = m.get("attachments") or []
            text = ("From: " + str(m.get("from", "")) + "\nTo: " + str(m.get("to", "")) +
                    "\nSubject: " + str(m.get("subject", "")) + "\nDate: " + str(m.get("date", "")) +
                    "\n\n" + str(m.get("body", "")))
            if attachments:
                text += "\n\nAttachments: " + ", ".join(str(a) for a in attachments)
            self.view("MESSAGE", text, lambda: self.folder(folder))
        self.task("OPENING MESSAGE", lambda: self.backend.read_message(folder, message["id"]), loaded,
                  lambda: self.folder(folder))

    def compose(self):
        self.menu("COMPOSE", [
            ("To: " + (self.draft["to"] or "(enter address)"), lambda: self.edit_draft("to")),
            ("Subject: " + (self.draft["subject"] or "(enter subject)"), lambda: self.edit_draft("subject")),
            ("Edit message body", lambda: self.edit_draft("body")),
            ("Review and queue", self.review_draft),
            ("Clear draft", self.clear_draft_confirm),
            ("Back", self.home),
        ], self.home)

    def edit_draft(self, field):
        def save(value):
            self.draft[field] = value
            self.compose()
        self.edit({"to": "TO", "subject": "SUBJECT", "body": "MESSAGE BODY"}[field],
                  self.draft[field], save, self.compose, multiline=field == "body")

    def clear_draft_confirm(self):
        def clear():
            self.draft = {"to": "", "subject": "", "body": ""}
            self.compose()
        self.menu("CLEAR DRAFT?", [("Keep draft", self.compose), ("Clear draft", clear)], self.compose)

    def review_draft(self):
        if not self.draft["to"].strip() or not self.draft["subject"].strip() or not self.draft["body"].strip():
            self.view("DRAFT INCOMPLETE", "Enter a recipient, subject, and message body.", self.compose)
            return
        self.view("REVIEW DRAFT", "To: " + self.draft["to"] + "\nSubject: " + self.draft["subject"] +
                  "\n\n" + self.draft["body"], self.compose)
        self.accept = self.queue_confirm
        self.accept_hint = "F/X SCROLL   ENTER QUEUE   ESC EDIT"

    def queue_confirm(self):
        self.menu("QUEUE MESSAGE?", [("Queue in Pat outbox", self.queue_draft),
                                      ("Keep editing", self.compose)], self.compose)

    def queue_draft(self):
        snapshot = self.draft.copy()
        def complete(_):
            self.draft = {"to": "", "subject": "", "body": ""}
            self.view("QUEUED", "Message saved in Pat's outbox. Connect via Telnet or packet to send it.")
        self.task("QUEUING MESSAGE", lambda: self.backend.queue_message(snapshot["to"], snapshot["subject"], snapshot["body"]),
                  complete, self.compose)

    def connect_confirm(self):
        self.menu("CONNECT VIA TELNET?", [
            ("Connect / send / receive", self.connect),
            ("Cancel", self.home),
        ], self.home)

    def connect(self):
        self.task("TELNET SESSION", self.backend.connect_telnet,
                  lambda output: self.view("SESSION FINISHED", output or "Session completed."),
                  session="telnet")

    def packet_settings(self):
        try:
            settings = self.radio.load()
        except Exception as exc:
            self.view("PACKET SETTINGS", str(exc), self.home)
            return
        self.menu("PACKET SETTINGS", [
            ("Bluetooth: " + (settings.get("mac") or "(set address)"), lambda: self.edit_radio_setting("mac")),
            ("Gateway: " + (settings.get("gateway") or "(set callsign)"), lambda: self.edit_radio_setting("gateway")),
            ("Via: " + (settings.get("via") or "Direct"), lambda: self.edit_radio_setting("via")),
            ("Setup help", self.packet_help),
            ("Back", self.home),
        ], self.home)

    def edit_radio_setting(self, field):
        try:
            current = self.radio.load().get(field, "")
        except Exception as exc:
            self.view("PACKET SETTINGS", str(exc), self.home)
            return
        def save(value):
            try:
                self.radio.update(field, value)
            except Exception as exc:
                self.view("INVALID SETTING", str(exc), self.packet_settings)
                return
            self.packet_settings()
        title = {"mac": "BLUETOOTH ADDRESS", "gateway": "RMS GATEWAY CALL", "via": "DIGIPEATER (OPTIONAL)"}[field]
        self.edit(title, current, save, self.packet_settings)

    def packet_help(self):
        self.view("PACKET SETUP", "Pair your UV-PRO with the Cardputer using Bluetooth. "
                  "Enable KISS TNC on the radio. Enter its Bluetooth address and an RMS gateway callsign here. "
                  "For a digipeater, enter its callsign in Via; leave Via blank for a direct connection.\n\n"
                  "Set and verify the radio's KISS frequency and 1200-baud packet settings for that gateway. "
                  "Check your radio and gateway instructions before transmitting.", self.packet_settings)

    def packet_intro(self):
        try:
            settings = self.radio.load()
        except Exception as exc:
            self.view("PACKET SETTINGS", str(exc), self.home)
            return
        if not settings.get("mac") or not settings.get("gateway"):
            self.view("PACKET SETUP NEEDED", "Enter the Bluetooth address and RMS gateway callsign in Packet radio settings.",
                      self.packet_settings)
            return
        route = str(settings.get("via") or "Direct")
        self.view("PACKET RADIO", "Gateway: " + str(settings["gateway"]) + "\nVia: " + route + "\n\n"
                  "This session will TRANSMIT via the UV-PRO. Pair the radio, enable KISS TNC, "
                  "and verify its packet frequency and 1200 baud match the gateway.\n\n"
                  "Press Enter to choose whether to connect.", self.home)
        self.accept = lambda: self.packet_confirm(settings)

    def packet_confirm(self, settings):
        route = "Via " + str(settings["via"]) if settings.get("via") else "Direct connection"
        self.menu("TX " + str(settings["gateway"]) + "?", [
            ("Cancel", self.home),
            (route, lambda: self.packet_connect(settings)),
        ], self.home)

    def packet_connect(self, settings):
        # The same lock gates cancellation and admission to the Pat session.
        # Once admitted, Escape cannot claim the radio operation was canceled.
        control = {"lock": threading.Lock(), "cancel": threading.Event(), "phase": "starting"}
        self._packet_control = control
        def work():
            try:
                call = self.backend.get_settings().get("callsign", "")
                if not call:
                    raise RuntimeError("Set your callsign in Settings before starting a packet session.")
                factory = self.tnc_factory
                if factory is None:
                    from uvpro_tnc import UVProTNC
                    factory = UVProTNC
                # A local listener alone does not prove Bluetooth or RF status.
                with factory(settings["mac"], call, port=8000, cancel_event=control["cancel"]):
                    with control["lock"]:
                        if control["cancel"].is_set():
                            return _PACKET_CANCELLED
                        control["phase"] = "session"
                    return self.backend.connect_packet(settings["gateway"], 8000, settings.get("via") or "")
            except Exception:
                with control["lock"]:
                    cancelled = control["cancel"].is_set() and control["phase"] != "session"
                if cancelled:
                    return _PACKET_CANCELLED
                raise
        def complete(output):
            if output is _PACKET_CANCELLED:
                self.view("PACKET CANCELED", "Bluetooth setup canceled. The packet session was not started.")
            else:
                self.view("SESSION FINISHED", output or "Packet session completed.")
        self.task("PACKET SESSION", work, complete, self.home, session="packet")

    def load_settings(self):
        def loaded(settings):
            self.settings = settings
            self.settings_menu()
        self.task("LOADING SETTINGS", self.backend.get_settings, loaded)

    def settings_menu(self):
        layout_name = {"classic": "Classic", "dashboard": "Dashboard", "radio": "Radio"}.get(self.layout, "Classic")
        self.menu("SETTINGS", [
            ("Callsign: " + (self.settings.get("callsign") or "(not set)"), lambda: self.edit_setting("callsign")),
            ("Grid: " + (self.settings.get("locator") or "(optional)"), lambda: self.edit_setting("locator")),
            ("Password: " + ("set" if self.settings.get("has_password") else "not set"), lambda: self.edit_setting("password")),
            ("Layout: " + layout_name, self.layout_menu),
            ("Back", self.home),
        ], self.home)

    def layout_menu(self):
        self.menu("CHOOSE LAYOUT", [
            ("Classic list" + (" (current)" if self.layout == "classic" else ""),
             lambda: self.set_layout("classic")),
            ("Tile dashboard" + (" (current)" if self.layout == "dashboard" else ""),
             lambda: self.set_layout("dashboard")),
            ("Radio style" + (" (current)" if self.layout == "radio" else ""),
             lambda: self.set_layout("radio")),
            ("Back", self.settings_menu),
        ], self.settings_menu)

    def set_layout(self, value):
        try:
            self.appearance.save(value)
        except (RuntimeError, ValueError) as exc:
            self.view("LAYOUT ERROR", str(exc), self.layout_menu)
            return
        self.layout = value
        self.home()

    def edit_setting(self, field):
        def save(value):
            current = dict(self.settings)
            if field == "password":
                password = value if value else None
            else:
                current[field] = value.strip()
                password = None
            self.task("SAVING SETTINGS", lambda: self.backend.save_settings(current.get("callsign", ""),
                      current.get("locator", ""), password), lambda _: self.load_settings(), self.settings_menu)
        self.edit({"callsign": "YOUR CALLSIGN", "locator": "GRID LOCATOR", "password": "WINLINK PASSWORD"}[field],
                  "" if field == "password" else self.settings.get(field, ""), save, self.settings_menu,
                  secret=field == "password")

    def help(self):
        self.view("HELP", "F/X or arrows move. Enter selects. Esc goes back.\n\n"
                  "On the radio-style home screen, F/X moves within a column; "
                  "Z/C or left/right switches columns.\n\n"
                  "In message body: Enter adds a line; Shift+Enter saves. "
                  "Left/right moves the cursor.\n\n"
                  "Queue a message, then use Telnet or packet to send and receive. "
                  "Packet needs a paired KISS radio and a reachable gateway. "
                  "Pat stores the password and mail on this device.")

    def input(self, key, char=None, ctrl=False):
        if self.mode == "busy":
            control = self._packet_control
            if key == "ESC" and control is not None:
                with control["lock"]:
                    if control["phase"] == "starting":
                        control["phase"] = "canceling"
                        control["cancel"].set()
            return
        if self.mode == "quit":
            return
        if self.mode == "menu":
            radio_home = self.title == "POCKETLINK" and self.layout == "radio"
            if radio_home and key in ("UP", "DOWN", "LEFT", "RIGHT"):
                column = 0 if self.sel < 4 else 1
                self._radio_selected[column] = self.sel
                if key in ("LEFT", "RIGHT"):
                    target = 0 if key == "LEFT" else 1
                    self._radio_focus = target
                    self.sel = self._radio_selected[target]
                else:
                    first, end = (0, 4) if column == 0 else (4, len(self.items))
                    step = -1 if key == "UP" else 1
                    self.sel = first + (self.sel - first + step) % (end - first)
                    self._radio_selected[column] = self.sel
                    self._radio_focus = column
            elif key in ("UP", "LEFT"):
                self.sel = (self.sel - 1) % len(self.items)
            elif key in ("DOWN", "RIGHT"):
                self.sel = (self.sel + 1) % len(self.items)
            elif key == "ENTER":
                if radio_home:
                    self._radio_focus = 0 if self.sel < 4 else 1
                    self._radio_selected[self._radio_focus] = self.sel
                self.items[self.sel][1]()
            elif key == "ESC":
                self.back()
        elif self.mode == "view":
            if key in ("UP", "LEFT"):
                self.top = max(0, self.top - 1)
            elif key in ("DOWN", "RIGHT"):
                self.top = min(max(0, len(self.view_lines) - ROWS), self.top + 1)
            elif key == "ENTER" and hasattr(self, "accept") and self.accept:
                action, self.accept = self.accept, None
                action()
            elif key in ("ENTER", "ESC"):
                self.accept = None
                self.back()
        elif self.mode == "edit":
            if key == "ESC":
                self.text = ""
                self.back()
            elif key == "LEFT":
                self.cursor = max(0, self.cursor - 1)
            elif key == "RIGHT":
                self.cursor = min(len(self.text), self.cursor + 1)
            elif key == "BACKSPACE" and self.cursor:
                self.text = self.text[:self.cursor-1] + self.text[self.cursor:]
                self.cursor -= 1
            elif key == "ENTER" and (not self.multiline or ctrl):
                value, self.text = self.text, ""
                self.done(value)
            else:
                insertion = "\n" if key == "ENTER" and self.multiline else (char or "")
                if insertion and len(self.text) < (256 if self.secret else 16000):
                    self.text = self.text[:self.cursor] + insertion + self.text[self.cursor:]
                    self.cursor += len(insertion)

    def draw(self):
        image = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(image)
        # The badge names the mail service, not connection or radio status.
        d.rectangle((0, 0, W, 27), fill=PANEL)
        d.rectangle((0, 0, 3, 27), fill=ACCENT)
        d.line((0, 27, W, 27), fill=EDGE)
        d.text((11, 5), fit(self.title, 235, TITLE), font=TITLE, fill=TEXT)
        if self.mode == "menu":
            dashboard_home = self.title == "POCKETLINK" and self.layout == "dashboard"
            radio_home = self.title == "POCKETLINK" and self.layout == "radio"
            if self.title == "POCKETLINK" and not dashboard_home and not radio_home:
                d.rounded_rectangle((259, 6, 311, 21), radius=3, fill=BADGE, outline=EDGE)
                d.text((263, 8), "WINLINK", font=SMALL, fill=ACCENT)
            else:
                if dashboard_home:
                    counter = f"{self.sel // 4 + 1}/{(len(self.items) + 3) // 4}"
                elif radio_home:
                    counter = (f"L {self.sel + 1}/4" if self.sel < 4
                               else f"R {self.sel - 3}/{len(self.items) - 4}")
                else:
                    counter = f"{self.sel+1}/{len(self.items)}"
                d.text((310-SMALL.getlength(counter), 8), counter, font=SMALL, fill=ACCENT)
            if dashboard_home:
                draw_dashboard(d, self.items, self.sel, BODY, SMALL, fit,
                               {"selected": CARD, "panel": PANEL, "accent": ACCENT,
                                "edge": EDGE, "text": TEXT, "muted": MUTED})
            elif radio_home:
                right_count = max(0, len(self.items) - 4)
                right_index = (self.sel if self.sel >= 4 else self._radio_selected[1]) - 4
                self._radio_right_top = max(0, min(self._radio_right_top, max(0, right_count - 4)))
                if right_index < self._radio_right_top:
                    self._radio_right_top = right_index
                elif right_index >= self._radio_right_top + 4:
                    self._radio_right_top = right_index - 3
                draw_radio_home(d, self.items, self.sel, BODY, SMALL, fit,
                                {"selected": CARD, "panel": PANEL, "accent": ACCENT,
                                 "edge": EDGE, "text": TEXT, "muted": MUTED},
                                right_top=self._radio_right_top)
            else:
                self.top = min(self.top, self.sel)
                if self.sel >= self.top + 4:
                    self.top = self.sel - 3
                if len(self.items) > 4:
                    d.line((317, 33, 317, 143), fill=DIVIDER, width=2)
                    thumb_h = max(8, int(110 * 4 / len(self.items)))
                    thumb_y = 33 + int((110 - thumb_h) * self.top / (len(self.items) - 4))
                    d.line((317, thumb_y, 317, thumb_y + thumb_h), fill=ACCENT, width=2)
                for row, (label, _) in enumerate(self.items[self.top:self.top+4]):
                    y = 32 + row * 28
                    selected = row + self.top == self.sel
                    if selected:
                        d.rounded_rectangle((6, y-1, 313, y+24), radius=4, fill=CARD, outline=EDGE)
                        d.rectangle((7, y+3, 9, y+20), fill=ACCENT)
                    else:
                        d.line((15, y+24, 307, y+24), fill=DIVIDER)
                    number = f"{row + self.top + 1:02d}"
                    d.text((16, y+6), number, font=SMALL, fill=ACCENT if selected else MUTED)
                    d.text((43, y+2), fit(label, 256), font=BODY, fill=TEXT if selected else MUTED)
            hint = ("F/X MOVE  Z/C COLUMN  ENTER OPEN" if radio_home
                    else "F/X OR ARROWS MOVE     ENTER OPEN")
        elif self.mode == "edit":
            before = self.text[:self.cursor]
            after = self.text[self.cursor:]
            shown = ("*" * len(before) if self.secret else before) + "|" + ("*" * len(after) if self.secret else after)
            before_lines = wrap(("*" * len(before) if self.secret else before) + "|")
            all_lines = wrap(shown)
            start = max(0, len(before_lines) - ROWS)
            for row, line in enumerate(all_lines[start:start+ROWS]):
                d.text((12, 32+row*22), line, font=BODY, fill=TEXT)
            hint = "SHIFT+ENTER SAVE   ESC BACK" if self.multiline else "ENTER SAVE   ESC BACK"
        else:
            busy_hint = "PLEASE WAIT"
            session_busy = self.mode == "busy" and self._session_progress is not None
            if session_busy and self._session_progress == "packet" and self._packet_control is not None:
                with self._packet_control["lock"]:
                    phase = self._packet_control["phase"]
                if phase == "starting":
                    busy_text = "Connecting to radio...\nThis may take about 3 minutes.\nEsc cancels Bluetooth setup."
                    busy_hint = "ESC CANCEL SETUP"
                elif phase == "canceling":
                    busy_text = "Canceling Bluetooth setup...\nPlease wait for cleanup."
                else:
                    busy_text = "Packet session in progress.\nWaiting for Pat's result.\nEsc cannot cancel this session."
            elif session_busy and self._session_progress == "telnet":
                busy_text = "Telnet session in progress.\nWaiting for Pat's result."
            else:
                busy_text = self.text
            wrapped = self.view_lines if self.mode == "view" else wrap(busy_text, width=VIEW_WIDTH, prefer_words=True)
            start = self.top if self.mode == "view" else 0
            # Clip glyph ink at the right and bottom edges, including the
            # unusual case of a single glyph wider than the available width.
            area = Image.new("RGB", (VIEW_WIDTH, 90 if session_busy else 110), BG)
            area_draw = ImageDraw.Draw(area)
            for row, line in enumerate(wrapped[start:start+ROWS]):
                area_draw.text((0, row*22), line, font=BODY, fill=TEXT)
            image.paste(area, (VIEW_LEFT, 32))
            if session_busy:
                # Pat does not provide a reliable overall session percentage.
                # A moving segment means activity, not estimated completion.
                left, right, y = VIEW_LEFT, W - 12, 132
                d.rounded_rectangle((left, y, right, y + 8), radius=3, fill=PANEL, outline=EDGE)
                travel, segment = right - left - 4, 52
                max_offset = travel - segment
                elapsed = max(0.0, time.monotonic() - self._session_started)
                offset = int(elapsed * 85) % (2 * max_offset)
                if offset > max_offset:
                    offset = 2 * max_offset - offset
                start_x = left + 2 + offset
                d.rounded_rectangle((start_x, y + 2, start_x + segment, y + 6), radius=2, fill=ACCENT)
            if self.mode == "view" and len(wrapped) > ROWS:
                d.line((316, 33, 316, 141), fill=DIVIDER, width=2)
                thumb_h = max(8, int(108 * ROWS / len(wrapped)))
                thumb_y = 33 + int((108 - thumb_h) * self.top / (len(wrapped) - ROWS))
                d.line((316, thumb_y, 316, thumb_y + thumb_h), fill=ACCENT, width=2)
            if self.mode == "busy":
                hint = busy_hint
            elif self.accept:
                hint = self.accept_hint
            else:
                hint = "F/X SCROLL    ENTER / ESC BACK"
        d.rectangle((0, 150, W, H), fill=PANEL)
        d.line((0, 150, W, 150), fill=EDGE)
        d.text((10, 153), fit(hint, 300, SMALL), font=SMALL, fill=TEXT)
        return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framebuffer", default=os.environ.get("WINLINK_FB") or os.environ.get("APPLAUNCH_LINUX_FBDEV_DEVICE") or "/dev/fb0")
    parser.add_argument("--keyboard", default=default_keyboard())
    parser.add_argument("--preview", metavar="PNG", help="render the home screen without opening hardware")
    args = parser.parse_args()
    os.umask(0o077)
    ui = UI()
    try:
        if args.preview:
            ui.draw().save(args.preview)
        else:
            run_device(ui, args.framebuffer, args.keyboard)
    finally:
        ui.backend.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print("PocketLink:", exc, file=sys.stderr)
        sys.exit(1)
