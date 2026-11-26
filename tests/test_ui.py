import time
import unittest

from winlink_gui import UI, wrap


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
        self.assertEqual(self.ui.draw().size, (320, 170))
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(self.ui.title, "INBOX")
        self.ui.input("ENTER")
        self.wait_task()
        self.assertIn("Hello", self.ui.text)

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
