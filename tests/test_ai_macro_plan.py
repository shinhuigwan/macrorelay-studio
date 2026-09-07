import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from macro_studio.ai_macro_plan import (
    VERSION,
    PlanError,
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


if __name__ == "__main__":
    unittest.main()
