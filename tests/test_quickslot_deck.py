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

    def test_double_click_opens_editor_without_running_macro(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import StreamDeckButton

        button = StreamDeckButton(0)
        button.set_slot_data("테스트", "", "hybrid", False)
        edit_spy = QtTest.QSignalSpy(button.edit_icon_requested)
        run_spy = QtTest.QSignalSpy(button.slot_triggered)
        button.show()
        QtTest.QTest.mouseDClick(button, QtCore.Qt.LeftButton)
        QtTest.QTest.qWait(self.app.doubleClickInterval() + 30)

        self.assertEqual(1, edit_spy.count())
        self.assertEqual(0, run_spy.count())
        button.close()

    def test_single_click_still_runs_after_double_click_window(self) -> None:
        from PySide6 import QtCore, QtTest
        from macro_studio.quickslot_deck import StreamDeckButton

        button = StreamDeckButton(0)
        button.set_slot_data("테스트", "", "hybrid", False)
        run_spy = QtTest.QSignalSpy(button.slot_triggered)
        button.show()
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        QtTest.QTest.qWait(self.app.doubleClickInterval() + 30)

        self.assertEqual(1, run_spy.count())
        button.close()

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
