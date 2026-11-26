#!/usr/bin/env python3
"""PocketLink: a keyboard-first Pat Winlink interface for Cardputer Zero."""
import argparse
import os
import queue
import re
import sys
import threading

from PIL import Image, ImageDraw, ImageFont
from backend import PatBackend
from device import W, H, default_keyboard, run_device

# Muted charcoal and high-contrast green evoke a modern field radio. These
# colors are decorative; none are used as an unverified connection indicator.
BG, PANEL, CARD = "#0d1518", "#172328", "#263c38"
TEXT, MUTED, ACCENT, GOLD = "#f0f5e9", "#aebfba", "#b7e77e", "#edc681"
EDGE, DIVIDER = "#3d5c50", "#24363a"


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


class UI:
    def __init__(self, backend=None):
        self.backend = backend or PatBackend()
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
            ("Settings", self.load_settings),
            ("About / help", self.help),
            ("Exit", self.quit),
        ], self.quit)

    def quit(self):
        self.mode = "quit"

    def view(self, title, text, back=None):
        self.mode, self.title, self.text = "view", title, str(text)
        # Message text is immutable for the lifetime of the view. Wrap once
        # to keep keyboard scrolling responsive even for long messages.
        self.view_lines = wrap(self.text, width=VIEW_WIDTH, prefer_words=True)
        self.top = 0
        self.back = back or self.home
        self.accept = None

    def edit(self, title, value, done, back, multiline=False, secret=False):
        self.mode, self.title, self.text = "edit", title, str(value)
        self.cursor = len(self.text)
        self.done, self.back = done, back
        self.multiline, self.secret = multiline, secret

    def task(self, title, work, done, back=None):
        self.mode, self.title = "busy", title
        self.text = "Please wait..."
        self.back = back or self.home
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

    def queue_confirm(self):
        self.menu("QUEUE MESSAGE?", [("Queue in Pat outbox", self.queue_draft),
                                      ("Keep editing", self.compose)], self.compose)

    def queue_draft(self):
        snapshot = self.draft.copy()
        def complete(_):
            self.draft = {"to": "", "subject": "", "body": ""}
            self.view("QUEUED", "Message saved in Pat's outbox. Connect via Telnet to send it.")
        self.task("QUEUING MESSAGE", lambda: self.backend.queue_message(snapshot["to"], snapshot["subject"], snapshot["body"]),
                  complete, self.compose)

    def connect_confirm(self):
        self.menu("CONNECT VIA TELNET?", [
            ("Connect / send / receive", self.connect),
            ("Cancel", self.home),
        ], self.home)

    def connect(self):
        self.task("TELNET SESSION", self.backend.connect_telnet,
                  lambda output: self.view("SESSION FINISHED", output or "Session completed."))

    def load_settings(self):
        def loaded(settings):
            self.settings = settings
            self.settings_menu()
        self.task("LOADING SETTINGS", self.backend.get_settings, loaded)

    def settings_menu(self):
        self.menu("SETTINGS", [
            ("Callsign: " + (self.settings.get("callsign") or "(not set)"), lambda: self.edit_setting("callsign")),
            ("Grid: " + (self.settings.get("locator") or "(optional)"), lambda: self.edit_setting("locator")),
            ("Password: " + ("set" if self.settings.get("has_password") else "not set"), lambda: self.edit_setting("password")),
            ("Back", self.home),
        ], self.home)

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
                  "In message body: Enter adds a line; Shift+Enter saves. "
                  "Left/right moves the cursor.\n\n"
                  "Queue a message, then connect via Telnet to send and receive. "
                  "Requires Wi-Fi, Pat, and a qualifying Winlink account. "
                  "Pat stores the password and mail on this device.")

    def input(self, key, char=None, ctrl=False):
        if self.mode in ("busy", "quit"):
            return
        if self.mode == "menu":
            if key in ("UP", "LEFT"):
                self.sel = (self.sel - 1) % len(self.items)
            elif key in ("DOWN", "RIGHT"):
                self.sel = (self.sel + 1) % len(self.items)
            elif key == "ENTER":
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
        # Header: the TELNET badge names the selected transport; it does not
        # claim the network or Winlink server is connected.
        d.rectangle((0, 0, W, 27), fill=PANEL)
        d.rectangle((0, 0, 3, 27), fill=ACCENT)
        d.line((0, 27, W, 27), fill=EDGE)
        d.text((11, 5), fit(self.title, 235, TITLE), font=TITLE, fill=TEXT)
        if self.mode == "menu":
            if self.title == "POCKETLINK":
                d.rounded_rectangle((259, 6, 311, 21), radius=3, fill="#2c4039", outline=EDGE)
                d.text((265, 8), "TELNET", font=SMALL, fill=ACCENT)
            else:
                counter = f"{self.sel+1}/{len(self.items)}"
                d.text((310-SMALL.getlength(counter), 8), counter, font=SMALL, fill=ACCENT)
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
            hint = "F/X OR ARROWS MOVE     ENTER OPEN"
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
            wrapped = self.view_lines if self.mode == "view" else wrap(self.text, width=VIEW_WIDTH, prefer_words=True)
            start = self.top if self.mode == "view" else 0
            # Clip glyph ink at the right and bottom edges, including the
            # unusual case of a single glyph wider than the available width.
            area = Image.new("RGB", (VIEW_WIDTH, 110), BG)
            area_draw = ImageDraw.Draw(area)
            for row, line in enumerate(wrapped[start:start+ROWS]):
                area_draw.text((0, row*22), line, font=BODY, fill=TEXT)
            image.paste(area, (VIEW_LEFT, 32))
            if self.mode == "view" and len(wrapped) > ROWS:
                d.line((316, 33, 316, 141), fill=DIVIDER, width=2)
                thumb_h = max(8, int(108 * ROWS / len(wrapped)))
                thumb_y = 33 + int((108 - thumb_h) * self.top / (len(wrapped) - ROWS))
                d.line((316, thumb_y, 316, thumb_y + thumb_h), fill=ACCENT, width=2)
            if self.mode == "busy":
                hint = "PLEASE WAIT"
            elif self.accept:
                hint = "F/X SCROLL   ENTER QUEUE   ESC EDIT"
            else:
                hint = "F/X SCROLL    ENTER / ESC BACK"
        d.rectangle((0, 150, W, H), fill=PANEL)
        d.line((0, 150, W, 150), fill=EDGE)
        d.rectangle((0, 151, 3, 169), fill=GOLD)
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
