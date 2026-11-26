"""Raw keyboard event translation; no Linux device or evdev install needed."""
import unittest
from types import SimpleNamespace

from device import KeyTranslator


# Linux input-event-codes.h values used by the Zero's v5 keyboard overlay.
KEYS = {
    'ESC': 1, 'BACKSPACE': 14, 'ENTER': 28, 'LEFTCTRL': 29, 'LEFTSHIFT': 42,
    'RIGHTSHIFT': 54, 'RIGHTCTRL': 97, 'KPENTER': 96,
    'UP': 103, 'DOWN': 108, 'LEFT': 105, 'RIGHT': 106, 'SPACE': 57,
    'MINUS': 12, 'EQUAL': 13, 'LEFTBRACE': 26, 'RIGHTBRACE': 27,
    'BACKSLASH': 43, 'SEMICOLON': 39, 'APOSTROPHE': 40, 'GRAVE': 41,
    'COMMA': 51, 'DOT': 52, 'SLASH': 53,
}
KEYS.update({str(i): c for i, c in zip(range(1, 10), range(2, 11))})
KEYS['0'] = 11
KEYS.update(dict(zip('QWERTYUIOP', range(16, 26))))
KEYS.update(dict(zip('ASDFGHJKL', range(30, 39))))
KEYS.update(dict(zip('ZXCVBNM', range(44, 51))))
E = SimpleNamespace(**{'KEY_' + key: value for key, value in KEYS.items()})


class KeyboardTests(unittest.TestCase):
    def test_all_stock_sym_layer_codes(self):
        keyboard = KeyTranslator(E, True)
        expected = {26:'!', 27:'@', 39:'#', 40:'$', 41:'%', 43:'^', 51:'&',
                    52:'*', 53:'(', 94:')', 55:'~', 69:'`', 70:'_', 71:'-',
                    72:'+', 73:'=', 74:'[', 75:']', 76:'{', 77:'}', 79:';',
                    80:':', 81:"'", 82:'"', 83:'<', 85:'>', 86:'\\', 89:'|',
                    90:',', 91:'.', 92:'/', 93:'?'}
        for code, char in expected.items():
            with self.subTest(code=code):
                self.assertEqual(keyboard.feed(code, 1, 'edit'), ('CHAR', char, False))
                self.assertIsNone(keyboard.feed(code, 0, 'edit'))

    def test_fn_layer_arrows_and_menu_dpad(self):
        keyboard = KeyTranslator(E, True)
        # The stock kernel converts Fn+F/X/Z/C to KEY_UP/DOWN/LEFT/RIGHT.
        for code, action in [(103, 'UP'), (108, 'DOWN'), (105, 'LEFT'), (106, 'RIGHT')]:
            self.assertEqual(keyboard.feed(code, 1, 'edit'), (action, None, False))
            keyboard.feed(code, 0, 'edit')
        self.assertEqual(keyboard.feed(E.KEY_F, 1, 'menu'), ('UP', None, False))
        self.assertEqual(keyboard.feed(E.KEY_X, 1, 'edit'), ('CHAR', 'x', False))
        self.assertEqual(keyboard.feed(79, 1, 'view'), ('UP', None, False))
        self.assertEqual(keyboard.feed(91, 1, 'view'), ('DOWN', None, False))

    def test_modifier_events_and_repeat(self):
        keyboard = KeyTranslator(E, True)
        self.assertIsNone(keyboard.feed(E.KEY_LEFTSHIFT, 1, 'edit'))
        self.assertEqual(keyboard.feed(E.KEY_A, 1, 'edit'), ('CHAR', 'A', False))
        self.assertEqual(keyboard.feed(E.KEY_A, 2, 'edit'), ('CHAR', 'A', False))
        self.assertEqual(keyboard.feed(E.KEY_ENTER, 1, 'edit'), ('ENTER', None, True))
        keyboard.feed(E.KEY_LEFTSHIFT, 0, 'edit')
        self.assertEqual(keyboard.feed(E.KEY_A, 1, 'edit'), ('CHAR', 'a', False))

    def test_external_keyboard_keeps_standard_us_keycodes(self):
        keyboard = KeyTranslator(E, False)
        self.assertEqual(keyboard.feed(E.KEY_LEFTBRACE, 1, 'edit'), ('CHAR', '[', False))
        self.assertIsNone(keyboard.feed(E.KEY_LEFTSHIFT, 1, 'edit'))
        self.assertEqual(keyboard.feed(E.KEY_2, 1, 'edit'), ('CHAR', '@', False))


if __name__ == '__main__':
    unittest.main()
