from __future__ import annotations

import copy
import json
import os
import queue
import base64
import tempfile
import time
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
        from PySide6 import QtGui
        from macro_studio.deck_starter_actions import starter_action_pack

        for key in ("youtube", "naver", "ldplayer", "explorer", "notepad", "calculator", "settings"):
            slots, icons = starter_action_pack(key)
            self.assertTrue(slots, key)
            self.assertEqual(len(slots), len(icons), key)
            for position, slot in enumerate(slots):
                self.assertEqual("deck_action", slot["mode"])
                self.assertTrue(slot["action"]["kind"])
                self.assertTrue(base64.b64decode(icons[str(position)]["image_data"]).startswith(b"\x89PNG"))
            image = QtGui.QImage.fromData(base64.b64decode(icons["0"]["image_data"]))
            self.assertEqual(255, image.pixelColor(0, 0).alpha())

    def test_default_browser_shortcuts_preserve_existing_pages(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            slots = [{"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(30)]
            slots[0]["macro"] = "기존 첫 슬롯"
            slots[15]["macro"] = "기존 두 번째 페이지"
            repository.save_hotkeys({"slots": slots, "deck_page_count": 2,
                                     "deck_page_names": ["첫 페이지", "두 번째 페이지"]})
            window = QuickSlotDeckWindow(repository)
            default = window.config["slot_presets"]["default"]
            self.assertEqual((3, 6), (default["rows"], default["cols"]))
            self.assertEqual("기존 첫 슬롯", default["slots"][0]["macro"])
            self.assertEqual("기존 두 번째 페이지", default["slots"][18]["macro"])
            self.assertEqual(["네이버", "유튜브", "트레이딩", "테트리스", "ChatGPT", "구글"],
                             [default["slots"][index]["macro"] for index in range(12, 18)])
            self.assertTrue(all(default["custom_icons"][str(index)]["image_data"]
                                for index in range(12, 18)))
            self.assertTrue(all(key in window.config["slot_presets"]
                                for key in ("starter-jstris", "starter-chatgpt", "starter-google")))
            self.assertFalse(window._seed_browser_shortcuts())
            window.close()

    def test_browser_bridge_command_round_trip_requires_token(self) -> None:
        from macro_studio.deck_browser_bridge import DeckBrowserBridge

        bridge = DeckBrowserBridge("secret", port=0)
        port = bridge.server.server_address[1]
        try:
            command_id = bridge.request_tab("https://www.naver.com/", "domain")
            with self.assertRaises(HTTPError):
                urlopen(Request(f"http://127.0.0.1:{port}/next-command"), timeout=2)
            request = Request(f"http://127.0.0.1:{port}/next-command",
                              headers={"X-MacroRelay-Token": "secret"})
            payload = json.loads(urlopen(request, timeout=2).read())
            self.assertEqual(command_id, payload["id"])
            self.assertEqual("activate_tab", payload["kind"])
            self.assertGreater(bridge.last_command_poll_at, 0)
            response = urlopen(Request(f"http://127.0.0.1:{port}/command-result",
                                       data=json.dumps({"id": command_id, "status": "ok",
                                                        "url": payload["url"]}).encode(),
                                       headers={"X-MacroRelay-Token": "secret",
                                                "Content-Type": "application/json"}), timeout=2)
            self.assertEqual(204, response.status)
            self.assertEqual(command_id, bridge.command_results.get_nowait()["id"])
        finally:
            bridge.close()

    def test_browser_navigation_command_reuses_site_tab(self) -> None:
        from macro_studio.deck_browser_bridge import DeckBrowserBridge

        bridge = DeckBrowserBridge("secret", port=0)
        try:
            bridge.request_navigation("https://www.youtube.com/", "domain")
            command = bridge.commands.get_nowait()
            self.assertEqual("navigate_tab", command["kind"])
            self.assertEqual("https://www.youtube.com/", command["url"])
        finally:
            bridge.close()

    def test_youtube_home_upgrades_saved_starter_action_and_queues_navigation(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            preset = window.config["slot_presets"]["starter-youtube"]
            home = next(slot for slot in preset["slots"] if slot.get("macro") == "YouTube 홈")
            self.assertEqual("navigate_browser_tab", home["action"]["kind"])
            home["action"] = {"kind": "open_target", "label": "YouTube 홈",
                              "target": "https://www.youtube.com/"}
            self.assertTrue(window._upgrade_youtube_home_action())
            self.assertFalse(window._upgrade_youtube_home_action())
            window._switch_slot_preset("starter-youtube")
            home = next(slot for slot in window.repository.load_hotkeys()["slots"]
                        if slot.get("macro") == "YouTube 홈")
            with mock.patch.object(window, "_find_whale_window", return_value=123), \
                    mock.patch.object(window, "_focus_whale_for_shortcut", return_value=True):
                window._execute_deck_action(home["action"], slot_index=1)
            command = window._browser_bridge.commands.get_nowait()
            self.assertEqual("navigate_tab", command["kind"])
            self.assertEqual("starter-youtube", window.config["active_slot_preset"])
            window.close()

    def test_cancelled_browser_command_is_not_delivered_after_reload(self) -> None:
        from macro_studio.deck_browser_bridge import DeckBrowserBridge

        bridge = DeckBrowserBridge("secret", port=0)
        port = bridge.server.server_address[1]
        try:
            command_id = bridge.request_tab("https://www.naver.com/", "domain")
            bridge.cancel_command(command_id)
            request = Request(f"http://127.0.0.1:{port}/next-command",
                              headers={"X-MacroRelay-Token": "secret"})
            self.assertEqual(204, urlopen(request, timeout=2).status)
        finally:
            bridge.close()

    def test_browser_command_is_dispatched_only_to_selected_browser(self) -> None:
        from macro_studio.deck_browser_bridge import DeckBrowserBridge

        bridge = DeckBrowserBridge("secret", port=0)
        port = bridge.server.server_address[1]
        try:
            command_id = bridge.request_tab("https://example.com/", "domain", browser="edge")
            headers = {"X-MacroRelay-Token": "secret"}
            whale = Request(f"http://127.0.0.1:{port}/next-command?browser=whale", headers=headers)
            edge = Request(f"http://127.0.0.1:{port}/next-command?browser=edge", headers=headers)
            self.assertEqual(204, urlopen(whale, timeout=2).status)
            payload = json.loads(urlopen(edge, timeout=2).read())
            self.assertEqual(command_id, payload["id"])
            self.assertEqual("edge", payload["browser"])
        finally:
            bridge.close()

    def test_browser_shortcut_switches_only_after_extension_ack(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            self.assertIsNotNone(window._browser_bridge)
            action = window.config["slot_presets"]["default"]["slots"][12]["action"]
            def focus_after_queue(_hwnd: int) -> bool:
                self.assertEqual(1, window._browser_bridge.commands.qsize())
                return True

            with mock.patch.object(window, "_find_whale_window", return_value=123), \
                    mock.patch.object(window, "_focus_whale_for_shortcut", side_effect=focus_after_queue):
                window._execute_deck_action(action, slot_index=12)
            self.assertEqual("default", window.config["active_slot_preset"])
            command_id = next(iter(window._pending_browser_actions))
            command = window._browser_bridge.commands.get_nowait()
            self.assertEqual(command_id, command["id"])
            window._browser_bridge.command_results.put_nowait(
                {"id": command_id, "status": "ok", "url": action["url"]})
            window._poll_browser_events()
            self.assertEqual("starter-naver", window.config["active_slot_preset"])
            self.assertFalse(window._pending_browser_actions)
            window.close()

    def test_edge_site_shortcut_queues_edge_only_command(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            action = dict(window.config["slot_presets"]["default"]["slots"][12]["action"])
            action["browser"] = "edge"
            with mock.patch.object(window, "_find_browser_window", return_value=123) as find_window, \
                    mock.patch.object(window, "_focus_whale_for_shortcut", return_value=True):
                window._execute_deck_action(action, slot_index=12)
            find_window.assert_called_once_with("edge")
            command = window._browser_bridge.commands.get_nowait()
            self.assertEqual("edge", command["browser"])
            self.assertEqual("activate_tab", command["kind"])
            window.close()

    def test_whale_window_title_fallback_when_exe_lookup_fails(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            with mock.patch.object(window, "_resolve_action_window",
                                   side_effect=[ValueError("path denied"), 456]) as resolve:
                self.assertEqual(456, window._find_whale_window())
            self.assertEqual("- Whale", resolve.call_args_list[1].args[0]["target_title"])
            window.close()

    def test_browser_shortcut_timeout_does_not_change_preset(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            action = window.config["slot_presets"]["default"]["slots"][12]["action"]
            with mock.patch.object(window, "_find_whale_window", return_value=123), \
                    mock.patch.object(window, "_focus_whale_for_shortcut", return_value=True):
                window._execute_deck_action(action, slot_index=12)
            command_id = next(iter(window._pending_browser_actions))
            preset_id, slot_index, label, _deadline = window._pending_browser_actions[command_id]
            window._pending_browser_actions[command_id] = (preset_id, slot_index, label, time.monotonic() - 1)
            window._poll_browser_events()
            self.assertEqual("default", window.config["active_slot_preset"])
            self.assertFalse(window._pending_browser_actions)
            window.close()

    def test_old_extension_event_does_not_fake_shortcut_success(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["auto_preset_enabled"] = True
            action = window.config["slot_presets"]["default"]["slots"][12]["action"]
            with mock.patch.object(window, "_find_whale_window", return_value=123), \
                    mock.patch.object(window, "_focus_whale_for_shortcut", return_value=True):
                window._execute_deck_action(action, slot_index=12)
            command_id = next(iter(window._pending_browser_actions))
            window._browser_action_requested_at[command_id] = time.monotonic() - 3
            window._browser_bridge.last_active_tab_at = time.monotonic()
            window._browser_bridge.events.put_nowait((int(time.time() * 1000), "https://www.naver.com/"))
            window._poll_browser_events()
            self.assertEqual("default", window.config["active_slot_preset"])
            self.assertEqual("확장앱 다시 로드 필요", window._browser_action_notice)
            self.assertFalse(window._pending_browser_actions)
            window.config["auto_preset_enabled"] = False
            window.close()

    def test_home_slot_insertion_preserves_full_preset_and_icon_positions(self) -> None:
        from macro_studio.quickslot_deck import insert_default_home_slot

        preset = {
            "rows": 5, "cols": 9,
            "slots": [{"macro": f"작업 {index}", "mode": "hybrid"} for index in range(45)],
            "custom_icons": {"0": {"emoji": "A"}, "44": {"emoji": "Z"}},
            "pinned_slots": {}, "deck_page_count": 1, "deck_page_names": ["첫 페이지"],
        }
        self.assertTrue(insert_default_home_slot(preset))
        self.assertEqual("default", preset["slots"][0]["action"]["preset_id"])
        self.assertEqual([f"작업 {index}" for index in range(45)],
                         [slot["macro"] for slot in preset["slots"][1:]])
        self.assertEqual("A", preset["custom_icons"]["1"]["emoji"])
        self.assertEqual("Z", preset["custom_icons"]["45"]["emoji"])
        self.assertEqual(2, preset["deck_page_count"])
        self.assertEqual(["첫 페이지", "페이지 2"], preset["deck_page_names"])
        self.assertFalse(insert_default_home_slot(preset))
        self.assertEqual(46, len(preset["slots"]))

    def test_active_saved_preset_migrates_live_hotkeys_on_startup(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            trading_slots = [{"macro": f"매크로 {index}", "mode": "hybrid"} for index in range(45)]
            repository.save_hotkeys({"slots": trading_slots, "deck_page_count": 1,
                                     "deck_page_names": ["첫 페이지"]})
            preset = {
                "name": "트레이딩", "slots": trading_slots,
                "custom_icons": {"44": {"emoji": "Z"}}, "pinned_slots": {},
                "rows": 5, "cols": 9, "deck_page_count": 1,
                "deck_page_names": ["첫 페이지"], "style": {},
            }
            config = {"rows": 5, "cols": 9, "config": {
                "starter_presets_version": 1, "active_slot_preset": "trading",
                "slot_presets": {"default": {"name": "기본 모드", "slots": [], "custom_icons": {},
                                             "rows": 5, "cols": 9, "deck_page_count": 1,
                                             "deck_page_names": ["페이지 1"], "style": {}},
                                 "trading": preset},
            }}
            (repository.root / ".quickslot_deck_config.json").write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
            window = QuickSlotDeckWindow(repository)
            hotkeys = repository.load_hotkeys()
            self.assertEqual("홈", hotkeys["slots"][0]["macro"])
            self.assertEqual("매크로 0", hotkeys["slots"][1]["macro"])
            self.assertEqual("매크로 44", hotkeys["slots"][45]["macro"])
            self.assertEqual(2, hotkeys["deck_page_count"])
            self.assertEqual("Z", window.custom_icons["45"]["emoji"])
            window.close()

    def test_saved_starter_icons_migrate_without_changing_user_icons(self) -> None:
        from macro_studio.deck_starter_actions import starter_action_pack
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            slots, legacy_icons = starter_action_pack("naver", legacy_icons=True)
            _, new_icons = starter_action_pack("naver")
            legacy_icons["0"]["image_data"] = "older-qt-png"
            legacy_icons["1"] = {"image_data": "user-image", "icon_source": "custom"}
            window.config["slot_presets"]["legacy"] = {
                "name": "예전 아이콘", "slots": slots, "custom_icons": legacy_icons,
                "rows": 3, "cols": 5, "deck_page_count": 1,
                "deck_page_names": ["페이지 1"], "pinned_slots": {},
            }
            self.assertTrue(window._install_home_tiles(sync_active=False))
            migrated = window.config["slot_presets"]["legacy"]
            self.assertEqual(new_icons["0"]["image_data"], migrated["custom_icons"]["1"]["image_data"])
            self.assertEqual("user-image", migrated["custom_icons"]["2"]["image_data"])
            self.assertFalse(window._install_home_tiles(sync_active=False))
            window.close()

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
            self.assertEqual("홈", presets["starter-youtube"]["slots"][0]["macro"])
            self.assertEqual("YouTube 홈", presets["starter-youtube"]["slots"][1]["macro"])
            self.assertEqual("네이버 홈", presets["starter-naver"]["slots"][1]["macro"])
            self.assertTrue(presets["starter-youtube"]["custom_icons"]["1"]["image_data"])
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

    def test_manual_preset_switch_defers_large_config_save_until_after_click(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            with mock.patch.object(window, "_save_config") as save:
                window._switch_slot_preset("starter-youtube")
                self.assertEqual("starter-youtube", window.config["active_slot_preset"])
                self.assertTrue(window._auto_config_save.isActive())
                save.assert_not_called()
                window.close()
                save.assert_called_once_with()

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
            self.assertEqual("홈", preset["slots"][0]["macro"])
            self.assertEqual("내 동작", preset["slots"][1]["macro"])
            self.assertEqual(2, len(preset["slots"]))
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
            self.assertEqual("Second", repository.load_hotkeys()["slots"][18]["macro"])
            dock.close(); window.close()

    def test_grid_resize_keeps_pinned_row_and_column(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            slots = [{"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(15)]
            slots[7]["macro"] = "Pinned macro"  # row 1, column 1 after default expands to 3x6
            repository.save_hotkeys({"slots": slots})
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            dock._pin_slot(7)
            dock.grid_rows_spin.setValue(4)
            dock.grid_cols_spin.setValue(4)
            with mock.patch("macro_studio.deck_dock.QtWidgets.QMessageBox.information") as info:
                dock._apply_grid_size()
            info.assert_not_called()
            self.assertIn("5", window._active_pinned_slots())
            self.assertNotIn("7", window._active_pinned_slots())
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

    def test_default_preset_uses_first_slot_and_preserves_existing_slot(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "내 슬롯", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            other = window._build_slot_preset("다른 모드")
            other["slots"] = [{"macro": "다른 슬롯", "mode": "hybrid"}]
            window.config["slot_presets"]["other"] = other
            window._switch_slot_preset("other")
            slots = repository.load_hotkeys()["slots"]
            self.assertEqual(2, len(slots))
            self.assertEqual("switch_preset", slots[0]["action"]["kind"])
            self.assertEqual("default", slots[0]["action"]["preset_id"])
            self.assertEqual("다른 슬롯", slots[1]["macro"])
            self.assertFalse(hasattr(window, "default_preset_button"))
            window._run_slot_macro(0, "홈")
            self.assertEqual("default", window.config["active_slot_preset"])
            self.assertEqual("내 슬롯", repository.load_hotkeys()["slots"][0]["macro"])
            window.close()

    def test_manual_preset_lock_does_not_survive_restart(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            window = QuickSlotDeckWindow(repository)
            window.config["auto_preset_enabled"] = True
            window.config["auto_preset_manual_lock"] = True
            window._save_config()
            window.close()

            reopened = QuickSlotDeckWindow(repository)
            self.assertTrue(reopened.config["auto_preset_enabled"])
            self.assertFalse(reopened.config["auto_preset_manual_lock"])
            reopened.config["auto_preset_enabled"] = False
            reopened.close()

    def test_manual_switch_resumes_when_same_program_regains_focus(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["slot_presets"]["stream"] = window._build_slot_preset("방송")
            window.config["auto_preset_enabled"] = True
            window.config["auto_program_rules"] = [{"exe": "obs64.exe", "preset_id": "stream"}]
            window._last_foreground_identity = ("obs64.exe", 23456)
            window._switch_slot_preset("default")
            self.assertTrue(window.config["auto_preset_manual_lock"])
            with mock.patch("macro_studio.quickslot_deck.foreground_program", return_value=("obs64.exe", 23456)):
                window._poll_foreground_program()
            self.assertFalse(window.config["auto_preset_manual_lock"])
            self.assertEqual("stream", window.config["active_slot_preset"])
            window.config["auto_preset_enabled"] = False
            window.close()

    def test_auto_switch_reuses_tiles_when_grid_is_unchanged(self) -> None:
        from macro_studio.deck_starter_actions import default_home_slot
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [
                {"macro": "First", "hotkey": "", "mode": "hybrid"},
                {"macro": "Second original", "hotkey": "", "mode": "hybrid"},
            ]})
            window = QuickSlotDeckWindow(repository)
            first_button = window.buttons[0]
            alternate = window._build_slot_preset("다른 모드")
            alternate["slots"] = copy.deepcopy(window.config["slot_presets"]["default"]["slots"])
            alternate["slots"][0] = default_home_slot()[0]
            alternate["slots"][1] = {"macro": "Second", "hotkey": "", "mode": "hybrid"}
            window.config["slot_presets"]["alternate"] = alternate
            window._switch_slot_preset("alternate", automatic=True)
            self.assertIs(first_button, window.buttons[0])
            self.assertEqual("홈", window.buttons[0].macro_name)
            self.assertEqual("Second", window.buttons[1].macro_name)
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
            self.assertFalse(window.config["auto_preset_manual_lock"])
            window._browser_bridge = None
            window.config["auto_preset_enabled"] = False
            window.close()

    def test_browser_bridge_retries_after_startup_failure(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.config["auto_preset_enabled"] = True
            window.config["auto_preset_rules"] = [{"site": "naver.com", "preset_id": "default"}]
            if window._browser_bridge is not None:
                window._browser_bridge.close()
                window._browser_bridge = None
            window._browser_bridge_retry_at = 0
            with mock.patch.object(window, "_sync_browser_bridge") as sync:
                window._poll_browser_events()
                sync.assert_called_once()
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
