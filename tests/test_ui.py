import time
import threading
import unittest
import tempfile
from pathlib import Path

from winlink_gui import BODY, ROWS, VIEW_LEFT, VIEW_WIDTH, RadioSettings, UI, wrap


class FakePat:
    def __init__(self):
        self.queued = []
        self.connected = 0
        self.packet_sessions = []
        self.settings = {"callsign": "N0CALL", "locator": "DM33", "has_password": True}

    def list_messages(self, folder):
        return [{"id": "ABC1", "subject": "Test mail", "unread": True}]

    def read_message(self, folder, mid):
        return {"id": mid, "from": "N0CALL", "to": "TEST", "subject": "Test mail", "body": "Hello"}

    def queue_message(self, recipient, subject, body):
        self.queued.append((recipient, subject, body))

    def connect_telnet(self):
        self.connected += 1
        return "Telnet session completed. Received 1 message(s)."

    def connect_packet(self, gateway, agw_port, via=""):
        self.packet_sessions.append((gateway, agw_port, via))
        return "Packet session completed."

    def get_settings(self):
        return self.settings.copy()

    def save_settings(self, callsign, locator, password=None):
        self.settings.update(callsign=callsign, locator=locator)
        self.last_password = password


class UITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = FakePat()
        self.ui = UI(self.backend, radio_config_path=Path(self.temp.name) / "radio.json",
                     appearance_config_path=Path(self.temp.name) / "appearance.json")

    def wait_task(self):
        for _ in range(100):
            self.ui.poll()
            if self.ui.mode != "busy":
                return
            time.sleep(.01)
        self.fail("worker did not finish")

    def test_home_and_inbox_navigation(self):
        self.assertEqual(self.ui.title, "POCKETLINK")
        self.assertEqual(self.ui.draw().size, (320, 170))
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(self.ui.title, "INBOX")
        self.ui.input("ENTER")
        self.wait_task()
        self.assertIn("Hello", self.ui.text)

    def test_layout_choice_persists_and_keeps_all_actions(self):
        actions = [label for label, _ in self.ui.items]
        self.ui.settings = self.backend.get_settings()
        self.ui.settings_menu()
        next(action for label, action in self.ui.items if label.startswith("Layout:"))()
        self.assertEqual(self.ui.title, "CHOOSE LAYOUT")
        self.ui.input("DOWN")
        self.ui.input("ENTER")
        self.assertEqual(self.ui.layout, "dashboard")
        self.assertEqual([label for label, _ in self.ui.items], actions)
        self.assertEqual(self.ui.draw().size, (320, 170))
        for expected in range(1, len(actions)):
            self.ui.input("DOWN")
            self.ui.draw()
            self.assertEqual(self.ui.sel, expected)
        self.ui.input("DOWN")
        self.assertEqual(self.ui.sel, 0)
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(self.ui.title, "INBOX")
        self.ui.input("ESC")
        self.assertEqual(self.ui.layout, "dashboard")
        self.assertEqual(self.ui.title, "POCKETLINK")
        restored = UI(self.backend, radio_config_path=Path(self.temp.name) / "radio.json",
                      appearance_config_path=Path(self.temp.name) / "appearance.json")
        self.assertEqual(restored.layout, "dashboard")
        restored.settings = self.backend.get_settings()
        restored.layout_menu()
        restored.input("ENTER")
        self.assertEqual(restored.layout, "classic")
        self.assertEqual(restored.appearance.load(), "classic")

    def test_invalid_layout_settings_do_not_block_mail_and_are_not_overwritten(self):
        path = Path(self.temp.name) / "bad-appearance.json"
        path.write_text("not json", encoding="utf-8")
        ui = UI(self.backend, radio_config_path=Path(self.temp.name) / "radio.json",
                appearance_config_path=path)
        self.assertEqual(ui.layout, "classic")
        ui.set_layout("dashboard")
        self.assertEqual(ui.title, "LAYOUT ERROR")
        self.assertEqual(path.read_text(encoding="utf-8"), "not json")

    def test_signal_blue_colors_are_used_in_menu_and_message(self):
        home = self.ui.draw()
        self.assertEqual(home.getpixel((0, 45)), (10, 25, 37))  # navy background
        self.assertEqual(home.getpixel((20, 15)), (16, 42, 59))  # blue header
        self.assertEqual(home.getpixel((300, 45)), (22, 64, 81))  # selected row
        self.assertEqual(home.getpixel((270, 7)), (27, 67, 82))  # Telnet badge
        self.ui.view("MESSAGE", "Example message")
        message = self.ui.draw()
        self.assertEqual(message.getpixel((0, 45)), (10, 25, 37))
        self.assertEqual(message.getpixel((210, 15)), (16, 42, 59))

    def test_menu_scroll_retains_access_to_all_actions(self):
        for _ in range(len(self.ui.items) - 1):
            self.ui.input("DOWN")
        self.assertEqual(self.ui.items[self.ui.sel][0], "Exit")
        self.ui.draw()
        self.assertEqual(self.ui.top, len(self.ui.items) - 4)
        self.ui.input("DOWN")
        self.ui.draw()
        self.assertEqual(self.ui.items[self.ui.sel][0], "Inbox")
        self.assertEqual(self.ui.top, 0)

    def test_received_crlf_is_displayed_as_lines_not_control_glyphs(self):
        body = "First\r\nSecond\r\n\r\nFourth\rFifth\tcolumn\x00"
        self.backend.read_message = lambda folder, mid: {
            "id": mid, "from": "TEST", "to": "N0CALL", "subject": "Round trip",
            "body": body,
        }
        self.ui.open_message("in", {"id": "ABC1"})
        self.wait_task()
        # Render-only normalization does not alter the message returned by Pat.
        self.assertIn(body, self.ui.text)
        rendered = wrap(self.ui.text, width=5000)
        self.assertEqual(rendered[-5:], ["First", "Second", "", "Fourth", "Fifth   column"])
        self.assertTrue(all("\r" not in line and "\x00" not in line for line in rendered))
        self.ui.draw()

    def test_view_wrap_prefers_words_and_keeps_paragraphs(self):
        width = BODY.getlength("Hello W")
        self.assertEqual(wrap("Hello World\r\n\r\nAgain", width=width, prefer_words=True),
                         ["Hello", "World", "", "Again"])
        self.assertEqual("".join(wrap("longunbrokenaddress", width=width, prefer_words=True)),
                         "longunbrokenaddress")
        self.assertNotEqual(wrap("Hello World", width=width),
                            wrap("Hello World", width=width, prefer_words=True))

    def test_whole_words_fit_exact_boundaries_and_indentation(self):
        # A word that fits by itself must move intact when indentation or a
        # preceding word would push even one letter outside the view.
        width = BODY.getlength("world")
        self.assertEqual(wrap("  world next", width=width, prefer_words=True),
                         ["world", "next"])
        boundary = BODY.getlength("hello world")
        self.assertEqual(wrap("hello world next", width=boundary, prefer_words=True),
                         ["hello world", "next"])
        # Overflowing whitespace must not move a word that already fits.
        width = BODY.getlength("hello world")
        self.assertEqual(wrap("hello world  a", width=width, prefer_words=True),
                         ["hello world", "a"])
        self.assertEqual(wrap("a   b", width=100, prefer_words=True), ["a   b"])

    def test_unbroken_address_wrap_and_blank_lines(self):
        address = "very-long-unbroken-address+longuser@example.test"
        width = BODY.getlength("address")
        lines = wrap(address + "\r\n\r\nNext", width=width, prefer_words=True)
        self.assertIn("", lines)
        self.assertEqual("".join(lines[:-2]), address)
        for line in lines:
            if line:
                self.assertLessEqual(max(BODY.getlength(line), BODY.getbbox(line)[2]), width)

    def test_sent_and_received_views_fit_and_scroll_the_same_lines(self):
        body = ("First line with several readable words and an "
                "extraordinarily-long-unbroken-address@example.test\r\n\r\n" +
                "Field report ready to deliver. " * 18)
        for folder in ("in", "out", "sent"):
            with self.subTest(folder=folder):
                self.backend.read_message = lambda folder, mid: {
                    "id": mid, "from": "TEST", "to": "N0CALL", "subject": "Field report",
                    "body": body,
                }
                self.ui.open_message(folder, {"id": "ABC1"})
                self.wait_task()
                wrapped = wrap(self.ui.text, width=VIEW_WIDTH, prefer_words=True)
                self.assertEqual("".join("".join(wrapped).split()),
                                 "".join(self.ui.text.split()))
                for line in wrapped:
                    if line:
                        self.assertLessEqual(max(BODY.getlength(line), BODY.getbbox(line)[2]), VIEW_WIDTH)
                self.assertIn("", wrapped)
                image = self.ui.draw()
                # The right margin reserved for the scrollbar contains no
                # text, even when the line is close to the width boundary.
                margin = image.crop((VIEW_LEFT+VIEW_WIDTH, 32, 313, 142))
                self.assertEqual(margin.getcolors(), [(margin.width * margin.height, image.getpixel((0, 32)))])
                for _ in range(len(wrapped) + 3):
                    self.ui.input("DOWN")
                self.assertEqual(self.ui.top, max(0, len(wrapped) - ROWS))
                self.ui.draw()

    def test_draft_queue_then_explicit_connect(self):
        self.ui.draft = {"to": "N0CALL@winlink.org", "subject": "Hi", "body": "Hello"}
        self.ui.review_draft()
        self.ui.input("ENTER")
        self.assertEqual(self.ui.title, "QUEUE MESSAGE?")
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(self.backend.queued[0][2], "Hello")
        self.assertEqual(self.backend.connected, 0)
        self.ui.connect_confirm()
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(self.backend.connected, 1)
        self.assertIn("Received 1", self.ui.text)

    def test_review_escape_preserves_draft(self):
        self.ui.draft = {"to": "N0CALL", "subject": "Hi", "body": "Hello"}
        self.ui.review_draft()
        self.ui.input("ESC")
        self.assertEqual(self.ui.title, "COMPOSE")
        self.assertEqual(self.ui.draft["body"], "Hello")
        self.assertFalse(self.backend.queued)

    def test_packet_settings_preserve_existing_fields_and_reject_invalid(self):
        radio = RadioSettings(Path(self.temp.name) / "radio.json")
        radio.path.write_text('{"custom": "keep"}', encoding="utf-8")
        radio.update("mac", "aa:bb:cc:dd:ee:ff")
        radio.update("gateway", "n0call-10")
        radio.update("via", "w7mot-6")
        self.assertEqual(radio.load(), {"custom": "keep", "mac": "AA:BB:CC:DD:EE:FF", "gateway": "N0CALL-10", "via": "W7MOT-6"})
        before = radio.path.read_bytes()
        for field, value in (("mac", "bad"), ("gateway", "n0call-16"), ("via", "n0call-16"), ("via", "W7MOT-6,OTHER")):
            with self.assertRaises(ValueError):
                radio.update(field, value)
            self.assertEqual(radio.path.read_bytes(), before)
        radio.update("via", "  ")
        self.assertEqual(radio.load()["via"], "")
        self.assertEqual(radio.load()["gateway"], "N0CALL-10")

    def test_packet_requires_explicit_confirmation_and_cleans_up(self):
        events = []
        class FakeTNC:
            def __init__(self, mac, callsign, port, cancel_event):
                events.append(("init", mac, callsign, port))
            def __enter__(self):
                events.append("start")
                return self
            def __exit__(self, *args):
                events.append("stop")
        self.ui.tnc_factory = FakeTNC
        self.ui.radio.update("mac", "AA:BB:CC:DD:EE:FF")
        self.ui.radio.update("gateway", "KT7RUN-10")
        self.ui.radio.update("via", "W7MOT-6")
        self.ui.packet_intro()
        self.assertIn("TRANSMIT", self.ui.text)
        self.assertIn("Gateway: KT7RUN-10", self.ui.text)
        self.assertIn("Via: W7MOT-6", self.ui.text)
        self.assertEqual(events, [])
        self.ui.input("ENTER")
        self.assertEqual(self.ui.title, "TX KT7RUN-10?")
        self.assertEqual(self.ui.items[0][0], "Cancel")
        self.assertEqual(self.ui.items[1][0], "Via W7MOT-6")
        self.ui.input("DOWN")
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(events, [("init", "AA:BB:CC:DD:EE:FF", "N0CALL", 8000), "start", "stop"])
        self.assertEqual(self.backend.packet_sessions, [("KT7RUN-10", 8000, "W7MOT-6")])
        self.assertEqual(self.ui.title, "SESSION FINISHED")
        self.assertEqual(self.backend.connected, 0)

    def test_packet_without_via_remains_direct_and_can_be_cancelled(self):
        self.ui.radio.update("mac", "AA:BB:CC:DD:EE:FF")
        self.ui.radio.update("gateway", "KT7RUN-10")
        self.ui.packet_intro()
        self.assertIn("Via: Direct", self.ui.text)
        self.ui.input("ENTER")
        self.assertEqual(self.ui.items[1][0], "Direct connection")
        self.ui.input("ENTER")  # default is Cancel
        self.assertEqual(self.backend.packet_sessions, [])

    def test_packet_without_via_passes_direct_route_to_backend(self):
        class FakeTNC:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
        self.ui.tnc_factory = FakeTNC
        self.ui.packet_connect({"mac": "AA:BB:CC:DD:EE:FF", "gateway": "KT7RUN-10"})
        self.wait_task()
        self.assertEqual(self.backend.packet_sessions, [("KT7RUN-10", 8000, "")])

    def test_packet_failure_cleans_up_and_preserves_telnet(self):
        events = []
        class FakeTNC:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): events.append("stop")
        self.ui.tnc_factory = FakeTNC
        self.backend.connect_packet = lambda *args: (_ for _ in ()).throw(RuntimeError("radio failed"))
        self.ui.packet_connect({"mac": "AA:BB:CC:DD:EE:FF", "gateway": "N0RMS"})
        self.wait_task()
        self.assertEqual(events, ["stop"])
        self.assertIn("radio failed", self.ui.text)
        self.ui.connect()
        self.wait_task()
        self.assertEqual(self.backend.connected, 1)

    def test_packet_escape_during_setup_cleans_up_without_starting_pat(self):
        ready, release = threading.Event(), threading.Event()
        events = []
        class BlockingTNC:
            def __init__(self, *args, cancel_event, **kwargs):
                self.cancel_event = cancel_event
            def __enter__(self):
                ready.set()
                if not release.wait(3):
                    raise RuntimeError("test timed out")
                return self
            def __exit__(self, *args):
                events.append("stopped")
        self.ui.tnc_factory = BlockingTNC
        self.ui.packet_connect({"mac": "AA:BB:CC:DD:EE:FF", "gateway": "KT7RUN-10"})
        self.assertTrue(ready.wait(2))
        self.ui.input("ESC")
        self.assertEqual(self.ui.mode, "busy")
        self.assertTrue(self.ui._packet_control["cancel"].is_set())
        self.ui.quit()
        self.assertEqual(self.ui.mode, "busy")
        release.set()
        self.wait_task()
        self.assertEqual(events, ["stopped"])
        self.assertEqual(self.backend.packet_sessions, [])
        self.assertEqual(self.ui.title, "PACKET CANCELED")

    def test_packet_escape_does_not_cancel_an_active_pat_session(self):
        started, release = threading.Event(), threading.Event()
        events = []
        class FakeTNC:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): events.append("stopped")
        def session(*args):
            started.set()
            if not release.wait(3):
                raise RuntimeError("test timed out")
            return "Session done"
        self.ui.tnc_factory = FakeTNC
        self.backend.connect_packet = session
        self.ui.packet_connect({"mac": "AA:BB:CC:DD:EE:FF", "gateway": "KT7RUN-10"})
        self.assertTrue(started.wait(2))
        self.assertEqual(self.ui._packet_control["phase"], "session")
        self.ui.input("ESC")
        self.assertFalse(self.ui._packet_control["cancel"].is_set())
        self.assertEqual(self.ui.mode, "busy")
        self.ui.quit()
        self.assertEqual(self.ui.mode, "busy")
        self.ui.draw()  # active session must remain renderable while blocked
        release.set()
        self.wait_task()
        self.assertEqual(events, ["stopped"])
        self.assertEqual(self.ui.title, "SESSION FINISHED")

    def test_edit_cursor_and_password_mask(self):
        self.ui.edit_draft("body")
        for ch in "ac":
            self.ui.input("CHAR", ch)
        self.ui.input("LEFT")
        self.ui.input("CHAR", "b")
        self.ui.input("ENTER", ctrl=True)
        self.assertEqual(self.ui.draft["body"], "abc")
        self.ui.load_settings()
        self.wait_task()
        self.ui.edit_setting("password")
        for ch in "secret":
            self.ui.input("CHAR", ch)
        self.assertTrue(self.ui.secret)
        self.assertEqual(self.ui.draw().size, (320, 170))
        self.ui.input("ENTER")
        self.wait_task()
        self.wait_task()
        self.assertEqual(self.backend.last_password, "secret")


if __name__ == "__main__":
    unittest.main()
