import json
from pathlib import Path
import tempfile
import unittest

from appearance import AppearanceSettings


class AppearanceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "pocketlink" / "appearance.json"
        self.settings = AppearanceSettings(self.path)

    def test_default_and_persistent_choice(self):
        self.assertEqual(self.settings.load(), "classic")
        self.settings.save("dashboard")
        self.assertEqual(AppearanceSettings(self.path).load(), "dashboard")
        self.settings.save("classic")
        self.assertEqual(self.settings.load(), "classic")
        self.settings.save("radio")
        self.assertEqual(AppearanceSettings(self.path).load(), "radio")

    def test_save_preserves_unknown_existing_fields(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"other": "keep", "layout": "classic"}', encoding="utf-8")
        self.settings.save("dashboard")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"other": "keep", "layout": "dashboard"})

    def test_invalid_file_is_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.settings.save("dashboard")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not json")

    def test_invalid_layout_cannot_be_written(self):
        with self.assertRaises(ValueError):
            self.settings.save("unrecognized")
        self.assertFalse(self.path.exists())
