from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile


class AIVideoTestTests(unittest.TestCase):
    def test_package_contains_video_compact_actions_and_feasibility_prompt(self) -> None:
        from macro_studio.ai_video_test import AIVideoTestPackageBuilder

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "source.mp4"
            video.write_bytes(b"test-video")
            events = [
                {
                    "type": "mouse",
                    "t": 120,
                    "x": 40,
                    "y": 50,
                    "button": "Left",
                    "image_sample_bmp": "VERY-LARGE-BINARY",
                    "window": {"exe": "game.exe", "title": "Test Game"},
                },
                {"type": "key", "t": 300, "char": "secret", "token": "Printable"},
            ]
            archive, stage = AIVideoTestPackageBuilder(root).build(events, video, "퀘스트 진행")

            self.assertTrue(archive.is_file())
            self.assertTrue((stage / "recording.mp4").is_file())
            timeline = json.loads((stage / "actions.json").read_text(encoding="utf-8"))
            self.assertEqual("퀘스트 진행", timeline["purpose"])
            self.assertEqual(2, timeline["action_count"])
            self.assertNotIn("image_sample_bmp", timeline["actions"][0])
            self.assertEqual("[REDACTED]", timeline["actions"][1]["char"])
            prompt = (stage / "prompt.txt").read_text(encoding="utf-8")
            self.assertIn("가능 / 일부 가능 / 불가능", prompt)
            self.assertIn("매크로 JSON이나 코드를 만들지 마세요", prompt)
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(
                    {"recording.mp4", "actions.json", "manifest.json", "prompt.txt", "README.txt"},
                    set(bundle.namelist()),
                )

    def test_controller_uses_thirty_second_continuous_video_profile(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.ai_video_test import AIVideoTestRecordingController
        from macro_studio.repository import MacroRepository

        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            controller = AIVideoTestRecordingController(MacroRepository(Path(directory)))
            self.assertTrue(controller.continuous_video)
            self.assertEqual(5.0, controller.video_fps)
            self.assertEqual(1920, controller.video_max_width)
            self.assertEqual(30_000, controller.max_duration_ms)
            self.assertFalse(controller.protect_typing)
            self.assertFalse(controller.right_click_condition)
            self.assertFalse(controller.workflow_controls)
            self.assertFalse(controller.capture_action_images)


if __name__ == "__main__":
    unittest.main()
