"""Address-book persistence and preservation tests; no mailbox or RF access."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from contacts import ContactBook, normalize_contact_address


class ContactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "pocketlink" / "contacts.json"
        self.book = ContactBook(self.path)

    def test_add_sort_persist_edit_and_delete_selected_contact(self):
        self.assertEqual(self.book.list_contacts(), [])
        bob = self.book.save("Bob", "w7mot-6")
        alice = self.book.save("Alice", "Alice+radio@example.com")
        restored = ContactBook(self.path)
        self.assertEqual([c["name"] for c in restored.list_contacts()], ["Alice", "Bob"])
        self.assertEqual(restored.list_contacts()[1]["address"], "W7MOT-6")
        updated = restored.save("Bobby", "SMTP:bob@example.com", bob["id"])
        self.assertEqual(updated["id"], bob["id"])
        self.assertEqual(updated["address"], "SMTP:bob@example.com")
        restored.delete(bob["id"])
        self.assertEqual(restored.list_contacts(), [alice])

    def test_existing_unknown_fields_survive_edit_and_delete(self):
        first = self.book.save("A", "N0AAA")
        second = self.book.save("B", "N0BBB")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["future"] = {"keep": True}
        data["contacts"][0]["note"] = "preserve"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.book.save("A new", "N0AAA", first["id"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["future"], {"keep": True})
        self.assertEqual(data["contacts"][0]["note"], "preserve")
        self.book.delete(second["id"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["future"], {"keep": True})

    def test_invalid_and_duplicate_input_never_changes_existing_file(self):
        contact = self.book.save("Alice", "Alice@example.com")
        before = self.path.read_bytes()
        for name, address in (("", "N0CALL"), ("Alice\nEve", "N0CALL"),
                              ("Bob", "a@example.org,b@example.org"),
                              ("Bob", "N0CALL-16"), ("Bob", "bad address"),
                              ("Bob", "alice@EXAMPLE.COM"), ("Bob", "SMTP:W1AW")):
            with self.subTest(name=name, address=address), self.assertRaises(ValueError):
                self.book.save(name, address)
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.book.delete("0" * 32)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.book.list_contacts()[0]["id"], contact["id"])

    def test_existing_malformed_or_future_book_is_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        for content in (b"broken json", b'{"version":2,"contacts":[]}',
                        b'{"version":1,"contacts":[{"name":"A","address":"W1AW"}]}'):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(RuntimeError):
                    self.book.save("Bob", "W1AW")
                self.assertEqual(self.path.read_bytes(), content)

    def test_external_change_is_detected_and_not_overwritten(self):
        self.book.save("Alice", "W1AW")
        replacement = b'{"version":1,"contacts":[],"external":true}\n'
        real_fsync = os.fsync
        def change_source(fd):
            self.path.write_bytes(replacement)
            real_fsync(fd)
        with mock.patch("contacts.os.fsync", side_effect=change_source):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                self.book.save("Bob", "N0CALL")
        self.assertEqual(self.path.read_bytes(), replacement)
        self.assertEqual(list(self.path.parent.glob(".contacts-*")), [])

    def test_address_normalization(self):
        self.assertEqual(normalize_contact_address(" w1aw-10 "), "W1AW-10")
        self.assertEqual(normalize_contact_address("smtp:Me+Test@example.org"), "SMTP:Me+Test@example.org")
        self.assertEqual(normalize_contact_address("n0call@winlink.org"), "n0call@winlink.org")

    def test_edit_and_delete_reject_stale_or_wrong_contact_snapshot(self):
        initial = self.book.save("Alice", "alice@example.org")
        snapshot = self.book.list_contacts()[0]
        self.assertEqual(snapshot, initial)
        changed = ContactBook(self.path).save("Alice revised", "alice@example.org", initial["id"])
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Contact changed"):
            self.book.save("Alice old edit", "alice@example.org", initial["id"], expected=snapshot)
        with self.assertRaisesRegex(RuntimeError, "Contact changed"):
            self.book.delete(initial["id"], expected=snapshot)
        with self.assertRaisesRegex(ValueError, "Invalid contact snapshot"):
            self.book.delete(initial["id"], expected={"id": "0" * 32})
        with self.assertRaisesRegex(ValueError, "Invalid contact snapshot"):
            self.book.save("New", "W1AW", expected=snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        updated = self.book.save("Current edit", "alice@example.org", initial["id"], expected=changed)
        self.assertEqual(updated["name"], "Current edit")
        self.book.delete(initial["id"], expected=updated)
        self.assertEqual(self.book.list_contacts(), [])


if __name__ == "__main__":
    unittest.main()
