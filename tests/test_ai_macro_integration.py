from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6 import QtCore, QtWidgets
from macro_studio.ai_macro_dialog import AiMacroDialog
from macro_studio.ai_macro_plan import VERSION, PlanError, compile_plan, export_recording
from macro_studio.automation import RecordingReviewDialog
from macro_studio.builder import BuilderPage
from macro_studio.repository import MacroRepository


class AiMacroStudioIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = MacroRepository(self.root)

        png_bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAACXBIWXMAAA9hAAAPYQGoP6dpAAAAHUlEQVQ4jWP8z8Dwn4ECwESJ5lEDRg0YNWAwGQAAWG0CHpmX3bgAAAAASUVORK5CYII=")
        asset_file = self.root / "input" / "test_btn.png"
        asset_file.parent.mkdir(parents=True, exist_ok=True)
        asset_file.write_bytes(png_bytes)
        self.repo.add_asset(asset_file, "test_btn")

        self.sample_event = {
            "event_id": "evt_1",
            "kind": "image_capture",
            "action": "image_search",
            "asset": "test_btn",
            "click_enabled": True,
            "click": {"offset": [10, 20], "click_offset": True},
            "search_region": [0, 0, 100, 100],
            "image_sample_bmp": base64.b64encode(png_bytes).decode("ascii"),
            "image_anchor": [0, 0],
            "_live_position": [100, 100],
        }

    def test_recording_review_dialog_has_ai_macro_button_and_signal(self):
        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)

        self.assertTrue(hasattr(dialog, "btn_ai_macro"))
        self.assertIn("자동 매크로", dialog.btn_ai_macro.text())
        self.assertTrue(hasattr(dialog, "ai_macro_ready"))

    def test_open_ai_macro_dialog_creates_non_modal_window(self):
        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)

        dialog._open_ai_macro_dialog()
        ai_dlg = dialog._ai_macro_dialog
        self.assertIsNotNone(ai_dlg)
        self.assertIsInstance(ai_dlg, AiMacroDialog)
        self.assertFalse(ai_dlg.isModal())
        self.addCleanup(ai_dlg.deleteLater)

        # Re-opening does not create duplicate
        dialog._open_ai_macro_dialog()
        self.assertIs(dialog._ai_macro_dialog, ai_dlg)

    def test_full_pipeline_export_compile_and_builder_create(self):
        # 1. Export recording
        export_dir = self.root / "exports" / "rec1"
        steps = [
            {
                "action": "image_search",
                "asset": "test_btn",
                "click_enabled": True,
                "click": {"offset": [5, 15], "click_offset": True},
            }
        ]
        pkg = export_recording(steps, "테스트 목적", export_dir, self.repo.asset_path)
        self.assertTrue(pkg.is_file())
        private_path = export_dir / "private-recording.json"
        self.assertTrue(private_path.is_file())

        # 2. Compile plan
        private_data = json.loads(private_path.read_text(encoding="utf-8"))
        plan = {
            "schema": VERSION,
            "recording_id": private_data["recording_id"],
            "name": "AI자동생성테스트",
            "entry": "n1",
            "nodes": [
                {
                    "id": "n1",
                    "record": "r001",
                    "mode": "replay",
                    "label": "버튼 클릭",
                    "success": "STOP",
                    "failure": "STOP",
                }
            ],
            "missing_images": [],
        }
        draft = compile_plan(plan, private_data, self.repo.asset_path)
        self.assertEqual(draft["name"], "AI자동생성테스트")
        self.assertEqual(len(draft["steps"]), 2)  # node1 + STOP terminal
        self.assertEqual(draft["steps"][0]["click"]["offset"], [5, 15])
        self.assertEqual(draft["steps"][1]["action"], "flow_control")
        self.assertEqual(draft["steps"][1]["jump_to"], 0)

        # 3. Builder integration
        builder = BuilderPage(self.repo)
        self.addCleanup(builder.deleteLater)

        with mock.patch.object(QtWidgets.QMessageBox, "information") as mock_info:
            builder._create_macro_from_ai_plan(draft)
            self.assertTrue(mock_info.called)

        # Verify saved on disk and selected in builder
        loaded = self.repo.load_macro("AI자동생성테스트")
        self.assertEqual(loaded["name"], "AI자동생성테스트")
        self.assertEqual(builder.current_name, "AI자동생성테스트")
        self.assertEqual(len(loaded["steps"]), 2)
        self.assertIn("1", loaded["graph_positions"])
        self.assertIn("2", loaded["graph_positions"])

        # 4. Test duplicate name numbering
        with mock.patch.object(QtWidgets.QMessageBox, "information"):
            builder._create_macro_from_ai_plan(draft)

        loaded_dup = self.repo.load_macro("AI자동생성테스트_1")
        self.assertEqual(loaded_dup["name"], "AI자동생성테스트_1")
        self.assertEqual(builder.current_name, "AI자동생성테스트_1")

    def test_recording_review_dialog_ai_plan_mode_does_not_append_raw_steps(self):
        builder = BuilderPage(self.repo)
        self.addCleanup(builder.deleteLater)
        builder.current_macro = {"name": "existing", "steps": []}
        builder.current_name = "existing"

        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)

        with mock.patch.object(builder, "_append_automation_steps") as mock_append:
            dialog.target_mode = "ai_plan"
            builder._finish_smart_recording_review(dialog)
            self.assertFalse(mock_append.called)

    def test_create_macro_unique_handles_corrupt_files_and_collision(self):
        corrupt_path = self.repo.macro_path("충돌테스트")
        corrupt_path.write_text("{corrupt json content", encoding="utf-8")

        name, path = self.repo.create_macro_unique("충돌테스트", {"steps": [{"action": "wait"}]})
        self.assertEqual(name, "충돌테스트_1")
        self.assertTrue(path.is_file())

        # Original corrupted file is untouched
        self.assertEqual(corrupt_path.read_text(encoding="utf-8"), "{corrupt json content")

        # Second creation increments counter
        name2, path2 = self.repo.create_macro_unique("충돌테스트", {"steps": [{"action": "wait"}]})
        self.assertEqual(name2, "충돌테스트_2")
        self.assertTrue(path2.is_file())

        order = self.repo.load_macro_order()
        self.assertIn("충돌테스트_1", order)
        self.assertIn("충돌테스트_2", order)

    def test_ai_macro_dialog_lifetime_open_close_cycles(self):
        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)

        for _ in range(12):
            dialog._open_ai_macro_dialog()
            ai_dlg = dialog._ai_macro_dialog
            self.assertIsNotNone(ai_dlg)
            self.assertTrue(dialog._is_ai_macro_dialog_alive())
            ai_dlg.close()
            self.app.processEvents()
            self.assertFalse(dialog._is_ai_macro_dialog_alive())

    def test_ai_macro_dialog_closed_on_parent_review_reject(self):
        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)
        dialog._open_ai_macro_dialog()
        ai_dlg = dialog._ai_macro_dialog
        self.assertIsNotNone(ai_dlg)

        dialog.reject()
        self.app.processEvents()
        self.assertFalse(dialog._is_ai_macro_dialog_alive())

    def test_save_failure_keeps_dialogs_open_and_enables_retry(self):
        builder = BuilderPage(self.repo)
        self.addCleanup(builder.deleteLater)
        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)

        dialog.ai_macro_ready.connect(builder._create_macro_from_ai_plan)
        builder.ai_macro_save_result.connect(dialog._on_ai_macro_save_result)

        dialog.show()
        dialog._open_ai_macro_dialog()
        ai_dlg = dialog._ai_macro_dialog
        self.assertIsNotNone(ai_dlg)

        # Draft with invalid graph_start_step to force validate_compiled_draft failure
        invalid_draft = {
            "name": "실패테스트",
            "steps": [{"action": "wait", "duration": 100}, {"action": "flow_control", "jump_to": 0}],
            "graph_start_step": 999,  # invalid
            "graph_positions": {"1": [0, 0], "2": [200, 0]},
            "meta": {"ai_plan_schema": VERSION},
        }
        ai_dlg.draft = invalid_draft
        ai_dlg.accept_draft.setEnabled(True)

        with mock.patch.object(QtWidgets.QMessageBox, "warning") as mock_warn:
            ai_dlg.emit_draft()
            self.app.processEvents()
            self.assertTrue(mock_warn.called)

        # Dialogs stay open, draft is kept, retry button is re-enabled
        self.assertTrue(dialog.isVisible() or not dialog.isHidden())
        self.assertTrue(dialog._is_ai_macro_dialog_alive())
        self.assertIsNotNone(ai_dlg.draft)
        self.assertTrue(ai_dlg.accept_draft.isEnabled())
        self.assertIn("저장 실패", ai_dlg.status.text())

    def test_save_success_closes_dialogs_and_loads_canvas(self):
        builder = BuilderPage(self.repo)
        self.addCleanup(builder.deleteLater)
        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)

        dialog.ai_macro_ready.connect(builder._create_macro_from_ai_plan)
        builder.ai_macro_save_result.connect(dialog._on_ai_macro_save_result)

        dialog._open_ai_macro_dialog()
        ai_dlg = dialog._ai_macro_dialog
        self.assertIsNotNone(ai_dlg)

        valid_draft = {
            "name": "성공테스트",
            "steps": [{"action": "wait", "duration": 100}, {"action": "flow_control", "jump_to": 0}],
            "graph_start_step": 1,
            "graph_positions": {"1": [0, 0], "2": [200, 0]},
            "meta": {"ai_plan_schema": VERSION},
        }
        ai_dlg.draft = valid_draft
        ai_dlg.accept_draft.setEnabled(True)

        with mock.patch.object(QtWidgets.QMessageBox, "information"):
            ai_dlg.emit_draft()
            self.app.processEvents()

        # Save success: builder loaded, dialog target mode set to ai_plan
        self.assertEqual(builder.current_name, "성공테스트")
        self.assertEqual(dialog.target_mode, "ai_plan")

    def test_build_steps_failure_does_not_delete_dialog(self):
        builder = BuilderPage(self.repo)
        self.addCleanup(builder.deleteLater)
        dialog = RecordingReviewDialog([self.sample_event], self.repo)
        self.addCleanup(dialog.deleteLater)
        builder._recording_review_dialog = dialog

        with mock.patch.object(dialog, "build_steps", side_effect=ValueError("변환 에러")):
            with mock.patch.object(QtWidgets.QMessageBox, "warning") as mock_warn:
                builder._finish_smart_recording_review(dialog)
                self.assertTrue(mock_warn.called)

        # Dialog was NOT cleared or deleted on error
        self.assertIs(builder._recording_review_dialog, dialog)

    def test_ai_plan_import_does_not_execute_macro(self):
        builder = BuilderPage(self.repo)
        self.addCleanup(builder.deleteLater)

        run_spy = mock.MagicMock()
        dry_run_spy = mock.MagicMock()
        step_spy = mock.MagicMock()
        builder.run_macro.connect(run_spy)
        builder.run_macro_dry_run.connect(dry_run_spy)
        builder.run_macro_step.connect(step_spy)

        valid_draft = {
            "name": "비실행테스트",
            "steps": [{"action": "wait", "duration": 100}, {"action": "flow_control", "jump_to": 0}],
            "graph_start_step": 1,
            "graph_positions": {"1": [0, 0], "2": [200, 0]},
            "meta": {"ai_plan_schema": VERSION},
        }

        with mock.patch.object(QtWidgets.QMessageBox, "information"):
            builder._create_macro_from_ai_plan(valid_draft)

        self.assertFalse(run_spy.called)
        self.assertFalse(dry_run_spy.called)
        self.assertFalse(step_spy.called)

    def test_sync_desktop_plan(self):
        steps = [{"action": "image_search", "asset": "test_btn", "click_enabled": True, "click": {"offset": [0, 0]}}]
        dialog = AiMacroDialog(self.repo, steps)
        self.addCleanup(dialog.deleteLater)

        export_dir = self.root / "export"
        package = export_recording(steps, "테스트", export_dir, self.repo.asset_path)
        dialog.local_recording = export_dir / "private-recording.json"
        private = json.loads(dialog.local_recording.read_text(encoding="utf-8"))

        plan = {
            "schema": VERSION,
            "recording_id": private["recording_id"],
            "name": "데스크톱플랜",
            "entry": "n1",
            "nodes": [{"id": "n1", "record": "r001", "mode": "check", "success": "STOP", "failure": "STOP"}],
            "missing_images": [],
        }

        mock_desktop = self.root / "Desktop"
        mock_desktop.mkdir(parents=True, exist_ok=True)
        desktop_plan_path = mock_desktop / "plan.json"
        desktop_plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

        with mock.patch("pathlib.Path.home", return_value=self.root):
            dialog.sync_desktop_plan()

        self.assertIsNotNone(dialog.draft)
        self.assertEqual(2, len(dialog.draft["steps"]))
        self.assertTrue(dialog.accept_draft.isEnabled())


if __name__ == "__main__":
    unittest.main()

