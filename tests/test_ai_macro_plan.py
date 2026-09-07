import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from macro_studio.ai_macro_plan import (
    VERSION,
    PlanError,
    build_auto_plan,
    compile_plan,
    export_recording,
    load_private_recording,
    validate_compiled_draft,
)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.image = self.root / "button.png"
        import base64
        self.image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAACXBIWXMAAA9hAAAPYQGoP6dpAAAAHUlEQVQ4jWP8z8Dwn4ECwESJ5lEDRg0YNWAwGQAAWG0CHpmX3bgAAAAASUVORK5CYII="))
        self.steps = [{"action": "image_search", "asset": "button", "engine": "opencv",
                       "click_enabled": True, "click": {"offset": [7, -4], "click_offset": True},
                       "on_success": 99, "repeat_on_success": True}]
        self.resolver = lambda alias: self.image if alias == "button" else None
        self.package = export_recording(self.steps, "버튼 확인", self.root / "export", self.resolver)
        self.private = json.loads((self.root / "export/private-recording.json").read_text(encoding="utf-8"))
        self.plan = {"schema": VERSION, "recording_id": self.private["recording_id"], "name": "테스트",
                     "entry": "n1", "nodes": [{"id": "n1", "record": "r001", "mode": "check",
                                                "success": "STOP", "failure": "STOP"}], "missing_images": []}

    def test_check_never_clicks_and_edges_end_explicitly(self):
        macro = compile_plan(self.plan, self.private, self.resolver)
        step = macro["steps"][0]
        self.assertFalse(step["click_enabled"])
        self.assertNotIn("click", step)
        self.assertNotIn("repeat_on_success", step)
        self.assertEqual((2, 2), (step["on_success"], step["on_fail"]))
        self.assertEqual(0, macro["steps"][1]["jump_to"])
        self.assertTrue(self.steps[0]["click_enabled"])

    def test_replay_preserves_offset_and_png_bytes(self):
        self.plan["nodes"][0]["mode"] = "replay"
        macro = compile_plan(self.plan, self.private, self.resolver)
        self.assertEqual([7, -4], macro["steps"][0]["click"]["offset"])
        with zipfile.ZipFile(self.package) as archive:
            self.assertNotIn("private-recording.json", archive.namelist())
            self.assertEqual(self.image.read_bytes(), archive.read("images/r001-1.png"))

    def test_wrong_recording_or_missing_image_is_rejected(self):
        self.plan["recording_id"] = "other"
        with self.assertRaises(PlanError):
            compile_plan(self.plan, self.private, self.resolver)
        self.plan["recording_id"] = self.private["recording_id"]
        self.image.unlink()
        with self.assertRaises(PlanError):
            compile_plan(self.plan, self.private, self.resolver)

    def test_bad_links_and_no_exit_loop_are_rejected(self):
        for target in ["unknown", "n1"]:
            self.plan["nodes"][0].update(success=target, failure=target)
            with self.assertRaises(PlanError):
                compile_plan(self.plan, self.private, self.resolver)

    def test_arbitrary_ai_fields_are_rejected(self):
        self.plan["nodes"][0]["command"] = "unexpected"
        with self.assertRaises(PlanError):
            compile_plan(self.plan, self.private, self.resolver)

    def test_needed_assets_do_not_produce_executable_macro(self):
        self.plan["missing_images"] = [{"label": "완료 화면", "reason": "결과 확인"}]
        with self.assertRaises(PlanError):
            compile_plan(self.plan, self.private, self.resolver)

    def test_load_private_recording_valid_and_limits(self):
        priv_path = self.root / "export/private-recording.json"
        loaded = load_private_recording(priv_path)
        self.assertEqual(loaded["recording_id"], self.private["recording_id"])

        huge_path = self.root / "huge-private.json"
        with open(huge_path, "wb") as f:
            f.seek(5_000_001)
            f.write(b"0")
        with self.assertRaises(PlanError) as ctx:
            load_private_recording(huge_path)
        self.assertIn("최대 5MB", str(ctx.exception))

        corrupt_path = self.root / "corrupt-private.json"
        corrupt_path.write_text("{bad json", encoding="utf-8")
        with self.assertRaises(PlanError) as ctx:
            load_private_recording(corrupt_path)
        self.assertIn("JSON 형식", str(ctx.exception))

        bad_schema = dict(self.private, schema="bad-schema")
        bad_schema_path = self.root / "bad-schema.json"
        bad_schema_path.write_text(json.dumps(bad_schema), encoding="utf-8")
        with self.assertRaises(PlanError) as ctx:
            load_private_recording(bad_schema_path)
        self.assertIn("버전", str(ctx.exception))

    def test_validate_compiled_draft_bounds_and_terminal(self):
        valid_draft = compile_plan(self.plan, self.private, self.resolver)
        validate_compiled_draft(valid_draft)

        bad_draft = json.loads(json.dumps(valid_draft))
        bad_draft["graph_start_step"] = 99
        with self.assertRaises(PlanError) as ctx:
            validate_compiled_draft(bad_draft)
        self.assertIn("시작 노드 번호", str(ctx.exception))

        bad_draft = json.loads(json.dumps(valid_draft))
        bad_draft["steps"][0]["on_success"] = 100
        with self.assertRaises(PlanError) as ctx:
            validate_compiled_draft(bad_draft)
        self.assertIn("유효 범위", str(ctx.exception))

        bad_draft = json.loads(json.dumps(valid_draft))
        bad_draft["steps"][1] = {"action": "wait", "duration": 100}
        with self.assertRaises(PlanError) as ctx:
            validate_compiled_draft(bad_draft)
        self.assertIn("flow_control", str(ctx.exception))

        bad_draft = json.loads(json.dumps(valid_draft))
        bad_draft["graph_positions"] = {"not_an_int": [0, 0]}
        with self.assertRaises(PlanError) as ctx:
            validate_compiled_draft(bad_draft)
        self.assertIn("정수", str(ctx.exception))

    def test_build_auto_plan_linear(self):
        plan = build_auto_plan(self.private, purpose="단일 흐름 테스트")
        self.assertEqual(plan["schema"], VERSION)
        self.assertEqual(plan["recording_id"], self.private["recording_id"])
        self.assertEqual(plan["name"], "단일 흐름 테스트")
        self.assertEqual(plan["entry"], "n001")
        self.assertEqual(len(plan["nodes"]), 1)
        self.assertEqual(plan["nodes"][0]["success"], "STOP")

        compiled = compile_plan(plan, self.private, self.resolver)
        self.assertEqual(len(compiled["steps"]), 2)  # 1 step + flow_control

    def test_build_auto_plan_branches_and_supplement(self):
        # Create a multi-workflow private recording mock
        mock_private = {
            "schema": VERSION,
            "recording_id": "test-rec-123",
            "records": {
                "r001": {
                    "step": {"action": "image_search", "asset": "button", "label": "후보 1",
                             "_automation": {"start_workflow_candidate": True}},
                    "images": [{"alias": "button", "file": "images/r001-1.png", "sha256": "fake"}]
                },
                "r002": {
                    "step": {"action": "image_search", "asset": "button", "label": "후보 1 동작",
                             "workflow_id": "wf1"},
                    "images": [{"alias": "button", "file": "images/r002-1.png", "sha256": "fake"}]
                },
                "r003": {
                    "step": {"action": "image_search", "asset": "button", "label": "후보 2",
                             "_automation": {"start_workflow_candidate": True}, "workflow_id": "wf2"},
                    "images": [{"alias": "button", "file": "images/r003-1.png", "sha256": "fake"}]
                },
                "r004": {
                    "step": {"action": "screen_condition", "asset": "button", "label": "보완 상승 확인",
                             "_base_record": "r002"},
                    "images": [{"alias": "button", "file": "images/r004-1.png", "sha256": "fake"}]
                }
            }
        }
        responses = {"req-1": {"records": ["r004"], "note": "상승 화살표 확인 시 보존"}}
        plan = build_auto_plan(mock_private, responses=responses, purpose="분기 매크로")
        node_map = {n["id"]: n for n in plan["nodes"]}

        # Candidate 1 branches to Candidate 2 on failure
        self.assertEqual(node_map["n001"]["failure"], "n003")

        # Candidate 2 is last candidate, fails to STOP
        self.assertEqual(node_map["n003"]["failure"], "STOP")

        # Supplement check node n004 attached to n002
        self.assertEqual(node_map["n002"]["success"], "n004")
        # Note contains "상승" / "보존" -> success is STOP (Preserve item)
        self.assertEqual(node_map["n004"]["success"], "STOP")


if __name__ == "__main__":
    unittest.main()
