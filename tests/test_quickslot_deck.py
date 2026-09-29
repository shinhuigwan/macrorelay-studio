from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _use_legacy_compact_default(repository) -> None:
    (repository.root / ".quickslot_deck_config.json").write_text(
        json.dumps({"config": {"browser_shortcuts_version": 1,
                               "starter_presets_version": 1,
                               "show_empty_slots": False}}), encoding="utf-8")


class QuickSlotDeckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_only_filled_slots_are_rendered_and_window_compacts(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({
                "rows": 3,
                "cols": 5,
                "slots": [
                    {"macro": "첫 번째", "hotkey": "Alt+1", "mode": "hybrid"},
                    {"macro": "", "hotkey": "", "mode": "hybrid"},
                    {"macro": "두 번째", "hotkey": "Alt+2", "mode": "hybrid"},
                ],
            })
            _use_legacy_compact_default(repository)
            window = QuickSlotDeckWindow(repository)
            self.app.processEvents()

            self.assertEqual(["첫 번째", "두 번째"], [button.macro_name for button in window.buttons])
            self.assertEqual(2, window.grid_layout.count())
            self.assertLessEqual(window.width(), 430)
            window._save_preset_hotkeys({"slots": [{"macro": "첫 번째", "hotkey": "", "mode": "hybrid"}]})
            window.refresh_slots()
            self.assertEqual(1, window.grid_layout.count())
            window.close()

    def test_quickslot_uses_short_double_click_interval(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.quickslot_deck import QUICKSLOT_DOUBLE_CLICK_INTERVAL_MS, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            self.assertEqual(240, QUICKSLOT_DOUBLE_CLICK_INTERVAL_MS)
            self.assertEqual(QUICKSLOT_DOUBLE_CLICK_INTERVAL_MS, QtWidgets.QApplication.doubleClickInterval())
            window.close()

    def test_tool_dialog_prefers_left_side_of_deck(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.quickslot_deck import position_dialog_beside

        available = self.app.primaryScreen().availableGeometry()
        parent = QtWidgets.QWidget()
        parent.resize(180, 180)
        parent.move(available.right() - 220, available.top() + 60)
        dialog = QtWidgets.QDialog(parent)
        dialog.setMinimumSize(300, 220)
        point = position_dialog_beside(parent, dialog)

        self.assertLess(point.x() + dialog.width(), parent.frameGeometry().left())
        self.assertGreaterEqual(point.y(), available.top())
        dialog.close()
        parent.close()

    def test_tile_scale_presets_include_half_and_quarter(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckSettingsDialog, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "첫 번째", "hotkey": "", "mode": "hybrid"}]})
            _use_legacy_compact_default(repository)
            window = QuickSlotDeckWindow(repository)
            dialog = QuickSlotDeckSettingsDialog(window)

            scales = [float(dialog.tile_scale_combo.itemData(index)) for index in range(dialog.tile_scale_combo.count())]
            self.assertEqual([1.0, 0.5, 0.25], scales)

            window.config["tile_scale"] = 0.25
            window.refresh_slots()
            self.assertEqual((64, 64), (window.width(), window.height()))
            dialog.close()
            window.close()

    def test_browser_settings_explain_optional_debugger_notice_flag(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.quickslot_deck import QuickSlotDeckSettingsDialog, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = QuickSlotDeckSettingsDialog(window)
            hint = dialog.tab_browser.findChild(QtWidgets.QLabel, "whale_debugger_launch_hint")
            self.assertIsNotNone(hint)
            self.assertIn('"C:\\Program Files\\Naver\\Naver Whale\\Application\\whale.exe" --silent-debugger-extension-api', hint.text())
            self.assertIn("다른 디버깅 확장앱의 경고도 숨깁니다", hint.text())
            dialog.tabs.setCurrentWidget(dialog.tab_browser)
            dialog.show()
            self.app.processEvents()
            self.assertTrue(dialog.tab_browser.rect().contains(hint.geometry().bottomRight()))
            dialog.close()
            window.close()

    def test_touch_sound_settings_and_playback_toggle(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckSettingsDialog, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = QuickSlotDeckSettingsDialog(window)
            self.assertTrue(dialog.touch_sound_check.isChecked())
            self.assertEqual(22, dialog.touch_volume_spin.value())

            effect = mock.Mock()
            effect.isPlaying.return_value = False
            window._touch_sound_effect = effect
            window.config["touch_sound_enabled"] = True
            window.config["touch_sound_volume"] = 35
            window._play_touch_sound()
            effect.setVolume.assert_called_with(0.35)
            effect.play.assert_called_once_with()

            effect.reset_mock()
            window.config["touch_sound_enabled"] = False
            window._play_touch_sound()
            effect.play.assert_not_called()
            dialog.close(); window.close()

    def test_border_resize_keeps_grid_cells_square(self) -> None:
        from PySide6 import QtCore
        from macro_studio.quickslot_deck import QuickSlotDeckWindow, WINDOW_PADDING
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({
                "slots": [
                    {"macro": "첫 번째", "hotkey": "", "mode": "hybrid"},
                    {"macro": "두 번째", "hotkey": "", "mode": "hybrid"},
                ]
            })
            window = QuickSlotDeckWindow(repository)
            original = window.geometry()
            window._resize_edge = "right"
            window._resize_start_geom = original
            window._resize_start_pos = QtCore.QPoint(original.right(), original.center().y())
            window._handle_border_resize(QtCore.QPoint(original.right() - 140, original.center().y()))

            gap = int(window.config["tile_gap"])
            cell_width = (window.width() - WINDOW_PADDING - gap) / 2
            cell_height = window.height() - WINDOW_PADDING
            self.assertAlmostEqual(cell_width, cell_height, delta=1.0)
            self.assertGreaterEqual(cell_width, 48)
            window.close()

    def test_deck_shows_bottom_right_resize_grip(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.show()
            self.app.processEvents()
            grip = window.resize_grip
            self.assertTrue(grip.isVisible())
            self.assertEqual(window.width() - grip.width(), grip.x())
            self.assertEqual(window.height() - grip.height(), grip.y())

            QtTest.QTest.mousePress(grip, QtCore.Qt.LeftButton, pos=QtCore.QPoint(12, 12))
            self.assertEqual("bottom_right", window._resize_edge)
            old_width = window.width()
            window._handle_border_resize(window._resize_start_pos + QtCore.QPoint(40, 40))
            self.assertGreater(window.width(), old_width)
            self.assertEqual(window.width() - grip.width(), grip.x())
            QtTest.QTest.mouseRelease(grip, QtCore.Qt.LeftButton, pos=QtCore.QPoint(12, 12))
            self.assertEqual("", window._resize_edge)
            window.close()

    def test_icon_refresh_preserves_manually_selected_window_size(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({
                "slots": [
                    {"macro": "첫 번째", "hotkey": "", "mode": "hybrid"},
                    {"macro": "두 번째", "hotkey": "", "mode": "hybrid"},
                ]
            })
            window = QuickSlotDeckWindow(repository)
            window.resize_grid_from_width(310)
            selected_size = window.size()
            window.custom_icons["0"] = {
                "emoji": "A", "full_stretch": True, "text_show": False,
            }

            window.refresh_slots()

            self.assertEqual(selected_size, window.size())
            self.assertGreater(float(window.config.get("manual_tile_side") or 0), 0)
            window.close()

    def test_radial_add_uses_first_empty_slot(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("추가할 매크로")
            repository.save_hotkeys({"slots": [{"macro": "", "hotkey": "", "mode": "hybrid"}]})
            _use_legacy_compact_default(repository)
            window = QuickSlotDeckWindow(repository)
            with mock.patch.object(QtWidgets.QInputDialog, "getItem", return_value=("추가할 매크로", True)):
                window._add_slot_from_radial()

            self.assertEqual("추가할 매크로", repository.load_hotkeys()["slots"][0]["macro"])
            self.assertEqual(["추가할 매크로"], [button.macro_name for button in window.buttons])
            window.close()

    def test_double_click_runs_only_once(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import StreamDeckButton

        button = StreamDeckButton(0)
        button.set_slot_data("테스트", "", "hybrid", False)
        run_spy = QtTest.QSignalSpy(button.slot_triggered)
        button.show()
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        QtTest.QTest.mouseDClick(button, QtCore.Qt.LeftButton)

        self.assertEqual(1, run_spy.count())
        button.close()

    def test_slot_double_click_does_not_open_preset_radial(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "테스트", "hotkey": "", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            window.show()
            with mock.patch.object(window, "_show_preset_radial") as show_radial:
                QtTest.QTest.mouseDClick(window.buttons[0], QtCore.Qt.LeftButton)

            show_radial.assert_not_called()
            window.close()

    def test_two_rapid_separate_slot_taps_trigger_only_once(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import StreamDeckButton, SLOT_ACCIDENTAL_REPEAT_MS

        button = StreamDeckButton(0)
        button.set_slot_data("테스트")
        button.show()
        run_spy = QtTest.QSignalSpy(button.slot_triggered)
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.assertEqual(1, run_spy.count())
        button._last_activation_at -= SLOT_ACCIDENTAL_REPEAT_MS / 1000 + 0.01
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.assertEqual(2, run_spy.count())
        self.assertTrue(button.title_label.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents))
        button.close()

    def test_rapid_preset_switch_taps_are_not_debounced(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import StreamDeckButton

        button = StreamDeckButton(0)
        button.set_slot_data("프리셋 전환")
        button.action_kind = "switch_preset"
        button.show()
        run_spy = QtTest.QSignalSpy(button.slot_triggered)
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        QtTest.QTest.mouseDClick(button, QtCore.Qt.LeftButton)
        QtTest.QTest.mouseRelease(button, QtCore.Qt.LeftButton)
        self.assertEqual(3, run_spy.count())
        button.close()

    def test_repeat_at_same_screen_position_is_suppressed_across_preset_rebuild(self) -> None:
        from PySide6 import QtCore
        from macro_studio.quickslot_deck import QuickSlotDeckWindow, SLOT_ACCIDENTAL_REPEAT_MS
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            point = QtCore.QPoint(100, 100)
            self.assertTrue(window._accept_slot_tap(point))
            window.refresh_slots()
            self.assertFalse(window._accept_slot_tap(point))
            self.assertTrue(window._accept_slot_tap(point, preset_switch=True))
            self.assertTrue(window._accept_slot_tap(point, preset_switch=True))
            self.assertFalse(window._accept_slot_tap(point))
            self.assertTrue(window._accept_slot_tap(QtCore.QPoint(190, 100)))
            window._last_slot_tap_at -= SLOT_ACCIDENTAL_REPEAT_MS / 1000 + 0.01
            self.assertTrue(window._accept_slot_tap(QtCore.QPoint(190, 100)))
            window.close()

    def test_editor_text_and_zoom_match_compact_tile(self) -> None:
        from PySide6 import QtCore
        from macro_studio.quickslot_deck import SlotIconEditDialog

        dialog = SlotIconEditDialog(0, "416 로그인", {
            "full_stretch": True, "text_show": True, "text_override": "416 로그인",
            "font_size": 12, "image_zoom_percent": 125,
        })
        dialog.show()
        self.app.processEvents()
        self.assertEqual(160, dialog.preview_card.width())
        self.assertEqual(90, dialog.actual_preview_card.width())
        self.assertEqual(125, dialog.zoom_spin.value())
        self.assertEqual("416 로그인", dialog.actual_preview_card.title_label.text())
        self.assertFalse(dialog.preview_card.title_label.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents))
        self.assertTrue(dialog.actual_preview_card.title_label.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents))
        dialog._on_preview_zoom_requested(1)
        self.assertEqual(130, dialog.zoom_spin.value())
        dialog.text_override_edit.setText("더 긴 로그인 표시 문구")
        self.assertEqual("더 긴 로그인 표시 문구", dialog.get_config()["text_override"])
        dialog.text_override_edit.setText("416 로그인")
        dialog.preview_card._move_preview_text_to(QtCore.QPoint(50, 60))
        self.assertEqual("custom", dialog.get_config()["text_position"])
        self.assertNotEqual(50, dialog.get_config()["text_x_percent"])
        self.assertLessEqual(dialog.actual_preview_card.title_label.width(), dialog.actual_preview_card.width())
        self.assertLessEqual(dialog.actual_preview_card.title_label.height(), dialog.actual_preview_card.height())
        self.assertEqual(dialog.actual_preview_card.title_label.font().pixelSize(),
                         dialog.preview_card.title_label.font().pixelSize())
        dialog.close()

    def test_single_click_runs_immediately(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import StreamDeckButton

        button = StreamDeckButton(0)
        button.set_slot_data("테스트", "", "hybrid", False)
        run_spy = QtTest.QSignalSpy(button.slot_triggered)
        button.show()
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)

        self.assertEqual(1, run_spy.count())
        button.close()

    def test_background_double_click_opens_sticky_preset_radial(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.show()
            with mock.patch.object(window, "_show_preset_radial") as show_radial:
                QtTest.QTest.mouseDClick(window, QtCore.Qt.LeftButton, pos=window.rect().center())

            self.assertEqual(1, show_radial.call_count)
            self.assertTrue(show_radial.call_args.kwargs["sticky"])
            window.close()

    def test_preset_badge_tap_and_empty_tile_double_click_open_radial(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.show()
            empty_button = next(button for button in window.buttons if not button.macro_name)
            with mock.patch.object(window, "_show_preset_radial") as show_radial:
                window.preset_badge.click()
                QtTest.QTest.mouseDClick(empty_button, QtCore.Qt.LeftButton)
            self.assertEqual(2, show_radial.call_count)
            self.assertTrue(all(call.kwargs["sticky"] for call in show_radial.call_args_list))
            window.close()

    def test_starter_icon_caption_fits_small_saved_tile(self) -> None:
        from macro_studio.deck_starter_actions import compact_starter_caption, starter_action_pack
        from macro_studio.quickslot_deck import StreamDeckButton

        for pack in ("youtube", "naver", "ldplayer", "explorer", "notepad", "calculator", "settings"):
            slots, old_icons = starter_action_pack(pack, legacy_icons=True)
            for index, slot in enumerate(slots):
                button = StreamDeckButton(index)
                button.resize(80, 80)
                button.set_custom_icon_config(old_icons[str(index)])
                button.set_slot_data(slot["macro"])
                button.show()
                self.app.processEvents()
                self.assertEqual(compact_starter_caption(slot["macro"]), button.title_label.text())
                self.assertTrue(button.rect().contains(button.title_label.geometry()))
                button.close()

    def test_starter_caption_uses_full_text_width_on_short_tile(self) -> None:
        from PySide6 import QtGui
        from macro_studio.deck_starter_actions import starter_action_pack
        from macro_studio.quickslot_deck import StreamDeckButton

        slots, icons = starter_action_pack("youtube")
        for index, slot in enumerate(slots):
            button = StreamDeckButton(index)
            button.resize(86, 55)
            button.set_custom_icon_config(icons[str(index)])
            button.set_slot_data(slot["macro"], display_title=slot["action"]["label"])
            button.show()
            self.app.processEvents()
            label = button.title_label
            text_width = QtGui.QFontMetrics(label.font()).horizontalAdvance(label.text())
            self.assertEqual(button.display_title, label.text())
            self.assertGreaterEqual(label.contentsRect().width(), text_width + 4)
            self.assertTrue(button.rect().contains(label.geometry()))
            button.close()

    def test_sticky_radial_stays_open_until_outside_click(self) -> None:
        from PySide6 import QtCore, QtTest, QtWidgets
        from macro_studio.quickslot_deck import RadialPieMenuWidget

        host = QtWidgets.QWidget()
        host.resize(80, 80)
        host.move(700, 500)
        host.show()
        menu = RadialPieMenuWidget()
        menu.load_deck_presets([("default", "기본 모드"), ("browser", "브라우저 모드")])
        menu.popup_at(QtCore.QPoint(300, 300), sticky=True)
        self.app.processEvents()

        QtTest.QTest.mouseRelease(menu, QtCore.Qt.LeftButton, pos=QtCore.QPoint(5, 5))
        self.assertTrue(menu.isVisible())
        QtTest.QTest.mouseClick(host, QtCore.Qt.LeftButton, pos=host.rect().center())
        self.app.processEvents()
        self.assertFalse(menu.isVisible())
        host.close()

    def test_sticky_preset_radial_near_screen_edge_is_visible_and_selectable(self) -> None:
        from PySide6 import QtCore, QtGui, QtTest
        from macro_studio.quickslot_deck import RadialPieMenuWidget

        available = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        menu = RadialPieMenuWidget()
        menu.load_deck_presets([("default", "기본 모드"), ("youtube", "유튜브")])
        selected = []
        menu.action_triggered.connect(selected.append)
        menu.popup_at(available.topLeft() + QtCore.QPoint(3, 3), sticky=True)
        self.app.processEvents()
        self.assertTrue(available.contains(menu.geometry()))
        QtTest.QTest.qWait(260)
        QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton,
                                pos=menu._get_item_center(1).toPoint())
        self.app.processEvents()
        self.assertEqual(["deck:youtube"], selected)
        menu.close()

    def test_radial_hides_before_dispatching_modal_action(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import RadialPieMenuWidget

        menu = RadialPieMenuWidget()
        visible_when_dispatched = []
        menu.action_triggered.connect(lambda _key: visible_when_dispatched.append(menu.isVisible()))
        menu.popup_at(QtCore.QPoint(300, 300), sticky=False)
        self.app.processEvents()
        item_center = menu._get_item_center(0).toPoint()
        QtTest.QTest.mouseRelease(menu, QtCore.Qt.LeftButton, pos=item_center)
        self.app.processEvents()

        self.assertEqual([False], visible_when_dispatched)
        menu.close()

    def test_radial_settings_uses_drag_canvas_without_slot_combos(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckSettingsDialog, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = QuickSlotDeckSettingsDialog(window)
            self.assertFalse(hasattr(dialog, "radial_combos"))
            self.assertEqual(300, dialog.radial_preview.width())
            self.assertEqual(8, len(dialog.radial_preview.items_keys))
            dialog.close()
            window.close()

    def test_right_click_radial_includes_deck_dock(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            keys = [item.key for item in window.radial_menu.items]
            self.assertIn("deck_dock", keys)
            window.close()

    def test_deck_dock_persists_actions_and_pages(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": []})
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            self.assertEqual(window.rows * window.cols, dock.grid.count())
            self.assertEqual(16, len(dock.action_buttons))

            dock.payload["slots"][0] = dock._slot_from_action({"kind": "page_next", "label": "다음"})
            dock.add_page()
            saved = repository.load_hotkeys()
            self.assertEqual(2, saved["deck_page_count"])
            self.assertEqual("page_next", saved["slots"][0]["action"]["kind"])
            self.assertEqual(window.rows * window.cols * 2, len(saved["slots"]))
            dock.close()
            window.close()

    def test_deck_dock_moves_multiple_selected_slots_between_pages(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            dock.add_page()
            size = dock._page_size()
            first = dock._slot_from_action({"kind": "text", "text": "A"})
            second = dock._slot_from_action({"kind": "text", "text": "B"})
            dock.payload["slots"][size] = first
            dock.payload["slots"][size + 2] = second
            window.custom_icons[str(size)] = {"emoji": "A"}
            dock.selected_slots = {size, size + 2}

            moved, message = dock._move_selected_slots(0)

            self.assertTrue(moved, message)
            self.assertEqual(["A", "B"], [dock.payload["slots"][i]["action"]["text"] for i in (0, 1)])
            self.assertFalse(dock._is_filled_slot(dock.payload["slots"][size]))
            self.assertEqual({0, 1}, dock.selected_slots)
            self.assertEqual("A", window.custom_icons["0"]["emoji"])
            dock.close(); window.close()

    def test_deck_dock_bulk_patch_changes_only_requested_fields(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            for index, text in enumerate(("old-a", "old-b")):
                dock.payload["slots"][index] = dock._slot_from_action({
                    "kind": "text", "text": text, "key_interval_ms": 5,
                    "press_enter": False, "target_exe": "whale.exe",
                })

            changed = dock._apply_bulk_patch([0, 1], {"text": "same", "key_interval_ms": 40, "press_enter": True})

            self.assertEqual(2, changed)
            for index in (0, 1):
                action = dock.payload["slots"][index]["action"]
                self.assertEqual("same", action["text"])
                self.assertEqual(40, action["key_interval_ms"])
                self.assertTrue(action["press_enter"])
                self.assertEqual("whale.exe", action["target_exe"])
            dock.close(); window.close()

    def test_deck_dock_bulk_browser_switch_clears_stale_window_identity(self) -> None:
        from macro_studio.deck_dock import DeckBulkEditDialog, DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            for index in (0, 1):
                dock.payload["slots"][index] = dock._slot_from_action({
                    "kind": "hotkey", "keys": "Ctrl+R", "target_exe": "whale.exe",
                    "target_title": "TradingView - Whale", "target_window": "ahk_id 0x1234",
                })
            dialog = DeckBulkEditDialog("hotkey", dock.payload["slots"][0]["action"], 2, dock)
            enabled, browser = dialog.controls["target_exe"]
            enabled.setChecked(True)
            browser.setCurrentIndex(browser.findData("msedge.exe"))
            self.assertEqual({"target_exe": "msedge.exe"}, dialog.patch())
            dock._apply_bulk_patch([0, 1], dialog.patch())
            for index in (0, 1):
                action = dock.payload["slots"][index]["action"]
                self.assertEqual("msedge.exe", action["target_exe"])
                self.assertNotIn("target_window", action)
                self.assertNotIn("target_title", action)
                self.assertEqual("Ctrl+R", action["keys"])
            dialog.close(); dock.close(); window.close()

    def test_deck_dock_bulk_site_tab_can_select_edge(self) -> None:
        from macro_studio.deck_dock import DeckBulkEditDialog

        dialog = DeckBulkEditDialog("activate_browser_tab", {"browser": "whale"}, 3)
        enabled, browser = dialog.controls["browser"]
        enabled.setChecked(True)
        browser.setCurrentIndex(browser.findData("edge"))
        self.assertEqual({"browser": "edge"}, dialog.patch())
        dialog.close()

    def test_deck_dock_bulk_browser_buttons_select_and_find_executable(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.deck_dock import DeckBulkEditDialog

        dialog = DeckBulkEditDialog("hotkey", {"target_exe": "whale.exe"}, 2)
        buttons = {button.text(): button for button in dialog.findChildren(QtWidgets.QPushButton)}
        browser_menu = buttons["브라우저 선택 ▾"].menu()
        next(action for action in browser_menu.actions() if action.text() == "Microsoft Edge").trigger()
        self.assertEqual({"target_exe": "msedge.exe"}, dialog.patch())

        with mock.patch("macro_studio.deck_dock.QtWidgets.QFileDialog.getOpenFileName",
                        return_value=("C:/Apps/custom-browser.exe", "")):
            buttons["찾기…"].click()
        self.assertEqual({"target_exe": "custom-browser.exe"}, dialog.patch())
        dialog.close()

    def test_deck_dock_bulk_save_button_accepts_and_persists_changes(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.deck_dock import DeckBulkEditDialog, DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        class SaveDialog(DeckBulkEditDialog):
            def exec(self) -> int:
                enabled, browser = self.controls["target_exe"]
                enabled.setChecked(True)
                browser.setCurrentIndex(browser.findData("msedge.exe"))
                buttons = self.findChild(QtWidgets.QDialogButtonBox)
                buttons.button(QtWidgets.QDialogButtonBox.Save).click()
                return self.result()

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            for index in (0, 1):
                dock.payload["slots"][index] = dock._slot_from_action({
                    "kind": "text", "text": "old", "target_exe": "whale.exe",
                })
            dock.selected_slots = {0, 1}
            with mock.patch("macro_studio.deck_dock.DeckBulkEditDialog", SaveDialog):
                dock._bulk_edit_selected()
            actions = [slot["action"] for slot in repository.load_hotkeys()["slots"][:2]]
            self.assertEqual(["msedge.exe", "msedge.exe"], [action["target_exe"] for action in actions])
            dock.close(); window.close()

    def test_deck_text_activation_preserves_maximized_browser_size(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            with mock.patch.object(window, "_resolve_action_window", return_value=1234), \
                    mock.patch("macro_studio.quickslot_deck.ctypes.windll.user32") as user32:
                user32.IsIconic.return_value = False
                window._activate_action_window({"target_exe": "msedge.exe"})
                user32.ShowWindow.assert_not_called()
                user32.SetForegroundWindow.assert_called_with(1234)

                user32.IsIconic.return_value = True
                window._activate_action_window({"target_exe": "msedge.exe"})
                user32.ShowWindow.assert_called_once_with(1234, 9)
            window.close()

    def test_deck_dock_single_browser_buttons_clear_old_window(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.deck_dock import DeckActionConfigDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = DeckActionConfigDialog("hotkey", {
                "target_exe": "whale.exe", "target_title": "Old Whale",
                "target_window": "ahk_id 0x1234",
            }, window)
            buttons = {button.text(): button for button in dialog.findChildren(QtWidgets.QPushButton)}
            browser_menu = buttons["브라우저 선택 ▾"].menu()
            next(action for action in browser_menu.actions() if action.text() == "Microsoft Edge").trigger()
            action = dialog.result_action()
            self.assertEqual("msedge.exe", action["target_exe"])
            self.assertEqual("", action["target_title"])
            self.assertEqual("", action["target_window"])

            with mock.patch("macro_studio.deck_dock.QtWidgets.QFileDialog.getOpenFileName",
                            return_value=("C:/Apps/custom-browser.exe", "")):
                buttons["찾기…"].click()
            self.assertEqual("custom-browser.exe", dialog.result_action()["target_exe"])
            dialog.close(); window.close()

    def test_deck_dock_drag_selects_rectangle_and_ctrl_drag_adds(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            dock.show()
            self.app.processEvents()
            dock.selection_toggle.setChecked(True)
            self.app.processEvents()
            first = dock.grid.itemAt(0).widget()
            second = dock.grid.itemAt(1).widget()
            origin_local = first.rect().topLeft() + QtCore.QPoint(8, 8)
            origin = first.mapToGlobal(origin_local)
            end = second.mapToGlobal(second.rect().bottomRight() - QtCore.QPoint(8, 8))
            for event_type, local, global_pos, button, buttons in (
                (QtCore.QEvent.MouseButtonPress, origin_local, origin, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton),
                (QtCore.QEvent.MouseMove, first.mapFromGlobal(end), end, QtCore.Qt.NoButton, QtCore.Qt.LeftButton),
                (QtCore.QEvent.MouseButtonRelease, first.mapFromGlobal(end), end, QtCore.Qt.LeftButton, QtCore.Qt.NoButton),
            ):
                event = QtGui.QMouseEvent(event_type, QtCore.QPointF(local), QtCore.QPointF(global_pos), button, buttons, QtCore.Qt.NoModifier)
                QtWidgets.QApplication.sendEvent(first, event)
            self.assertTrue({0, 1}.issubset(dock.selected_slots))
            before = set(dock.selected_slots)
            third = dock.grid.itemAt(3).widget()
            origin = third.mapToGlobal(third.rect().topLeft())
            end = third.mapToGlobal(third.rect().bottomRight())
            dock._start_selection_drag(origin, end, True)
            dock._finish_selection_drag(end)
            self.assertTrue(before.issubset(dock.selected_slots))
            self.assertIn(3, dock.selected_slots)
            dock.close(); window.close()

    def test_deck_dock_selection_does_not_rebuild_icon_cards(self) -> None:
        from PySide6 import QtCore
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            dock.show()
            self.app.processEvents()
            first = dock.grid.itemAt(0).widget()
            second = dock.grid.itemAt(1).widget()
            with mock.patch.object(dock, "_render_page", wraps=dock._render_page) as render:
                dock.selection_toggle.setChecked(True)
                dock._toggle_slot_selection(0)
                self.assertTrue(first.selected)
                origin = first.mapToGlobal(first.rect().topLeft() + QtCore.QPoint(8, 8))
                end = second.mapToGlobal(second.rect().bottomRight() - QtCore.QPoint(8, 8))
                dock._start_selection_drag(origin, end, False)
                dock._finish_selection_drag(end)
                self.assertTrue(second.selected)
                dock._clear_selection()
                self.assertFalse(first.selected)
                render.assert_not_called()
            self.assertIs(first, dock.grid.itemAt(0).widget())
            dock.close(); window.close()

    def test_deck_dock_bulk_edits_existing_macro_and_start_point(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.deck_dock import DeckBulkEditDialog, DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            for name, entry in (("네이버", "A 로그인"), ("엣지", "B 로그인")):
                repository.create_macro(name)
                macro = repository.load_macro(name)
                macro["steps"] = [{"action": "wait", "duration": 1, "entry_name": entry}]
                repository.save_macro(name, macro)
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            for index in (0, 1):
                dock.payload["slots"][index] = dock._slot_from_action({
                    "kind": "run_macro", "label": f"슬롯 {index}",
                    "macro": "네이버", "entry_name": "A 로그인",
                })
            dock.selected_slots = {0, 1}

            class SaveDialog(DeckBulkEditDialog):
                def exec(self) -> int:
                    macro_check, macro_combo = self.controls["macro"]
                    entry_check, entry_combo = self.controls["entry_name"]
                    macro_check.setChecked(True)
                    macro_combo.setCurrentIndex(macro_combo.findData("엣지"))
                    entry_check.setChecked(True)
                    entry_combo.setCurrentIndex(entry_combo.findData("B 로그인"))
                    self.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Save).click()
                    return self.result()

            with mock.patch("macro_studio.deck_dock.DeckBulkEditDialog", SaveDialog):
                dock._bulk_edit_selected()
            actions = [slot["action"] for slot in repository.load_hotkeys()["slots"][:2]]
            self.assertEqual(["엣지", "엣지"], [action["macro"] for action in actions])
            self.assertEqual(["B 로그인", "B 로그인"], [action["entry_name"] for action in actions])
            self.assertEqual(["슬롯 0", "슬롯 1"], [action["label"] for action in actions])

            self.assertEqual("", dock._bulk_macro_patch_error([0, 1], {"macro": "네이버"}))
            dock._apply_bulk_patch([0, 1], {"macro": "네이버"})
            self.assertEqual(["", ""], [dock.payload["slots"][index]["action"]["entry_name"] for index in (0, 1)])
            self.assertIn("시작 지점이 없습니다", dock._bulk_macro_patch_error([0, 1], {"entry_name": "B 로그인"}))
            dock.close(); window.close()

    def test_deck_backup_restores_slots_actions_pages_and_icons(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({
                "deck_page_count": 2,
                "deck_page_names": ["기본", "트레이딩"],
                "slots": [{
                    "macro": "텍스트 입력", "hotkey": "", "mode": "deck_action",
                    "action": {"kind": "text", "text": "EURUSD", "key_interval_ms": 25, "press_enter": True},
                }],
            })
            window = QuickSlotDeckWindow(repository)
            window.custom_icons["0"] = {"emoji": "T", "full_stretch": True}
            payload = window.build_deck_backup_payload()
            self.assertEqual("macrorelay-deck-backup", payload["format"])
            self.assertEqual(2, payload["hotkeys"]["deck_page_count"])
            self.assertEqual({}, payload["deck_config"]["custom_icons"])

            window._save_preset_hotkeys({"slots": []})
            window.custom_icons = {}
            window.restore_deck_backup_payload(payload)
            restored = repository.load_hotkeys()
            action = restored["slots"][0]["action"]
            self.assertEqual("EURUSD", action["text"])
            self.assertEqual(25, action["key_interval_ms"])
            self.assertEqual(["기본", "트레이딩"], restored["deck_page_names"])
            self.assertEqual("T", window.custom_icons["0"]["emoji"])
            window.close()

    def test_legacy_deck_config_import_restores_active_preset_slots(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            window = QuickSlotDeckWindow(repository)
            legacy = {
                "rows": 2, "cols": 2, "custom_icons": {"0": {"emoji": "L"}},
                "config": {
                    "active_slot_preset": "trading",
                    "slot_presets": {"trading": {
                        "name": "트레이딩", "rows": 2, "cols": 2,
                        "slots": [{"macro": "대기", "mode": "deck_action", "action": {"kind": "wait", "ms": 350}}],
                        "deck_page_count": 1, "deck_page_names": ["트레이딩"],
                        "custom_icons": {"0": {"emoji": "L"}}, "style": {},
                    }},
                },
            }
            window.restore_deck_backup_payload(legacy)
            restored = repository.load_hotkeys()
            self.assertEqual("switch_preset", restored["slots"][0]["action"]["kind"])
            self.assertEqual(350, restored["slots"][1]["action"]["ms"])
            self.assertEqual("트레이딩", restored["deck_page_names"][0])
            window.close()

    def test_deck_backup_embeds_referenced_macro_and_image_asset(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = MacroRepository(Path(source_dir))
            source.save_hotkeys({"slots": [{
                "macro": "매크로 실행", "mode": "deck_action",
                "action": {"kind": "run_macro", "macro": "차트 클릭"},
            }]})
            image = source.assets_dir / "marker.png"
            image.write_bytes(b"sample-image")
            source._write_json(source.assets_index_path, {"marker": {"file": "assets/marker.png"}})
            source.save_macro("차트 클릭", {"steps": [{"action": "image_search", "asset": "marker"}]})
            source_window = QuickSlotDeckWindow(source)
            backup = source_window.build_deck_backup_payload()
            self.assertIn("차트 클릭", backup["macros"])
            self.assertIn("marker", backup["assets"])

            target = MacroRepository(Path(target_dir))
            target_window = QuickSlotDeckWindow(target)
            target_window.restore_deck_backup_payload(backup)
            self.assertEqual("marker", target.load_macro("차트 클릭")["steps"][0]["asset"])
            self.assertEqual(b"sample-image", (target.assets_dir / "marker.png").read_bytes())
            self.assertEqual("차트 클릭", target.load_hotkeys()["slots"][0]["action"]["macro"])
            source_window.close(); target_window.close()

    def test_deck_dock_image_drop_applies_full_tile_icon(self) -> None:
        from PySide6 import QtCore, QtGui
        from macro_studio.deck_dock import DeckDockWindow, DeckSlotButton
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "dropped-icon.png"
            image = QtGui.QImage(32, 32, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#FF7700"))
            self.assertTrue(image.save(str(image_path)))

            mime = QtCore.QMimeData()
            mime.setUrls([QtCore.QUrl.fromLocalFile(str(image_path))])
            self.assertEqual(image_path.resolve(), Path(DeckSlotButton._first_local_image(mime)).resolve())

            repository = MacroRepository(root)
            repository.save_hotkeys({"slots": []})
            window = QuickSlotDeckWindow(repository)
            dock = DeckDockWindow(window)
            dock._apply_dropped_icon(0, str(image_path))

            config = window.custom_icons["0"]
            self.assertTrue(config.get("image_data"))
            self.assertTrue(config.get("full_stretch"))
            self.assertFalse(config.get("text_show"))
            self.assertEqual("manual", config.get("icon_source"))
            dock.close(); window.close()

    def test_deck_dock_uses_execution_label_centered_pager_and_large_grid(self) -> None:
        from macro_studio.deck_dock import ACTION_TITLES, DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            self.assertEqual("실행", ACTION_TITLES["open_target"])
            self.assertEqual("이름 변경", dock.rename_page_btn.text())
            self.assertEqual("＋ 페이지", dock.add_page_btn.text())
            self.assertEqual("－ 페이지", dock.delete_page_btn.text())
            self.assertEqual(10, dock.grid_rows_spin.maximum())
            self.assertEqual(10, dock.grid_cols_spin.maximum())
            dock.grid_rows_spin.setValue(10); dock.grid_cols_spin.setValue(10)
            dock._apply_grid_size()
            self.assertEqual((10, 10), (window.rows, window.cols))
            self.assertEqual(100, dock.grid.count())
            dock.close(); window.close()

    def test_deck_page_action_changes_runtime_page(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({
                "deck_page_count": 2,
                "deck_page_names": ["첫 페이지", "둘째 페이지"],
                "slots": [{"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(30)],
            })
            window = QuickSlotDeckWindow(repository)
            window._execute_deck_action({"kind": "page_next", "label": "다음"})
            self.assertEqual(1, window.current_page)
            window._execute_deck_action({"kind": "page_first", "label": "처음"})
            self.assertEqual(0, window.current_page)
            window.close()

    def test_parallel_multi_action_starts_each_selected_macro(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            action = {"kind": "multi_macros", "macros": ["A", "B"], "mode": "parallel", "continue_on_error": True}
            with mock.patch.object(window, "_start_registered_macro") as run_macro:
                window._execute_deck_action(action)
            self.assertEqual([mock.call("A"), mock.call("B")], run_macro.call_args_list)
            window.close()

    def test_every_deck_dock_palette_action_has_a_config_dialog(self) -> None:
        from macro_studio.deck_dock import ACTION_LIBRARY, DeckActionConfigDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            kinds = [kind for entries in ACTION_LIBRARY.values() for kind, _title, _description in entries]
            for kind in kinds:
                dialog = DeckActionConfigDialog(kind, None, window)
                result = dialog.result_action()
                self.assertEqual(kind, result["kind"])
                self.assertEqual("", result["label"])
                dialog.close()
            window.close()

    def test_deck_macro_action_can_select_a_named_entry(self) -> None:
        from macro_studio.deck_dock import DeckActionConfigDialog, action_title
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [{"action": "wait", "duration": 1, "entry_name": "A 로그인"},
                              {"action": "wait", "duration": 1, "entry_name": "B 로그인"}]
            repository.save_macro("네이버", macro)
            window = QuickSlotDeckWindow(repository)
            dialog = DeckActionConfigDialog("run_macro", {"macro": "네이버", "entry_name": "B 로그인"}, window)
            self.assertEqual("B 로그인", dialog.widgets["entry_name"].currentData())
            action = dialog.result_action()
            self.assertEqual("B 로그인", action_title(action))
            with mock.patch.object(window, "_start_registered_macro") as start:
                window._execute_deck_action(action)
            start.assert_called_once_with("네이버", entry_name="B 로그인")
            dialog.close()
            window.close()

    def test_deck_opens_studio_step_settings_and_saves_the_underlying_macro(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.deck_dock import DeckActionConfigDialog, DeckMacroStepsDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [
                {"action": "mouse_click", "label": "로그인", "entry_name": "A 로그인",
                 "window_exe": "whale.exe", "x": 10, "y": 20}
            ]
            repository.save_macro("네이버", macro)
            window = QuickSlotDeckWindow(repository)
            slot_dialog = DeckActionConfigDialog("run_macro", {"macro": "네이버", "entry_name": "A 로그인"}, window)
            button = next(button for button in slot_dialog.findChildren(QtWidgets.QPushButton)
                          if button.text() == "매크로 내부 단계 상세 편집…")
            self.assertTrue(button.isEnabled())
            steps_dialog = DeckMacroStepsDialog(repository, "네이버", slot_dialog)
            self.assertEqual("whale.exe", steps_dialog.steps_list.topLevelItem(0).text(2))
            steps_dialog.steps_list.topLevelItem(0).setSelected(True)

            changed = dict(macro["steps"][0], window_exe="msedge.exe")
            fake_editor = mock.Mock()
            fake_editor.exec.return_value = QtWidgets.QDialog.Accepted
            fake_editor.payload.return_value = changed
            with mock.patch("macro_studio.action_editor.ActionEditorDialog", return_value=fake_editor) as editor_type:
                steps_dialog._edit_selected_step()
            editor_type.assert_called_once()
            self.assertIs(steps_dialog, editor_type.call_args.args[2])
            self.assertEqual("msedge.exe", repository.load_macro("네이버")["steps"][0]["window_exe"])
            self.assertEqual("msedge.exe", steps_dialog.steps_list.topLevelItem(0).text(2))
            self.assertEqual("A 로그인", slot_dialog.widgets["entry_name"].currentData())
            steps_dialog.close(); slot_dialog.close(); window.close()

    def test_deck_step_settings_cancel_keeps_macro_unchanged(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.deck_dock import DeckMacroStepsDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [{"action": "wait", "duration": 100}]
            repository.save_macro("네이버", macro)
            dialog = DeckMacroStepsDialog(repository, "네이버")
            dialog.steps_list.topLevelItem(0).setSelected(True)
            fake_editor = mock.Mock()
            fake_editor.exec.return_value = QtWidgets.QDialog.Rejected
            with mock.patch("macro_studio.action_editor.ActionEditorDialog", return_value=fake_editor):
                dialog._edit_selected_step()
            fake_editor.payload.assert_not_called()
            self.assertEqual(100, repository.load_macro("네이버")["steps"][0]["duration"])
            dialog.close()

    def test_studio_action_editor_can_load_a_deck_macro_step(self) -> None:
        from macro_studio.action_editor import ActionEditorDialog
        from macro_studio.deck_dock import DeckMacroStepsDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [{"action": "mouse_click", "window_exe": "whale.exe", "x": 10, "y": 20}]
            repository.save_macro("네이버", macro)
            parent = DeckMacroStepsDialog(repository, "네이버")
            editor = ActionEditorDialog(repository, macro["steps"][0], parent)
            self.assertEqual("whale.exe", editor.payload()["window_exe"])
            editor.close(); parent.close()

    def test_deck_macro_preview_shows_saved_flow_and_readable_unselected_rows(self) -> None:
        from PySide6 import QtCore, QtGui
        from macro_studio.deck_dock import DeckMacroMiniCanvas, DeckMacroStepsDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [
                {"action": "image_search", "label": "로그인 화면 찾기", "on_success": 2, "on_fail": 3},
                {"action": "mouse_click", "label": "로그인 클릭", "window_exe": "msedge.exe"},
                {"action": "wait", "label": "실패 대기"},
            ]
            macro["graph_positions"] = {"1": [0, 0], "2": [320, 0], "3": [320, 240]}
            repository.save_macro("네이버", macro)
            dialog = DeckMacroStepsDialog(repository, "네이버")
            dialog.show()
            self.app.processEvents()
            self.assertEqual([(2, "success"), (3, "fail")],
                             DeckMacroMiniCanvas._targets(macro["steps"][0], 1, 3))
            self.assertEqual(3, len(dialog.mini_canvas.nodes))
            self.assertGreater(dialog.mini_canvas.nodes[3].pos().y(), dialog.mini_canvas.nodes[2].pos().y())
            dialog.mini_canvas.node_selected.emit(3)
            self.assertEqual(2, dialog.steps_list.currentItem().data(0, QtCore.Qt.UserRole))
            self.assertFalse(dialog.steps_list.alternatingRowColors())
            self.assertEqual(QtGui.QColor("#F4F6FA"), dialog.steps_list.topLevelItem(1).foreground(1).color())
            row = dialog.steps_list.visualItemRect(dialog.steps_list.topLevelItem(1))
            pixel = dialog.steps_list.viewport().grab().toImage().pixelColor(
                dialog.steps_list.viewport().width() - 10, row.center().y())
            self.assertLess(pixel.lightness(), 110)
            dialog.close()

    def test_deck_multi_click_selection_bulk_changes_only_targeted_steps(self) -> None:
        from PySide6 import QtCore, QtTest, QtWidgets
        from macro_studio.deck_dock import DeckMacroStepsDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [
                {"action": "image_search", "region_window_exe": "whale.exe", "region_window": "Old",
                 "click": {"window_exe": "whale.exe", "window": "Old"}, "asset": "logo"},
                {"action": "inactive_click", "window_exe": "whale.exe", "window": "Old", "x": 12},
                {"action": "wait", "duration": 100},
            ]
            repository.save_macro("네이버", macro)
            dialog = DeckMacroStepsDialog(repository, "네이버")
            dialog.show()
            self.app.processEvents()
            self.assertEqual(QtWidgets.QAbstractItemView.MultiSelection, dialog.steps_list.selectionMode())
            for row in (0, 1):
                item = dialog.steps_list.topLevelItem(row)
                point = dialog.steps_list.visualItemRect(item).center()
                QtTest.QTest.mouseClick(dialog.steps_list.viewport(), QtCore.Qt.LeftButton, pos=point)
            self.assertEqual([0, 1], dialog._selected_indexes())
            fake_picker = mock.Mock()
            fake_picker.exec.return_value = QtWidgets.QDialog.Accepted
            fake_picker.selected_executable.return_value = "msedge.exe"
            with mock.patch("macro_studio.deck_dock.DeckTargetProgramDialog", return_value=fake_picker):
                dialog._change_selected_targets()
            steps = repository.load_macro("네이버")["steps"]
            self.assertEqual("msedge.exe", steps[0]["region_window_exe"])
            self.assertEqual("msedge.exe", steps[0]["click"]["window_exe"])
            self.assertNotIn("region_window", steps[0])
            self.assertNotIn("window", steps[0]["click"])
            self.assertEqual("logo", steps[0]["asset"])
            self.assertEqual("msedge.exe", steps[1]["window_exe"])
            self.assertEqual(12, steps[1]["x"])
            self.assertEqual(macro["steps"][2], steps[2])
            dialog.close()

    def test_deck_click_step_can_repick_client_position_directly(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.deck_dock import DeckMacroStepsDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [{"action": "inactive_click", "window_exe": "whale.exe", "window": "Old",
                               "x": 1, "y": 2}]
            repository.save_macro("네이버", macro)
            dialog = DeckMacroStepsDialog(repository, "네이버")
            dialog.steps_list.topLevelItem(0).setSelected(True)
            picker = mock.Mock()
            picker.exec.return_value = QtWidgets.QDialog.Accepted
            picker.selected_client_point.return_value = QtCore.QPoint(25, 35)
            picker.exe_name = "msedge.exe"
            picker.window_token = "ahk_id 0x1234"
            with mock.patch("macro_studio.action_editor.WindowPickerDialog", return_value=picker):
                dialog._repick_selected_click()
            step = repository.load_macro("네이버")["steps"][0]
            self.assertEqual((25, 35), (step["x"], step["y"]))
            self.assertEqual("msedge.exe", step["window_exe"])
            self.assertEqual("ahk_id 0x1234", step["window"])
            self.assertEqual("client", step["coordinate_scope"])
            dialog.close()

    def test_deck_visual_image_edit_saves_preview_result(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio import region_visual_test
        from macro_studio.deck_dock import DeckMacroStepsDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            macro = repository.load_macro("네이버")
            macro["steps"] = [{"action": "image_search", "asset": "old", "assets": ["old"],
                               "region_window_exe": "whale.exe", "on_success": 2}]
            repository.save_macro("네이버", macro)
            dialog = DeckMacroStepsDialog(repository, "네이버")
            dialog.steps_list.topLevelItem(0).setSelected(True)
            visual = mock.Mock()
            visual.exec.return_value = QtWidgets.QDialog.Accepted
            visual.step = macro["steps"][0]
            visual.get_aliases.return_value = ["new"]
            visual.get_asset_regions.return_value = {"new": [1, 2, 30, 40]}
            visual.get_asset_offsets.return_value = {"new": [3, 4]}
            visual.get_click_target.return_value = "first_image"
            visual.get_match_condition.return_value = "all_matched"
            visual.get_required_count.return_value = 1
            visual.get_bounding_region.return_value = [1, 2, 30, 40]
            with mock.patch.object(region_visual_test, "RegionVisualTestDialog", return_value=visual):
                dialog._edit_selected_image_visually()
            step = repository.load_macro("네이버")["steps"][0]
            self.assertEqual("new", step["asset"])
            self.assertEqual(["new"], step["assets"])
            self.assertEqual("whale.exe", step["region_window_exe"])
            self.assertEqual(2, step["on_success"])
            dialog.close()

    def test_deck_can_replace_one_search_image_without_changing_other_steps(self) -> None:
        from PySide6 import QtGui
        from macro_studio.deck_dock import DeckMacroStepsDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("네이버")
            replacement = Path(directory) / "replacement.png"
            image = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#33AA77"))
            self.assertTrue(image.save(str(replacement)))
            macro = repository.load_macro("네이버")
            macro["steps"] = [
                {"action": "image_search", "asset": "old", "assets": ["old"],
                 "asset_regions": {"old": [1, 2, 30, 40]}, "on_success": 2},
                {"action": "image_search", "asset": "old", "assets": ["old"]},
            ]
            repository.save_macro("네이버", macro)
            dialog = DeckMacroStepsDialog(repository, "네이버")
            dialog.steps_list.topLevelItem(0).setSelected(True)
            self.assertTrue(dialog.replace_image_button.isEnabled())
            with mock.patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(replacement), "")):
                dialog._replace_selected_image()
            steps = repository.load_macro("네이버")["steps"]
            alias = steps[0]["asset"]
            self.assertNotEqual("old", alias)
            self.assertEqual([alias], steps[0]["assets"])
            self.assertEqual({alias: [1, 2, 30, 40]}, steps[0]["asset_regions"])
            self.assertEqual("old", steps[1]["asset"])
            self.assertTrue(repository.asset_path(alias).is_file())
            dialog.close()

    def test_deck_input_actions_offer_target_mode_icon_and_test_controls(self) -> None:
        from macro_studio.deck_dock import DeckActionConfigDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            for kind in ("text", "hotkey", "mouse_click"):
                dialog = DeckActionConfigDialog(kind, None, window, slot_index=2)
                self.assertEqual("inactive", dialog.widgets["input_mode"].currentData())
                self.assertIn("target_window", dialog.widgets)
                self.assertIn("target_exe", dialog.widgets)
                self.assertTrue(dialog.btn_edit_icon.text())
                self.assertTrue(dialog.btn_test.text())
                if kind == "text":
                    self.assertEqual(5, dialog.widgets["text_method"].count())
                    self.assertEqual("auto", dialog.widgets["text_method"].currentData())
                if kind == "mouse_click":
                    self.assertIn("x", dialog.widgets)
                    self.assertIn("y", dialog.widgets)
                dialog.close()
            window.close()

    def test_target_picker_is_child_of_modal_action_dialog(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.deck_dock import DeckActionConfigDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        class FakePicker:
            window_hwnd = 0
            window_token = "ahk_id 0x1234"
            exe_name = "sample.exe"

            def exec(self) -> int:
                return QtWidgets.QDialog.Accepted

            def selected_client_point(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = DeckActionConfigDialog("text", None, window, slot_index=0)
            with mock.patch("macro_studio.action_editor.WindowPickerDialog", return_value=FakePicker()) as picker_type:
                dialog._pick_target(False)
            self.assertIs(dialog, picker_type.call_args.args[0])
            self.assertEqual("sample.exe", dialog.widgets["target_exe"].text())
            self.assertEqual("ahk_id 0x1234", dialog.widgets["target_window"].text())
            dialog.close(); window.close()

    def test_program_action_automatically_uses_native_executable_icon(self) -> None:
        from macro_studio.deck_dock import DeckActionConfigDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository
        from PySide6 import QtGui

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = DeckActionConfigDialog("open_target", None, window, slot_index=0)
            dialog.widgets["target"].setText(sys.executable)
            icon_config = dialog.result_icon_config()
            self.assertTrue(icon_config.get("image_data"))
            self.assertTrue(icon_config.get("full_stretch"))
            self.assertFalse(icon_config.get("text_show"))
            self.assertEqual("program_auto", icon_config.get("icon_source"))
            if os.name == "nt":
                image = QtGui.QImage.fromData(base64.b64decode(icon_config["image_data"]))
                self.assertEqual((256, 256), (image.width(), image.height()))
                self.assertEqual("native", icon_config.get("icon_quality"))
            dialog.close(); window.close()

    @unittest.skipUnless(os.name == "nt", "Windows shortcut icon extraction")
    def test_shortcut_icon_uses_target_executable_without_shortcut_thumbnail(self) -> None:
        from PySide6 import QtGui
        from macro_studio.quickslot_deck import extract_program_icon_config

        with tempfile.TemporaryDirectory() as directory:
            shortcut = Path(directory) / "program.lnk"
            shortcut.write_bytes(b"shortcut test")
            resolved = subprocess.CompletedProcess([], 0, f"{sys.executable},0\n{sys.executable}\n", "")
            with mock.patch("macro_studio.quickslot_deck.subprocess.run", return_value=resolved):
                icon = extract_program_icon_config(str(shortcut))
            image = QtGui.QImage.fromData(base64.b64decode(icon["image_data"]))
            self.assertEqual((256, 256), (image.width(), image.height()))
            self.assertEqual("native", icon["icon_quality"])

    @unittest.skipUnless(os.name == "nt", "Windows executable icon extraction")
    def test_existing_manual_executable_icon_upgrades_without_changing_layout(self) -> None:
        from macro_studio.quickslot_deck import upgrade_manual_executable_icon

        previous = {"icon_source": "manual", "image_path": sys.executable,
                    "image_data": "AA==", "full_stretch": False, "text_show": True}
        upgraded = upgrade_manual_executable_icon(previous)
        self.assertNotEqual("AA==", upgraded["image_data"])
        self.assertEqual(2, upgraded["icon_extractor_version"])
        self.assertFalse(upgraded["full_stretch"])
        self.assertTrue(upgraded["text_show"])

    def test_macro_action_uses_program_icon_from_selected_entry(self) -> None:
        from macro_studio.deck_dock import DeckActionConfigDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow, macro_program_icon_target
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_macro("실행 테스트", {"steps": [
                {"action": "wait", "duration": 100},
                {"action": "run_program", "command": sys.executable, "entry_name": "프로그램 시작"},
            ]})
            self.assertEqual(sys.executable, macro_program_icon_target(repository, "실행 테스트", "프로그램 시작"))
            window = QuickSlotDeckWindow(repository)
            dialog = DeckActionConfigDialog(
                "run_macro", {"kind": "run_macro", "macro": "실행 테스트", "entry_name": "프로그램 시작"},
                window, slot_index=0,
            )
            icon = dialog.result_icon_config()
            self.assertTrue(icon.get("image_data"))
            self.assertEqual("program_auto", icon.get("icon_source"))
            dialog.close()
            legacy = DeckActionConfigDialog(
                "run_macro", {"kind": "run_macro", "macro": "실행 테스트", "entry_name": "프로그램 시작"},
                window, slot_index=0, icon_config={"emoji": "▶", "icon_size": 58},
            )
            self.assertTrue(legacy.result_icon_config().get("image_data"))
            legacy.close(); window.close()

    def test_program_action_repairs_layout_only_icon_config(self) -> None:
        from macro_studio.deck_dock import DeckActionConfigDialog
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dialog = DeckActionConfigDialog(
                "open_target", None, window, slot_index=0,
                icon_config={"full_stretch": True, "text_show": True, "emoji": ""},
            )
            dialog.widgets["target"].setText(sys.executable)
            icon_config = dialog.result_icon_config()
            self.assertTrue(icon_config.get("image_data"))
            self.assertEqual("program_auto", icon_config.get("icon_source"))
            dialog.close(); window.close()

    def test_quickslot_refresh_repairs_missing_program_icon(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{
                "macro": "실행", "hotkey": "", "mode": "deck_action",
                "action": {"kind": "open_target", "label": "", "target": sys.executable},
            }]})
            window = QuickSlotDeckWindow(repository)
            window.custom_icons["0"] = {"full_stretch": True, "text_show": True, "emoji": ""}
            window.refresh_slots()
            self.assertTrue(window.custom_icons["0"].get("image_data"))
            self.assertEqual("program_auto", window.custom_icons["0"].get("icon_source"))
            self.assertTrue(window.buttons[0].custom_icon_config.get("full_stretch"))
            window.close()

    def test_deck_inactive_text_and_mouse_dispatch_without_activation(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            with mock.patch.object(window, "_resolve_action_window", return_value=321) as resolve, mock.patch.object(window, "_send_inactive_text") as send_text:
                action = {"kind": "text", "label": "입력", "text": "hello", "input_mode": "inactive", "target_exe": "notepad.exe"}
                window._execute_deck_action(action)
                resolve.assert_called_once_with(action)
                send_text.assert_called_once_with(321, "hello", "wm_char", 0)
            with mock.patch.object(window, "_click_action_target") as click:
                action = {"kind": "mouse_click", "label": "클릭", "x": 40, "y": 50, "input_mode": "inactive"}
                window._execute_deck_action(action)
                click.assert_called_once_with(action, inactive=True)
            window.close()

    def test_action_window_title_change_falls_back_to_same_program(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow

        candidates = [
            (101, "TradingView - Whale".casefold(), "whale.exe"),
            (202, "RUSD 1.4552 forex - Whale".casefold(), "whale.exe"),
            (303, "메모장", "notepad.exe"),
        ]

        exact = QuickSlotDeckWindow._select_action_window_candidate(
            candidates,
            title_fragment="tradingview - whale",
            exe_name="whale.exe",
            foreground_hwnd=202,
        )
        changed_title = QuickSlotDeckWindow._select_action_window_candidate(
            candidates,
            title_fragment="이전 종목 - whale",
            exe_name="whale.exe",
            foreground_hwnd=202,
        )

        self.assertEqual(101, exact)
        self.assertEqual(202, changed_title)

    def test_deck_save_button_saves_once_and_closes(self) -> None:
        from macro_studio.deck_dock import DeckDockWindow
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            dock = DeckDockWindow(window)
            with mock.patch.object(dock, "save", return_value=True) as save, mock.patch.object(dock, "close") as close:
                dock._save_and_close()
                save.assert_called_once_with()
                close.assert_called_once_with()
            dock.close(); window.close()

    def test_text_action_supports_clipboard_typing_and_enter(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            inactive = {
                "kind": "text", "label": "붙여넣기", "text": "hello", "input_mode": "inactive",
                "text_method": "clipboard", "key_interval_ms": 12, "press_enter": True,
            }
            with mock.patch.object(window, "_resolve_action_window", return_value=44), mock.patch.object(window, "_send_inactive_text") as send_text, mock.patch.object(window, "_send_inactive_hotkey") as send_key:
                window._execute_deck_action(inactive)
                send_text.assert_called_once_with(44, "hello", "clipboard", 12)
                send_key.assert_called_once_with(44, "Enter")

            active = {
                "kind": "text", "label": "타이핑", "text": "가나다", "input_mode": "active",
                "text_method": "type", "key_interval_ms": 25,
            }
            with mock.patch.object(window, "_activate_window_by_title"), mock.patch.object(window, "_send_active_unicode_text") as type_text:
                window._execute_deck_action(active)
                type_text.assert_called_once_with("가나다", 25)
            window.close()

    def test_windows_unicode_input_uses_native_input_structure_size(self) -> None:
        import ctypes
        from macro_studio.quickslot_deck import _INPUT, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(expected_size, ctypes.sizeof(_INPUT))

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            sent_sizes = []
            fake_user32 = mock.Mock()
            fake_user32.SendInput.side_effect = lambda count, _events, size: sent_sizes.append(size) or count
            with mock.patch.object(ctypes.windll, "user32", fake_user32):
                window._send_active_unicode_text("가A", 0)
            self.assertEqual([expected_size, expected_size], sent_sizes)
            window.close()

    def test_system_deck_actions_dispatch_to_runtime_services(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            with mock.patch("macro_studio.quickslot_deck.webbrowser.open") as open_url:
                window._execute_deck_action({"kind": "open_target", "target": "https://example.com", "label": "웹"})
                open_url.assert_called_once_with("https://example.com")
            with mock.patch.object(window, "_activate_window_by_title") as activate, mock.patch.object(window, "_send_windows_hotkey") as send_keys:
                window._execute_deck_action({"kind": "hotkey", "keys": "Ctrl+K", "target_title": "메모장", "label": "키"})
                activate.assert_called_once_with("메모장")
                send_keys.assert_called_once_with("Ctrl+K")
            with mock.patch.object(window, "_open_studio") as open_studio:
                window._execute_deck_action({"kind": "studio", "label": "Studio"})
                open_studio.assert_called_once()
            window.close()

    def test_whale_deck_launch_opens_tab_in_existing_window(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow, is_plain_whale_launch
        from macro_studio.repository import MacroRepository

        self.assertTrue(is_plain_whale_launch(r'C:\Program Files\Naver\Whale\whale.exe'))
        self.assertFalse(is_plain_whale_launch('whale.exe --new-window'))
        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            action = {"kind": "open_target", "target": r'C:\Program Files\Naver\Whale\whale.exe'}
            try:
                with mock.patch.object(window, "_open_whale_tab_if_running", return_value=True) as open_tab, \
                     mock.patch("macro_studio.quickslot_deck.os.startfile") as startfile:
                    window._execute_deck_action(action)
                    open_tab.assert_called_once_with()
                    startfile.assert_not_called()
                with mock.patch.object(window, "_open_whale_tab_if_running", return_value=False), \
                     mock.patch("macro_studio.quickslot_deck.subprocess.Popen") as launch:
                    window._execute_deck_action(action)
                    launch.assert_called_once_with([action["target"], "--silent-debugger-extension-api"])
                quoted = {**action, "target": f'"{action["target"]}"'}
                with mock.patch.object(window, "_open_whale_tab_if_running", return_value=False), \
                     mock.patch("macro_studio.quickslot_deck.subprocess.Popen") as launch:
                    window._execute_deck_action(quoted)
                    launch.assert_called_once_with([action["target"], "--silent-debugger-extension-api"])
            finally:
                window.close()

    def test_whale_tab_is_sent_only_after_its_window_is_foreground(self) -> None:
        import ctypes
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            user32 = mock.Mock()
            user32.IsIconic.return_value = False
            try:
                with mock.patch.object(window, "_resolve_action_window", return_value=42) as resolve, \
                     mock.patch.object(window, "_send_windows_hotkey") as send, \
                     mock.patch.object(ctypes.windll, "user32", user32):
                    user32.GetForegroundWindow.return_value = 42
                    self.assertTrue(window._open_whale_tab_if_running())
                    resolve.assert_called_once_with({
                        "target_exe": "whale.exe", "target_class": "Chrome_WidgetWin_1",
                    })
                    send.assert_called_once_with("Ctrl+T")
                    user32.GetForegroundWindow.return_value = 7
                    with self.assertRaisesRegex(RuntimeError, "활성화하지 못했습니다"):
                        window._open_whale_tab_if_running()
                    send.assert_called_once()
            finally:
                window.close()

    def test_right_click_and_hold_radials_have_separate_content(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({
                "slots": [
                    {"macro": "첫 번째", "hotkey": "", "mode": "hybrid"},
                    {"macro": "두 번째", "hotkey": "", "mode": "hybrid"},
                ]
            })
            window = QuickSlotDeckWindow(repository)
            browser = window._build_slot_preset("브라우저 모드")
            browser["slots"] = [{"macro": "두 번째", "hotkey": "", "mode": "hybrid"}]
            browser["custom_icons"] = {"0": {"emoji": "B"}}
            browser["style"]["theme_index"] = 2
            browser["style"]["show_empty_slots"] = False
            window.config["slot_presets"]["browser"] = browser
            window._refresh_preset_radial_menu()

            self.assertIn("preset_settings", [item.key for item in window.radial_menu.items])
            self.assertEqual("close_app", window.radial_menu.items[-1].key)
            preset_items = {item.key: item for item in window.preset_radial_menu.items}
            self.assertIn("deck:default", preset_items)
            self.assertIn("deck:browser", preset_items)
            self.assertEqual("브라우저 모드", preset_items["deck:browser"].title)

            window._handle_preset_radial_action("deck:browser")
            self.assertEqual("browser", window.config["active_slot_preset"])
            self.assertEqual(["홈", "두 번째"], [button.macro_name for button in window.buttons])
            self.assertEqual("B", window.custom_icons["1"]["emoji"])
            self.assertEqual(2, window.config["theme_index"])

            window._handle_preset_radial_action("deck:default")
            self.assertEqual("default", window.config["active_slot_preset"])
            self.assertEqual(["첫 번째", "두 번째"], [button.macro_name for button in window.buttons[:2]])
            self.assertEqual(0, window.config["theme_index"])
            window.close()

    def test_theme_live_change_reuses_existing_slot_widgets(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckSettingsDialog, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "테마 테스트", "hotkey": "", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            original_button = window.buttons[0]
            dialog = QuickSlotDeckSettingsDialog(window)
            dialog._on_theme_live_changed(2)

            self.assertEqual(2, window.config["theme_index"])
            self.assertIs(original_button, window.buttons[0])
            dialog.close()
            window.close()

    def test_icon_only_themes_hide_tile_chrome_and_apply_compact_size(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckSettingsDialog, QuickSlotDeckWindow, THEMES
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "아이콘 슬롯", "hotkey": "", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            window.show(); self.app.processEvents()
            dialog = QuickSlotDeckSettingsDialog(window)
            self.assertTrue(THEMES[4]["icon_only"])
            self.assertTrue(THEMES[5]["icon_only"])
            self.assertEqual(6, dialog.theme_combo.count())
            dialog._on_theme_live_changed(5)
            button = window.buttons[0]
            self.assertTrue(button.slot_number_label.isHidden())
            self.assertTrue(button.opt_btn.isHidden())
            self.assertTrue(button.title_label.isHidden())
            self.assertLessEqual(float(window.config["tile_scale"]), 0.5)
            dialog.close(); window.close()

    def test_empty_slot_toggle_renders_full_configured_grid(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckSettingsDialog, QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "하나", "hotkey": "", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            window.rows, window.cols = 2, 3
            window.config["empty_slot_opacity"] = 100
            dialog = QuickSlotDeckSettingsDialog(window)
            dialog._on_theme_live_changed(5)
            dialog.show_empty_slots_check.setChecked(True)
            self.app.processEvents()
            self.assertTrue(window.config["show_empty_slots"])
            self.assertEqual(6, len(window.buttons))
            empty_button = window.buttons[1]
            self.assertEqual("", empty_button.icon_label.text())
            self.assertTrue(empty_button.icon_label.isHidden())
            self.assertIn(" solid ", empty_button.styleSheet())
            self.assertNotIn("dashed", empty_button.styleSheet())
            rendered = empty_button.grab().toImage()
            border_pixel = rendered.pixelColor(1, rendered.height() // 2)
            center_pixel = rendered.pixelColor(rendered.width() // 2, rendered.height() // 2)
            self.assertNotEqual(border_pixel.rgb(), center_pixel.rgb())
            dialog.show_empty_slots_check.setChecked(False)
            self.app.processEvents()
            self.assertEqual(1, len(window.buttons))
            dialog.close(); window.close()

    def test_icon_editor_changes_are_previewed_on_real_slot(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow, SlotIconEditDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({"slots": [{"macro": "미리보기", "hotkey": "", "mode": "hybrid"}]})
            window = QuickSlotDeckWindow(repository)
            dialog = SlotIconEditDialog(0, "미리보기", {}, window)
            dialog.live_config_changed.connect(window._preview_slot_icon)
            dialog.emoji_edit.setText("★")
            self.app.processEvents()

            self.assertEqual("★", window.buttons[0].custom_icon_config["emoji"])
            dialog.close()
            window.close()

    def test_plain_left_drag_moves_window_and_cancels_hold(self) -> None:
        from PySide6 import QtCore, QtGui
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            window = QuickSlotDeckWindow(MacroRepository(Path(directory)))
            window.move(100, 100)
            press = QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonPress,
                QtCore.QPointF(20, 20),
                QtCore.QPointF(120, 120),
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
            )
            window._start_window_drag_candidate(press)
            window._start_mouse_hold_check(QtCore.QPoint(120, 120))
            self.assertTrue(window._hold_timer.isActive())

            move = QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove,
                QtCore.QPointF(45, 35),
                QtCore.QPointF(145, 135),
                QtCore.Qt.NoButton,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
            )
            self.assertTrue(window._handle_window_drag_move(move))
            self.assertEqual(QtCore.QPoint(125, 115), window.pos())
            self.assertFalse(window._hold_timer.isActive())

            release = QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonRelease,
                QtCore.QPointF(45, 35),
                QtCore.QPointF(145, 135),
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,
            )
            self.assertTrue(window._handle_window_drag_release(release))
            window.close()

    def test_slot_options_menu_contains_remove_action(self) -> None:
        from PySide6 import QtTest
        from macro_studio.quickslot_deck import StreamDeckButton

        button = StreamDeckButton(3)
        button.set_slot_data("테스트", "", "hybrid", False)
        remove_spy = QtTest.QSignalSpy(button.remove_slot_requested)
        menu = button._build_options_menu()
        actions = {action.text(): action for action in menu.actions() if action.text()}

        self.assertIn("🖼️ 아이콘 불러오기 / 편집", actions)
        self.assertIn("🗑 슬롯 제거", actions)
        actions["🗑 슬롯 제거"].trigger()
        self.assertEqual(1, remove_spy.count())

    def test_remove_slot_persists_and_shrinks_grid(self) -> None:
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkeys({
                "slots": [
                    {"macro": "첫 번째", "hotkey": "Alt+1", "mode": "hybrid"},
                    {"macro": "두 번째", "hotkey": "Alt+2", "mode": "hybrid"},
                ]
            })
            _use_legacy_compact_default(repository)
            window = QuickSlotDeckWindow(repository)
            window.custom_icons["1"] = {"emoji": "★"}
            window._remove_slot(1)

            self.assertEqual("", repository.load_hotkeys()["slots"][1]["macro"])
            self.assertNotIn("1", window.custom_icons)
            self.assertEqual(["첫 번째"], [button.macro_name for button in window.buttons])
            window.close()

    def test_bundled_icon_gallery_selects_and_embeds_image(self) -> None:
        from PySide6 import QtGui
        from macro_studio.quickslot_deck import SlotIconEditDialog, quickslot_preset_icon_paths

        project_root = Path(__file__).resolve().parents[1]
        presets = quickslot_preset_icon_paths(project_root)
        self.assertEqual(16, len(presets))
        self.assertTrue(all(not QtGui.QPixmap(str(path)).isNull() for path in presets))

        dialog = SlotIconEditDialog(0, "테스트")
        dialog._select_preset_icon(presets[0])
        config = dialog.get_config()
        self.assertEqual(str(presets[0].resolve()), config["image_path"])
        self.assertTrue(config["image_data"].startswith("data:image/png;base64,"))
        self.assertTrue(config["full_stretch"])
        dialog.close()

    def test_icon_editor_pastes_clipboard_bitmap_and_preserves_it_without_path(self) -> None:
        from PySide6 import QtCore, QtGui
        from macro_studio.quickslot_deck import SlotIconEditDialog, load_pixmap_from_config

        image = QtGui.QImage(24, 24, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("#36BDF8"))
        mime = QtCore.QMimeData()
        mime.setImageData(image)
        clipboard = mock.Mock()
        clipboard.mimeData.return_value = mime
        clipboard.image.return_value = image

        dialog = SlotIconEditDialog(0, "테스트")
        dialog._paste_image_from_clipboard(clipboard)
        config = dialog.get_config()
        self.assertEqual("", config["image_path"])
        self.assertTrue(config["image_data"].startswith("data:image/png;base64,"))
        self.assertTrue(config["full_stretch"])
        self.assertFalse(load_pixmap_from_config(config).isNull())
        dialog.close()

        reopened = SlotIconEditDialog(0, "테스트", config)
        self.assertEqual(config["image_data"], reopened.get_config()["image_data"])
        reopened.close()

    def test_icon_editor_pastes_copied_image_file(self) -> None:
        from PySide6 import QtCore, QtGui
        from macro_studio.quickslot_deck import SlotIconEditDialog

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "copied.png"
            image = QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#8B5CF6"))
            self.assertTrue(image.save(str(path)))
            mime = QtCore.QMimeData()
            mime.setUrls([QtCore.QUrl.fromLocalFile(str(path))])
            clipboard = mock.Mock()
            clipboard.mimeData.return_value = mime

            dialog = SlotIconEditDialog(0, "테스트")
            dialog._paste_image_from_clipboard(clipboard)
            config = dialog.get_config()
            self.assertEqual(path, Path(config["image_path"]))
            self.assertTrue(config["image_data"].startswith("data:image/png;base64,"))
            dialog.close()

    def test_multiple_user_icons_are_saved_and_reloaded(self) -> None:
        from PySide6 import QtGui
        from macro_studio.quickslot_deck import quickslot_user_icon_paths, save_quickslot_user_icons

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "sources"
            source_dir.mkdir()
            first = source_dir / "first.png"
            second = source_dir / "second.png"
            first_pixmap = QtGui.QPixmap(24, 24)
            first_pixmap.fill(QtGui.QColor("#38BDF8"))
            self.assertTrue(first_pixmap.save(str(first)))
            second_pixmap = QtGui.QPixmap(24, 24)
            second_pixmap.fill(QtGui.QColor("#A855F7"))
            self.assertTrue(second_pixmap.save(str(second)))

            saved = save_quickslot_user_icons(root, [str(first), str(second)])
            self.assertEqual(2, len(saved))
            self.assertEqual(saved, quickslot_user_icon_paths(root))
            self.assertTrue(all(path.parent.name == "quickslot-user-icons" for path in saved))

    def test_full_stretch_icon_has_visible_rounded_default_frame(self) -> None:
        from PySide6 import QtGui, QtWidgets
        from macro_studio.quickslot_deck import StreamDeckButton, encode_image_file_to_base64

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat-icon.png"
            image = QtGui.QImage(32, 32, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#00A050"))
            self.assertTrue(image.save(str(path)))

            for theme_index in (0, 5):
                with self.subTest(theme_index=theme_index):
                    parent = QtWidgets.QWidget()
                    parent.config = {"theme_index": theme_index, "tile_radius": 6}
                    parent.setFixedSize(90, 90)
                    button = StreamDeckButton(0, parent)
                    button.setFixedSize(90, 90)
                    button.set_custom_icon_config({
                        "image_data": encode_image_file_to_base64(str(path)),
                        "full_stretch": True,
                        "text_show": False,
                    })
                    button.set_slot_data("테스트")
                    parent.show()
                    self.app.processEvents()
                    rendered = button.grab().toImage()
                    center = rendered.pixelColor(45, 45)
                    border = rendered.pixelColor(1, 45)
                    corner = rendered.pixelColor(0, 0)

                    self.assertEqual(QtGui.QColor("#00A050").rgb(), center.rgb())
                    self.assertGreater(border.red(), center.red() + 100)
                    self.assertNotEqual(center.rgb(), corner.rgb())
                    parent.close()

    def test_full_stretch_resizes_to_card_without_cropping_edges(self) -> None:
        from PySide6 import QtCore, QtGui
        from macro_studio.quickslot_deck import StreamDeckButton

        image = QtGui.QImage(20, 60, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("#22C55E"))
        for y in range(10):
            for x in range(image.width()):
                image.setPixelColor(x, y, QtGui.QColor("#EF4444"))
                image.setPixelColor(x, image.height() - 1 - y, QtGui.QColor("#3B82F6"))
        pixmap = QtGui.QPixmap.fromImage(image)

        scaled = StreamDeckButton._scale_full_stretch_pixmap(pixmap, QtCore.QSize(180, 40))
        self.assertEqual(QtCore.QSize(180, 40), scaled.size())
        scaled_image = scaled.toImage()
        self.assertEqual(QtGui.QColor("#EF4444"), scaled_image.pixelColor(90, 0))
        self.assertEqual(QtGui.QColor("#3B82F6"), scaled_image.pixelColor(90, 39))

    def test_full_stretch_zoom_and_position_select_image_detail(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.quickslot_deck import StreamDeckButton

        image = QtGui.QImage(20, 20, QtGui.QImage.Format_ARGB32)
        for x in range(20):
            for y in range(20):
                image.setPixelColor(x, y, QtGui.QColor("#EF4444" if x < 10 else "#3B82F6"))
        data = QtCore.QByteArray()
        buffer = QtCore.QBuffer(data)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        buffer.close()
        parent = QtWidgets.QWidget()
        parent.config = {"theme_index": 0}
        button = StreamDeckButton(0, parent)
        button.setFixedSize(100, 100)
        parent.show()
        for position, expected in ((0, "#EF4444"), (100, "#3B82F6")):
            button.set_custom_icon_config({"image_data": bytes(data.toBase64()).decode("ascii"),
                                           "full_stretch": True, "text_show": False,
                                           "image_zoom_percent": 200, "image_x_percent": position})
            button.set_slot_data("테스트")
            self.app.processEvents()
            self.assertEqual(QtGui.QColor(expected).rgb(), button.grab().toImage().pixelColor(50, 50).rgb())
        parent.close()


if __name__ == "__main__":
    unittest.main()
