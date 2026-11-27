"""Keyboard-level tests for inbox reply and the local contact book."""
import tempfile
import time
import unittest
from pathlib import Path

from contacts import ContactBook
from winlink_gui import UI


class FakePat:
    def __init__(self):
        self.queued = []
        self.sessions = []
        self.message = {"id": "ABC1", "from": "W1AW", "to": "N0CALL",
                        "subject": "Field report", "body": "Ready", "date": ""}

    def list_messages(self, folder):
        return [{"id": "ABC1", "subject": self.message["subject"], "unread": True}]

    def read_message(self, folder, message_id):
        return dict(self.message)

    def queue_message(self, to, subject, body):
        self.queued.append((to, subject, body))

    def connect_telnet(self):
        self.sessions.append("telnet")
        return "Complete"

    def connect_packet(self, gateway, port, via):
        self.sessions.append("packet")
        return "Complete"


class ContactsUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = FakePat()
        self.ui = UI(self.backend, radio_config_path=self.root / "radio.json",
                     appearance_config_path=self.root / "appearance.json",
                     contacts_path=self.root / "contacts.json",
                     gateway_profiles_path=self.root / "profiles.json")

    def wait_task(self):
        for _ in range(150):
            self.ui.poll()
            if self.ui.mode != "busy":
                return
            time.sleep(.01)
        self.fail("worker did not finish")

    def choose(self, label_prefix):
        self.assertEqual(self.ui.mode, "menu")
        for index, (label, _) in enumerate(self.ui.items):
            if label.startswith(label_prefix):
                self.ui.sel = index
                self.ui.input("ENTER")
                return
        self.fail("No menu action starts with " + label_prefix)

    def enter_text(self, value):
        self.assertEqual(self.ui.mode, "edit")
        for _ in self.ui.text:
            self.ui.input("BACKSPACE")
        for char in value:
            self.ui.input("CHAR", char)
        self.ui.input("ENTER")

    def enter_reply(self, sender, subject):
        self.ui.display_message("in", {"from": sender, "to": "N0CALL",
                                       "subject": subject, "body": "Hello"})
        self.ui.input("ENTER")
        self.choose("Reply")

    def assert_no_send(self):
        self.assertEqual(self.backend.queued, [])
        self.assertEqual(self.backend.sessions, [])

    def test_inbox_reply_prefills_callsign_and_requires_explicit_queue(self):
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(self.ui.title, "INBOX")
        self.ui.input("ENTER")
        self.wait_task()
        self.assertEqual(self.ui.title, "MESSAGE")
        self.ui.input("ENTER")
        self.assertEqual(self.ui.title, "MESSAGE ACTIONS")
        self.choose("Reply")
        self.assertEqual(self.ui.title, "COMPOSE")
        self.assertEqual(self.ui.draft, {"to": "W1AW", "subject": "Re: Field report", "body": ""})
        self.assert_no_send()

    def test_reply_accepts_email_and_smtp_without_duplicating_re_prefix(self):
        for sender, subject, expected_to, expected_subject in (
                ("alice@example.org", "rE: Checking in", "alice@example.org", "rE: Checking in"),
                ("smtp:Alice+field@example.org", "Radio report", "SMTP:Alice+field@example.org", "Re: Radio report"),
                ("w1aw-3", "Re : Already", "W1AW-3", "Re : Already")):
            with self.subTest(sender=sender):
                self.ui.draft = {"to": "", "subject": "", "body": ""}
                self.enter_reply(sender, subject)
                self.assertEqual(self.ui.title, "COMPOSE")
                self.assertEqual(self.ui.draft["to"], expected_to)
                self.assertEqual(self.ui.draft["subject"], expected_subject)
        self.assert_no_send()

    def test_reply_rejects_multiple_or_bad_senders_and_cleans_subject_controls(self):
        for sender in ("", "a@example.org,b@example.org", "W1AW N0CALL", "bad\n@example.org", "Person <a@example.org>"):
            with self.subTest(sender=sender):
                self.ui.draft = {"to": "", "subject": "", "body": ""}
                self.enter_reply(sender, "Hello")
                self.assertEqual(self.ui.title, "REPLY UNAVAILABLE")
                self.assertEqual(self.ui.draft, {"to": "", "subject": "", "body": ""})
        self.enter_reply("W1AW", "Hi\r\nCC:\x00other\x85text")
        self.assertEqual(self.ui.draft["subject"], "Re: Hi CC: other text")
        self.assert_no_send()

    def test_reply_never_replaces_existing_draft_without_confirmation(self):
        existing = {"to": "old@example.org", "subject": "Old", "body": "Keep me"}
        self.ui.draft = dict(existing)
        self.enter_reply("W1AW", "New")
        self.assertEqual(self.ui.title, "REPLACE DRAFT?")
        self.assertEqual(self.ui.items[0][0], "Keep current draft")
        self.ui.input("ENTER")
        self.assertEqual(self.ui.draft, existing)
        self.choose("Reply")
        self.choose("Start reply")
        self.assertEqual(self.ui.draft, {"to": "W1AW", "subject": "Re: New", "body": ""})
        self.assert_no_send()

    def test_add_view_edit_and_confirm_delete_contact(self):
        self.ui.address_book(False, self.ui.home)
        self.choose("Add contact")
        self.choose("Name:")
        self.enter_text("Alice")
        self.choose("Address:")
        self.enter_text("Alice+field@example.org")
        self.choose("Save contact")
        self.assertEqual(self.ui.title, "ADDRESS BOOK")
        self.choose("Alice")
        self.choose("View details")
        self.assertIn("Alice+field@example.org", self.ui.text)
        self.ui.input("ESC")
        self.choose("Edit contact")
        self.choose("Name:")
        self.enter_text("Alice Updated")
        self.choose("Save contact")
        self.choose("Alice Updated")
        self.choose("Delete contact")
        self.assertEqual(self.ui.title, "DELETE CONTACT?")
        self.ui.input("ENTER")  # Default is keep.
        self.assertEqual(len(self.ui.contacts.list_contacts()), 1)
        self.choose("Delete contact")
        self.choose("Delete Alice Updated")
        self.assertEqual(self.ui.contacts.list_contacts(), [])
        self.assert_no_send()

    def test_choose_contact_populates_only_recipient(self):
        self.ui.contacts.save("Field partner", "partner@example.org")
        self.ui.draft = {"to": "", "subject": "Keep subject", "body": "Keep body"}
        self.ui.compose()
        self.choose("Choose contact")
        self.choose("Field partner")
        self.choose("Use as recipient")
        self.assertEqual(self.ui.title, "COMPOSE")
        self.assertEqual(self.ui.draft, {"to": "partner@example.org", "subject": "Keep subject", "body": "Keep body"})
        self.assert_no_send()

    def test_stale_contact_edit_delete_and_choice_do_not_replace_data(self):
        old = self.ui.contacts.save("Alice", "alice@example.org")
        self.ui.address_book(True, self.ui.home)
        self.choose("Alice")
        self.choose("Edit contact")
        self.choose("Name:")
        self.enter_text("Old edit")
        external = ContactBook(self.root / "contacts.json")
        current = external.save("Changed elsewhere", "new@example.org", old["id"])
        self.choose("Save contact")
        self.assertEqual(self.ui.title, "CONTACT ERROR")
        self.assertIn("Contact changed", self.ui.text)
        self.assertEqual(external.list_contacts()[0], current)

        self.ui.delete_contact_confirm(old, True, self.ui.home)
        self.choose("Delete Alice")
        self.assertEqual(self.ui.title, "CONTACT ERROR")
        self.assertEqual(external.list_contacts()[0], current)

        self.ui.contact_actions(old, True, self.ui.home)
        self.choose("Use as recipient")
        self.assertEqual(self.ui.title, "CONTACT ERROR")
        self.assertEqual(self.ui.draft["to"], "")
        self.assert_no_send()


if __name__ == "__main__":
    unittest.main()
