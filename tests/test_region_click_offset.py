from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest


class RegionClickOffsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_offset_is_calculated_from_detected_image_center(self) -> None:
        from macro_studio.region_visual_test import image_click_offset

        result = {"found": True, "hit_x": 310, "hit_y": 220}

        self.assertEqual([42, -18], image_click_offset(result, [352, 202]))
        self.assertIsNone(image_click_offset({"found": False}, [352, 202]))

    def test_canvas_one_click_picker_respects_preview_zoom(self) -> None:
        import numpy as np
        from PySide6 import QtCore, QtTest
        from macro_studio.region_visual_test import InteractiveCanvas

        canvas = InteractiveCanvas()
        canvas.set_frame(np.zeros((80, 100, 3), dtype=np.uint8))
        canvas.set_scale(2.0)
        received: list[tuple[str, list[int]]] = []
        canvas.click_point_picked.connect(lambda alias, point: received.append((alias, list(point))))

        canvas.begin_click_offset_pick("target")
        QtTest.QTest.mouseClick(canvas, QtCore.Qt.LeftButton, pos=QtCore.QPoint(80, 100))

        self.assertEqual([("target", [40, 50])], received)
        self.assertEqual("", canvas._offset_pick_alias)
        canvas.close()

    def test_region_visual_default_size_uses_most_of_the_screen(self) -> None:
        from PySide6 import QtCore
        from macro_studio.region_visual_test import region_visual_default_size

        self.assertEqual(QtCore.QSize(1840, 1015), region_visual_default_size(QtCore.QSize(1920, 1080)))
        self.assertEqual(QtCore.QSize(1311, 728), region_visual_default_size(QtCore.QSize(1366, 768)))
        self.assertEqual(QtCore.QSize(1000, 728), region_visual_default_size(QtCore.QSize(1024, 768)))
        self.assertEqual(QtCore.QSize(616, 440), region_visual_default_size(QtCore.QSize(640, 480)))

    def test_region_visual_left_panel_is_wider_but_bounded(self) -> None:
        from macro_studio.region_visual_test import region_visual_left_panel_width

        self.assertEqual(500, region_visual_left_panel_width(1311))
        self.assertEqual(534, region_visual_left_panel_width(1840))
        self.assertEqual(560, region_visual_left_panel_width(2400))

    def test_visual_offsets_replace_stale_action_editor_offsets(self) -> None:
        from PySide6 import QtGui
        from macro_studio.action_editor import ActionEditor
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            for alias, colour in (("first", "#20C997"), ("fifth", "#7C6CFF")):
                image = QtGui.QImage(24, 18, QtGui.QImage.Format_RGB32)
                image.fill(QtGui.QColor(colour))
                repository.add_asset_image(image, alias)

            editor = ActionEditor(repository)
            editor.refresh_sources()
            editor.load_step(
                {
                    "action": "multi_image_search",
                    "asset": "first",
                    "assets": ["first", "fifth"],
                    "asset_offsets": {"first": [1, 2], "fifth": [3, 4]},
                    "click_target": "first_image",
                }
            )
            editor._apply_visual_asset_offsets(
                "multi_image_search",
                ["first", "fifth"],
                {"first": [0, 0], "fifth": [137, -42]},
            )
            rebuilt = editor.build_step()

            self.assertEqual({"first": [0, 0], "fifth": [137, -42]}, rebuilt["asset_offsets"])
            editor.close()


if __name__ == "__main__":
    unittest.main()
