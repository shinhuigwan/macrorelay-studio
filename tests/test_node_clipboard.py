import tempfile
import unittest
from pathlib import Path

from macro_studio.node_clipboard import copy_nodes, paste_nodes
from macro_studio.repository import MacroRepository


class NodeClipboardTests(unittest.TestCase):
    def test_internal_links_remap_and_external_links_clear(self):
        source = {
            "steps": [
                {"action": "wait", "on_success": 2, "on_fail": 3,
                 "edge_conditions": [{"target": 2, "kind": "success"}, {"target": 3, "kind": "fail"}],
                 "asset_routes": {"image": {"true": 2, "fail": 3}}},
                {"action": "image_search", "on_success": 3, "on_fail": 1},
                {"action": "wait"},
            ],
            "graph_positions": {"1": [100, 100], "2": [300, 100], "3": [500, 100]},
        }
        target = {"steps": [{"action": "wait"}], "graph_positions": {"1": [0, 0]}}
        added = paste_nodes(target, copy_nodes(source, [1, 2]), 700, 300)
        self.assertEqual([2, 3], added)
        self.assertEqual(3, target["steps"][1]["on_success"])
        self.assertEqual(0, target["steps"][1]["on_fail"])
        self.assertEqual([{"target": 3, "kind": "success"}], target["steps"][1]["edge_conditions"])
        self.assertEqual({"true": 3, "fail": 0}, target["steps"][1]["asset_routes"]["image"])
        self.assertEqual(0, target["steps"][2]["on_success"])
        self.assertEqual(2, target["steps"][2]["on_fail"])
        self.assertEqual([700, 300], target["graph_positions"]["2"])
        self.assertEqual([900, 300], target["graph_positions"]["3"])

    def test_implicit_link_only_kept_inside_selection(self):
        source = {"steps": [{"action": "wait"} for _ in range(3)]}
        target = {"steps": []}
        paste_nodes(target, copy_nodes(source, [1, 2]), 0, 0)
        self.assertEqual(2, target["steps"][0]["on_success"])
        self.assertTrue(target["steps"][1]["stop_on_success"])

    def test_unlinked_pasted_wait_does_not_fall_into_existing_tail(self):
        import macro_tool

        macro = {"steps": [{"action": "wait", "duration": 10, "stop_on_success": True},
                           {"action": "wait", "duration": 20}]}
        script = macro_tool.render_macro_script(macro, {})
        first = script.split("Step1:", 1)[1].split("Step2:", 1)[0]
        self.assertIn("Return", first)


class MacroRenameTests(unittest.TestCase):
    def test_rename_updates_macro_links_and_quickslot(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = MacroRepository(Path(folder))
            repo.save_macro("원본", {"name": "원본", "steps": [{"action": "wait"}]})
            repo.save_macro("호출", {"name": "호출", "steps": [{"action": "call_submacro", "macro": "원본"}]})
            repo.save_hotkeys({"slots": [{"macro": "원본"}]})
            repo._write_json(repo.root / ".quickslot_deck_config.json", {
                "config": {"slot_presets": {"trading": {"slots": [{"macro": "원본"}]}}}
            })
            repo.assign_macro_group(["원본"], "작업")
            repo.duplicate_macro("원본", "복제본")
            self.assertEqual("작업", repo.load_macro_tags()["복제본"])
            repo.rename_macro("원본", "새 이름")
            self.assertFalse(repo.macro_path("원본").exists())
            self.assertEqual("새 이름", repo.load_macro("호출")["steps"][0]["macro"])
            self.assertEqual("새 이름", repo.load_hotkeys()["slots"][0]["macro"])
            deck_config = repo._read_json(repo.root / ".quickslot_deck_config.json", {})
            self.assertEqual("새 이름", deck_config["config"]["slot_presets"]["trading"]["slots"][0]["macro"])
            self.assertEqual("작업", repo.load_macro_tags()["새 이름"])


class MultiRegionSpeedTests(unittest.TestCase):
    def test_nearby_regions_share_capture_and_stop_at_first_match(self):
        import numpy as np
        from vision_engine import VisionState

        engine = VisionState()
        engine._template = lambda _path, _profile: ({"path": "test"}, True)
        engine._modules = lambda: (None, None)
        captures = []
        comparisons = []

        def capture(region, _context, _cache_ms):
            captures.append(region)
            return np.zeros((region[3] - region[1], region[2] - region[0], 3), dtype=np.uint8), False

        def match(frame, _prepared, _threshold, _profile):
            comparisons.append(frame.shape)
            return (0.95, (10, 10), 4, 4), 0.95

        engine._capture = capture
        engine._match = match
        result = engine.search({"image": "test", "regions": [[0, 0, 50, 50], [50, 0, 100, 50]],
                                "threshold": 0.85, "timeout": 0})
        self.assertTrue(result["found"])
        self.assertEqual([(0, 0, 100, 50)], captures)
        self.assertEqual(1, len(comparisons))


if __name__ == "__main__":
    unittest.main()
