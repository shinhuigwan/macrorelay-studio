from __future__ import annotations

import os
import unittest
from unittest import mock


class AnimationSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_temporal_mask_keeps_stable_icon_and_removes_changing_background(self) -> None:
        from PySide6 import QtGui
        from macro_studio.automation import build_animation_templates

        frames = []
        for index in range(8):
            image = QtGui.QImage(40, 40, QtGui.QImage.Format_RGBA8888)
            image.fill(QtGui.QColor((index * 31) % 255, (index * 67) % 255, (index * 97) % 255, 255))
            painter = QtGui.QPainter(image)
            painter.fillRect(12, 12, 16, 16, QtGui.QColor(30, 220, 90, 255))
            painter.end()
            frames.append(image)

        templates, stable_ratio = build_animation_templates(frames, stability_threshold=8, max_templates=4)

        self.assertEqual(4, len(templates))
        self.assertGreater(stable_ratio, 0.04)
        self.assertLess(stable_ratio, 0.5)
        self.assertEqual(0, templates[0].pixelColor(2, 2).alpha())
        self.assertEqual(255, templates[0].pixelColor(20, 20).alpha())

    def test_action_template_uses_opencv_multi_frame_defaults(self) -> None:
        from macro_studio.action_editor import ACTION_LABELS, action_template

        step = action_template("animation_search")

        self.assertEqual("애니메이션 서치", ACTION_LABELS["animation_search"])
        self.assertEqual("animation_search", step["action"])
        self.assertEqual("opencv", step["engine"])
        self.assertEqual(1200, step["animation_capture_ms"])
        self.assertTrue(step["animation_auto_mask"])

    def test_runtime_dispatches_animation_search_to_image_engine_without_auto_run(self) -> None:
        import macro_tool

        step = {
            "action": "animation_search",
            "assets": ["anim_1", "anim_2"],
            "asset": "anim_1",
            "engine": "opencv",
            "click_enabled": False,
        }
        with mock.patch.object(macro_tool, "render_image_search", return_value=["vision"]) as renderer:
            result = macro_tool.render_step(step, {}, 1)

        self.assertEqual(["vision"], result)
        prepared = renderer.call_args.args[0]
        self.assertEqual("animation_search", prepared["action"])
        self.assertFalse(prepared["click_enabled"])


if __name__ == "__main__":
    unittest.main()
