import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gateway_profiles import GatewayProfiles


class GatewayProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "pocketlink" / "gateways.json"
        self.store = GatewayProfiles(self.path)

    def test_save_select_edit_delete_without_changing_active_route(self):
        radio = self.path.parent / "radio.json"
        radio.parent.mkdir(parents=True)
        radio.write_text('{"mac":"AA:BB:CC:DD:EE:FF","gateway":"KT7RUN-10","via":"W7MOT-6","custom":42}', encoding="utf-8")
        before = radio.read_bytes()
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.path.exists())
        entry = self.store.create("Home", "kt7run-10", "w7mot-6", "145.050 MHz")
        self.assertEqual((entry["gateway"], entry["via"], entry["frequency"]),
                         ("KT7RUN-10", "W7MOT-6", "145.050 MHz"))
        fresh = GatewayProfiles(self.path)
        self.assertEqual(fresh.get(entry["id"]), entry)
        copy = fresh.get(entry["id"])
        copy["gateway"] = "CHANGED"
        self.assertEqual(fresh.get(entry["id"])["gateway"], "KT7RUN-10")
        updated = fresh.update(entry["id"], "Field", "n0call-15", "", "")
        self.assertEqual(updated["name"], "Field")
        self.assertEqual(updated["gateway"], "N0CALL-15")
        fresh.delete(entry["id"])
        self.assertEqual(fresh.list(), [])
        self.assertEqual(radio.read_bytes(), before)

    def test_rejects_invalid_values_without_changing_existing_file(self):
        self.store.create("Home", "KT7RUN-10")
        before = self.path.read_bytes()
        invalid = [
            ("", "KT7RUN-10", "", ""),
            ("HOME", "KT7RUN-10", "", ""),
            ("Field", "KT7RUN-16", "", ""),
            ("Field", "N0CALL\n[server]", "", ""),
            ("Field", "KT7RUN-10", "W7MOT-6,OTHER", ""),
            ("Field", "KT7RUN-10", "", "145\nother"),
        ]
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.store.create(*values)
                self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_fields_preserved_on_edit_and_delete(self):
        one = self.store.create("One", "N0CALL")
        two = self.store.create("Two", "KT7RUN-10")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["future"] = {"keep": True}
        data["profiles"][0]["future_field"] = "keep"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        updated = self.store.update(one["id"], "One", "N0CALL-1", "", "")
        self.assertEqual(updated["future_field"], "keep")
        self.store.delete(two["id"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["future"], {"keep": True})
        self.assertEqual(data["profiles"][0]["future_field"], "keep")

    def test_corrupt_and_future_files_never_overwritten(self):
        self.path.parent.mkdir(parents=True)
        for content in (b"not json", b'{"version":2,"profiles":[]}',
                        b'{"version":1,"profiles":[{"id":"invalid"}]}'):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(RuntimeError):
                    self.store.create("Home", "N0CALL")
                self.assertEqual(self.path.read_bytes(), content)

    def test_concurrent_change_and_replace_failure_preserve_files(self):
        self.store.create("Home", "N0CALL")
        before, data = self.store._read()
        changed = self.path.read_bytes() + b" "
        self.path.write_bytes(changed)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.store._write(before, data)
        self.assertEqual(self.path.read_bytes(), changed)
        with mock.patch("gateway_profiles.os.replace", side_effect=OSError("no space")):
            with self.assertRaisesRegex(RuntimeError, "Cannot save"):
                self.store.create("Field", "KT7RUN-10")
        self.assertEqual(self.path.read_bytes(), changed)
        self.assertEqual(list(self.path.parent.glob(".gateways-*")), [])

    def test_stale_editor_and_delete_confirmation_do_not_overwrite_changes(self):
        original = self.store.create("Home", "N0CALL", "", "145.050 MHz")
        external = GatewayProfiles(self.path)
        external.update(original["id"], "Home", "KT7RUN-10", "W7MOT-6", "145.090 MHz")
        changed = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "changed; reload"):
            self.store.update(original["id"], "Home", "N0CALL", "", "146.000 MHz", expected=original)
        self.assertEqual(self.path.read_bytes(), changed)
        with self.assertRaisesRegex(RuntimeError, "changed; reload"):
            self.store.delete(original["id"], expected=original)
        self.assertEqual(self.path.read_bytes(), changed)
        current = self.store.get(original["id"])
        updated = self.store.update(current["id"], "Home", "KT7RUN-10", "W7MOT-6",
                                    "145.100 MHz", expected=current)
        self.store.delete(updated["id"], expected=updated)
        self.assertEqual(self.store.list(), [])


if __name__ == "__main__":
    unittest.main()
