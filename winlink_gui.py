#!/usr/bin/env python3
"""Keyboard-first Pat Winlink interface for the Cardputer Zero."""
import argparse
import os
import queue
import sys
import threading

from PIL import Image, ImageDraw, ImageFont
from backend import PatBackend
from device import W, H, default_keyboard, run_device

BG, PANEL, CARD = "#08121d", "#102334", "#193749"
TEXT, MUTED, ACCENT, GOLD = "#edf6f7", "#a4bdc5", "#55e3ce", "#f8c46c"


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


def fit(value, width, face=BODY):
    value = str(value)
    while value and face.getlength(value) > width:
        value = value[:-1]
    return value


def wrap(text, width=300, face=BODY):
    """Wrap on pixel width, retaining newlines and long unbroken strings."""
    result = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            result.append("")
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
        self.menu("CARDPUTER WINLINK", [
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
                self.top = min(max(0, len(wrap(self.text)) - ROWS), self.top + 1)
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
        d.rectangle((0, 0, W, 28), fill=PANEL)
        d.rectangle((0, 0, 4, 28), fill=ACCENT)
        d.text((11, 5), fit(self.title, 248, TITLE), font=TITLE, fill=TEXT)
        if self.mode == "menu":
            d.text((272, 9), f"{self.sel+1}/{len(self.items)}", font=SMALL, fill=ACCENT)
            self.top = min(self.top, self.sel)
            if self.sel >= self.top + 4:
                self.top = self.sel - 3
            for row, (label, _) in enumerate(self.items[self.top:self.top+4]):
                y = 32 + row * 28
                selected = row + self.top == self.sel
                if selected:
                    d.rounded_rectangle((6, y-1, 313, y+24), radius=5, fill=CARD, outline="#327a83")
                d.text((13, y+2), fit(("> " if selected else "  ") + label, 292), font=BODY,
                       fill=TEXT if selected else MUTED)
            hint = "F/X OR ARROWS MOVE     ENTER OPEN"
        elif self.mode == "edit":
            before = self.text[:self.cursor]
            after = self.text[self.cursor:]
            shown = ("*" * len(before) if self.secret else before) + "|" + ("*" * len(after) if self.secret else after)
            before_lines = wrap(("*" * len(before) if self.secret else before) + "|")
            all_lines = wrap(shown)
            start = max(0, len(before_lines) - ROWS)
            for row, line in enumerate(all_lines[start:start+ROWS]):
                d.text((10, 32+row*22), line, font=BODY, fill=TEXT)
            hint = "SHIFT+ENTER SAVE   ESC BACK" if self.multiline else "ENTER SAVE   ESC BACK"
        else:
            for row, line in enumerate(wrap(self.text)[self.top:self.top+ROWS] if self.mode == "view" else wrap(self.text)[:ROWS]):
                d.text((10, 32+row*22), line, font=BODY, fill=TEXT)
            if self.mode == "busy":
                hint = "PLEASE WAIT"
            elif self.accept:
                hint = "F/X SCROLL   ENTER QUEUE   ESC EDIT"
            else:
                hint = "F/X SCROLL    ENTER / ESC BACK"
        d.rectangle((0, 150, W, H), fill=PANEL)
        d.line((0, 150, W, 150), fill="#2e5260")
        d.text((9, 153), hint, font=SMALL, fill=GOLD)
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
        print("Cardputer Winlink:", exc, file=sys.stderr)
        sys.exit(1)
