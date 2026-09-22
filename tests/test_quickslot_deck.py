from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
            window = QuickSlotDeckWindow(repository)
            self.app.processEvents()

            self.assertEqual(["첫 번째", "두 번째"], [button.macro_name for button in window.buttons])
            self.assertEqual(2, window.grid_layout.count())
            self.assertLessEqual(window.width(), 430)
            repository.save_hotkeys({"slots": [{"macro": "첫 번째", "hotkey": "", "mode": "hybrid"}]})
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
            window = QuickSlotDeckWindow(repository)
            dialog = QuickSlotDeckSettingsDialog(window)

            scales = [float(dialog.tile_scale_combo.itemData(index)) for index in range(dialog.tile_scale_combo.count())]
            self.assertEqual([1.0, 0.5, 0.25], scales)

            window.config["tile_scale"] = 0.25
            window.refresh_slots()
            self.assertEqual((64, 64), (window.width(), window.height()))
            dialog.close()
            window.close()

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

    def test_radial_add_uses_first_empty_slot(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.quickslot_deck import QuickSlotDeckWindow
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("추가할 매크로")
            repository.save_hotkeys({"slots": [{"macro": "", "hotkey": "", "mode": "hybrid"}]})
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

    def test_slot_double_click_also_opens_sticky_preset_radial(self) -> None:
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

            self.assertEqual(1, show_radial.call_count)
            self.assertTrue(show_radial.call_args.kwargs["sticky"])
            window.close()

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
            self.assertEqual(15, len(dock.action_buttons))

            dock.payload["slots"][0] = dock._slot_from_action({"kind": "page_next", "label": "다음"})
            dock.add_page()
            saved = repository.load_hotkeys()
            self.assertEqual(2, saved["deck_page_count"])
            self.assertEqual("page_next", saved["slots"][0]["action"]["kind"])
            self.assertEqual(window.rows * window.cols * 2, len(saved["slots"]))
            dock.close()
            window.close()

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
                self.assertTrue(result["label"])
                dialog.close()
            window.close()

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
                if kind == "mouse_click":
                    self.assertIn("x", dialog.widgets)
                    self.assertIn("y", dialog.widgets)
                dialog.close()
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
                send_text.assert_called_once_with(321, "hello")
            with mock.patch.object(window, "_click_action_target") as click:
                action = {"kind": "mouse_click", "label": "클릭", "x": 40, "y": 50, "input_mode": "inactive"}
                window._execute_deck_action(action)
                click.assert_called_once_with(action, inactive=True)
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
            window.config["slot_presets"]["browser"] = browser
            window._refresh_preset_radial_menu()

            self.assertIn("preset_settings", [item.key for item in window.radial_menu.items])
            self.assertEqual(["deck:default", "deck:browser"], [item.key for item in window.preset_radial_menu.items])
            self.assertEqual("브라우저 모드", window.preset_radial_menu.items[1].title)

            window._handle_preset_radial_action("deck:browser")
            self.assertEqual("browser", window.config["active_slot_preset"])
            self.assertEqual(["두 번째"], [button.macro_name for button in window.buttons])
            self.assertEqual("B", window.custom_icons["0"]["emoji"])
            self.assertEqual(2, window.config["theme_index"])

            window._handle_preset_radial_action("deck:default")
            self.assertEqual("default", window.config["active_slot_preset"])
            self.assertEqual(["첫 번째", "두 번째"], [button.macro_name for button in window.buttons])
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


if __name__ == "__main__":
    unittest.main()
