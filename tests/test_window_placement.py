from __future__ import annotations

import unittest

from PySide6 import QtCore

from macro_studio.main_window import MainWindow


class WindowPlacementTests(unittest.TestCase):
    def test_saved_rect_is_preserved_on_same_screen(self) -> None:
        saved = QtCore.QRect(120, 80, 1500, 850)
        screen = QtCore.QRect(0, 0, 1920, 1080)
        self.assertEqual(saved, MainWindow._clamp_window_rect(saved, [screen]))

    def test_off_screen_rect_is_clamped_to_available_screen(self) -> None:
        saved = QtCore.QRect(5000, 4000, 1800, 1000)
        screen = QtCore.QRect(0, 0, 1600, 900)
        restored = MainWindow._clamp_window_rect(saved, [screen])
        self.assertEqual(QtCore.QSize(1600, 900), restored.size())
        self.assertTrue(screen.contains(restored.topLeft()))
        self.assertTrue(screen.contains(restored.bottomRight()))


if __name__ == "__main__":
    unittest.main()
