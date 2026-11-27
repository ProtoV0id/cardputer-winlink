"""Session activity indicator and real packet stage transitions."""
import threading
import time
import unittest
from unittest import mock

from PIL import ImageChops
import winlink_gui as gui


class WaitingBackend:
    def __init__(self):
        self.session_started = threading.Event()
        self.release = threading.Event()
        self.packet_calls = []

    def connect_telnet(self):
        self.session_started.set()
        if not self.release.wait(3):
            raise RuntimeError("test did not release session")
        return "Session complete"

    def get_settings(self):
        return {"callsign": "N0CALL"}

    def connect_packet(self, gateway, port, via):
        self.packet_calls.append((gateway, port, via))
        self.session_started.set()
        if not self.release.wait(3):
            raise RuntimeError("test did not release session")
        return "Packet complete"


class SessionProgressTests(unittest.TestCase):
    def setUp(self):
        self.backend = WaitingBackend()
        self.addCleanup(self.backend.release.set)
        self.ui = gui.UI(self.backend)

    def finish_task(self):
        self.backend.release.set()
        for _ in range(100):
            self.ui.poll()
            if self.ui.mode != "busy":
                return
            time.sleep(0.01)
        self.fail("session worker did not finish")

    def drawn_text(self):
        original = gui.wrap
        with mock.patch.object(gui, "wrap", wraps=original) as wrap:
            self.ui.draw()
        return wrap.call_args.args[0]

    def test_telnet_uses_moving_activity_not_a_percentage(self):
        self.ui.connect()
        self.assertTrue(self.backend.session_started.wait(1))
        self.assertEqual(self.ui._session_progress, "telnet")
        text = self.drawn_text()
        self.assertIn("Telnet session in progress", text)
        self.assertIn("Waiting for Pat", text)
        self.assertNotIn("%", text)
        first = self.ui.draw().crop((0, 130, 320, 142))
        self.ui._session_started -= 1.0
        second = self.ui.draw().crop((0, 130, 320, 142))
        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())
        self.finish_task()
        self.assertEqual(self.ui.title, "SESSION FINISHED")
        self.assertIsNone(self.ui._session_progress)

    def test_packet_shows_radio_setup_then_actual_pat_session(self):
        entered, release_radio = threading.Event(), threading.Event()
        self.addCleanup(release_radio.set)

        class Radio:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                entered.set()
                if not release_radio.wait(3):
                    raise RuntimeError("test did not release radio")
                return self

            def __exit__(self, *_):
                pass

        self.ui.tnc_factory = Radio
        self.ui.packet_connect({"mac": "AA:BB:CC:DD:EE:FF", "gateway": "N0GAT", "via": "N0DIG"})
        self.assertTrue(entered.wait(1))
        self.assertIn("Connecting to radio", self.drawn_text())
        self.assertEqual(self.backend.packet_calls, [])
        release_radio.set()
        self.assertTrue(self.backend.session_started.wait(1))
        self.assertIn("Packet session in progress", self.drawn_text())
        self.assertEqual(self.backend.packet_calls, [("N0GAT", 8000, "N0DIG")])
        self.finish_task()
        self.assertIsNone(self.ui._session_progress)

    def test_other_busy_tasks_do_not_claim_session_activity(self):
        blocked = threading.Event()
        self.addCleanup(blocked.set)
        self.ui.task("LOADING", lambda: blocked.wait(2), lambda _: self.ui.home())
        self.assertIsNone(self.ui._session_progress)
        self.assertEqual(self.drawn_text(), "Please wait...")
        blocked.set()


if __name__ == "__main__":
    unittest.main()
