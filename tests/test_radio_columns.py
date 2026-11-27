"""Radio home navigation uses two independent, keyboard-accessible columns."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import ImageDraw

from dashboard import TILES
from device import KeyTranslator
from tests.test_keyboard import E
from winlink_gui import TEXT, UI


class RadioColumnsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.ui = UI(backend=object(), radio_config_path=root / "radio.json",
                     appearance_config_path=root / "appearance.json")
        self.ui.set_layout("radio")
        self.keyboard = KeyTranslator(E, True)

    def selected(self):
        return self.ui.items[self.ui.sel][0]

    def press(self, code):
        result = self.keyboard.feed(code, 1, self.ui.mode)
        self.assertIsNotNone(result)
        key, char, ctrl = result
        self.ui.input(key, char, ctrl=ctrl)
        self.keyboard.feed(code, 0, self.ui.mode)

    def test_mail_keys_stay_in_left_column_and_wrap(self):
        self.assertEqual(self.selected(), "Inbox")
        self.press(E.KEY_F)
        self.assertEqual(self.selected(), "Sent messages")
        self.press(E.KEY_X)
        self.assertEqual(self.selected(), "Inbox")
        for expected in ("Compose message", "Outbox", "Sent messages", "Inbox"):
            self.press(E.KEY_X)
            self.assertEqual(self.selected(), expected)

    def test_columns_switch_independently_and_remember_positions(self):
        self.press(E.KEY_X)  # Compose
        self.press(E.KEY_C)  # right column
        self.assertEqual(self.selected(), "Connect via Telnet")
        self.press(E.KEY_X)
        self.press(E.KEY_X)
        self.assertEqual(self.selected(), "Packet radio settings")
        self.press(E.KEY_Z)
        self.assertEqual(self.selected(), "Compose message")
        self.press(E.KEY_C)
        self.assertEqual(self.selected(), "Packet radio settings")
        self.press(E.KEY_C)  # already in right column
        self.assertEqual(self.selected(), "Packet radio settings")
        self.press(E.KEY_Z)
        self.press(E.KEY_Z)  # already in left column
        self.assertEqual(self.selected(), "Compose message")

    def test_all_right_actions_stay_on_right_and_wrap(self):
        self.press(E.KEY_C)
        expected = ("Connect via Telnet", "Connect via packet", "Packet radio settings",
                    "Settings", "About / help", "Exit")
        for label in expected:
            self.assertEqual(self.selected(), label)
            self.press(E.KEY_X)
        self.assertEqual(self.selected(), expected[0])
        self.press(E.KEY_F)
        self.assertEqual(self.selected(), "Exit")

    def test_fn_arrow_layer_and_text_editing(self):
        self.press(E.KEY_RIGHT)
        self.assertEqual(self.selected(), "Connect via Telnet")
        self.press(E.KEY_DOWN)
        self.assertEqual(self.selected(), "Connect via packet")
        self.press(E.KEY_LEFT)
        self.assertEqual(self.selected(), "Inbox")
        self.ui.edit("EDIT", "", lambda _: None, self.ui.home)
        for code in (E.KEY_Z, E.KEY_C, E.KEY_F, E.KEY_X):
            self.press(code)
        self.assertEqual(self.ui.text, "zcfx")

    def test_right_selection_and_scroll_survive_submenu_round_trip(self):
        self.press(E.KEY_C)
        for _ in range(5):
            self.press(E.KEY_X)
            self.ui.draw()
        self.assertEqual(self.selected(), "Exit")
        for _ in range(3):
            self.press(E.KEY_F)
            self.ui.draw()
        self.assertEqual(self.selected(), "Packet radio settings")
        self.assertEqual(self.ui._radio_right_top, 2)
        self.press(E.KEY_ENTER)
        self.assertEqual(self.ui.title, "PACKET SETTINGS")
        self.press(E.KEY_ESC)
        self.assertEqual(self.ui.title, "POCKETLINK")
        self.assertEqual(self.selected(), "Packet radio settings")
        rendered = []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            rendered.append((text, kwargs.get("fill")))
            return original(draw, xy, text, *args, **kwargs)
        with mock.patch.object(ImageDraw.ImageDraw, "text", new=record):
            self.ui.draw()
        self.assertIn(("Radio setup", TEXT), rendered)
        self.press(E.KEY_Z)
        self.assertEqual(self.selected(), "Inbox")
        self.press(E.KEY_C)
        self.assertEqual(self.selected(), "Packet radio settings")

    def test_visible_selection_and_enter_for_all_actions(self):
        labels = [label for label, _ in self.ui.items]
        invoked = []
        self.ui.items = [(label, lambda i=i: invoked.append(i))
                         for i, label in enumerate(labels)]
        original = ImageDraw.ImageDraw.text

        def rendered_text():
            output = []
            def record(draw, xy, text, *args, **kwargs):
                output.append((text, kwargs.get("fill")))
                return original(draw, xy, text, *args, **kwargs)
            with mock.patch.object(ImageDraw.ImageDraw, "text", new=record):
                image = self.ui.draw()
            self.assertEqual(image.size, (320, 170))
            return output

        for index, label in enumerate(labels):
            if index == 4:
                self.press(E.KEY_C)
            visible = rendered_text()
            self.assertIn((TILES[label][0], TEXT), visible, label)
            for mail in ("Inbox", "Compose", "Outbox", "Sent"):
                self.assertTrue(any(text == mail for text, _ in visible), mail)
            self.press(E.KEY_ENTER)
            self.assertEqual(invoked[-1], index)
            if index != 3:
                self.press(E.KEY_X)

    def test_other_layouts_keep_sequential_navigation(self):
        for layout in ("classic", "dashboard"):
            with self.subTest(layout=layout):
                self.ui.set_layout(layout)
                self.assertEqual(self.selected(), "Inbox")
                self.press(E.KEY_C)
                self.assertEqual(self.selected(), "Compose message")
                self.press(E.KEY_Z)
                self.assertEqual(self.selected(), "Inbox")
                for _ in range(len(self.ui.items)):
                    self.press(E.KEY_X)
                self.assertEqual(self.selected(), "Inbox")


if __name__ == "__main__":
    unittest.main()
