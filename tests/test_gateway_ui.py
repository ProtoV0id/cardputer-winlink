"""Gateway presets are editable from the device without starting a session."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gateway_profiles import GatewayProfiles
from winlink_gui import UI


class GatewayUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.radio_path = self.root / "radio.json"
        self.profile_path = self.root / "gateways.json"
        self.radio_data = {"mac": "AA:BB:CC:DD:EE:FF", "gateway": "KT7RUN-10",
                           "via": "W7MOT-6", "custom": {"preserve": True}}
        self.radio_path.write_text(json.dumps(self.radio_data), encoding="utf-8")
        self.backend = mock.Mock()
        self.tnc_factory = mock.Mock()
        self.ui = UI(self.backend, radio_config_path=self.radio_path,
                     appearance_config_path=self.root / "appearance.json",
                     contacts_path=self.root / "contacts.json",
                     gateway_profiles_path=self.profile_path,
                     tnc_factory=self.tnc_factory)

    def choose(self, prefix):
        self.assertEqual(self.ui.mode, "menu")
        for _ in range(len(self.ui.items)):
            if self.ui.items[self.ui.sel][0].startswith(prefix):
                self.ui.input("ENTER")
                return
            self.ui.input("DOWN")
        self.fail("Menu action not found: " + prefix)

    def enter_field(self, label, value):
        self.choose(label)
        self.assertEqual(self.ui.mode, "edit")
        for _ in range(len(self.ui.text)):
            self.ui.input("BACKSPACE")
        for char in value:
            self.ui.input("CHAR", char=char)
        self.ui.input("ENTER")

    def assert_no_session(self):
        self.tnc_factory.assert_not_called()
        self.backend.connect_packet.assert_not_called()
        self.backend.connect_telnet.assert_not_called()

    def test_save_current_route_and_show_frequency_note(self):
        before = self.radio_path.read_bytes()
        self.ui.packet_settings()
        self.choose("Gateway profiles")
        self.choose("Add / save current route")
        self.assertEqual(self.ui._profile_draft["gateway"], "KT7RUN-10")
        self.assertEqual(self.ui._profile_draft["via"], "W7MOT-6")
        self.enter_field("Name:", "Local")
        self.enter_field("Frequency note:", "145.050 MHz")
        self.choose("Save profile")
        self.assertEqual(self.ui.title, "GATEWAY PROFILES")
        saved = self.ui.gateway_profiles.list()[0]
        self.assertEqual((saved["name"], saved["gateway"], saved["via"], saved["frequency"]),
                         ("Local", "KT7RUN-10", "W7MOT-6", "145.050 MHz"))
        self.assertEqual(self.radio_path.read_bytes(), before)
        self.choose("Local")
        self.choose("View details")
        self.assertIn("145.050 MHz", self.ui.text)
        self.assertIn("note only", self.ui.text)
        self.assertIn("Tune the radio manually", self.ui.text)
        self.assert_no_session()

    def test_use_profile_updates_both_route_fields_once_and_preserves_other_fields(self):
        self.ui.gateway_profiles.create("Alternate", "N0CALL-10", "N0DIGI-1", "146.500 MHz")
        self.ui.packet_settings()
        self.choose("Gateway profiles")
        self.choose("Alternate")
        with mock.patch.object(self.ui.radio, "_update_fields", wraps=self.ui.radio._update_fields) as update:
            self.choose("Use this route")
        update.assert_called_once_with({"gateway": "N0CALL-10", "via": "N0DIGI-1"})
        self.assertEqual(self.ui.title, "ROUTE SELECTED")
        self.assertIn("146.500 MHz", self.ui.text)
        self.assertIn("Tune the radio manually", self.ui.text)
        self.assertIn("No session has started", self.ui.text)
        active = json.loads(self.radio_path.read_text(encoding="utf-8"))
        self.assertEqual(active, {"mac": "AA:BB:CC:DD:EE:FF", "gateway": "N0CALL-10",
                                  "via": "N0DIGI-1", "custom": {"preserve": True}})
        self.assert_no_session()

    def test_failed_route_write_does_not_leave_a_partial_route(self):
        self.ui.gateway_profiles.create("Alternate", "N0CALL-10", "N0DIGI-1")
        self.ui.gateway_profile_list()
        self.choose("Alternate")
        before = self.radio_path.read_bytes()
        with mock.patch("winlink_gui.os.replace", side_effect=OSError("storage error")):
            self.choose("Use this route")
        self.assertEqual(self.ui.title, "PROFILE ERROR")
        self.assertEqual(self.radio_path.read_bytes(), before)
        self.assert_no_session()

    def test_edit_and_delete_require_explicit_save_or_confirmation(self):
        profile = self.ui.gateway_profiles.create("Local", "KT7RUN-10", "W7MOT-6")
        self.ui.gateway_profile_list()
        self.choose("Local")
        self.choose("Edit profile")
        self.enter_field("Name:", "New name")
        self.choose("Cancel")
        self.assertEqual(self.ui.gateway_profiles.get(profile["id"])["name"], "Local")
        self.choose("Local")
        self.choose("Edit profile")
        self.enter_field("Name:", "New name")
        self.choose("Save profile")
        self.assertEqual(self.ui.gateway_profiles.get(profile["id"])["name"], "New name")
        self.choose("New name")
        self.choose("Delete profile")
        self.assertEqual(self.ui.title, "DELETE PROFILE?")
        self.ui.input("ENTER")  # Default is Keep profile.
        self.assertEqual(len(self.ui.gateway_profiles.list()), 1)
        self.choose("Delete profile")
        self.choose("Delete New name")
        self.assertEqual(self.ui.gateway_profiles.list(), [])
        self.assertEqual(json.loads(self.radio_path.read_text(encoding="utf-8")), self.radio_data)
        self.assert_no_session()

    def test_stale_edit_and_stale_delete_leave_newer_profile_untouched(self):
        profile = self.ui.gateway_profiles.create("Local", "KT7RUN-10", "W7MOT-6")
        self.ui.gateway_profile_list()
        self.choose("Local")
        self.choose("Edit profile")
        self.enter_field("Name:", "Old edit")
        external = GatewayProfiles(self.profile_path)
        current = external.update(profile["id"], "New route", "N0CALL-10", "N0DIGI-1")
        changed = self.profile_path.read_bytes()
        self.choose("Save profile")
        self.assertEqual(self.ui.title, "PROFILE ERROR")
        self.assertIn("changed", self.ui.text)
        self.assertEqual(self.profile_path.read_bytes(), changed)
        self.ui.gateway_profile_list()
        self.choose("New route")
        self.choose("Delete profile")
        external.update(current["id"], "Newer route", "N0CALL-10", "")
        changed = self.profile_path.read_bytes()
        self.choose("Delete New route")
        self.assertEqual(self.ui.title, "PROFILE ERROR")
        self.assertEqual(self.profile_path.read_bytes(), changed)
        self.assert_no_session()

    def test_stale_profile_cannot_select_an_unseen_route(self):
        profile = self.ui.gateway_profiles.create("Local", "KT7RUN-10", "W7MOT-6")
        self.ui.gateway_profile_list()
        self.choose("Local")
        GatewayProfiles(self.profile_path).update(profile["id"], "Local", "N0CALL-10", "")
        before = self.radio_path.read_bytes()
        self.choose("Use this route")
        self.assertEqual(self.ui.title, "PROFILE ERROR")
        self.assertIn("changed", self.ui.text)
        self.assertEqual(self.radio_path.read_bytes(), before)
        self.assert_no_session()

    def test_corrupt_profile_store_reports_error_without_overwriting(self):
        self.profile_path.write_bytes(b"invalid JSON")
        self.ui.packet_settings()
        self.choose("Gateway profiles")
        self.assertEqual(self.ui.title, "PROFILES ERROR")
        self.assertEqual(self.profile_path.read_bytes(), b"invalid JSON")
        self.assertEqual(json.loads(self.radio_path.read_text(encoding="utf-8")), self.radio_data)
        self.assert_no_session()


if __name__ == "__main__":
    unittest.main()
