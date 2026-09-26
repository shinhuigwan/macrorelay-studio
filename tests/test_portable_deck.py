"""Regression checks for cross-PC Deck backup handling."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from macro_studio.portable_deck import adapt_backup_for_host, host_fingerprint, rebind_command
from macro_studio.quickslot_deck import bundled_deck_path, save_bundled_deck_payload


class PortableDeckTests(unittest.TestCase):
    def test_rebind_executable_keeps_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "example.exe"
            executable.touch()
            with mock.patch("macro_studio.portable_deck.shutil.which", return_value=str(executable)):
                command, changed, unresolved = rebind_command('"C:\\Old PC\\example.exe" --profile work')
            self.assertEqual(f'"{executable}" --profile work', command)
            self.assertTrue(changed)
            self.assertFalse(unresolved)

    def test_other_host_rebinds_and_reports_unportable_coordinates(self) -> None:
        payload = {
            "source_environment": {"host_fingerprint": "different-host"},
            "deck_config": {
                "geometry": "old-monitor",
                "config": {"slot_presets": {"one": {"slots": [{
                    "action": {"kind": "text", "target_window": "ahk_id 0x123", "target_exe": "whale.exe"}
                }]}}},
            },
            "hotkeys": {"slots": []},
            "macros": {"Example": {"steps": [
                {"action": "image_search", "engine": "opencv", "search_profile": "fast",
                 "region_mode": "client", "region": [10, 10, 50, 50],
                 "region_window": "ahk_id 0x456", "region_window_exe": "whale.exe"},
                {"action": "inactive_click", "coordinate_scope": "screen", "x": 200, "y": 300},
            ]}},
        }
        restored, report = adapt_backup_for_host(payload)
        self.assertEqual("old-monitor", payload["deck_config"]["geometry"])
        self.assertNotIn("geometry", restored["deck_config"])
        self.assertEqual("ahk_exe whale.exe", restored["deck_config"]["config"]["slot_presets"]["one"]["slots"][0]["action"]["target_window"])
        image = restored["macros"]["Example"]["steps"][0]
        self.assertEqual("precise", image["search_profile"])
        self.assertTrue(image["fallback_full_region"])
        self.assertEqual("ahk_exe whale.exe", image["region_window"])
        self.assertEqual(1, report["screen_coordinates"])

    def test_same_host_preserves_search_speed_and_geometry(self) -> None:
        payload = {"source_environment": {"host_fingerprint": host_fingerprint()},
                   "deck_config": {"geometry": "existing"}, "macros": {"M": {"steps": [
                       {"action": "image_search", "search_profile": "fast"}
                   ]}}}
        restored, report = adapt_backup_for_host(payload)
        self.assertEqual(payload, restored)
        self.assertFalse(any(report.values()))

    def test_local_bundled_save_keeps_previous_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initial = {"format": "macrorelay-deck-backup", "macros": {"one": {}}, "hotkeys": {"slots": []}}
            later = {"format": "macrorelay-deck-backup", "macros": {"two": {}}, "hotkeys": {"slots": []}}
            path = save_bundled_deck_payload(root, initial)
            self.assertEqual(path, bundled_deck_path(root))
            save_bundled_deck_payload(root, later)
            self.assertEqual(later, json.loads(path.read_text(encoding="utf-8")))
            backups = list(path.parent.glob("macrorelay_bundled_deck.backup-*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(initial, json.loads(backups[0].read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
