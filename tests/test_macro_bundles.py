from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class MacroBundleTests(unittest.TestCase):
    def test_repository_creates_ordered_editable_bundle(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            for name in ("던전", "성장", "상점", "이벤트"):
                repository.create_macro(name)
                child = repository.load_macro(name)
                child["steps"] = [{"action": "wait", "duration": 1}]
                repository.save_macro(name, child)

            path = repository.save_macro_bundle("일일 루틴", ["던전", "성장", "상점", "이벤트"])
            payload = repository.load_macro(path.stem)
            self.assertTrue(payload["meta"]["macro_bundle"])
            self.assertEqual(["던전", "성장", "상점", "이벤트"], payload["meta"]["bundle_items"])
            self.assertEqual([2, 3, 4, 5], [step["on_success"] for step in payload["steps"][:-1]])
            self.assertEqual("flow_control", payload["steps"][-1]["action"])

            repository.save_macro_bundle("일일 루틴", ["상점", "던전"])
            updated = repository.load_macro("일일 루틴")
            self.assertEqual(["상점", "던전"], updated["meta"]["bundle_items"])

    def test_child_terminal_flow_continues_to_next_bundle_item(self) -> None:
        import macro_tool

        with tempfile.TemporaryDirectory() as directory:
            macro_dir = Path(directory)
            repository_root = macro_dir.parent
            child = {
                "name": "던전",
                "steps": [{"action": "flow_control", "label": "던전 종료", "jump_to": 0, "repeat_count": 0}],
            }
            import json
            (macro_dir / "던전.json").write_text(json.dumps(child, ensure_ascii=False), encoding="utf-8")
            parent = {
                "steps": [
                    {"action": "call_submacro", "macro": "던전", "on_success": 2, "on_fail": 2},
                    {"action": "wait", "duration": 1},
                ]
            }
            with mock.patch.object(macro_tool, "MACRO_DIR", macro_dir):
                expanded = macro_tool._expand_macro_steps(parent["steps"])
            self.assertEqual(2, expanded[0]["jump_to"])


if __name__ == "__main__":
    unittest.main()
