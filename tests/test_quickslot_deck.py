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


if __name__ == "__main__":
    unittest.main()
