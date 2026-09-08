from __future__ import annotations

import unittest

from PySide6 import QtCore

from macro_studio.region_visual_test import RegionVisualTestDialog


class _RegionHarness:
    def __init__(self, *, mode: str, coords: str, base_x: int = 0, base_y: int = 0) -> None:
        self.step = {"region_mode": mode, "region_coords": coords, "click": {}}
        self._base_x = base_x
        self._base_y = base_y
        self._asset_regions = {"first": [110, 220, 150, 260]}
        self._is_color_mode = False
        self._color_regions = {}
        self.capture_count = 0

    def _uses_screen_coordinates(self) -> bool:
        return RegionVisualTestDialog._uses_screen_coordinates(self)

    def _apply_window_target(self, target, *, refresh=True):
        return RegionVisualTestDialog._apply_window_target(self, target, refresh=refresh)

    def _target_at_native_rect(self, rect):
        self.last_native_rect = QtCore.QRect(rect)
        return self.target

    def _do_capture_and_test(self) -> None:
        self.capture_count += 1


class RegionWindowBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {
            "window": "ahk_class SampleWindow ahk_exe sample.exe",
            "exe": "sample.exe",
            "client_origin": [100, 200],
            "client_size": [800, 600],
        }

    def test_screen_regions_rebase_to_detected_client(self) -> None:
        harness = _RegionHarness(mode="screen", coords="screen")

        applied = RegionVisualTestDialog._apply_window_target(harness, self.target, refresh=False)

        self.assertTrue(applied)
        self.assertEqual([10, 20, 50, 60], harness._asset_regions["first"])
        self.assertEqual("client", harness.step["region_mode"])
        self.assertEqual("relative", harness.step["region_coords"])
        self.assertEqual("sample.exe", harness.step["region_window_exe"])
        self.assertEqual(self.target["window"], harness.step["region_window"])
        self.assertEqual("sample.exe", harness.step["click"]["window_exe"])

    def test_canvas_drag_automatically_binds_underlying_program(self) -> None:
        harness = _RegionHarness(mode="screen", coords="screen", base_x=-1920, base_y=0)
        harness.target = {
            **self.target,
            "client_origin": [-1900, 100],
            "client_size": [1000, 700],
        }

        RegionVisualTestDialog._on_canvas_region_dragged(harness, "first", [40, 130, 140, 210])

        self.assertEqual(QtCore.QRect(-1880, 130, 100, 80), harness.last_native_rect)
        self.assertEqual([20, 30, 120, 110], harness._asset_regions["first"])
        self.assertEqual("sample.exe", harness.step["region_window_exe"])
        self.assertEqual(1, harness.capture_count)

    def test_region_from_another_window_expands_safely(self) -> None:
        harness = _RegionHarness(mode="screen", coords="screen")
        harness._asset_regions["first"] = [4000, 4000, 4100, 4100]

        RegionVisualTestDialog._apply_window_target(harness, self.target, refresh=False)

        self.assertEqual([0, 0, 800, 600], harness._asset_regions["first"])


if __name__ == "__main__":
    unittest.main()
