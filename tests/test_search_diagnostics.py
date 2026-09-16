import unittest

from macro_studio.search_diagnostics import (
    diagnose_capture_failure,
    diagnose_image_result,
    inactive_click_preflight,
    predicted_click_points,
)


class SearchDiagnosticTests(unittest.TestCase):
    def test_diagnoses_region_miss_separately_from_missing_image(self):
        title, _ = diagnose_image_result({"found": False, "full_found": True, "full_score": 0.94, "threshold": 0.86})
        self.assertEqual("검색 영역 이탈", title)

    def test_inactive_click_never_silently_falls_back_when_target_is_missing(self):
        status = inactive_click_preflight(
            {"click_enabled": True, "click_target": "first_image", "click": {"mode": "inactive", "method": "auto", "window_exe": "game.exe"}},
            0,
        )
        self.assertFalse(status["valid"])
        self.assertIn("활성 클릭으로 대체하지 않고", status["detail"])

    def test_auto_method_shows_ldplayer_postmessage_route(self):
        status = inactive_click_preflight(
            {"click_enabled": True, "click_target": "first_image", "click": {"mode": "inactive", "method": "auto", "window_exe": "dnplayer.exe"}},
            123,
            "LDPlayerMainFrame",
        )
        self.assertTrue(status["valid"])
        self.assertIn("PostMessage", status["detail"])

    def test_click_preview_uses_match_center_and_per_asset_offset(self):
        points = predicted_click_points(
            ["a", "b"],
            {"a": {"found": True, "hit_x": 100, "hit_y": 80}, "b": {"found": True, "hit_x": 30, "hit_y": 40}},
            {"a": [12, -3], "b": [1, 2]},
            "each_image",
        )
        self.assertEqual([(112, 77), (31, 42)], [(p["x"], p["y"]) for p in points])

    def test_capture_failure_names_missing_target(self):
        title, detail = diagnose_capture_failure("대상 창(game.exe)을 찾지 못했습니다.", {"region_window_exe": "game.exe"})
        self.assertEqual("대상 창 없음", title)
        self.assertIn("game.exe", detail)


if __name__ == "__main__":
    unittest.main()
