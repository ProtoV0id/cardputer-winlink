import time
import unittest

from winlink_gui import BODY, ROWS, VIEW_LEFT, VIEW_WIDTH, UI, wrap


class FakePat:
    def __init__(self):
        self.queued = []
        self.connected = 0
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

    def get_settings(self):
        return self.settings.copy()

    def save_settings(self, callsign, locator, password=None):
        self.settings.update(callsign=callsign, locator=locator)
        self.last_password = password


class UITest(unittest.TestCase):
    def setUp(self):
        self.backend = FakePat()
        self.ui = UI(self.backend)

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
        for _ in range(7):
            self.ui.input("DOWN")
        self.assertEqual(self.ui.items[self.ui.sel][0], "Exit")
        self.ui.draw()
        self.assertEqual(self.ui.top, 4)
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
