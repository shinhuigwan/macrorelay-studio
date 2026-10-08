from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DeckPanelControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_window(self, root):
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository
        repository = MacroRepository(Path(root))
        (repository.root / ".quickslot_deck_config.json").write_text(json.dumps({
            "rows": 3, "cols": 5,
            "config": {"starter_presets_version": 1, "browser_shortcuts_version": 1,
                       "manual_tile_side": 68, "show_empty_slots": False},
        }), encoding="utf-8")
        return QuickSlotDeckWindow(repository)

    def test_panel_is_tool_window_and_stays_visible_when_topmost_changes(self):
        from PySide6 import QtCore
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            self.assertEqual(QtCore.Qt.Tool, window.windowType())
            self.assertFalse(window.isVisible())
            window._apply_always_on_top()
            self.assertFalse(window.isVisible())
            window.show()
            self.app.processEvents()
            for side in ("free", "left", "right", "top", "bottom"):
                window._set_edge_panel_mode(side)
                self.app.processEvents()
                geometry = window.geometry()
                for topmost in (False, True, False):
                    window.always_on_top = topmost
                    window._apply_always_on_top()
                    self.app.processEvents()
                    self.assertEqual(QtCore.Qt.Tool, window.windowType())
                    self.assertTrue(window.isVisible())
                    self.assertEqual(geometry, window.geometry())
                    self.assertEqual(topmost, bool(window.windowFlags() & QtCore.Qt.WindowStaysOnTopHint))
                    self.assertFalse(window.windowFlags() & QtCore.Qt.WindowDoesNotAcceptFocus)
            window.close()

    def test_layout_edit_icon_drag_swaps_and_moves_into_blank_cells_without_running(self):
        from PySide6 import QtCore, QtTest
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            slots = [{"macro": f"슬롯 {i}", "mode": "deck_action",
                      "action": {"kind": "text", "text": str(i)}} for i in range(3)]
            window._save_preset_hotkeys({"slots": slots})
            window.custom_icons = {str(i): {"icon_text": str(i)} for i in range(3)}
            original_icons = dict(window.custom_icons)
            window._set_edge_panel_mode("right")
            window.show()
            self.app.processEvents()
            with mock.patch.object(window, "_execute_deck_action") as execute:
                for edge in ("left", "right", "top", "bottom"):
                    window._set_edge_panel_mode(edge)
                    QtTest.QTest.mouseClick(window.layout_edit_button, QtCore.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertTrue(window._slot_layout_editing)
                    self.assertEqual("", window.layout_edit_button.text())
                    origin = window.pos()
                    source, target = window.buttons[0], window.buttons[2]
                    self.send_drag(source, target.mapToGlobal(target.rect().center()))
                    self.assertEqual(2, window._visual_slot_positions[0])
                    self.assertEqual(0, window._visual_slot_positions[2])
                    self.assertEqual(origin, window.pos())
                    blank = next(widget for widget in window._layout_placeholders
                                 if widget.property("deck_layout_position") == 3)
                    source = next(button for button in window.buttons if button.slot_index == 0)
                    self.send_drag(source, blank.mapToGlobal(blank.rect().center()))
                    self.assertEqual(3, window._visual_slot_positions[0])
                    QtTest.QTest.mouseClick(window.buttons[1], QtCore.Qt.LeftButton)
                    QtTest.QTest.mouseDClick(window.buttons[1], QtCore.Qt.LeftButton)
                    QtTest.QTest.mouseClick(window.layout_edit_button, QtCore.Qt.LeftButton)
                    self.app.processEvents()
                    self.assertFalse(window._slot_layout_editing)
                    self.assertFalse(window._layout_placeholders)
                    self.assertEqual(3, window._visual_slot_positions[0])
                    self.assertEqual(slots, window.repository.load_hotkeys()["slots"])
                    self.assertEqual(original_icons, window.custom_icons)
                    self.assertTrue(all(button.size() == QtCore.QSize(68, 68) for button in window.buttons))
                execute.assert_not_called()
            window.close()

    def test_layout_placement_survives_preset_capture_restart_and_backup_restore(self):
        from PySide6 import QtCore
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as target:
            window = self.make_window(root)
            window._save_preset_hotkeys({"slots": [{"macro": "슬롯"}]})
            window._set_slot_layout_editing(True)
            self.assertTrue(window._place_visual_slot(0, 8))
            self.app.processEvents()
            window._set_slot_layout_editing(False)
            self.assertEqual(8, window._visual_slot_positions[0])
            self.assertEqual((1, 3), window.grid_layout.getItemPosition(
                window.grid_layout.indexOf(window.buttons[0]))[:2])
            window._set_edge_panel_mode("left")
            window._set_slot_layout_editing(True)
            self.assertTrue(window._place_visual_slot(0, 1))
            self.app.processEvents()
            window._set_slot_layout_editing(False)
            self.assertEqual(1, window._visual_slot_positions[0])
            window._set_edge_panel_mode("right")
            self.assertEqual(0, window._visual_slot_positions[0])
            window._set_edge_panel_mode("left")
            self.assertEqual(1, window._visual_slot_positions[0])
            other = window._build_slot_preset("다른 프리셋")
            window.config["slot_presets"]["other"] = other
            window._set_slot_layout_editing(True)
            window._switch_slot_preset("other", automatic=True)
            self.assertEqual("default", window.config["active_slot_preset"])
            window._set_slot_layout_editing(False)
            window._switch_slot_preset("other", automatic=True)
            self.assertEqual(0, window._visual_slot_positions[0])
            window._switch_slot_preset("default", automatic=True)
            self.assertEqual(1, window._visual_slot_positions[0])
            payload = window.build_deck_backup_payload()
            window.close()
            reopened = QuickSlotDeckWindow(window.repository)
            self.assertFalse(reopened._slot_layout_editing)
            self.assertEqual(1, reopened._visual_slot_positions[0])
            reopened.close()
            restored = QuickSlotDeckWindow(MacroRepository(Path(target)))
            restored.restore_deck_backup_payload(payload)
            self.assertEqual(1, restored._visual_slot_positions[0])
            self.assertEqual(QtCore.QSize(68, 68), restored.buttons[0].size())
            restored.close()

    def test_layout_drop_outside_and_tap_do_not_reorder_and_header_button_is_visible(self):
        from PySide6 import QtCore, QtTest
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._save_preset_hotkeys({"slots": [{"macro": "슬롯"}]})
            window._set_edge_panel_mode("left")
            window.show()
            self.app.processEvents()
            self.assertTrue(window.centralWidget().rect().contains(window.layout_edit_button.geometry()))
            self.assertFalse(window.preset_badge.geometry().intersects(window.layout_edit_button.geometry()))
            window._set_slot_layout_editing(True)
            original_position = window.pos()
            self.send_drag(window.swipe_container,
                           window.swipe_container.mapToGlobal(window.swipe_container.rect().center()) + QtCore.QPoint(100, 0))
            self.assertEqual(original_position, window.pos())
            QtTest.QTest.mouseClick(window.buttons[0], QtCore.Qt.LeftButton)
            self.assertEqual({0: 0}, window._visual_slot_positions)
            self.send_drag(window.buttons[0], window.mapToGlobal(QtCore.QPoint(-300, -300)))
            self.assertEqual({0: 0}, window._visual_slot_positions)
            self.assertFalse(window.layout_drop_marker.isVisible())
            window._mark_slot_status(0, "completed")  # Blank drop targets are not executable slots.
            window.close()

    def test_visual_layout_validates_duplicate_and_out_of_range_positions(self):
        from macro_studio.deck_edge_panel import slot_visual_positions
        positions = slot_visual_positions([10, 20, 30, 40], {"10": 5, "20": 5, "30": -1, "40": 999999})
        self.assertEqual(5, positions[10])
        self.assertEqual(4, len(set(positions.values())))
        self.assertTrue(all(0 <= value < 64 for value in positions.values()))

    def test_edge_size_button_opens_controls_without_unpinning_even_on_small_screen(self):
        from PySide6 import QtCore, QtTest
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._set_edge_panel_mode("left")
            window.show()
            screen = mock.Mock()
            screen.availableGeometry.return_value = QtCore.QRect(-640, 24, 300, 280)
            with mock.patch("macro_studio.quickslot_deck.QtGui.QGuiApplication.screenAt", return_value=screen):
                window._show_edge_panel_menu(screen.availableGeometry().topLeft())
                menu = window.edge_radial_menu
                self.app.processEvents()
                self.assertTrue(screen.availableGeometry().contains(menu.geometry()))
                self.assertEqual(QtCore.QSize(280, 280), menu.size())
                self.assertEqual("슬롯 크기", menu.center_secondary_action_text)
                QtTest.QTest.qWait(260)
                with mock.patch.object(window, "_show_edge_panel_size_dialog") as show:
                    QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton,
                                           pos=menu._center_button_rect().center().toPoint())
                    self.app.processEvents()
                    show.assert_called_once()
                    self.assertFalse(menu.isVisible())
                    self.assertEqual("left", window.config["edge_panel_side"])
            window.close()

    def test_size_dialog_controls_update_square_tiles_live_at_all_edges(self):
        from PySide6 import QtCore, QtTest, QtWidgets
        from macro_studio.deck_edge_panel import EdgePanelSizeDialog
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            slots = [{"macro": f"슬롯 {i}"} for i in range(40)]
            window._save_preset_hotkeys({"slots": slots})
            free_size = window._saved_manual_tile_side()
            window._set_edge_panel_mode("right")
            window.show()
            dialog = EdgePanelSizeDialog(window._edge_panel_tile_size(), window)
            dialog.size_changed.connect(window._set_edge_panel_tile_size)
            dialog.show()
            QtTest.QTest.mouseClick(dialog.increase_button, QtCore.Qt.LeftButton)
            self.assertEqual(76, window._edge_panel_tile_size())
            QtTest.QTest.mouseClick(dialog.decrease_button, QtCore.Qt.LeftButton)
            self.assertEqual(68, window._edge_panel_tile_size())
            dialog.size_slider.setValue(96)
            self.assertEqual(96, dialog.size_spin.value())
            self.assertEqual(96, window._edge_panel_tile_size())
            large = next(button for button in dialog.findChildren(QtWidgets.QPushButton)
                         if button.text() == "크게 128")
            QtTest.QTest.mouseClick(large, QtCore.Qt.LeftButton)
            self.assertEqual(128, dialog.size_slider.value())
            for edge in ("left", "right", "top", "bottom"):
                window._set_edge_panel_mode(edge)
                for size in (48, 84, 128, 256):
                    dialog.size_spin.setValue(size)
                    self.app.processEvents()
                    self.assertEqual(list(range(40)), [button.slot_index for button in window.buttons])
                    self.assertTrue(all(button.size() == QtCore.QSize(size, size) for button in window.buttons))
                    self.assertTrue(window.screen().availableGeometry().contains(window.geometry()))
                    self.assertEqual(edge, window.config["edge_panel_side"])
                    self.assertEqual(free_size, window._saved_manual_tile_side())
                    self.assertEqual(slots, window.repository.load_hotkeys()["slots"])
            self.assertFalse(dialog.increase_button.isEnabled())
            dialog.size_spin.setValue(48)
            self.assertFalse(dialog.decrease_button.isEnabled())
            dialog.close()
            window.close()

    def test_panel_size_persists_when_reentering_switching_presets_and_restarting(self):
        from PySide6 import QtCore
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._save_preset_hotkeys({"slots": [{"macro": "실행"}]})
            window.refresh_slots()
            window.show()
            self.app.processEvents()
            free_geometry = window.geometry()
            # It can also be configured before the panel is docked.
            window._set_edge_panel_tile_size(100)
            self.assertEqual(free_geometry, window.geometry())
            window._set_edge_panel_mode("left")
            self.assertEqual(QtCore.QSize(100, 100), window.buttons[0].size())
            window.config["slot_presets"]["other"] = window._build_slot_preset("다른 모드")
            window._switch_slot_preset("other", automatic=True)
            self.assertEqual(QtCore.QSize(100, 100), window.buttons[0].size())
            window._set_edge_panel_mode("free")
            self.assertEqual(68, window._saved_manual_tile_side())
            window._set_edge_panel_mode("right")
            self.assertEqual(100, window._edge_panel_tile_size())
            window.close()
            reopened = QuickSlotDeckWindow(window.repository)
            self.assertEqual(100, reopened._edge_panel_tile_size())
            self.assertEqual("right", reopened.config["edge_panel_side"])
            self.assertEqual(QtCore.QSize(100, 100), reopened.buttons[0].size())
            reopened.close()

    def test_panel_size_validates_legacy_values(self):
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            for value, expected in ((12, 48), (9999, 256), ("invalid", 68), (float("nan"), 68)):
                window.config["edge_panel_tile_side"] = value
                self.assertEqual(expected, window._edge_panel_tile_size())
            window._set_edge_panel_tile_size(-1)
            self.assertEqual(48, window._edge_panel_tile_size())
            window.close()

    def test_pin_survives_window_tab_changes_and_restart_then_auto_catches_up(self):
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            repository = window.repository
            window.config["slot_presets"]["video"] = window._build_slot_preset("영상")
            window.config["slot_presets"]["studio"] = window._build_slot_preset("방송")
            window.config["auto_preset_enabled"] = True
            window.config["auto_preset_rules"] = [{"site": "youtube.com", "preset_id": "video"}]
            window.config["auto_program_rules"] = [{"exe": "obs64.exe", "preset_id": "studio"}]
            if window._browser_bridge:
                window._browser_bridge.close()
            bridge = mock.Mock()
            bridge.command_results = queue.Queue()
            bridge.events = queue.Queue()
            window._browser_bridge = bridge
            window._handle_radial_action("preset_mode")
            bridge.events.put((1, "https://www.youtube.com/"))
            window._poll_browser_events()
            with mock.patch("macro_studio.quickslot_deck.foreground_program", return_value=("obs64.exe", 10001)):
                window._poll_foreground_program()
            self.assertEqual("default", window.config["active_slot_preset"])
            self.assertTrue(window.config["auto_preset_pinned"])
            self.assertIn("고정", window.preset_badge.toolTip())
            window.close()

            reopened = QuickSlotDeckWindow(repository)
            self.assertTrue(reopened.config["auto_preset_pinned"])
            with mock.patch("macro_studio.quickslot_deck.foreground_program", return_value=("obs64.exe", 10001)):
                reopened._handle_radial_action("preset_mode")
            self.assertFalse(reopened.config["auto_preset_pinned"])
            self.assertEqual("studio", reopened.config["active_slot_preset"])
            reopened.close()

    def test_actual_radial_click_pins_without_stopping_running_macros(self):
        from PySide6 import QtCore, QtTest
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window.config["auto_preset_enabled"] = True
            window.show()
            menu = window.radial_menu
            menu.popup_at(window.frameGeometry().center(), sticky=True)
            index = next(i for i, item in enumerate(menu.items) if item.key == "preset_mode")
            QtTest.QTest.qWait(270)
            with mock.patch.object(window, "stop_all_macros") as stop:
                QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=menu._get_item_center(index).toPoint())
                self.app.processEvents()
                self.assertTrue(window.config["auto_preset_pinned"])
                stop.assert_not_called()
            window.close()

    def test_edge_layout_wraps_inward_and_respects_negative_monitor_coordinates(self):
        from PySide6 import QtCore
        from macro_studio.deck_edge_panel import edge_panel_layout
        available = QtCore.QRect(-1920, 40, 1920, 1040)
        for edge in ("left", "right", "top", "bottom"):
            one = edge_panel_layout(edge, 1, available, 68, 10)
            self.assertEqual((1, 1), (one.rows, one.cols))
            layout = edge_panel_layout(edge, 40, available, 68, 10)
            self.assertTrue(available.contains(layout.geometry))
            self.assertEqual(40, len({layout.cell(i) for i in range(40)}))
            if edge in {"left", "right"}:
                self.assertGreater(layout.cols, 1)
                self.assertLessEqual(layout.content_size.height() + 44, available.height())
            else:
                self.assertGreater(layout.rows, 1)
                self.assertLessEqual(layout.content_size.width() + 16, available.width())
            if edge == "left":
                self.assertEqual(available.left(), layout.geometry.left())
            elif edge == "right":
                self.assertEqual(available.right(), layout.geometry.right())
            elif edge == "top":
                self.assertEqual(available.top(), layout.geometry.top())
            else:
                self.assertEqual(available.bottom(), layout.geometry.bottom())

    def test_panel_shows_all_pages_without_changing_slots_grid_or_tile_size(self):
        from PySide6 import QtCore
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            slots = [{"macro": f"동작 {i}", "mode": "deck_action",
                      "action": {"kind": "text", "text": str(i)}} for i in range(40)]
            window._save_preset_hotkeys({"slots": slots, "deck_page_count": 3})
            window.refresh_slots()
            window.show()
            self.app.processEvents()
            window.move(window.screen().availableGeometry().topLeft() + QtCore.QPoint(24, 24))
            free_position, free_size = window.pos(), window.size()
            screen = window.screen()
            available = screen.availableGeometry()
            for edge in ("left", "right", "top", "bottom"):
                window._handle_edge_panel_action(f"edge:{edge}")
                self.app.processEvents()
                self.assertEqual(list(range(40)), [button.slot_index for button in window.buttons])
                self.assertEqual((3, 5), (window.rows, window.cols))
                self.assertEqual(slots, window.repository.load_hotkeys()["slots"])
                self.assertTrue(all(button.size() == QtCore.QSize(68, 68) for button in window.buttons))
                self.assertTrue(available.contains(window.geometry()))
                self.assertFalse(window.resize_grip.isVisible())
                self.assertFalse(window.bottom_padding_grip.isVisible())
            window._set_edge_panel_mode("free")
            self.app.processEvents()
            self.assertEqual(free_position, window.pos())
            self.assertEqual(free_size, window.size())
            self.assertEqual(list(range(15)), [button.slot_index for button in window.buttons])
            self.assertEqual(slots, window.repository.load_hotkeys()["slots"])
            window.close()

    def test_panel_reflows_on_preset_switch_and_restores_edge_after_restart(self):
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._save_preset_hotkeys({"slots": [{"macro": f"동작 {i}"} for i in range(40)]})
            other = window._build_slot_preset("한 칸")
            other["slots"] = []
            window.config["slot_presets"]["other"] = other
            window.refresh_slots()
            window._set_edge_panel_mode("right")
            with mock.patch.object(window, "stop_all_macros") as stop:
                window._switch_slot_preset("other", automatic=True)
                stop.assert_not_called()
            self.assertEqual(1, len(window.buttons))
            self.assertEqual(window.screen().availableGeometry().right(), window.geometry().right())
            window.close()
            reopened = QuickSlotDeckWindow(window.repository)
            self.assertEqual("right", reopened.config["edge_panel_side"])
            self.assertEqual(reopened.screen().availableGeometry().right(), reopened.geometry().right())
            self.assertEqual(1, len(reopened.buttons))
            reopened.close()

    def test_repeated_page_pin_appears_once_with_original_action_indexes(self):
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            slots = [{"macro": f"동작 {i}"} for i in range(31)]
            window._save_preset_hotkeys({"slots": slots})
            window._active_pinned_slots()["0"] = {"slot": {"macro": "고정 슬롯"}, "icon": {}}
            window._set_edge_panel_mode("left")
            self.assertEqual(1, sum(button.macro_name == "고정 슬롯" for button in window.buttons))
            self.assertEqual([i for i in range(31) if i not in {15, 30}],
                             [button.slot_index for button in window.buttons])
            self.assertEqual(slots, window.repository.load_hotkeys()["slots"])
            window.close()

    def test_resolution_change_reflows_and_extreme_overflow_remains_scrollable(self):
        from PySide6 import QtCore
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._save_preset_hotkeys({"slots": [{"macro": f"동작 {i}"} for i in range(40)]})
            screen = mock.Mock()
            screen.name.return_value = "test-screen"
            screen.availableGeometry.return_value = QtCore.QRect(-1200, 40, 1200, 1000)
            with mock.patch.object(window, "_edge_panel_screen", return_value=screen):
                window._set_edge_panel_mode("right")
                window.show()
                self.app.processEvents()
                original_columns = window._visible_cols
                screen.availableGeometry.return_value = QtCore.QRect(-1200, 40, 300, 200)
                window._reflow_edge_panel()
                self.app.processEvents()
                self.app.processEvents()
                self.assertGreater(window._visible_cols, original_columns)
                self.assertTrue(screen.availableGeometry().contains(window.geometry()))
                self.assertGreater(window.edge_scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(40, len(window.buttons))
                self.assertTrue(all(button.width() == button.height() == 68 for button in window.buttons))
            window.close()

    def test_radial_menus_at_all_corners_are_wholly_visible_in_both_opening_modes(self):
        from PySide6 import QtCore, QtGui, QtTest
        from macro_studio.quickslot_deck import RadialPieMenuWidget
        available = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        menu = RadialPieMenuWidget()
        selected = []
        menu.action_triggered.connect(selected.append)
        for sticky in (False, True):
            for corner in (available.topLeft(), available.topRight(),
                           available.bottomLeft(), available.bottomRight()):
                menu.popup_at(corner, sticky=sticky)
                self.app.processEvents()
                self.assertTrue(available.contains(menu.geometry()))
                if sticky:
                    QtTest.QTest.qWait(260)
                index = 2
                QtTest.QTest.mouseRelease(menu, QtCore.Qt.LeftButton,
                                         pos=menu._get_item_center(index).toPoint())
                self.app.processEvents()
                self.assertEqual(menu.items[index].key, selected[-1])
        self.assertEqual(8, len(selected))
        menu.close()

    def test_small_monitor_scales_whole_radial_and_keeps_hit_testing_correct(self):
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import RadialPieMenuWidget
        screen = mock.Mock()
        available = QtCore.QRect(-640, 24, 300, 280)
        screen.availableGeometry.return_value = available
        menu = RadialPieMenuWidget()
        selected = []
        menu.action_triggered.connect(selected.append)
        with mock.patch("macro_studio.quickslot_deck.QtGui.QGuiApplication.screenAt", return_value=screen):
            menu.popup_at(available.topLeft(), sticky=False)
            self.app.processEvents()
            self.assertTrue(available.contains(menu.geometry()))
            self.assertEqual(QtCore.QSize(280, 280), menu.size())
            QtTest.QTest.mouseRelease(menu, QtCore.Qt.LeftButton,
                                     pos=menu._get_item_center(6).toPoint())
            self.app.processEvents()
            self.assertEqual([menu.items[6].key], selected)
        menu.close()

    def test_panel_direction_menu_center_returns_to_free_layout(self):
        from PySide6 import QtCore, QtTest
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._set_edge_panel_mode("left")
            window.show()
            window._show_edge_panel_menu(window.frameGeometry().center())
            menu = window.edge_radial_menu
            self.assertEqual(["edge:top", "edge:right", "edge:bottom", "edge:left"],
                             [item.key for item in menu.items])
            center = menu.rect().center()
            self.assertLess(menu._get_item_center(0).y(), center.y())
            self.assertGreater(menu._get_item_center(1).x(), center.x())
            self.assertGreater(menu._get_item_center(2).y(), center.y())
            self.assertLess(menu._get_item_center(3).x(), center.x())
            QtTest.QTest.qWait(260)
            QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=center)
            self.app.processEvents()
            self.assertEqual("free", window.config["edge_panel_side"])
            window.close()

    def send_drag(self, widget, target):
        from PySide6 import QtCore, QtGui, QtTest, QtWidgets
        QtTest.QTest.mousePress(widget, QtCore.Qt.LeftButton, pos=widget.rect().center())
        QtWidgets.QApplication.sendEvent(widget, QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove, QtCore.QPointF(widget.mapFromGlobal(target)), QtCore.QPointF(target),
            QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
        QtWidgets.QApplication.sendEvent(widget, QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(widget.mapFromGlobal(target)), QtCore.QPointF(target),
            QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
        self.app.processEvents()

    def test_dragging_badge_and_slot_snaps_to_each_edge_without_running_actions(self):
        from PySide6 import QtCore, QtTest
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._save_preset_hotkeys({"slots": [{"macro": "클릭 동작", "mode": "deck_action",
                                                  "action": {"kind": "text", "text": "never"}}]})
            window._set_edge_panel_mode("left")
            window.show()
            available = window.screen().availableGeometry()
            center = available.center()
            targets = {
                "right": QtCore.QPoint(available.right() - 1, center.y()),
                "bottom": QtCore.QPoint(center.x(), available.bottom() - 1),
                "left": QtCore.QPoint(available.left() + 1, center.y()),
                "top": QtCore.QPoint(center.x(), available.top() + 1),
            }
            with mock.patch.object(window, "_execute_deck_action") as execute, \
                    mock.patch.object(window, "_switch_slot_preset") as switch:
                for edge, target in targets.items():
                    window._hold_triggered = True  # A previous long press must not block a new drag.
                    self.send_drag(window.preset_badge, target)
                    self.assertEqual(edge, window.config["edge_panel_side"])
                    self.assertTrue(available.contains(window.geometry()))
                    self.assertEqual(QtCore.QSize(68, 68), window.buttons[0].size())
                self.send_drag(window.buttons[0], targets["right"])
                self.assertEqual("right", window.config["edge_panel_side"])
                execute.assert_not_called()
                switch.assert_not_called()
                QtTest.QTest.mouseClick(window.preset_badge, QtCore.Qt.LeftButton)
                switch.assert_called_once_with("default")
            window.close()

    def test_drop_on_another_monitor_changes_saved_monitor_and_edge(self):
        from PySide6 import QtCore
        with tempfile.TemporaryDirectory() as root:
            window = self.make_window(root)
            window._set_edge_panel_mode("left")
            secondary = mock.Mock()
            secondary.name.return_value = "secondary"
            available = QtCore.QRect(-1920, -120, 1920, 1040)
            secondary.availableGeometry.return_value = available
            target = available.topRight() - QtCore.QPoint(4, -400)
            with mock.patch("macro_studio.quickslot_deck.QtGui.QGuiApplication.screenAt", return_value=secondary), \
                    mock.patch("macro_studio.quickslot_deck.QtGui.QGuiApplication.screens", return_value=[secondary]):
                window._snap_edge_panel_to(target)
                self.assertEqual("secondary", window.config["edge_panel_screen"])
                self.assertEqual("right", window.config["edge_panel_side"])
                self.assertEqual(available.right(), window.geometry().right())
            window.close()


if __name__ == "__main__":
    unittest.main()
