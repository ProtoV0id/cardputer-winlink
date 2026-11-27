import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import ImageDraw
from dashboard import TILES
from winlink_gui import BODY, TEXT, UI, fit


class RadioLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.appearance_path = self.root / "appearance.json"
        self.radio_path = self.root / "radio.json"
        self.radio_path.write_text('{"gateway":"KT7RUN-10"}', encoding="utf-8")
        self.backend = object()
        self.ui = UI(self.backend, radio_config_path=self.radio_path,
                     appearance_config_path=self.appearance_path)

    def test_select_persist_and_switch_back_without_changing_radio(self):
        before = self.radio_path.read_bytes()
        self.ui.settings = {}
        self.ui.layout_menu()
        self.ui.input("DOWN")
        self.ui.input("DOWN")
        self.assertTrue(self.ui.items[self.ui.sel][0].startswith("Radio style"))
        self.ui.input("ENTER")
        self.assertEqual(self.ui.layout, "radio")
        self.assertEqual(self.ui.title, "POCKETLINK")
        self.assertEqual(self.radio_path.read_bytes(), before)
        restored = UI(self.backend, radio_config_path=self.radio_path,
                      appearance_config_path=self.appearance_path)
        self.assertEqual(restored.layout, "radio")
        restored.settings = {}
        restored.settings_menu()
        self.assertIn("Layout: Radio", [x[0] for x in restored.items])
        restored.layout_menu()
        restored.input("ENTER")
        self.assertEqual(restored.layout, "classic")
        self.assertEqual(self.radio_path.read_bytes(), before)

    def test_all_actions_are_visible_highlighted_and_keyboard_accessible(self):
        self.ui.set_layout("radio")
        labels = [label for label, _ in self.ui.items]
        selected = []
        self.ui.items = [(label, lambda index=index: selected.append(index))
                         for index, label in enumerate(labels)]
        self.assertEqual(len(labels), 10)
        original_text = ImageDraw.ImageDraw.text
        for index, label in enumerate(labels):
            with self.subTest(index=index, label=label):
                visible = []
                def record(draw, xy, text, *args, **kwargs):
                    visible.append((text, kwargs.get("fill")))
                    return original_text(draw, xy, text, *args, **kwargs)
                with mock.patch.object(ImageDraw.ImageDraw, "text", new=record):
                    image = self.ui.draw()
                self.assertEqual(image.size, (320, 170))
                title = TILES.get(label, (label, ""))[0]
                expected = fit(title, 100 if 4 <= index <= 6 else 140, BODY)
                self.assertIn((expected, TEXT), visible)
                self.ui.input("ENTER")
                self.assertEqual(selected[-1], index)
                self.ui.input("DOWN")
        self.assertEqual(self.ui.sel, 0)


if __name__ == "__main__":
    unittest.main()
