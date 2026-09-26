from __future__ import annotations

import json
import os
import queue
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DeckAutoFeaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_starter_packs_have_editable_actions_and_embedded_icons(self) -> None:
        from macro_studio.deck_starter_actions import starter_action_pack

        for key in ("youtube", "naver", "ldplayer", "explorer", "notepad", "calculator", "settings"):
            slots, icons = starter_action_pack(key)
            self.assertTrue(slots, key)
            self.assertEqual(len(slots), len(icons), key)
            for position, slot in enumerate(slots):
                self.assertEqual("deck_action", slot["mode"])
                self.assertTrue(slot["action"]["kind"])
                self.assertTrue(base64.b64decode(icons[str(position)]["image_data"]).startswith(b"\x89PNG"))

    def test_site_matching_uses_host_and_path_not_substrings(self) -> None:
        from macro_studio.deck_browser_bridge import normalize_site_rule, preset_for_url

        rules = [
            {"site": "youtube.com", "preset_id": "youtube"},
            {"site": "tradingview.com/chart", "preset_id": "trading"},
            {"site": "naver.com", "preset_id": "naver"},
        ]
        self.assertEqual("youtube", preset_for_url("https://www.youtube.com/watch?v=123", rules))
        self.assertEqual("trading", preset_for_url("https://www.tradingview.com/chart/abc", rules))
        self.assertEqual("", preset_for_url("https://not-tradingview.com/chart", rules))
        self.assertEqual("", preset_for_url("https://tradingview.com/charts", rules))
        self.assertEqual("", preset_for_url("chrome://newtab", rules))
        self.assertEqual("naver.com", normalize_site_rule("https://NAVER.com/"))

    def test_program_matching_uses_executable_name(self) -> None:
        from macro_studio.deck_browser_bridge import normalize_program_rule, preset_for_program

        rules = [{"exe": "obs64.exe", "preset_id": "stream"}]
        self.assertEqual("obs64.exe", normalize_program_rule(r"C:\Program Files\OBS\obs64.exe"))
        self.assertEqual("stream", preset_for_program("OBS64.EXE", rules))
        self.assertEqual("", preset_for_program("not-obs64.exe", rules))

    def test_starter_modes_keep_existing_slots_and_auto_switch_off(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "My macro", "hotkey": "", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            presets = window.config["slot_presets"]
            self.assertEqual("My macro", presets["default"]["slots"][0]["macro"])
            self.assertIn("starter-youtube", presets)
            self.assertIn("starter-naver", presets)
            self.assertIn("starter-ldplayer", presets)
            self.assertEqual("YouTube 홈", presets["starter-youtube"]["slots"][0]["macro"])
            self.assertEqual("네이버 홈", presets["starter-naver"]["slots"][0]["macro"])
            self.assertTrue(presets["starter-youtube"]["custom_icons"]["0"]["image_data"])
            self.assertFalse(window.config["auto_preset_enabled"])
            self.assertIn("기본 모드", window.preset_badge.text())
            self.assertIn("▶", [item.icon_text for item in window.preset_radial_menu.items])
            window.close()

    def test_mode_badge_tracks_auto_switch_with_preset_grid_size(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["auto_preset_enabled"] = True
            window._switch_slot_preset("starter-youtube", automatic=True)
            self.assertIn("유튜브", window.preset_badge.text())
            self.assertIn("자동", window.preset_badge.text())
            preset = window.config["slot_presets"]["starter-youtube"]
            self.assertEqual(preset["rows"], window.rows)
            self.assertEqual(preset["cols"], window.cols)
            window._switch_slot_preset("default")
            self.assertIn("고정", window.preset_badge.text())
            window.config["auto_preset_enabled"] = False
            window.close()

    def test_windows_starter_mode_is_optional_and_does_not_change_live_config_until_saved(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow, SlotPresetDialog, STARTER_PRESETS
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = SlotPresetDialog(window)
            explorer = next(item for item in STARTER_PRESETS if item[0] == "explorer")
            dialog._add_starter(explorer)
            self.assertIn("starter-explorer", dialog.presets)
            self.assertEqual("▣", dialog.presets["starter-explorer"]["icon"])
            self.assertNotIn("starter-explorer", window.config["slot_presets"])
            self.assertEqual("explorer.exe", dialog.starter_rules[-1][2][0])
            dialog.close(); window.close()

    def test_existing_starter_slots_are_not_replaced(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            (repository.root / ".quickslot_deck_config.json").write_text(
                json.dumps({"config": {
                    "starter_presets_version": 1,
                    "slot_presets": {
                        "starter-youtube": {
                            "name": "유튜브", "slots": [{"macro": "내 동작", "hotkey": ""}],
                            "icon_configs": {}, "pages": []
                        }
                    }
                }}), encoding="utf-8"
            )
            window = QuickSlotDeckWindow(repository)
            preset = window.config["slot_presets"]["starter-youtube"]
            self.assertEqual("내 동작", preset["slots"][0]["macro"])
            self.assertEqual(1, len(preset["slots"]))
            window.close()

    def test_removed_starter_actions_do_not_reappear_after_switch(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            window = QuickSlotDeckWindow(repository)
            window._switch_slot_preset("starter-youtube")
            hotkeys = repository.load_hotkeys()
            hotkeys["slots"] = []
            repository.save_hotkeys(hotkeys)
            window._switch_slot_preset("default")
            self.assertTrue(window.config["slot_presets"]["starter-youtube"]["starter_actions_initialized"])
            self.assertEqual([], window.config["slot_presets"]["starter-youtube"]["slots"])
            window.close()

    def test_management_menu_always_keeps_close_on_legacy_and_custom_layouts(self) -> None:
        from macro_studio.quickslot_deck import (
            QuickSlotDeckWindow, RadialWheelPreviewWidget, normalize_management_radial_items,
        )
        from macro_studio.repository import MacroRepository

        old_layout = ["prev_page", "next_page", "settings", "deck_dock",
                      "preset_settings", "emergency", "studio", "topmost"]
        self.assertEqual("close_app", normalize_management_radial_items(old_layout)[-1])
        self.assertEqual("close_app", normalize_management_radial_items(old_layout + ["close_app"])[-1])
        preview = RadialWheelPreviewWidget()
        preview.set_radial_keys(old_layout)
        self.assertEqual("close_app", preview.items_keys[-1])
        preview.close()
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            (repository.root / ".quickslot_deck_config.json").write_text(
                json.dumps({"config": {"radial_items": old_layout}}), encoding="utf-8"
            )
            window = QuickSlotDeckWindow(repository)
            self.assertEqual("close_app", window.config["radial_items"][-1])
            self.assertEqual("close_app", window.radial_menu.items[-1].key)
            window.close()

    def test_bridge_requires_token_and_only_accepts_small_url_events(self) -> None:
        from macro_studio.deck_browser_bridge import DeckBrowserBridge

        bridge = DeckBrowserBridge("a-secret-token", port=0)
        port = bridge.server.server_port
        try:
            payload = json.dumps({"url": "https://www.youtube.com/watch"}).encode()
            bad = Request(f"http://127.0.0.1:{port}/active-tab", payload, method="POST")
            bad.add_header("X-MacroRelay-Token", "wrong")
            with self.assertRaises(HTTPError) as caught:
                urlopen(bad, timeout=2)
            self.assertEqual(403, caught.exception.code)
            good = Request(f"http://127.0.0.1:{port}/active-tab", payload, method="POST")
            good.add_header("X-MacroRelay-Token", "a-secret-token")
            with urlopen(good, timeout=2) as response:
                self.assertEqual(204, response.status)
            self.assertEqual("https://www.youtube.com/watch", bridge.events.get_nowait()[1])
        finally:
            bridge.close()

    def test_pinned_slot_repeats_on_pages_and_undo_restores_original(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            slots = [{"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(30)]
            slots[0] = {"macro": "Pinned macro", "hotkey": "", "mode": "hybrid"}
            repository.save_hotkeys({"deck_page_count": 2, "slots": slots})
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            dock._pin_slot(0)
            self.assertIn("0", window._active_pinned_slots())
            window.current_page = 1
            window.refresh_slots()
            self.assertEqual("Pinned macro", window.buttons[0].macro_name)
            dock.undo()
            self.assertNotIn("0", window._active_pinned_slots())
            self.assertEqual("Pinned macro", repository.load_hotkeys()["slots"][0]["macro"])
            dock.redo()
            self.assertIn("0", window._active_pinned_slots())
            dock.close(); window.close()

    def test_pin_refuses_to_overwrite_a_page_slot(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            slots = [{"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(30)]
            slots[0]["macro"] = "First"
            slots[15]["macro"] = "Second"
            repository.save_hotkeys({"deck_page_count": 2, "slots": slots})
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            with mock.patch("macro_studio.deck_dock.QtWidgets.QMessageBox.information"):
                dock._pin_slot(0)
            self.assertNotIn("0", window._active_pinned_slots())
            self.assertEqual("First", repository.load_hotkeys()["slots"][0]["macro"])
            self.assertEqual("Second", repository.load_hotkeys()["slots"][15]["macro"])
            dock.close(); window.close()

    def test_grid_resize_keeps_pinned_row_and_column(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            slots = [{"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(15)]
            slots[6]["macro"] = "Pinned macro"  # row 1, column 1 of a 3x5 grid
            repository.save_hotkeys({"slots": slots})
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            dock._pin_slot(6)
            dock.grid_rows_spin.setValue(4)
            dock.grid_cols_spin.setValue(4)
            with mock.patch("macro_studio.deck_dock.QtWidgets.QMessageBox.information") as info:
                dock._apply_grid_size()
            info.assert_not_called()
            self.assertIn("5", window._active_pinned_slots())
            self.assertNotIn("6", window._active_pinned_slots())
            dock.close(); window.close()

    def test_deck_dock_undo_redo_page_edit(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            self.assertEqual(1, dock.page_count)
            dock.add_page()
            self.assertEqual(2, dock.page_count)
            dock.undo()
            self.assertEqual(1, dock.page_count)
            dock.redo()
            self.assertEqual(2, dock.page_count)
            dock.close(); window.close()

    def test_failed_deck_action_is_inline_and_recorded_without_popup(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            with mock.patch("macro_studio.quickslot_deck.QtWidgets.QMessageBox.warning") as warning:
                window._execute_deck_action({"kind": "open_target", "target": "", "label": "Broken"}, slot_index=0)
            warning.assert_not_called()
            self.assertEqual("failed", window.execution_history[-1]["state"])
            self.assertIn("비어", window.execution_history[-1]["detail"])
            window.close()

    def test_auto_switch_does_not_stop_running_macros_and_manual_switch_locks(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["slot_presets"]["youtube"] = window._build_slot_preset("유튜브")
            with mock.patch.object(window, "stop_all_macros") as stop:
                window._switch_slot_preset("youtube", automatic=True)
                stop.assert_not_called()
            window.config["auto_preset_enabled"] = True
            window._switch_slot_preset("default")
            self.assertTrue(window.config["auto_preset_manual_lock"])
            window.config["auto_preset_enabled"] = False
            window.close()

    def test_auto_switch_reuses_tiles_when_grid_is_unchanged(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "First", "hotkey": "", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            first_button = window.buttons[0]
            alternate = window._build_slot_preset("다른 모드")
            alternate["slots"][0]["macro"] = "Second"
            window.config["slot_presets"]["alternate"] = alternate
            window._switch_slot_preset("alternate", automatic=True)
            self.assertIs(first_button, window.buttons[0])
            self.assertEqual("Second", window.buttons[0].macro_name)
            window.close()

    def test_backup_excludes_browser_connection_secret(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["auto_preset_token"] = "private-connection-code"
            window.config["auto_preset_rules"] = [{"site": "youtube.com", "preset_id": "default"}]
            payload = window.build_deck_backup_payload()
            self.assertNotIn("auto_preset_token", payload["deck_config"]["config"])
            self.assertIn("youtube.com", str(payload["deck_config"]["config"]["auto_preset_rules"]))
            window.close()

    def test_browser_events_switch_only_on_matching_rules_and_respect_manual_lock(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["slot_presets"]["youtube"] = window._build_slot_preset("유튜브")
            window.config["auto_preset_enabled"] = True
            window.config["auto_preset_rules"] = [{"site": "youtube.com", "preset_id": "youtube"}]
            bridge = mock.Mock()
            bridge.events = queue.Queue()
            window._browser_bridge = bridge
            bridge.events.put((1, "https://other.example/"))
            window._poll_browser_events()
            self.assertEqual("default", window.config["active_slot_preset"])
            bridge.events.put((2, "https://www.youtube.com/watch"))
            window._poll_browser_events()
            self.assertEqual("youtube", window.config["active_slot_preset"])
            window.config["auto_preset_manual_lock"] = True
            bridge.events.put((3, "https://other.example/"))
            window._poll_browser_events()
            self.assertEqual("youtube", window.config["active_slot_preset"])
            window._browser_bridge = None
            window.config["auto_preset_enabled"] = False
            window.close()

    def test_program_focus_switches_without_interrupting_browser_site_priority(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["slot_presets"]["stream"] = window._build_slot_preset("방송")
            window.config["auto_preset_enabled"] = True
            window.config["auto_program_rules"] = [{"exe": "obs64.exe", "preset_id": "stream"}]
            window.config["auto_preset_rules"] = [{"site": "youtube.com", "preset_id": "default"}]
            with mock.patch("macro_studio.quickslot_deck.foreground_program", return_value=("obs64.exe", 23456)):
                window._poll_foreground_program()
            self.assertEqual("stream", window.config["active_slot_preset"])
            with mock.patch("macro_studio.quickslot_deck.foreground_program", return_value=("whale.exe", 34567)):
                window._poll_foreground_program()
            self.assertEqual("stream", window.config["active_slot_preset"])
            window.config["auto_preset_enabled"] = False
            window.close()


if __name__ == "__main__":
    unittest.main()
