from __future__ import annotations

import os
import base64
import json
from copy import deepcopy
import ctypes
import io
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class EngineBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        import macro_tool

        self.engine = macro_tool

    def test_unlinked_steps_fall_through_sequentially(self) -> None:
        macro = {
            "name": "sequence-test",
            "meta": {"coord_mode": "Screen"},
            "steps": [
                {"action": "wait", "duration": 10},
                {"action": "wait", "duration": 20},
            ],
        }
        script = self.engine.render_macro_script(macro, {})
        first = script.split("Step1:", 1)[1].split("Step2:", 1)[0]
        self.assertNotIn("Return", first)
        self.assertIn("Step2:", script)

    def test_only_referenced_tables_are_embedded(self) -> None:
        original = self.engine.load_data_tables
        self.engine.load_data_tables = lambda: {"A": [["keep"]], "B": [["private"]]}
        try:
            macro = {
                "name": "table-test",
                "meta": {"coord_mode": "Screen"},
                "steps": [{"action": "table_store", "table": "A", "row": 1, "col": 1, "value": "x"}],
            }
            script = self.engine.render_macro_script(macro, {})
        finally:
            self.engine.load_data_tables = original
        self.assertIn('Table_Set("A"', script)
        self.assertNotIn('Table_Set("B"', script)
        self.assertNotIn("private", script)

    def test_edge_count_condition_renders_visible_branch_logic(self) -> None:
        macro = {
            "name": "edge-condition-test",
            "meta": {"coord_mode": "Screen"},
            "steps": [
                {
                    "action": "wait",
                    "duration": 10,
                    "on_success": 2,
                    "edge_conditions": [
                        {
                            "kind": "success",
                            "label": "3회 이상은 예외 처리",
                            "source": "edge_count",
                            "operator": ">=",
                            "value": 3,
                            "target": 3,
                            "reset_on_match": True,
                        }
                    ],
                },
                {"action": "wait", "duration": 20},
                {"action": "wait", "duration": 30},
            ],
        }
        script = self.engine.render_macro_script(macro, {})
        self.assertIn('EdgeCounter[__edge_key] += 1', script)
        self.assertIn('if (EdgeCounter[__edge_key] >= 3)', script)
        self.assertIn('Goto, Step3', script)
        self.assertIn('edge condition: 3회 이상은 예외 처리', script)

    def test_ocr_number_variable_drives_dynamic_step_repeat(self) -> None:
        macro = {
            "name": "ocr-repeat",
            "steps": [
                {
                    "action": "ocr",
                    "ocr_action": "extract_number",
                    "value_regex": r"횟수\s*[:=]?\s*(\d+)",
                    "value_group": 1,
                    "store_var": "run_count",
                    "on_success": 2,
                },
                {"action": "wait", "duration": 5, "repeat_var": "run_count"},
            ],
        }
        script = self.engine.render_macro_script(macro, {})
        self.assertIn('""value_regex"": ""횟수\\\\s*[:=]?\\\\s*(\\\\d+)""', script)
        self.assertIn('run_count := OCR_LastNumber', script)
        self.assertIn('__rep_limit2 := Floor(run_count + 0)', script)
        self.assertIn('if (__rep2 < __rep_limit2)', script)
        self.assertIn('dynamic repeat loaded: run_count=', script)
        self.assertIn('REPEAT_VALUE_INVALID', script)

    def test_ocr_regex_group_extracts_keyword_value(self) -> None:
        from ocr_postprocess import extract_regex_value

        text = "현재 횟수: 3회 / 남은 시간 20초"
        self.assertEqual("3", extract_regex_value(text, r"횟수\s*[:=]?\s*(\d+)", 1))
        self.assertIsNone(extract_regex_value(text, r"레벨\s*(\d+)", 1))
        self.assertIsNone(extract_regex_value(text, "(", 1))

    def test_image_search_can_repeat_on_success_until_first_failure(self) -> None:
        macro = {
            "name": "search-until-missing",
            "steps": [
                {
                    "action": "image_search",
                    "asset": "target",
                    "repeat_on_success": True,
                    "repeat_on_success_delay": 25,
                    "on_fail": 2,
                },
                {"action": "wait", "duration": 10},
            ],
        }
        script = self.engine.render_macro_script(macro, {"target": {"file": "target.png"}})
        success_loop = script.index("image search success loop: step 1")
        failure_branch = script.index('TraceStep(1, "image_search", "FAIL")')
        self.assertLess(success_loop, failure_branch)
        self.assertIn("Sleep, 25", script[success_loop:failure_branch])
        self.assertIn("Goto, Step1", script[success_loop:failure_branch])
        self.assertIn("Goto, Step2", script[failure_branch:])

    def test_multi_image_search_uses_one_engine_request_and_forces_opencv(self) -> None:
        step = {
            "action": "image_search",
            "asset": "first",
            "assets": ["first", "second"],
            "engine": "ahk",
            "regions": [[0, 0, 800, 600]],
            "confidence": 82,
            "asset_offsets": {"first": [12, -8], "second": [-30, 44]},
            "click": {"click_offset": True, "click_image": False, "offset": [0, 0]},
        }
        script = "\n".join(
            self.engine.render_image_search(
                step,
                {"first": {"file": "first.png"}, "second": {"file": "second.png"}},
                3,
            )
        )
        self.assertIn('""images"":[', script)
        self.assertIn("MultiImagePath2", script)
        self.assertIn("multi=2", script)
        self.assertIn('MatchedImageIndex := VisionEngine_ParseField(VisionResp, "match_index")', script)
        self.assertIn("if (MatchedImageIndex = 1)", script)
        self.assertIn("MatchedOffsetX := 12", script)
        self.assertIn("MatchedOffsetY := 44", script)
        self.assertIn("ClickX := FoundX + Round(MatchedOffsetX * FoundScaleX)", script)
        self.assertIn("ClickY := FoundY + Round(MatchedOffsetY * FoundScaleY)", script)
        self.assertNotIn("engine=ahk", script)

    def test_vision_engine_multi_search_captures_region_once_and_selects_best(self) -> None:
        import vision_engine

        state = vision_engine.VisionState()
        state._modules = lambda: (object(), object())
        state._template = lambda path, _profile: (
            {"path": path, "canvas_size": (30, 20)},
            True,
        )
        state._match = lambda _frame, prepared, _threshold, _profile: (
            ((0.94, (11, 13), 30, 20), 0.94)
            if prepared["path"].endswith("second.png")
            else ((0.86, (3, 5), 20, 10), 0.86)
        )
        captures: list[tuple[int, int, int, int]] = []

        def fake_capture(left, top, right, bottom, _grabber=None):
            captures.append((left, top, right, bottom))
            return object()

        with mock.patch.object(vision_engine.search, "capture_region", side_effect=fake_capture):
            result = state.search(
                {
                    "images": ["first.png", "second.png"],
                    "regions": [[100, 200, 900, 800]],
                    "threshold": 0.8,
                    "timeout": 0,
                    "profile": "fast",
                }
            )
        self.assertEqual([(100, 200, 900, 800)], captures)
        self.assertTrue(result["found"])
        self.assertEqual(2, result["match_index"])
        self.assertEqual((126, 223), (result["x"], result["y"]))

    def test_image_search_uses_centered_single_click_and_optimized_opencv(self) -> None:
        step = {
            "action": "image_search",
            "asset": "target",
            "engine": "opencv",
            "search_profile": "precise",
            "confidence": 92,
            "timeout": 1200,
            "poll_delay": 60,
            "click": {"click_offset": True, "offset": [24, -12]},
        }
        script = "\n".join(self.engine.render_image_search(step, {"target": {"file": "target.png"}}, 1))
        self.assertIn('" --profile " . OpenCvProfile', script)
        self.assertIn('" --timeout " . OpenCvTimeout', script)
        self.assertIn('OpenCvProfile := "precise"', script)
        self.assertIn("FileDelete, %OpenCvOut%", script)
        self.assertIn('OpenCvOut := A_Temp . "\\MacroRelay_OpenCV_"', script)
        self.assertIn("FileGetSize, OpenCvResultSize", script)

        self.assertIn("OpenCvResultDeadline", script)
        self.assertNotIn("OpenCvNativeHit", script)
        self.assertNotIn("image search fast-path: native exact-size hit", script)
        self.assertIn("VisionEngine_Send(VisionPayload", script)
        self.assertIn("StringSplit, OpenCvParts, OpenCvResult, `,", script)
        self.assertNotIn("OUTPUT_MISSING", script)
        self.assertIn('OpenCvErrorCode = "IMPORT_FAILED"', script)
        self.assertIn("engine=opencv", script)
        self.assertIn('. OpenCvScript .', script)
        command_line = next(line for line in script.splitlines() if "OpenCvCmd :=" in line)
        self.assertNotIn('%OpenCvScript%', command_line)
        self.assertNotIn("????????", script)
        self.assertIn("FoundImageW := OpenCvParts5", script)
        self.assertIn("SourceImageW := FoundImageW", script)
        self.assertIn("FoundScaleX := (SourceImageW > 0 ? FoundImageW / SourceImageW : 1.0)", script)
        self.assertIn("ClickX := FoundX + Round(24 * FoundScaleX)", script)
        self.assertIn("ClickY := FoundY - Round(12 * FoundScaleY)", script)
        self.assertIn("OpenCvSearchStatus := ErrorLevel", script)
        self.assertIn("ErrorLevel := OpenCvSearchStatus", script)
        self.assertIn('FoundX := ""', script)
        self.assertIn('FoundY := ""', script)
        self.assertIn('success without coordinates; treating as not found', script)
        self.assertLess(script.index("OpenCvSearchStatus := ErrorLevel"), script.index("ErrorLevel := OpenCvSearchStatus"))
        self.assertEqual(1, script.count("MouseClick,"))

        two_point_script = "\n".join(
            self.engine.render_image_search(
                {
                    **step,
                    "click": {
                        "click_image": True,
                        "click_offset": True,
                        "offset": [24, -12],
                        "between_click_delay": 80,
                    },
                },
                {"target": {"file": "target.png"}},
                1,
            )
        )
        self.assertEqual(2, two_point_script.count("MouseClick,"))
        self.assertIn("Sleep, 80", two_point_script)
        self.assertIn("points=2", two_point_script)

        inactive_script = "\n".join(
            self.engine.render_image_search(
                {
                    **step,
                    "click": {
                        "mode": "inactive",
                        "method": "controlclick",
                        "window": "ahk_exe whale.exe",
                        "click_offset": True,
                        "offset": [24, -12],
                    },
                },
                {"target": {"file": "target.png"}},
                1,
            )
        )
        self.assertIn('DllCall("ScreenToClient", "ptr", ClickHwnd', inactive_script)
        self.assertIn("FoundClickX := FoundX + Round(24 * FoundScaleX)", inactive_script)
        self.assertIn("FoundClickY := FoundY - Round(12 * FoundScaleY)", inactive_script)
        self.assertIn("ControlClick, x%ClickX% y%ClickY%, ahk_id %ClickHwnd%, , Left, 1, NA", inactive_script)
        self.assertNotIn("NA x%ClickX% y%ClickY%", inactive_script)
        self.assertNotIn("ClickY := FoundY - ClickTop", inactive_script)

        recorded_click_script = "\n".join(
            self.engine.render_inactive_click(
                {
                    "window": "ahk_exe KakaoTalk.exe",
                    "window_exe": "KakaoTalk.exe",
                    "x": 357,
                    "y": 57,
                    "button": "Left",
                    "clicks": 1,
                    "method": "auto",
                    "options": "NA",
                }
            )
        )
        self.assertIn('__click_control := "x" . ClickX . " y" . ClickY', recorded_click_script)
        self.assertIn("ControlClick, %__click_control%, %__click_target%, , %__click_button%", recorded_click_script)
        self.assertIn('InStr(TargetClass, "EVA_")', recorded_click_script)
        self.assertIn('DllCall("WindowFromPoint", "Int64", __point_value, "Ptr")', recorded_click_script)
        self.assertNotIn("%__click_target%, , , %__click_button%", recorded_click_script)

        auto_image_script = "\n".join(
            self.engine.render_image_search(
                {
                    **step,
                    "click": {
                        "mode": "inactive",
                        "method": "auto",
                        "window": "KakaoTalk ahk_exe KakaoTalk.exe",
                        "window_exe": "KakaoTalk.exe",
                    },
                },
                {"target": {"file": "target.png"}},
                1,
            )
        )
        self.assertIn('DllCall("WindowFromPoint", "Int64", PointValue, "Ptr")', auto_image_script)
        self.assertIn("PostMessage, 0x200, 0, %lParam%", auto_image_script)
        self.assertNotIn("Elancia", auto_image_script)

        direct_script = "\n".join(
            self.engine.render_image_search(
                {
                    **step,
                    "click": {
                        "mode": "inactive",
                        "method": "direct_postmessage",
                        "window": "Sample ahk_exe sample.exe",
                        "window_exe": "sample.exe",
                    },
                },
                {"target": {"file": "target.png"}},
                1,
            )
        )
        self.assertIn("DirectPost := 1", direct_script)
        self.assertIn("ClickHwnd := TargetHwnd", direct_script)
        self.assertIn("inactive click direct post: mousemove/down/up sent", direct_script)
        header_without_elevation = "\n".join(
            self.engine.build_macro_header(
                {
                    "name": "background-click",
                    "steps": [
                        {
                            "action": "image_search",
                            "click": {"mode": "inactive", "method": "direct_postmessage"},
                        }
                    ],
                }
            )
        )
        self.assertNotIn("MacroRequiresAdmin", header_without_elevation)
        self.assertNotIn("*RunAs", header_without_elevation)

        handle_script = "\n".join(
            self.engine.render_inactive_click(
                {
                    "window": "KakaoTalk ahk_exe KakaoTalk.exe",
                    "window_exe": "KakaoTalk.exe",
                    "x": 420,
                    "y": 315,
                    "method": "handle_probe",
                    "target_control": "EVA_Window_Dblclk1",
                    "target_hwnd": "0x1234",
                }
            )
        )
        self.assertIn("ControlGet, ManualClickHwnd, Hwnd,, EVA_Window_Dblclk1", handle_script)
        self.assertIn("SavedClickHwnd := 4660", handle_script)
        self.assertIn("ManualChild := 1", handle_script)
        self.assertIn("if (!DirectPost && !ManualChild)", handle_script)
        self.assertIn("inactive click handle engine selected", handle_script)
        self.assertIn("PostMessage, 0x200", handle_script)

        handle_image_script = "\n".join(
            self.engine.render_image_search(
                {
                    **step,
                    "click": {
                        "mode": "inactive",
                        "method": "handle_probe",
                        "window": "KakaoTalk ahk_exe KakaoTalk.exe",
                        "window_exe": "KakaoTalk.exe",
                        "target_control": "EVA_Window_Dblclk1",
                        "target_hwnd": "0x1234",
                    },
                },
                {"target": {"file": "target.png"}},
                1,
            )
        )
        self.assertIn("ControlGet, ManualClickHwnd, Hwnd,, EVA_Window_Dblclk1", handle_image_script)
        self.assertIn('method: handle_probe', handle_image_script)
        self.assertIn("if (!DirectPost && !ManualChild)", handle_image_script)

        ahk_script = "\n".join(
            self.engine.render_image_search({**step, "engine": "ahk"}, {"target": {"file": "target.png"}}, 1)
        )
        self.assertIn("FoundX += Floor(FoundImageW / 2)", ahk_script)
        self.assertEqual(1, ahk_script.count("MouseClick,"))

        header = "\n".join(self.engine.build_macro_header({"name": "dpi-test", "meta": {}}))
        self.assertIn("SetThreadDpiAwarenessContext", header)
        self.assertIn("runtime_packages", header)

        no_offset_script = "\n".join(
            self.engine.render_image_search(
                {
                    "action": "image_search",
                    "asset": "target",
                    "engine": "ahk",
                    "abort_on_fail": False,
                    "click": {"mode": "inactive", "method": "auto", "click_offset": False, "offset": [800, 900]},
                },
                {"target": {"file": "target.png"}},
                1,
            )
        )
        self.assertIn("click skipped: image not found", no_offset_script)
        self.assertIn("image center click: enabled", no_offset_script)
        self.assertNotIn("image offset click: enabled", no_offset_script)
        self.assertNotIn("FoundClickY := FoundY + 900", no_offset_script)

        resized_window_script = "\n".join(
            self.engine.render_image_search(
                {
                    **step,
                    "region_mode": "client",
                    "region_coords": "relative",
                    "regions": [[0, 0, 392, 591]],
                    "fallback_full_region": True,
                },
                {"target": {"file": "target.png"}},
                8,
            )
        )
        self.assertNotIn("region coords fallback: screen", resized_window_script)
        self.assertIn("Max(__img_region_8_base_x", resized_window_script)
        self.assertIn("__img_region_8_base_x + __img_region_8_width - 1", resized_window_script)
        self.assertEqual(2, resized_window_script.count('OpenCvCmd .= " --region "'))

    def test_recorded_client_click_activates_window_and_converts_coordinates(self) -> None:
        step = {
            "action": "mouse_click",
            "window": "ahk_exe whale.exe",
            "window_exe": "whale.exe",
            "window_hwnd": 12345,
            "coordinate_scope": "client",
            "x": 59,
            "y": 278,
            "button": "Left",
            "count": 1,
        }
        script = "\n".join(self.engine.render_mouse_click(step))
        self.assertIn('WinExist("ahk_id 12345")', script)
        self.assertIn("WinActivate, ahk_id %TargetHwnd%", script)
        self.assertIn('DllCall("ClientToScreen"', script)
        self.assertIn("MouseClick, Left, %ClickX%, %ClickY%, 1", script)

    def test_image_search_defaults_to_full_virtual_desktop(self) -> None:
        step = {"action": "image_search", "asset": "target", "engine": "ahk", "timeout": 0}
        script = "\n".join(self.engine.render_image_search(step, {"target": {"file": "target.png"}}, 1))
        self.assertIn("VirtualLeft", script)
        self.assertIn("VirtualTop", script)
        self.assertIn("VirtualRight", script)
        self.assertIn("VirtualBottom", script)

    def test_opencv_result_is_written_atomically(self) -> None:
        import opencv_search

        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result.txt"
            opencv_search.write_result(str(result), "FOUND,10,20,0.9900,30,40\n")
            self.assertEqual("FOUND,10,20,0.9900,30,40\n", result.read_text(encoding="utf-8"))
            self.assertEqual([], list(Path(directory).glob("*.tmp")))

    def test_opencv_multiple_regions_use_one_python_process(self) -> None:
        step = {
            "action": "image_search",
            "asset": "target",
            "engine": "opencv",
            "regions": [[0, 0, 100, 100], [100, 0, 200, 100], [0, 100, 200, 200]],
            "timeout": 900,
        }
        script = "\n".join(self.engine.render_image_search(step, {"target": {"file": "target.png"}}, 4))
        self.assertEqual(1, script.count("RunWait, %OpenCvCmd%"))
        self.assertEqual(3, script.count('OpenCvCmd .= " --region "'))
        self.assertIn("image search regions: 3 · single process", script)
        self.assertIn("OpenCvTimeout := 900", script)
        self.assertIn("opencv best confidence", script)

    def test_generated_macro_writes_structured_result_status(self) -> None:
        macro = {
            "name": "result-status",
            "steps": [
                {
                    "action": "image_search",
                    "asset": "target",
                    "engine": "opencv",
                    "abort_on_fail": False,
                }
            ],
        }
        script = self.engine.render_macro_script(macro, {"target": {"file": "target.png"}})
        self.assertIn("MACRORELAY_RESULT_FILE", script)
        self.assertIn('SetRunResult("RUNNING", "STARTED"', script)
        self.assertIn('SetRunResult("PARTIAL", "IMAGE_NOT_FOUND"', script)
        self.assertIn('SetRunResult("SUCCESS", "COMPLETED"', script)

    def test_generated_macro_reports_final_click_coordinates(self) -> None:
        macro = {
            "name": "click-preview",
            "steps": [
                {
                    "action": "image_search",
                    "asset": "target",
                    "engine": "ahk",
                    "click": {"mode": "active", "click_offset": True, "offset": [18, -9]},
                }
            ],
        }
        script = self.engine.render_macro_script(macro, {"target": {"file": "target.png"}})
        self.assertIn("MACRORELAY_CLICK_FILE", script)
        self.assertIn("SetLastClick(screenX, screenY", script)
        self.assertIn('SetLastClick(ClickX, ClickY, "image")', script)

        inactive = "\n".join(
            self.engine.render_inactive_click(
                {"window": "ahk_exe sample.exe", "x": 10, "y": 20, "method": "auto"}
            )
        )
        self.assertIn('SetLastClick(ScreenX, ScreenY, "inactive")', inactive)

    def test_generated_macro_reports_current_step_progress(self) -> None:
        macro = {
            "name": "progress-test",
            "steps": [{"action": "wait", "duration": 10}, {"action": "wait", "duration": 20}],
        }
        script = self.engine.render_macro_script(macro, {})
        self.assertIn("MACRORELAY_PROGRESS_FILE", script)
        self.assertIn("SetRunProgress(step)", script)
        self.assertIn("SetRunProgress(1)", script)
        self.assertIn("SetRunProgress(2)", script)
        self.assertIn("SetRunProgress(0)", script)

    def test_start_search_candidate_stops_on_success_and_falls_through_on_failure(self) -> None:
        macro = {
            "name": "start-search-group",
            "graph_start_step": 1,
            "steps": [
                {
                    "action": "image_search",
                    "asset": "first",
                    "abort_on_fail": True,
                    "stop_on_success": True,
                    "on_fail": 2,
                },
                {
                    "action": "image_search",
                    "asset": "second",
                    "abort_on_fail": True,
                    "stop_on_success": True,
                },
                {"action": "wait", "duration": 10},
            ],
        }
        script = self.engine.render_macro_script(
            macro,
            {"first": {"file": "first.png"}, "second": {"file": "second.png"}},
        )
        first = script[script.index("if (__step_found_1)"):script.index("; Step 2:")]
        self.assertIn("Goto, Step2", first)
        self.assertIn("    Return", first)

    def test_smart_capture_control_bypasses_countdown_and_excluded_window(self) -> None:
        import smart_recorder

        recorder = smart_recorder.Recorder(Path("unused.jsonl"), exclude_pid=123, delay=20)
        recorder.handle = io.StringIO()
        recorder.emit_control({"type": "capture_request", "window": {"pid": 123}, "vk": smart_recorder.VK_F8})
        payload = recorder.handle.getvalue()
        self.assertIn('"type": "capture_request"', payload)
        self.assertIn('"request_id":', payload)

    def test_smart_recorder_uses_backtick_gate_and_shift_backtick_modes(self) -> None:
        import smart_recorder

        recorder = smart_recorder.Recorder(Path("unused.jsonl"), exclude_pid=0, delay=0)
        recorder.handle = io.StringIO()
        recorder.started -= 1.0
        recorder.emit({"type": "mouse", "x": 10, "y": 20})
        self.assertEqual("", recorder.handle.getvalue())

        key_down = smart_recorder.KBDLLHOOKSTRUCT(
            vkCode=smart_recorder.VK_OEM_3,
            scanCode=0x29,
            flags=0,
            time=0,
            dwExtraInfo=0,
        )
        key_up = smart_recorder.KBDLLHOOKSTRUCT(
            vkCode=smart_recorder.VK_OEM_3,
            scanCode=0x29,
            flags=0,
            time=0,
            dwExtraInfo=0,
        )
        recorder._shift_down = False
        self.assertEqual(
            1,
            recorder._keyboard_proc(
                smart_recorder.HC_ACTION,
                smart_recorder.WM_KEYDOWN,
                ctypes.addressof(key_down),
            ),
        )
        self.assertTrue(recorder.gate_down)
        recorder.emit({"type": "mouse", "x": 30, "y": 40})
        recorded = recorder.handle.getvalue()
        self.assertIn('"type": "gate_state"', recorded)
        self.assertIn('"type": "mouse"', recorded)
        self.assertIn('"record_mode": "action"', recorded)

        self.assertEqual(
            1,
            recorder._keyboard_proc(
                smart_recorder.HC_ACTION,
                smart_recorder.WM_KEYUP,
                ctypes.addressof(key_up),
            ),
        )
        self.assertTrue(recorder.gate_down)

        recorder._shift_down = True
        recorder._keyboard_proc(
            smart_recorder.HC_ACTION,
            smart_recorder.WM_KEYDOWN,
            ctypes.addressof(key_down),
        )
        recorder._keyboard_proc(
            smart_recorder.HC_ACTION,
            smart_recorder.WM_KEYUP,
            ctypes.addressof(key_up),
        )
        self.assertEqual("branch", recorder.record_mode)
        before = recorder.handle.getvalue().count('"type": "mouse"')
        recorder.emit({"type": "mouse", "x": 50, "y": 60})
        branch_recording = recorder.handle.getvalue()
        self.assertEqual(before + 1, branch_recording.count('"type": "mouse"'))
        self.assertIn('"record_mode": "branch"', branch_recording)

        recorder._shift_down = False
        recorder._keyboard_proc(
            smart_recorder.HC_ACTION,
            smart_recorder.WM_KEYDOWN,
            ctypes.addressof(key_down),
        )
        recorder._keyboard_proc(
            smart_recorder.HC_ACTION,
            smart_recorder.WM_KEYUP,
            ctypes.addressof(key_up),
        )
        self.assertFalse(recorder.gate_down)

    def test_recording_drafts_preserve_branch_actions_text_and_waits(self) -> None:
        from macro_studio.automation import recording_drafts

        window = {"hwnd": 10, "exe": "sample.exe"}
        events = [
            {"type": "mouse", "t": 100, "record_mode": "action", "button": "Left", "x": 10, "y": 20, "window": window},
            {"type": "mouse", "t": 1200, "record_mode": "branch", "button": "Left", "x": 30, "y": 40, "window": window},
            {"type": "key", "t": 2300, "record_mode": "branch", "char": "가", "token": "가", "window": window},
        ]
        branch_drafts = [draft for draft in recording_drafts(events) if draft.get("record_mode") == "branch"]
        self.assertEqual(["mouse", "text"], [draft.get("kind") for draft in branch_drafts])

    def test_repository_repairs_legacy_smart_image_search_settings(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("legacy-smart")
            macro = repository.load_macro("legacy-smart")
            macro["steps"] = [
                {
                    "action": "image_search",
                    "asset": "자동녹화-legacy-1",
                    "engine": "ahk",
                    "timeout": 1200,
                    "region_mode": "client",
                    "region_coords": "relative",
                    "region_window": "ahk_exe pythonw.exe",
                    "region_window_exe": "pythonw.exe",
                    "regions": [[0, 0, 1780, 980]],
                    "click": {"mode": "inactive", "window": "ahk_exe pythonw.exe", "window_exe": "pythonw.exe"},
                }
            ]
            repository.save_macro("legacy-smart", macro)
            repaired = repository.load_macro("legacy-smart")["steps"][0]
            self.assertEqual("opencv", repaired["engine"])
            self.assertEqual("fast", repaired["search_profile"])
            self.assertEqual(800, repaired["timeout"])
            self.assertTrue(repaired["fallback_full_region"])
            self.assertEqual("screen", repaired["region_mode"])
            self.assertNotIn("region_window_exe", repaired)
            self.assertEqual("active", repaired["click"]["mode"])

    def test_opencv_reads_image_from_unicode_path(self) -> None:
        import opencv_search

        try:
            import cv2
            import numpy as np
        except Exception as exc:
            self.skipTest(f"full OpenCV binary is unavailable in this test interpreter: {exc}")

        if not hasattr(cv2, "imdecode") or not hasattr(cv2, "imencode"):
            self.skipTest("full OpenCV binary is unavailable in this test interpreter")

        with tempfile.TemporaryDirectory() as directory:
            image_dir = Path(directory) / "한글 경로" / "OB클릭-portable"
            image_dir.mkdir(parents=True)
            image_path = image_dir / "검색 이미지.png"
            expected = np.zeros((7, 9, 3), dtype=np.uint8)
            expected[:, :, 1] = 180
            encoded = cv2.imencode(".png", expected)[1]
            encoded.tofile(str(image_path))

            decoded = opencv_search.read_image_unicode(str(image_path), cv2, np)

            self.assertIsNotNone(decoded)
            self.assertEqual((7, 9, 3), decoded.shape)

    def test_opencv_uses_png_alpha_as_search_mask(self) -> None:
        import opencv_search

        try:
            import cv2
            import numpy as np
        except Exception as exc:
            self.skipTest(f"full OpenCV binary is unavailable in this test interpreter: {exc}")

        frame = np.zeros((80, 120, 3), dtype=np.uint8)
        frame[:] = (15, 110, 190)
        cv2.circle(frame, (57, 35), 8, (20, 220, 250), -1)
        template = np.zeros((30, 30, 3), dtype=np.uint8)
        template[:] = (220, 10, 10)  # deliberately different transparent background
        cv2.circle(template, (15, 15), 8, (20, 220, 250), -1)
        mask = np.zeros((30, 30), dtype=np.uint8)
        cv2.circle(mask, (15, 15), 9, 255, -1)

        prepared = opencv_search.prepare_templates(template, "precise", cv2, mask)
        match, score = opencv_search.match_frame(frame, prepared, 0.80, "precise", cv2, np)

        self.assertIsNotNone(match)
        self.assertGreater(score, 0.90)
        self.assertLessEqual(abs(match[1][0] - 42), 2)
        self.assertLessEqual(abs(match[1][1] - 20), 2)

    def test_opencv_constant_colour_template_does_not_match_everywhere(self) -> None:
        import opencv_search

        try:
            import cv2
            import numpy as np
        except Exception as exc:
            self.skipTest(f"full OpenCV binary is unavailable in this test interpreter: {exc}")

        rng = np.random.default_rng(42)
        frame = rng.integers(0, 90, size=(100, 150, 3), dtype=np.uint8)
        template = np.full((18, 24, 3), (40, 180, 220), dtype=np.uint8)
        expected_x, expected_y = 83, 47
        frame[expected_y : expected_y + 18, expected_x : expected_x + 24] = template
        prepared = opencv_search.prepare_templates(template, "fast", cv2)

        match, score = opencv_search.match_frame(frame, prepared, 0.90, "fast", cv2, np)

        self.assertIsNotNone(match)
        self.assertGreater(score, 0.99)
        self.assertEqual((expected_x, expected_y), match[1])

    def test_opencv_adaptive_standard_match_keeps_exact_coordinates(self) -> None:
        import opencv_search

        try:
            import cv2
            import numpy as np
        except Exception as exc:
            self.skipTest(f"full OpenCV binary is unavailable in this test interpreter: {exc}")

        rng = np.random.default_rng(77)
        frame = rng.integers(0, 256, size=(1000, 1200, 3), dtype=np.uint8)
        expected_x, expected_y = 827, 614
        template = frame[expected_y : expected_y + 54, expected_x : expected_x + 72].copy()
        match, score = opencv_search.adaptive_standard_match(
            frame,
            template,
            None,
            0.90,
            "balanced",
            cv2,
            np,
            {},
        )

        self.assertIsNotNone(match)
        self.assertGreater(score, 0.99)
        self.assertEqual((expected_x, expected_y), match[1])

    def test_opencv_precise_search_refines_scaled_transparent_template(self) -> None:
        import opencv_search

        try:
            import cv2
            import numpy as np
        except Exception as exc:
            self.skipTest(f"full OpenCV binary is unavailable in this test interpreter: {exc}")

        template_height, template_width = 44, 36
        template = np.zeros((template_height, template_width, 3), dtype=np.uint8)
        template[:] = (180, 20, 180)
        mask = np.zeros((template_height, template_width), dtype=np.uint8)
        cv2.ellipse(mask, (18, 21), (12, 17), 0, 0, 360, 255, -1)
        cv2.rectangle(mask, (15, 20), (21, 41), 255, -1)
        template[mask > 0] = (20, 220, 245)
        cv2.circle(template, (14, 16), 4, (30, 80, 220), -1)

        expected_scale = 1.27
        scaled_width = round(template_width * expected_scale)
        scaled_height = round(template_height * expected_scale)
        scaled = cv2.resize(template, (scaled_width, scaled_height), interpolation=cv2.INTER_CUBIC)
        scaled_mask = cv2.resize(mask, (scaled_width, scaled_height), interpolation=cv2.INTER_NEAREST)
        frame = np.zeros((260, 420, 3), dtype=np.uint8)
        frame[:] = (60, 120, 30)
        expected_x, expected_y = 213, 117
        target = frame[expected_y : expected_y + scaled_height, expected_x : expected_x + scaled_width]
        target[scaled_mask > 0] = scaled[scaled_mask > 0]

        cropped_template, cropped_mask, crop_origin, canvas_size = opencv_search.trim_transparent_template(
            template, mask, np
        )
        match, score = opencv_search.adaptive_precise_match(
            frame,
            cropped_template,
            cropped_mask,
            0.80,
            cv2,
            np,
            {},
            crop_origin=crop_origin,
            canvas_size=canvas_size,
        )

        self.assertIsNotNone(match)
        self.assertGreater(score, 0.90)
        self.assertLessEqual(abs(match[1][0] - expected_x), 3)
        self.assertLessEqual(abs(match[1][1] - expected_y), 3)
        self.assertLessEqual(abs(match[2] - scaled_width), 2)
        self.assertLessEqual(abs(match[3] - scaled_height), 2)

    def test_missing_image_asset_is_logged_in_generated_script(self) -> None:
        without_asset = "\n".join(self.engine.render_image_search({"action": "image_search"}, {}, 2))
        missing_asset = "\n".join(
            self.engine.render_image_search({"action": "image_search", "asset": "선택 안 함"}, {}, 3)
        )
        self.assertIn("__step_found_2 := 0", without_asset)
        self.assertIn("no asset selected", without_asset)
        self.assertIn("__step_found_3 := 0", missing_asset)
        self.assertIn("missing asset", missing_asset)

    def test_opencv_macro_uses_persistent_vision_engine_with_cli_fallback(self) -> None:
        macro = {
            "name": "persistent-vision",
            "steps": [
                {
                    "action": "image_search",
                    "asset": "button",
                    "engine": "opencv",
                    "region": [0, 0, 100, 100],
                    "confidence": 88,
                }
            ],
        }
        script = self.engine.render_macro_script(macro, {"button": {"file": "assets/button.png"}})

        self.assertIn("VisionEnginePort := 9235", script)
        self.assertIn("VisionEngine_Send(VisionPayload", script)
        self.assertIn("vision_engine.py", script)
        self.assertIn("one-shot OpenCV fallback", script)
        self.assertIn("RunWait, %OpenCvCmd%", script)
        self.assertIn('""regions"":["', script)
        self.assertNotIn("OpenCvNativeHit", script)
        self.assertLess(script.index('if (VisionResp = "")'), script.index("RunWait, %OpenCvCmd%"))

    def test_opencv_export_copies_persistent_and_fallback_helpers(self) -> None:
        macro = {
            "name": "vision-export",
            "steps": [{"action": "image_search", "asset": "missing", "engine": "opencv"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "vision-export.ahk"
            self.engine.export_macro_payload(macro, target)
            self.assertTrue((target.parent / "vision_engine.py").is_file())
            self.assertTrue((target.parent / "opencv_search.py").is_file())

    def test_multi_image_export_copies_every_selected_asset(self) -> None:
        macro = {
            "steps": [
                {
                    "action": "image_search",
                    "asset": "first",
                    "assets": ["first", "second"],
                    "engine": "opencv",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").mkdir()
            (root / "source" / "first.png").write_bytes(b"first")
            (root / "source" / "second.png").write_bytes(b"second")
            records = {
                "first": {"file": "source/first.png"},
                "second": {"file": "source/second.png"},
            }
            with mock.patch.object(self.engine, "BASE_DIR", root), mock.patch.object(
                self.engine, "read_assets", return_value=records
            ):
                self.engine.copy_assets_for_macro(macro, root / "export")
            self.assertEqual(b"first", (root / "export" / "assets" / "first.png").read_bytes())
            self.assertEqual(b"second", (root / "export" / "assets" / "second.png").read_bytes())

    def test_remote_notify_renderer_remains_available_with_vision_helpers(self) -> None:
        rendered = self.engine.render_remote_notify(
            {"action": "remote_notify", "message": "완료", "include_last_ocr": True}
        )
        self.assertIsInstance(rendered, list)
        self.assertTrue(any("remote_notify.py" in line for line in rendered))
        self.assertTrue(any("OCR_LastText" in line for line in rendered))

    def test_generated_macro_records_structured_step_trace(self) -> None:
        macro = {"name": "trace", "steps": [{"action": "wait", "duration": 10, "label": "짧은 대기"}]}
        script = self.engine.render_macro_script(macro, {})
        self.assertIn("MACRORELAY_TRACE_FILE", script)
        self.assertIn('TraceStep(1, "짧은 대기", "START")', script)
        self.assertIn('TraceStep(1, "짧은 대기", "SUCCESS")', script)


class DiagnosticBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        import macro_tool

        self.engine = macro_tool

    def test_bundle_redacts_remote_credentials_and_excludes_user_assets(self) -> None:
        import zipfile

        from macro_studio.diagnostics import build_diagnostic_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "exports").mkdir()
            (root / "assets").mkdir()
            (root / "exports" / "studio_run.log").write_text(
                'Authorization: Bearer very-secret-token\n{"token":"mobile-token"}', encoding="utf-8"
            )
            (root / "remote_config.json").write_text(
                json.dumps({"relay_url": "https://relay.example", "device_secret": "pc-secret"}), encoding="utf-8"
            )
            (root / "assets" / "private.png").write_bytes(b"private-image")
            destination = root / "diagnostics.zip"

            build_diagnostic_bundle(root, destination)

            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
                combined = "\n".join(
                    archive.read(name).decode("utf-8", errors="replace") for name in names
                )
            self.assertIn("system.json", names)
            self.assertIn("remote_config.sanitized.json", names)
            self.assertNotIn("pc-secret", combined)
            self.assertNotIn("mobile-token", combined)
            self.assertNotIn("very-secret-token", combined)
            self.assertFalse(any("private.png" in name for name in names))

    def test_vision_engine_accepts_newline_request_without_client_shutdown(self) -> None:
        from vision_engine import VisionServer

        server = VisionServer(0)
        port = int(server.server_address[1])
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as client:
                client.settimeout(2.0)
                client.sendall(b'{"cmd":"ping"}\n')
                response = json.loads(client.recv(65536).decode("utf-8"))
            self.assertTrue(response["ok"])
            self.assertTrue(response["running"])
        finally:
            worker.join(timeout=2.0)
            server.server_close()

    def test_generated_helpers_prefer_bundled_python_runtime(self) -> None:
        macro = {
            "name": "portable-python",
            "steps": [
                {"action": "browser_action", "selector": "body"},
                {"action": "ocr", "mode": "region", "region": [0, 0, 10, 10]},
                {"action": "table_excel_read", "table": "sheet", "excel_path": "book.xlsx"},
            ],
        }
        script = self.engine.render_macro_script(macro, {})
        self.assertIn('PythonExe := A_ScriptDir . "\\runtime\\python.exe"', script)
        self.assertIn('MacroPackages := A_ScriptDir . "\\runtime_packages"', script)
        self.assertIn("MACRORELAY_PYTHON_EXE", script)
        self.assertIn("MACRORELAY_PYTHON_PACKAGES", script)
        self.assertIn('"%PythonExe%" "%A_ScriptDir%\\browser_action.py"', script)
        self.assertIn("__python_path := PythonExe", script)
        self.assertEqual(1, script.count(str(Path(sys.executable))))


class ProjectDataTests(unittest.TestCase):
    def setUp(self) -> None:
        from macro_studio.repository import MacroRepository
        from macro_studio.validation import ProjectValidator

        self.repository = MacroRepository(ROOT)
        self.validator = ProjectValidator(self.repository)

    def test_existing_project_loads(self) -> None:
        stats = self.validator.stats()
        self.assertGreaterEqual(stats["macros"], 1)
        self.assertGreaterEqual(stats["steps"], 1)
        self.assertGreaterEqual(stats["assets"], 1)

    def test_opencv_macro_is_blocked_while_component_install_is_running(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("opencv-lock")
            payload = repository.load_macro("opencv-lock")
            payload["steps"] = [{"action": "image_search", "engine": "opencv", "asset": "sample"}]
            repository.save_macro("opencv-lock", payload)
            (repository.root / ".component-installing").write_text("OpenCV 이미지 서치", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "설치하고 있습니다"):
                repository.run_macro("opencv-lock")

    def test_known_data_issues_are_reported(self) -> None:
        from macro_studio.repository import MacroRepository
        from macro_studio.validation import ProjectValidator

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("invalid-data")
            repository.save_macro(
                "invalid-data",
                {
                    "name": "invalid-data",
                    "steps": [
                        {"action": "table_store", "table": "missing-table"},
                        {"action": "image_search", "asset": "missing-image", "regions": [[10, 10, 10, 20]]},
                    ],
                },
            )
            issues = ProjectValidator(repository).validate()
            titles = [issue.title for issue in issues]
            self.assertIn("데이터 테이블 누락", titles)
            self.assertIn("검색 영역이 한 점입니다", titles)

    def test_automation_blocks_round_trip(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            steps = [{"action": "wait", "duration": 300}, {"action": "mouse_click", "x": 10, "y": 20}]
            repository.save_automation_block("로그인 준비", steps, "재사용 테스트")
            loaded = repository.load_automation_blocks()
            self.assertEqual(steps, loaded["로그인 준비"]["steps"])
            self.assertEqual("재사용 테스트", loaded["로그인 준비"]["description"])
            repository.remove_automation_block("로그인 준비")
            self.assertNotIn("로그인 준비", repository.load_automation_blocks())

    def test_single_step_export_sets_start_and_end_without_mutating_macro(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("step-test")
            payload = repository.load_macro("step-test")
            payload["steps"] = [{"action": "wait", "duration": 10}, {"action": "wait", "duration": 20}]
            repository.save_macro("step-test", payload)
            sentinel = object()
            with mock.patch.object(repository, "_launch_macro_payload", return_value=sentinel) as launch:
                result = repository.run_macro_step("step-test", 2)
            self.assertIs(sentinel, result)
            launched_payload = launch.call_args.args[1]
            self.assertEqual(2, launched_payload["graph_start_step"])
            self.assertEqual(2, launched_payload["graph_end_step"])
            self.assertNotIn("graph_start_step", repository.load_macro("step-test"))
            self.assertTrue(launch.call_args.args[2].is_file())

    def test_export_preserves_korean_macro_name_with_spaces(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("자동화 테스트")
            payload = repository.load_macro("자동화 테스트")
            payload["steps"] = [{"action": "wait", "duration": 25}]
            repository.save_macro("자동화 테스트", payload)
            exported = repository.export("자동화 테스트")
            self.assertTrue(exported.is_file())
            self.assertIn("Sleep, 25", exported.read_text(encoding="utf-8-sig"))

    def test_full_run_uses_loaded_korean_payload_without_legacy_name_lookup(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("자동화 테스트")
            payload = repository.load_macro("자동화 테스트")
            payload["steps"] = [{"action": "wait", "duration": 30}]
            repository.save_macro("자동화 테스트", payload)
            sentinel = object()
            with mock.patch.object(repository, "export", side_effect=AssertionError("legacy export must not run")), mock.patch.object(
                repository, "_launch_macro_payload", return_value=sentinel
            ) as launch:
                result = repository.run_macro("자동화 테스트")
            self.assertIs(sentinel, result)
            self.assertEqual("자동화 테스트", launch.call_args.args[0])
            self.assertTrue(launch.call_args.args[2].is_file())

    def test_legacy_f9_f10_navigation_shortcuts_migrate_to_smart_recording(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.save_hotkey_actions({"tab_export": "F9", "tab_hotkey_settings": "F10"})
            saved = repository.load_hotkey_actions()
            self.assertEqual("F9", saved["action_smart_record"])
            self.assertEqual("", saved["tab_export"])
            self.assertEqual("", saved["tab_hotkey_settings"])

    def test_studio_shortcut_settings_round_trip(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            expected = {"tab_macro": "F5", "tab_image": "F6", "action_create_macro": "Shift+F2"}
            repository.save_hotkey_actions(expected)
            self.assertEqual(expected, repository.load_hotkey_actions())

    def test_archived_macro_and_asset_can_be_restored(self) -> None:
        from PySide6 import QtGui
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("undo-macro")
            archived_macro = repository.archive_macro("undo-macro")
            restored_macro = repository.restore_macro(archived_macro, "undo-macro")
            self.assertTrue(restored_macro.is_file())
            self.assertEqual("undo-macro", repository.load_macro("undo-macro")["name"])

            source = root / "source.png"
            image = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#336699"))
            self.assertTrue(image.save(str(source)))
            alias = repository.add_asset(source, "undo-image")
            metadata = dict(repository.load_assets()[alias])
            archived_asset = repository.archive_asset(alias)
            self.assertNotIn(alias, repository.load_assets())
            restored_alias = repository.restore_asset(alias, metadata, archived_asset)
            self.assertEqual(alias, restored_alias)
            self.assertTrue(repository.asset_path(restored_alias).is_file())

    def test_macro_add_order_and_groups_round_trip(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("first")
            repository.create_macro("second")
            repository.assign_macro_group(["second"], "업무")
            self.assertEqual(["first", "second"], [item.name for item in repository.list_macros()])
            self.assertEqual("업무", repository.load_macro_tags()["second"])

    def test_portable_export_creates_folder_zip_manifest_without_overwrite(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("portable-basic")

            def fake_export(_name, output, _browser_fast=False, _runtime_mode="auto"):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("#SingleInstance Force\n", encoding="utf-8")
                return output

            def fake_compile(script):
                executable = script.with_suffix(".exe")
                executable.write_bytes(b"portable-exe")
                return executable

            with mock.patch.object(repository, "export", side_effect=fake_export), mock.patch.object(
                repository, "compile", side_effect=fake_compile
            ):
                first = repository.export_portable("portable-basic", root / "out" / "portable-basic.ahk")
                second = repository.export_portable("portable-basic", root / "out" / "portable-basic.ahk")

            self.assertTrue(first.executable.is_file())
            self.assertTrue(first.archive.is_file())
            self.assertTrue((first.folder / "portable_manifest.json").is_file())
            self.assertTrue((first.folder / "사용방법.txt").is_file())
            self.assertFalse((first.folder / "runtime").exists())
            self.assertNotEqual(first.folder, second.folder)

    def test_single_file_export_wraps_temporary_portable_bundle_and_preserves_existing_file(self) -> None:
        from macro_studio.repository import MacroRepository, PortableExportResult

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("single-basic")
            requested = root / "single-basic.exe"
            requested.write_bytes(b"existing")

            def fake_portable(_name, output, _browser_fast=False, _runtime_mode="auto"):
                folder = output.parent / "single-basic-portable"
                folder.mkdir(parents=True)
                script = folder / "single-basic.ahk"
                executable = folder / "single-basic.exe"
                archive = output.parent / "single-basic-portable.zip"
                script.write_text("#SingleInstance Force\n", encoding="utf-8")
                executable.write_bytes(b"inner-exe")
                archive.write_bytes(b"portable-zip")
                return PortableExportResult(
                    folder=folder,
                    archive=archive,
                    executable=executable,
                    script=script,
                    features=("AutoHotkey EXE",),
                    notes=(),
                )

            def fake_launcher(_payload, output, entrypoint, _macro_name):
                self.assertEqual(str(Path("single-basic-portable") / "single-basic.exe"), entrypoint)
                output.write_bytes(b"single-file-exe")

            with mock.patch.object(repository, "export_portable", side_effect=fake_portable), mock.patch.object(
                repository, "_compile_single_file_launcher", side_effect=fake_launcher
            ):
                result = repository.export_single_file("single-basic", requested)

            self.assertEqual(b"existing", requested.read_bytes())
            self.assertEqual("single-basic-2.exe", result.executable.name)
            self.assertEqual(b"single-file-exe", result.executable.read_bytes())
            self.assertIn("단일 파일 자동 압축 해제", result.features)

    def test_single_file_launcher_source_has_cache_and_zip_path_guards(self) -> None:
        from macro_studio.repository import MacroRepository

        source = MacroRepository._single_file_launcher_source(r"bundle\macro.exe", "macro")
        self.assertIn('"MacroRelay", "Cache"', source)
        self.assertIn("안전하지 않은 압축 경로", source)
        self.assertIn("AddDays(-30)", source)
        self.assertIn(r'bundle\\macro.exe', source)

    def test_portable_requirements_include_opencv_runtime(self) -> None:
        from macro_studio.repository import MacroRepository

        requirements = MacroRepository._portable_requirements(
            [{"action": "image_search", "engine": "opencv", "asset": "target"}]
        )
        self.assertTrue(requirements["python"])
        self.assertIn("cv2", requirements["packages"])
        self.assertIn("numpy", requirements["packages"])
        self.assertIn("mss", requirements["packages"])

    def test_export_runtime_mode_switches_image_engine_and_blocks_python_only_actions(self) -> None:
        import macro_tool

        macro = {"steps": [{"action": "image_search", "engine": "ahk", "asset": "target"}]}
        python_macro = macro_tool.prepare_macro_for_runtime(macro, "python")
        ahk_macro = macro_tool.prepare_macro_for_runtime(
            {"steps": [{"action": "image_search", "engine": "opencv", "asset": "target"}]},
            "ahk",
        )
        self.assertEqual("opencv", python_macro["steps"][0]["engine"])
        self.assertEqual("ahk", ahk_macro["steps"][0]["engine"])
        self.assertEqual("ahk", macro["steps"][0]["engine"], "원본 매크로 설정은 변경하지 않아야 합니다.")
        with self.assertRaisesRegex(ValueError, "Python 필수"):
            macro_tool.prepare_macro_for_runtime({"steps": [{"action": "ocr"}]}, "ahk")
        with self.assertRaisesRegex(ValueError, "멀티 이미지 서치"):
            macro_tool.prepare_macro_for_runtime(
                {
                    "steps": [
                        {"action": "image_search", "asset": "one", "assets": ["one", "two"]}
                    ]
                },
                "ahk",
            )

    def test_browser_portable_requirement_uses_greenlet_package_without_duplicate_binary(self) -> None:
        from macro_studio.repository import MacroRepository

        requirements = MacroRepository._portable_requirements(
            [{"action": "browser_action", "selector": "body"}]
        )
        self.assertIn("greenlet", requirements["packages"])
        self.assertNotIn("_greenlet", requirements["packages"])
        self.assertIn("playwright.sync_api", requirements["imports"])

    def test_portable_package_copy_selects_greenlet_for_current_python_abi(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root / "project")
            wrong = root / "wrong"
            correct = root / "correct"
            (wrong / "greenlet").mkdir(parents=True)
            (correct / "greenlet").mkdir(parents=True)
            abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
            wrong_abi = "cp312" if abi == "cp311" else "cp311"
            (wrong / "greenlet" / "__init__.py").write_text("", encoding="utf-8")
            (wrong / "greenlet" / f"_greenlet.{wrong_abi}-win_amd64.pyd").write_bytes(b"wrong")
            (correct / "greenlet" / "__init__.py").write_text("", encoding="utf-8")
            (correct / "greenlet" / f"_greenlet.{abi}-win_amd64.pyd").write_bytes(b"correct")
            destination = root / "bundle"
            with mock.patch.object(repository, "_portable_package_roots", return_value=[wrong, correct]):
                repository._copy_portable_packages(destination, {"greenlet"})
            self.assertTrue((destination / "greenlet" / f"_greenlet.{abi}-win_amd64.pyd").is_file())
            self.assertFalse((destination / "greenlet" / f"_greenlet.{wrong_abi}-win_amd64.pyd").exists())

    def test_portable_package_copy_selects_numpy_for_copied_python_abi(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root / "project")
            wrong = root / "wrong"
            correct = root / "correct"
            for package_root, abi in ((wrong, "cp311"), (correct, "cp312")):
                core = package_root / "numpy" / "_core"
                core.mkdir(parents=True)
                (package_root / "numpy" / "__init__.py").write_text("", encoding="utf-8")
                (core / f"_multiarray_umath.{abi}-win_amd64.pyd").write_bytes(abi.encode("ascii"))
            destination = root / "bundle"
            with mock.patch.object(repository, "_portable_package_roots", return_value=[wrong, correct]):
                repository._copy_portable_packages(destination, {"numpy"}, "cp312")
            self.assertTrue((destination / "numpy" / "_core" / "_multiarray_umath.cp312-win_amd64.pyd").is_file())
            self.assertFalse((destination / "numpy" / "_core" / "_multiarray_umath.cp311-win_amd64.pyd").exists())


class QuickSlotsRunnerTests(unittest.TestCase):
    def test_qt_hotkeys_convert_to_autohotkey_v1(self) -> None:
        from macro_studio.runner import QuickSlotsRunner

        self.assertEqual("!1", QuickSlotsRunner.to_ahk_hotkey("Alt+1"))
        self.assertEqual("^!Pause", QuickSlotsRunner.to_ahk_hotkey("Ctrl+Alt+Pause"))
        self.assertEqual("#+F12", QuickSlotsRunner.to_ahk_hotkey("Meta+Shift+F12"))

    def test_duplicate_and_emergency_hotkeys_are_rejected(self) -> None:
        from macro_studio.runner import QuickSlotsRunner

        runner = QuickSlotsRunner(self.repository_stub())
        payload = {
            "runner": {"emergency_hotkey": "Ctrl+Alt+Pause"},
            "slots": [
                {"macro": "one", "hotkey": "Alt+1"},
                {"macro": "two", "hotkey": "Alt+1"},
                {"macro": "three", "hotkey": "Ctrl+Alt+Pause"},
            ],
        }
        errors = runner.validate(payload)
        self.assertTrue(any("중복" in error for error in errors))
        self.assertTrue(any("긴급 중지" in error for error in errors))

    @staticmethod
    def repository_stub():
        class RepositoryStub:
            root = ROOT

            @staticmethod
            def macro_path(name: str) -> Path:
                return ROOT / "macros" / f"{name}.json"

        return RepositoryStub()


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def test_all_pages_construct_and_refresh(self) -> None:
        from macro_studio.app import create_app

        app, window = create_app(ROOT)
        for page in ("builder", "assets", "data", "hotkeys", "export", "settings"):
            window.switch_page(page)
            app.processEvents()
        self.assertEqual(6, window.stack.count())
        self.assertNotIn("dashboard", window.pages)
        self.assertIs(window.stack.widget(0), window.pages["builder"])
        self.assertFalse(app.windowIcon().isNull())
        self.assertFalse(window.windowIcon().isNull())
        self.assertEqual(app.windowIcon().cacheKey(), window.windowIcon().cacheKey())
        window.close()

    def test_builder_snapshot_undo_redo_and_collapsible_sidebar(self) -> None:
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("history-test")
            (root / "remote_config.json").write_text('{"enabled": false}', encoding="utf-8")
            app, window = create_app(root)
            builder = window.pages["builder"]
            builder.refresh("history-test")
            original_count = len(builder.current_macro.get("steps") or [])

            builder.current_macro.setdefault("steps", []).append({"action": "wait", "duration": 250})
            builder._persist("테스트 편집")
            self.assertEqual(original_count + 1, len(repository.load_macro("history-test")["steps"]))
            self.assertTrue(builder.undo_edit())
            self.assertEqual(original_count, len(repository.load_macro("history-test")["steps"]))
            self.assertTrue(builder.redo_edit())
            self.assertEqual(original_count + 1, len(repository.load_macro("history-test")["steps"]))

            original_state = window._sidebar_collapsed
            window._toggle_sidebar()
            self.assertNotEqual(original_state, window._sidebar_collapsed)
            self.assertEqual(74 if window._sidebar_collapsed else 260, window.sidebar.width())
            window.close()
            app.processEvents()

    def test_smart_recording_compresses_events_and_diagnostics_fix_connections(self) -> None:
        from macro_studio.automation import AutomationAnalyzer, recording_drafts

        window_info = {"hwnd": 10, "exe": "sample.exe", "title": "Sample", "client_origin": [100, 200]}
        events = [
            {"type": "mouse", "t": 100, "button": "Left", "x": 120, "y": 230, "client_x": 20, "client_y": 30, "window": window_info},
            {"type": "mouse", "t": 280, "button": "Left", "x": 121, "y": 231, "client_x": 21, "client_y": 31, "window": window_info},
            {"type": "key", "t": 520, "char": "가", "token": "가", "window": window_info},
            {"type": "key", "t": 610, "char": "나", "token": "나", "window": window_info},
            {"type": "mouse", "t": 1900, "button": "Right", "x": 300, "y": 400, "client_x": 200, "client_y": 200, "window": window_info},
        ]
        drafts = recording_drafts(events)
        self.assertEqual(2, drafts[0]["count"])
        self.assertTrue(any(item.get("kind") == "text" and item.get("text") == "가나" for item in drafts))
        self.assertTrue(any(item.get("kind") == "wait" for item in drafts))

        macro = {"steps": [{"action": "wait", "duration": 10}, {"action": "wait", "duration": 20}]}
        issues = AutomationAnalyzer.analyze(macro, {})
        fixes = [issue.fix for issue in issues if issue.fix]
        self.assertIn("connect_sequential", fixes)
        self.assertEqual(1, AutomationAnalyzer.apply_fixes(macro, fixes))
        self.assertEqual(2, macro["steps"][0]["on_success"])

        recorded = {
            "steps": [
                {
                    "action": "inactive_click",
                    "window": "ahk_exe whale.exe",
                    "window_exe": "whale.exe",
                    "x": 20,
                    "y": 30,
                    "clicks": 1,
                    "_automation": {"recorded_window": window_info},
                }
            ]
        }
        recorded_issues = AutomationAnalyzer.analyze(recorded, {})
        recorded_fixes = [issue.fix for issue in recorded_issues if issue.fix]
        self.assertIn("recorded_foreground:1", recorded_fixes)
        AutomationAnalyzer.apply_fixes(recorded, recorded_fixes)
        self.assertEqual("mouse_click", recorded["steps"][0]["action"])
        self.assertEqual("client", recorded["steps"][0]["coordinate_scope"])

    def test_automation_replaces_untouched_starter_node(self) -> None:
        from macro_studio.action_editor import action_template
        from macro_studio.builder import _is_unconfigured_template_step

        starter = action_template("inactive_click")
        starter["on_success"] = 2
        self.assertTrue(_is_unconfigured_template_step(starter))
        starter["label"] = "사용자가 설정한 클릭"
        self.assertFalse(_is_unconfigured_template_step(starter))

    def test_recorded_image_strategy_uses_click_time_sample(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordingReviewDialog
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        image = QtGui.QImage(96, 64, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#24A8FF"))
        payload = QtCore.QByteArray()
        buffer = QtCore.QBuffer(payload)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "BMP"))
        buffer.close()
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            event = {
                "type": "mouse",
                "t": 100,
                "button": "Left",
                "x": 120,
                "y": 230,
                "client_x": 20,
                "client_y": 30,
                "image_sample_bmp": base64.b64encode(bytes(payload)).decode("ascii"),
                "window": {"hwnd": 10, "exe": "sample.exe", "client_size": [800, 600]},
            }
            dialog = RecordingReviewDialog([event], repository)
            strategy = dialog.table.cellWidget(0, 4)
            strategy.setCurrentIndex(strategy.findData("image"))
            steps = dialog.build_steps()
            self.assertEqual("image_search", steps[0]["action"])
            self.assertEqual("opencv", steps[0]["engine"])
            self.assertEqual("fast", steps[0]["search_profile"])
            self.assertEqual([[0, 0, 240, 190]], steps[0]["regions"])
            self.assertTrue(steps[0]["fallback_full_region"])
            self.assertIsNotNone(repository.asset_path(steps[0]["asset"]))
            dialog.close()
        app.processEvents()

    def test_smart_recording_defaults_all_clicks_to_background_mode(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.automation import RecordingReviewDialog, _expanded_capture_rect, _window_token
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        event = {
            "type": "mouse",
            "t": 100,
            "button": "Left",
            "x": 320,
            "y": 240,
            "client_x": 120,
            "client_y": 90,
            "window": {"hwnd": 10, "exe": "sample.exe", "title": "Sample", "client_size": [800, 600]},
        }
        with tempfile.TemporaryDirectory() as directory:
            dialog = RecordingReviewDialog([event], MacroRepository(Path(directory)))
            app.processEvents()
            self.assertTrue(dialog.background_clicks.isChecked())
            self.assertEqual("inactive", dialog.table.cellWidget(0, 4).currentData())
            step = dialog.build_steps()[0]
            self.assertEqual("inactive_click", step["action"])
            self.assertEqual("auto", step["method"])
            dialog.background_clicks.setChecked(False)
            self.assertEqual("window", dialog.table.cellWidget(0, 4).currentData())
            dialog.close()
        self.assertEqual("Sample ahk_exe sample.exe", _window_token(event["window"]))
        expanded = _expanded_capture_rect(QtCore.QRect(500, -500, 16, 17), QtCore.QRect(0, -1080, 5120, 2520))
        self.assertGreaterEqual(expanded.width(), 64)
        self.assertGreaterEqual(expanded.height(), 48)
        app.processEvents()

    def test_recording_review_edits_wait_and_removes_multiple_selected_rows(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.automation import RecordingReviewDialog
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            dialog = RecordingReviewDialog([], MacroRepository(Path(directory)))
            dialog.drafts = [
                {"kind": "wait", "duration": 500, "detail": "화면 반응 대기 500 ms"},
                {"kind": "text", "text": "수정할 내용", "detail": "텍스트 입력 · 6자"},
                {"kind": "key", "token": "Enter", "detail": "키 입력 · Enter"},
            ]
            dialog._populate_table()
            wait_editor = dialog.table.cellWidget(0, 2)
            self.assertIsInstance(wait_editor, QtWidgets.QSpinBox)
            wait_editor.setValue(1750)
            self.assertEqual(1750, dialog.drafts[0]["duration"])
            self.assertEqual(1750, dialog.build_steps()[0]["duration"])
            self.assertGreaterEqual(dialog.minimumWidth(), 1180)
            dialog.table.item(1, 0).setCheckState(QtCore.Qt.Unchecked)

            selection = dialog.table.selectionModel()
            flags = QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows
            selection.select(dialog.table.model().index(0, 0), flags)
            selection.select(dialog.table.model().index(2, 0), flags)
            self.assertEqual([0, 2], dialog._selected_rows())
            dialog._remove_selected_rows()
            self.assertEqual(1, len(dialog.drafts))
            self.assertEqual("text", dialog.drafts[0]["kind"])
            self.assertEqual("수정할 내용", dialog.table.item(0, 2).text())
            self.assertEqual(QtCore.Qt.Unchecked, dialog.table.item(0, 0).checkState())
            dialog.close()
        app.processEvents()

    def test_recorded_image_crop_uses_adjustable_click_center_area(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordedImageCropDialog, RecordingReviewDialog
        from macro_studio.image_editor import ScreenCaptureDialog
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        image = QtGui.QImage(360, 240, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#24A8FF"))
        payload = QtCore.QByteArray()
        buffer = QtCore.QBuffer(payload)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "BMP"))
        buffer.close()

        crop_dialog = RecordedImageCropDialog(image)
        self.assertEqual(QtCore.QSize(96, 64), crop_dialog.crop_size())
        crop_dialog._resize_crop(1)
        self.assertEqual(QtCore.QSize(120, 80), crop_dialog.crop_size())
        crop_dialog.shape_combo.setCurrentIndex(crop_dialog.shape_combo.findData("circle"))
        crop_dialog._resize_crop(1)
        self.assertEqual(crop_dialog.crop_size().width(), crop_dialog.crop_size().height())
        selection = QtCore.QRect(100, 80, 120, 90)
        self.assertEqual("move", ScreenCaptureDialog._hit_test(selection, QtCore.QPoint(150, 120)))
        self.assertEqual("nw", ScreenCaptureDialog._hit_test(selection, selection.topLeft()))
        moved = ScreenCaptureDialog._drag_selection(
            "move", selection, QtCore.QPoint(150, 120), QtCore.QPoint(180, 150), QtCore.QRect(0, 0, 360, 240)
        )
        self.assertEqual(QtCore.QPoint(130, 110), moved.topLeft())
        resized = ScreenCaptureDialog._drag_selection(
            "w", selection, selection.topLeft(), QtCore.QPoint(70, 80), QtCore.QRect(0, 0, 360, 240)
        )
        self.assertEqual(70, resized.left())
        self.assertEqual(selection.right(), resized.right())
        crop_dialog.close()

        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            event = {
                "type": "mouse",
                "t": 100,
                "button": "Left",
                "x": 120,
                "y": 230,
                "client_x": 20,
                "client_y": 30,
                "image_sample_bmp": base64.b64encode(bytes(payload)).decode("ascii"),
                "image_anchor": [180, 120],
                "window": {"hwnd": 10, "exe": "sample.exe", "client_size": [800, 600]},
            }
            review = RecordingReviewDialog([event], repository)
            strategy = review.table.cellWidget(0, 4)
            strategy.setCurrentIndex(strategy.findData("image"))
            review.crop_sizes[0] = QtCore.QSize(144, 96)
            steps = review.build_steps()
            saved = QtGui.QImage(str(repository.asset_path(steps[0]["asset"])))
            self.assertEqual(QtCore.QSize(144, 96), saved.size())
            review.crop_rects[0] = QtCore.QRect(10, 20, 80, 60)
            moved_steps = review.build_steps()
            moved_saved = QtGui.QImage(str(repository.asset_path(moved_steps[0]["asset"])))
            self.assertEqual(QtCore.QSize(80, 60), moved_saved.size())
            self.assertTrue(moved_steps[0]["click"]["click_offset"])
            self.assertEqual([131, 71], moved_steps[0]["click"]["offset"])
            review.close()
        app.processEvents()

    def test_recorded_crop_wheel_resizes_nearest_edge_and_center(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordedCropCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        image = QtGui.QImage(400, 300, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#245B7A"))
        canvas = RecordedCropCanvas(image)
        canvas.resize(680, 450)
        canvas.set_crop_rect(QtCore.QRect(140, 110, 100, 80))

        widget_rect = canvas._widget_crop_rect()
        original = QtCore.QRect(canvas.crop_rect)
        self.assertTrue(canvas.resize_from_wheel(QtCore.QPoint(widget_rect.center().x(), widget_rect.top()), 120))
        after_top = QtCore.QRect(canvas.crop_rect)
        self.assertLess(after_top.top(), original.top())
        self.assertEqual(after_top.bottom(), original.bottom())
        self.assertEqual(after_top.width(), original.width())

        widget_rect = canvas._widget_crop_rect()
        before_right = QtCore.QRect(canvas.crop_rect)
        self.assertTrue(canvas.resize_from_wheel(QtCore.QPoint(widget_rect.right(), widget_rect.center().y()), 120))
        after_right = QtCore.QRect(canvas.crop_rect)
        self.assertEqual(after_right.left(), before_right.left())
        self.assertGreater(after_right.right(), before_right.right())
        self.assertEqual(after_right.height(), before_right.height())

        widget_rect = canvas._widget_crop_rect()
        before_center = QtCore.QRect(canvas.crop_rect)
        self.assertTrue(canvas.resize_from_wheel(widget_rect.center(), -120))
        self.assertLess(canvas.crop_rect.width(), before_center.width())
        self.assertLess(canvas.crop_rect.height(), before_center.height())
        canvas.close()
        app.processEvents()

    def test_handle_lab_preserves_stable_classnn_and_session_hwnd(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.inactive_click_lab import HandleCandidate, HandleProbeResult, InactiveClickLabDialog

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        candidate = HandleCandidate(
            hwnd=0x1234,
            class_name="EVA_Window_Dblclk",
            class_nn="EVA_Window_Dblclk1",
            text="",
            rect=QtCore.QRect(100, 100, 500, 400),
            client_point=QtCore.QPoint(25, 30),
            depth=1,
            source="커서 경로",
        )
        result = HandleProbeResult(
            point=QtCore.QPoint(125, 130),
            root_hwnd=0x1000,
            window_token="KakaoTalk ahk_exe KakaoTalk.exe",
            exe_name="KakaoTalk.exe",
            title="KakaoTalk",
            class_name="EVA_Window_Dblclk",
            root_client_point=QtCore.QPoint(20, 25),
            candidates=(candidate,),
        )
        dialog = InactiveClickLabDialog(result)
        self.assertFalse(dialog.test_button.isEnabled())
        self.assertFalse(dialog.use_button.isEnabled())
        dialog.probe = HandleProbeResult(
            point=result.point,
            root_hwnd=0,
            window_token=result.window_token,
            exe_name=result.exe_name,
            title=result.title,
            class_name=result.class_name,
            root_client_point=result.root_client_point,
            candidates=result.candidates,
        )
        dialog.set_test_point(QtCore.QPoint(140, 150))
        self.assertTrue(dialog.test_button.isEnabled())
        self.assertIn("화면 140, 150", dialog.coordinate_label.text())
        dialog.selected_candidate = candidate
        payload = dialog.selected_payload()
        self.assertEqual("handle_probe", payload["method"])
        self.assertEqual("EVA_Window_Dblclk1", payload["target_control"])
        self.assertEqual("0x1234", payload["target_hwnd"])
        self.assertEqual([140, 150], [payload["x"], payload["y"]])
        dialog.close()
        app.processEvents()

    def test_recording_review_saves_tested_handle_and_builds_reusable_click_data(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordingReviewDialog, recording_drafts
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        image = QtGui.QImage(96, 64, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#38CFA2"))
        encoded = QtCore.QByteArray()
        buffer = QtCore.QBuffer(encoded)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        buffer.close()
        event = {
            "type": "mouse",
            "t": 200,
            "x": 420,
            "y": 330,
            "client_x": 220,
            "client_y": 130,
            "button": "Left",
            "image_sample_bmp": base64.b64encode(bytes(encoded)).decode("ascii"),
            "image_sample_size": [96, 64],
            "image_anchor": [48, 32],
            "window": {"hwnd": 10, "title": "Sample", "exe": "sample.exe", "client_size": [900, 700]},
        }
        profile = {
            "window": "Sample ahk_exe sample.exe",
            "window_exe": "sample.exe",
            "x": 55,
            "y": 66,
            "method": "handle_probe",
            "target_control": "Button1",
            "target_hwnd": "0x1234",
            "target_child_class": "Button",
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            dialog = RecordingReviewDialog([event], repository)
            review_buttons = {button.text() for button in dialog.findChildren(QtWidgets.QPushButton)}
            self.assertFalse(any("핸들 테스트" in text or "핸들 실험실" in text for text in review_buttons))
            persisted: list[list[dict]] = []
            dialog.events_changed.connect(lambda events: persisted.append(events))
            dialog._apply_handle_profile(0, profile, "handle_probe")

            self.assertTrue(persisted)
            self.assertEqual(profile, event["_handle_profile"])
            self.assertEqual(profile, recording_drafts([event])[0]["handle_profile"])
            self.assertEqual("handle_probe", dialog.table.cellWidget(0, 4).currentData())
            self.assertIn("Button1", dialog.table.item(0, 3).text())

            fixed_step = dialog.build_steps()[0]
            self.assertEqual("inactive_click", fixed_step["action"])
            self.assertEqual("handle_probe", fixed_step["method"])
            self.assertEqual("Button1", fixed_step["target_control"])
            self.assertEqual([55, 66], [fixed_step["x"], fixed_step["y"]])

            strategy = dialog.table.cellWidget(0, 4)
            strategy.setCurrentIndex(strategy.findData("image"))
            image_step = dialog.build_steps()[0]
            self.assertEqual("image_search", image_step["action"])
            self.assertEqual("handle_probe", image_step["click"]["method"])
            self.assertEqual("Button1", image_step["click"]["target_control"])
            dialog.close()
        app.processEvents()

    def test_manual_recording_capture_is_visible_and_builds_full_image_search_asset(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordingReviewDialog, recording_drafts
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        image = QtGui.QImage(148, 92, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#3ACFA5"))
        payload = QtCore.QByteArray()
        buffer = QtCore.QBuffer(payload)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        buffer.close()
        event = {
            "type": "capture",
            "t": 500,
            "x": 400,
            "y": 300,
            "client_x": 200,
            "client_y": 150,
            "button": "Left",
            "image_sample_bmp": base64.b64encode(bytes(payload)).decode("ascii"),
            "image_sample_size": [148, 92],
            "image_anchor": [74, 46],
            "window": {"hwnd": 10, "exe": "sample.exe", "client_size": [800, 600]},
        }
        self.assertEqual("image_capture", recording_drafts([event])[0]["kind"])
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            dialog = RecordingReviewDialog([event], repository)
            app.processEvents()
            self.assertEqual("image", dialog.table.cellWidget(0, 4).currentData())
            self.assertIsNotNone(dialog.preview_labels[0].pixmap())
            steps = dialog.build_steps()
            self.assertEqual("image_search", steps[0]["action"])
            saved = QtGui.QImage(str(repository.asset_path(steps[0]["asset"])))
            self.assertEqual(QtCore.QSize(148, 92), saved.size())
            dialog.close()
        app.processEvents()

    def test_recording_review_merges_selected_images_into_one_multi_search_node(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordingReviewDialog
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        image = QtGui.QImage(64, 48, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#4ACFA6"))
        payload = QtCore.QByteArray()
        buffer = QtCore.QBuffer(payload)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        buffer.close()
        encoded = base64.b64encode(bytes(payload)).decode("ascii")
        events = [
            {
                "type": "capture",
                "t": 100 + index * 300,
                "x": 400 + index * 20,
                "y": 300,
                "client_x": 200 + index * 20,
                "client_y": 150,
                "image_sample_bmp": encoded,
                "image_sample_size": [64, 48],
                "image_anchor": [32, 24],
                "window": {"hwnd": 10, "exe": "sample.exe", "client_size": [800, 600]},
            }
            for index in range(2)
        ]
        with tempfile.TemporaryDirectory() as directory:
            dialog = RecordingReviewDialog(events, MacroRepository(Path(directory)))
            selection = dialog.table.selectionModel()
            for row in range(2):
                selection.select(
                    dialog.table.model().index(row, 0),
                    QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows,
                )
            dialog._merge_selected_images()
            steps = dialog.build_steps()
            self.assertEqual(1, len(steps))
            self.assertEqual("image_search", steps[0]["action"])
            self.assertEqual("opencv", steps[0]["engine"])
            self.assertEqual(2, len(steps[0]["assets"]))
            self.assertEqual(set(steps[0]["assets"]), set(steps[0]["asset_offsets"]))
            self.assertIn("멀티 이미지 서치", steps[0]["label"])
            dialog.close()
        app.processEvents()

    def test_branch_mode_captures_build_priority_image_fallback_chain(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordingReviewDialog, recording_drafts
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        image = QtGui.QImage(80, 60, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#47CFA4"))
        payload = QtCore.QByteArray()
        buffer = QtCore.QBuffer(payload)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        buffer.close()
        encoded = base64.b64encode(bytes(payload)).decode("ascii")
        window = {"hwnd": 10, "exe": "sample.exe", "client_size": [800, 600]}

        def capture(t: int, mode: str) -> dict:
            return {
                "type": "capture",
                "t": t,
                "record_mode": mode,
                "x": 400,
                "y": 300,
                "client_x": 200,
                "client_y": 150,
                "button": "Left",
                "image_sample_bmp": encoded,
                "image_sample_size": [80, 60],
                "image_anchor": [40, 30],
                "window": window,
            }

        events = [
            capture(100, "action"),
            capture(1600, "branch"),
            capture(3100, "branch"),
            {
                "type": "mouse",
                "t": 3300,
                "record_mode": "action",
                "button": "Left",
                "x": 500,
                "y": 320,
                "client_x": 300,
                "client_y": 170,
                "window": window,
            },
        ]
        drafts = recording_drafts(events)
        self.assertEqual(["action", "branch", "branch", "action"], [draft.get("record_mode") for draft in drafts])
        with tempfile.TemporaryDirectory() as directory:
            dialog = RecordingReviewDialog(events, MacroRepository(Path(directory)))
            app.processEvents()
            self.assertEqual("분기 이미지", dialog.table.item(1, 1).text())
            steps = dialog.build_steps()
            self.assertEqual(4, len(steps))
            self.assertEqual([4, 4, 4], [steps[index].get("on_success") for index in range(3)])
            self.assertEqual(2, steps[0].get("on_fail"))
            self.assertEqual(3, steps[1].get("on_fail"))
            self.assertNotIn("on_fail", steps[2])
            self.assertTrue(steps[2]["abort_on_fail"])
            self.assertEqual("recorded-image-fallback-1", steps[0]["_automation"]["sequential_image_group"])
            dialog.close()
        app.processEvents()

    def test_recording_crop_exposes_detail_editor_and_reuses_edited_template(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordedImageCropDialog, RecordedImageDetailDialog, RecordingReviewDialog, _encoded_png
        from macro_studio.image_editor import ImageEditorDialog
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        source = QtGui.QImage(96, 64, QtGui.QImage.Format_RGB32)
        source.fill(QtGui.QColor("#35CFA1"))
        edited = QtGui.QImage(96, 64, QtGui.QImage.Format_ARGB32)
        edited.fill(QtGui.QColor("#E34D6F"))
        crop = RecordedImageCropDialog(source, source.size())
        self.assertEqual("✨ 이미지 상세 편집", crop.detail_button.text())
        detail = RecordedImageDetailDialog(source)
        self.assertEqual(3, detail.tabs.count())
        self.assertTrue(detail.precise_search_enabled())
        self.assertTrue(any("수동 상세 편집" in button.text() for button in detail.findChildren(QtWidgets.QPushButton)))
        self.assertTrue(any("자동 누끼" in button.text() for button in detail.findChildren(QtWidgets.QPushButton)))
        detail.click_point = QtCore.QPoint(70, 54)
        self.assertEqual(QtCore.QPoint(22, 22), detail.click_offset())
        detail.close()

        transparent = QtGui.QImage(40, 32, QtGui.QImage.Format_ARGB32)
        transparent.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(transparent)
        painter.fillRect(QtCore.QRect(10, 6, 20, 22), QtGui.QColor("#FFE889"))
        painter.end()
        masked_detail = RecordedImageDetailDialog(transparent)
        variants = masked_detail._variants()
        self.assertEqual(0, variants[1].pixelColor(2, 2).alpha())
        self.assertEqual(0, variants[2].pixelColor(2, 2).alpha())
        self.assertGreater(variants[1].pixelColor(20, 16).alpha(), 240)
        self.assertIn("누끼 적용", masked_detail.tabs.tabText(1))
        self.assertIn("누끼 적용", masked_detail.tabs.tabText(2))
        self.assertEqual("✎ 누끼 결과 추가 편집", masked_detail.manual_button.text())
        self.assertIn("추가 편집 가능", masked_detail.info.text())
        masked_detail.close()
        crop.close()

        try:
            import cv2  # noqa: F401
        except Exception as exc:
            self.skipTest(f"full OpenCV binary is unavailable in this test interpreter: {exc}")
        with tempfile.TemporaryDirectory() as editor_directory:
            image_path = Path(editor_directory) / "auto-cutout.png"
            cutout_source = QtGui.QImage(160, 120, QtGui.QImage.Format_RGB32)
            cutout_source.fill(QtGui.QColor("#6A88AF"))
            painter = QtGui.QPainter(cutout_source)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#FFE889"))
            painter.drawEllipse(QtCore.QRect(52, 15, 56, 88))
            painter.setBrush(QtGui.QColor("#15171C"))
            painter.drawRect(10, 12, 24, 12)
            painter.drawRect(126, 12, 24, 12)
            painter.end()
            cutout_source.save(str(image_path), "PNG")
            editor = ImageEditorDialog(image_path, "자동 누끼 테스트", Path(editor_directory) / "history")
            editor.auto_cutout()
            self.assertGreater(editor.image.pixelColor(80, 55).alpha(), 220)
            self.assertLess(editor.image.pixelColor(4, 4).alpha(), 20)
            self.assertEqual(QtCore.QSize(160, 120), editor.image.size())
            editor.close()

        event = {
            "type": "capture",
            "t": 300,
            "x": 400,
            "y": 300,
            "client_x": 200,
            "client_y": 150,
            "button": "Left",
            "image_sample_bmp": _encoded_png(source),
            "image_sample_size": [96, 64],
            "image_anchor": [48, 32],
            "_review_crop_rect": [0, 0, 96, 64],
            "_review_edited_image_bmp": _encoded_png(edited),
            "_review_detail_precise": True,
            "_review_detail_click_offset": [22, 22],
            "window": {"hwnd": 10, "exe": "sample.exe", "client_size": [800, 600]},
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            review = RecordingReviewDialog([event], repository)
            self.assertIn(0, review.detail_images)
            self.assertIn(0, review.detail_precise_rows)
            step = review.build_steps()[0]
            self.assertEqual("precise", step["search_profile"])
            self.assertFalse(step["click"]["click_image"])
            self.assertTrue(step["click"]["click_offset"])
            self.assertEqual([22, 22], step["click"]["offset"])
            saved = QtGui.QImage(str(repository.asset_path(step["asset"])))
            self.assertEqual(QtCore.QSize(96, 64), saved.size())
            self.assertEqual(QtGui.QColor("#E34D6F"), saved.pixelColor(20, 20))
            review.close()
        app.processEvents()

    def test_wait_nodes_support_selected_and_all_duration_changes(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("wait-bulk")
            payload = repository.load_macro("wait-bulk")
            payload["steps"] = [
                {"action": "wait", "duration": 100},
                {"action": "mouse_click", "x": 10, "y": 20},
                {"action": "wait", "duration": 300},
            ]
            repository.save_macro("wait-bulk", payload)
            _created_app, window = create_app(root)
            builder = window.pages["builder"]
            builder.refresh("wait-bulk")
            with mock.patch.object(QtWidgets.QInputDialog, "getInt", return_value=(750, True)):
                builder._set_selected_wait_durations([1, 2, 3])
            steps = builder.current_macro["steps"]
            self.assertEqual([750, 750], [steps[0]["duration"], steps[2]["duration"]])
            self.assertEqual("mouse_click", steps[1]["action"])
            with mock.patch.object(QtWidgets.QInputDialog, "getInt", return_value=(1200, True)):
                builder._set_all_wait_durations()
            self.assertEqual([1200, 1200], [steps[0]["duration"], steps[2]["duration"]])
            self.assertTrue(hasattr(builder.node_canvas, "wait_duration_requested"))
            window.close()
        app.processEvents()

    def test_node_multiselection_survives_inspector_sync_and_deletes_as_batch(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("batch-delete")
            payload = repository.load_macro("batch-delete")
            payload["steps"] = [
                {"action": "wait", "duration": 10},
                {"action": "wait", "duration": 20},
                {"action": "wait", "duration": 30},
                {"action": "wait", "duration": 40},
            ]
            payload["graph_positions"] = {"1": [0, 0], "2": [338, 0], "3": [676, 0], "4": [1014, 0]}
            repository.save_macro("batch-delete", payload)
            _created_app, window = create_app(root)
            window.resize(1500, 900)
            window.show()
            builder = window.pages["builder"]
            builder.refresh("batch-delete")
            emitted: list[int] = []
            builder.node_canvas.node_selected.connect(emitted.append)
            app.processEvents()
            builder.node_canvas.scene.clearSelection()
            view = builder.node_canvas.view
            start = view.mapFromScene(QtCore.QPointF(-30, -30))
            end = view.mapFromScene(QtCore.QPointF(630, 150))
            QTest.mousePress(view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
            QTest.mouseMove(view.viewport(), end, 80)
            QTest.mouseRelease(view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
            app.processEvents()
            self.assertEqual([2], emitted)
            self.assertEqual([1, 2], builder.node_canvas.selected_indexes())
            builder.delete_selected()
            self.assertEqual(2, len(builder.current_macro["steps"]))
            self.assertEqual(2, len(builder.current_macro["meta"]["archived_steps"]))
            window.close()
        app.processEvents()

    def test_new_recording_asset_is_not_lost_by_stale_editor_sources(self) -> None:
        from PySide6 import QtGui, QtWidgets
        from macro_studio.action_editor import ActionEditor
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            editor = ActionEditor(repository)
            editor.refresh_sources()
            image = QtGui.QImage(32, 24, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor("#35D4A4"))
            alias = repository.add_asset_image(image, "just-recorded")
            editor.load_step({"action": "image_search", "asset": alias, "click_enabled": True})
            asset_combo = editor.widgets["image_search"]["asset"]
            self.assertTrue(asset_combo.isEditable())
            self.assertEqual(alias, asset_combo.currentData())
            self.assertEqual(alias, editor.build_step()["asset"])
            editor.close()
        app.processEvents()

    def test_builder_exposes_smart_automation_controls(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("automation-ui")
            app, window = create_app(root)
            window.show()
            builder = window.pages["builder"]
            builder.refresh("automation-ui")
            labels = {button.text() for button in builder.findChildren(QtWidgets.QPushButton)}
            self.assertTrue(any(label.startswith("● 스마트 녹화") and "F9" in label for label in labels))
            self.assertTrue(any(label.startswith("⚡ 자동 설정") for label in labels))
            self.assertTrue(any(label.startswith("✓ 자동 진단") for label in labels))
            self.assertIn("■ 정지", labels)
            self.assertFalse(builder.stop_button.isEnabled())
            self.assertIn("▤ 최근 녹화 검토", labels)
            self.assertIn("⌑ 비활성 클릭 핸들 실험실", labels)
            self.assertIn("⑂ 선택 노드 분기 묶기", labels)
            self.assertGreater(
                builder.inactive_handle_lab_btn.geometry().left(),
                builder.review_recording_btn.geometry().right(),
            )
            self.assertGreater(
                builder.branch_group_btn.geometry().left(),
                builder.inactive_handle_lab_btn.geometry().right(),
            )
            self.assertIn("▶ 선택 단계 테스트", labels)
            self.assertIn("블록 저장", labels)
            self.assertIn("＋ 블록 추가", labels)
            window.close()

    def test_start_search_group_and_selected_success_node_insertion(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("start-search-ui")
            payload = repository.load_macro("start-search-ui")
            payload["steps"] = [
                {"action": "image_search", "asset": f"candidate-{index}", "abort_on_fail": False}
                for index in range(1, 5)
            ]
            payload["graph_positions"] = {
                "1": [0, 0], "2": [0, 160], "3": [0, 320], "4": [0, 480]
            }
            repository.save_macro("start-search-ui", payload)
            app, window = create_app(root)
            builder = window.pages["builder"]
            builder.refresh("start-search-ui")
            builder._configure_start_search_candidates([1, 2, 3, 4])
            grouped = builder.current_macro
            self.assertEqual(1, grouped["graph_start_step"])
            self.assertEqual([1, 2, 3, 4], grouped["start_search_candidates"])
            self.assertEqual([2, 3, 4], [grouped["steps"][index].get("on_fail") for index in range(3)])
            self.assertNotIn("on_fail", grouped["steps"][3])
            self.assertTrue(all(step.get("stop_on_success") for step in grouped["steps"][:4]))
            self.assertEqual((1, 4), builder.node_canvas.start_candidate_position(1))

            wait_index = builder.action_combo.findData("wait")
            self.assertGreaterEqual(wait_index, 0)
            builder.action_combo.setCurrentIndex(wait_index)
            builder.node_canvas.select_node(1)
            builder._add_step()
            self.assertEqual(5, builder.current_macro["steps"][0]["on_success"])
            self.assertEqual([350.0, 0.0], builder.current_macro["graph_positions"]["5"])

            builder.node_canvas.select_node(1)
            builder._add_step()
            self.assertEqual(6, builder.current_macro["steps"][0]["on_success"])
            self.assertEqual(5, builder.current_macro["steps"][5]["on_success"])
            self.assertIn("시작 검색 묶기", {button.text() for button in builder.findChildren(QtWidgets.QPushButton)})
            window.close()
            app.processEvents()

    def test_selected_ocr_nodes_can_be_grouped_as_fallback_branches(self) -> None:
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("ocr-fallback-ui")
            payload = repository.load_macro("ocr-fallback-ui")
            payload["steps"] = [
                {"action": "ocr", "find_text": "첫째"},
                {"action": "ocr", "find_text": "둘째"},
            ]
            repository.save_macro("ocr-fallback-ui", payload)
            app, window = create_app(root)
            builder = window.pages["builder"]
            builder.refresh("ocr-fallback-ui")
            builder._configure_start_search_candidates([1, 2])
            self.assertEqual([1, 2], builder.current_macro["start_search_candidates"])
            self.assertEqual(2, builder.current_macro["steps"][0]["on_fail"])
            self.assertTrue(builder.current_macro["steps"][0]["stop_on_success"])
            window.close()
            app.processEvents()

    def test_recording_bar_is_placed_next_to_main_window(self) -> None:
        from PySide6 import QtGui, QtWidgets
        from macro_studio.automation import RecordingBar

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        screen = QtGui.QGuiApplication.primaryScreen()
        self.assertIsNotNone(screen)
        available = screen.availableGeometry()
        host = QtWidgets.QWidget()
        host.resize(100, 360)
        host.move(available.left() + 10, available.top() + 40)
        host.show()
        bar = RecordingBar(host)
        bar.show()
        app.processEvents()
        bar._position_next_to_studio()
        host_rect = host.frameGeometry()
        self.assertEqual(host_rect.right() + 1, bar.x())
        self.assertEqual(host_rect.top(), bar.y())
        bar.close()
        host.close()
        app.processEvents()

    def test_saved_handle_profile_is_reused_for_future_recording_clicks(self) -> None:
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("profile-reuse")
            app, window = create_app(root)
            builder = window.pages["builder"]
            profile = {
                "window": "Sample ahk_exe sample.exe",
                "window_exe": "sample.exe",
                "x": 10,
                "y": 20,
                "method": "handle_probe",
                "target_control": "Button1",
                "target_hwnd": "0x1234",
                "target_child_class": "Button",
                "target_root_class": "SampleWindow",
            }
            builder._save_inactive_handle_profile(profile)
            self.assertEqual(profile, builder._load_inactive_handle_profiles()[0])
            events = [
                {
                    "type": "mouse",
                    "t": 100,
                    "x": 500,
                    "y": 400,
                    "client_x": 321,
                    "client_y": 222,
                    "window": {
                        "hwnd": 55,
                        "title": "Current Sample",
                        "exe": "sample.exe",
                        "class": "SampleWindow",
                    },
                }
            ]
            prepared = builder._apply_saved_handle_profiles(events)
            reused = prepared[0]["_handle_profile"]
            self.assertEqual("handle_probe", reused["method"])
            self.assertEqual("Button1", reused["target_control"])
            self.assertEqual([321, 222], [reused["x"], reused["y"]])
            self.assertIn("Current Sample", reused["window"])
            self.assertNotIn("_handle_profile", events[0], "원본 녹화 데이터는 직접 변경하지 않아야 합니다.")
            wrong_class = deepcopy(events)
            wrong_class[0]["window"]["class"] = "OtherWindow"
            self.assertNotIn("_handle_profile", builder._apply_saved_handle_profiles(wrong_class)[0])
            window.close()
            app.processEvents()

    def test_shortcuts_are_shown_beside_navigation_and_builder_buttons(self) -> None:
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("shortcut-labels")
            repository.save_hotkey_actions({"tab_macro": "Alt+1", "action_smart_record": "F9"})
            app, window = create_app(root)
            app.processEvents()
            nav = window.nav_buttons["builder"]
            self.assertIn("Alt+1", nav.text() + " " + nav.toolTip())
            self.assertIn("F9", window.pages["builder"].record_btn.text())
            window.close()

    def test_image_search_node_has_hover_preview_badge(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            aliases = []
            for index, colour in enumerate(("#4D9FFF", "#35C89A"), start=1):
                source = root / f"preview-{index}.png"
                image = QtGui.QImage(64, 40, QtGui.QImage.Format_RGB32)
                image.fill(QtGui.QColor(colour))
                self.assertTrue(image.save(str(source)))
                aliases.append(repository.add_asset(source, f"preview-{index}"))
            repository.create_macro("preview-node")
            payload = repository.load_macro("preview-node")
            payload["steps"] = [{"action": "image_search", "asset": aliases[0], "assets": aliases}]
            repository.save_macro("preview-node", payload)
            _created_app, window = create_app(root)
            window.show()
            builder = window.pages["builder"]
            builder.refresh("preview-node")
            badge = builder.node_canvas.nodes[1].preview_badge
            self.assertIsNotNone(badge)
            self.assertEqual("멀티 이미지 서치", builder.node_canvas.nodes[1].display_title)
            self.assertIn("멀티 이미지 서치", builder._step_summary(payload["steps"][0]))
            builder._update_action_summary(payload["steps"][0])
            self.assertTrue(builder.action_summary_label.text().startswith("멀티 이미지 서치"))
            self.assertEqual("▦", badge.text())
            self.assertIn("전체 이미지", badge.toolTip())
            entries = [(alias, repository.asset_path(alias)) for alias in aliases]
            builder.node_canvas.show_image_preview(entries, QtCore.QPoint(100, 100))
            app.processEvents()
            self.assertTrue(builder.node_canvas._preview_popup.isVisible())
            popup_pixmap = builder.node_canvas._preview_popup.image.pixmap()
            self.assertFalse(popup_pixmap.isNull())
            self.assertLessEqual(builder.node_canvas._preview_popup.width(), popup_pixmap.width() + 28)
            self.assertIn("2개", builder.node_canvas._preview_popup.title.text())
            builder.node_canvas.hide_image_preview()
            window.close()
        app.processEvents()

    def test_running_step_is_visually_marked_and_wide_layout_is_applied(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("running-visual")
            payload = repository.load_macro("running-visual")
            payload["steps"] = [{"action": "wait", "duration": 10}, {"action": "wait", "duration": 20}]
            repository.save_macro("running-visual", payload)
            _created_app, window = create_app(root)
            window.show()
            builder = window.pages["builder"]
            builder.refresh("running-visual")
            progress = root / "progress.txt"
            progress.write_text("2", encoding="utf-8")
            self.assertEqual(2, window._read_macro_progress(progress))
            window._set_running_node("running-visual", 2)
            app.processEvents()
            self.assertEqual(2, builder.node_canvas.active_step)
            self.assertEqual(20, builder.node_canvas.nodes[2].zValue())
            self.assertGreaterEqual(window.minimumWidth(), 1120)
            self.assertIn(window.nav_buttons["builder"].parentWidget().width(), {74, 260})
            self.assertTrue(builder.run_button.isVisible())
            builder.set_running_step(0)
            self.assertEqual(0, builder.node_canvas.active_step)
            window.close()
        app.processEvents()

    def test_running_macro_can_be_stopped_from_builder_toolbar(self) -> None:
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        class FakeProcess:
            pid = 424242

            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("stop-loop")
            app, window = create_app(root)
            builder = window.pages["builder"]
            builder.refresh("stop-loop")
            process = FakeProcess()
            window._track_macro_process("stop-loop", process)
            self.assertFalse(builder.run_button.isEnabled())
            self.assertTrue(builder.stop_button.isEnabled())
            with mock.patch.object(window, "_terminate_macro_process") as terminate:
                builder.stop_button.click()
            terminate.assert_called_once_with(process.pid, process)
            self.assertEqual({}, window._running_macro_processes)
            self.assertTrue(builder.run_button.isEnabled())
            self.assertFalse(builder.stop_button.isEnabled())
            self.assertFalse(window._run_monitor.isActive())
            window.close()
        app.processEvents()

    def test_saved_node_graph_positions_are_restored(self) -> None:
        from macro_studio.app import create_app

        app, window = create_app(ROOT)
        builder = window.pages["builder"]
        macro_name = next(
            summary.name
            for summary in builder.repository.list_macros()
            if builder.repository.load_macro(summary.name).get("graph_positions")
        )
        builder.refresh(macro_name)
        app.processEvents()
        steps = builder.current_macro.get("steps") or []
        saved = builder.current_macro.get("graph_positions") or {}
        self.assertEqual(len(steps), len(builder.node_canvas.nodes))
        self.assertGreater(len(saved), 0)
        first_saved_index = min(int(index) for index in saved)
        node_pos = builder.node_canvas.nodes[first_saved_index].pos()
        self.assertAlmostEqual(float(saved[str(first_saved_index)][0]), node_pos.x(), places=1)
        self.assertAlmostEqual(float(saved[str(first_saved_index)][1]), node_pos.y(), places=1)
        window.close()

    def test_dragging_edge_to_empty_canvas_requests_removal(self) -> None:
        from PySide6 import QtCore
        from macro_studio.node_editor import NodeCanvas

        canvas = NodeCanvas()
        canvas.set_macro(
            {
                "steps": [
                    {"action": "wait", "on_success": 2},
                    {"action": "wait"},
                ],
                "graph_positions": {"1": [0, 0], "2": [320, 0]},
            }
        )
        removed: list[tuple[int, int, str]] = []
        canvas.edge_delete_requested.connect(lambda source, target, kind: removed.append((source, target, kind)))
        edge = canvas.edges[0]
        canvas.begin_edge_drag(edge)
        canvas.end_edge_drag(edge, QtCore.QPointF(2000, 2000))
        self.assertEqual([(1, 2, "success")], removed)
        self.assertEqual(0, len(canvas.edges), "저장 신호를 기다리지 않고 화면에서 즉시 제거되어야 합니다.")
        canvas.close()

    def test_builder_restores_action_forms_and_collapses_json(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.action_editor import ACTION_LABELS, ActionEditor, action_template
        from macro_studio.builder import BuilderPage
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            editor = ActionEditor(repository)
            editor.refresh_sources()
            for action in ACTION_LABELS:
                source = action_template(action)
                editor.load_step(source)
                result = editor.build_step()
                self.assertEqual(action, result["action"])
            builder = BuilderPage(repository)
            self.assertFalse(builder.json_panel.isVisible())
            self.assertIn("duration", editor.widgets["wait"])
            self.assertIn("asset", editor.widgets["image_search"])
            self.assertIn("value_regex", editor.widgets["ocr"])
            self.assertIn("value_group", editor.widgets["ocr"])
            self.assertTrue(hasattr(builder, "repeat_var_edit"))
            self.assertNotIn("target_control", editor.widgets["inactive_click"])
            self.assertNotIn("target_hwnd", editor.widgets["inactive_click"])
            inactive_method = editor.widgets["inactive_click"]["method"]
            self.assertLess(inactive_method.findData("handle_probe"), 0)
            self.assertNotIn("click.target_control", editor.widgets["image_search"])
            inactive_buttons = [button.text() for button in editor.pages["inactive_click"].findChildren(QtWidgets.QPushButton)]
            image_buttons = [button.text() for button in editor.pages["image_search"].findChildren(QtWidgets.QPushButton)]
            self.assertFalse(any("핸들 실험실" in text for text in inactive_buttons))
            self.assertFalse(any("핸들 실험실" in text for text in image_buttons))
            self.assertIn("command", editor.widgets["run_program"])
            self.assertEqual("이미지 서치", ACTION_LABELS["image_search"])
            editor.load_step(
                {
                    "action": "image_search",
                    "asset": "sample",
                    "click": {"click_image": True, "click_offset": True, "offset": [35, -20]},
                }
            )
            rebuilt = editor.build_step()
            self.assertTrue(rebuilt["click"]["click_image"])
            self.assertTrue(rebuilt["click"]["click_offset"])
            self.assertEqual([35, -20], rebuilt["click"]["offset"])
            editor.load_step(
                {
                    "action": "inactive_click",
                    "window": "KakaoTalk ahk_exe KakaoTalk.exe",
                    "method": "handle_probe",
                    "target_control": "EVA_Window1",
                    "target_hwnd": "0x1234",
                }
            )
            rebuilt_handle = editor.build_step()
            self.assertEqual("handle_probe", rebuilt_handle["method"])
            self.assertEqual("EVA_Window1", rebuilt_handle["target_control"])
            self.assertEqual("0x1234", rebuilt_handle["target_hwnd"])
            editor.load_step(
                {
                    "action": "image_search",
                    "asset": "sample",
                    "click_enabled": True,
                    "click": {
                        "method": "handle_probe",
                        "target_control": "EVA_Window1",
                        "target_hwnd": "0x1234",
                    },
                }
            )
            rebuilt_image_handle = editor.build_step()["click"]
            self.assertEqual("handle_probe", rebuilt_image_handle["method"])
            self.assertEqual("EVA_Window1", rebuilt_image_handle["target_control"])
            profile = editor.widgets["image_search"]["search_profile"]
            profile.setCurrentIndex(profile.findData("precise"))
            self.assertEqual(6, editor.widgets["image_search"]["variation"].value())
            self.assertEqual(92, editor.widgets["image_search"]["confidence"].value())
            self.assertEqual(90, editor.widgets["image_search"]["poll_delay"].value())
            asset_combo = editor.widgets["image_search"]["asset"]
            asset_combo.setCurrentIndex(0)
            editor.set_action("image_search")
            self.assertEqual("", editor.build_step().get("asset", ""))
            builder._update_action_summary({"action": "image_search", "asset": "sample", "region_mode": "client"})
            self.assertIn("검색 범위: 클라이언트 전체", builder.action_summary_label.text())
            builder._loading = True
            builder.inspector_action.setCurrentIndex(builder.inspector_action.findData("browser_action"))
            builder._loading = False
            self.assertEqual("browser_action", builder.action_combo.currentData())
            editor.load_step(
                {
                    "action": "image_search",
                    "asset": "sample",
                    "regions": [[100, 100, 100, 100], [0, 0, 0, 0]],
                }
            )
            self.assertNotIn("regions", editor.build_step())
            builder.close()
            editor.close()
        app.processEvents()

    def test_action_editor_multi_asset_picker_round_trips_and_forces_opencv(self) -> None:
        from PySide6 import QtGui, QtWidgets
        from macro_studio.action_editor import ActionEditor, MultiAssetPicker
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            for alias, colour in (("첫 이미지", "#33CFAA"), ("둘째 이미지", "#4D9FFF")):
                image = QtGui.QImage(20, 14, QtGui.QImage.Format_RGB32)
                image.fill(QtGui.QColor(colour))
                repository.add_asset_image(image, alias)
            editor = ActionEditor(repository)
            editor.refresh_sources()
            editor.load_step(
                {
                    "action": "image_search",
                    "asset": "첫 이미지",
                    "assets": ["첫 이미지", "둘째 이미지"],
                    "engine": "ahk",
                }
            )
            picker = editor.widgets["image_search"]["assets"]
            self.assertIsInstance(picker, MultiAssetPicker)
            self.assertEqual(["첫 이미지", "둘째 이미지"], picker.value())
            self.assertEqual(["첫 이미지", "둘째 이미지"], picker.preview_aliases)
            self.assertTrue(picker.preview_scroll.isVisibleTo(editor))
            picker.set_offsets({"첫 이미지": [18, -7], "둘째 이미지": [-24, 35]})
            step = editor.build_step()
            self.assertEqual(["첫 이미지", "둘째 이미지"], step["assets"])
            self.assertEqual({"첫 이미지": [18, -7], "둘째 이미지": [-24, 35]}, step["asset_offsets"])
            self.assertEqual("opencv", step["engine"])
            editor.close()
        app.processEvents()

    def test_recent_click_preview_uses_hover_icon_and_click_pin(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.builder import BuilderPage
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            builder = BuilderPage(MacroRepository(Path(directory)))
            builder.resize(1500, 900)
            builder.show()
            app.processEvents()
            source = QtGui.QPixmap(360, 220)
            source.fill(QtGui.QColor("#182233"))
            builder.show_recent_click_preview(source, 1376, 506, "image-inactive")
            app.processEvents()
            button = builder.recent_click_preview_btn
            rendered = button.popup.image.pixmap()
            self.assertIsNotNone(rendered)
            self.assertTrue(button.isEnabled())
            self.assertIn("X 1376, Y 506", button.popup.detail.text())
            hover = QtGui.QEnterEvent(
                QtCore.QPointF(1, 1), QtCore.QPointF(1, 1), QtCore.QPointF(1, 1)
            )
            button.enterEvent(hover)
            app.processEvents()
            self.assertTrue(button.popup.isVisible())
            button.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
            app.processEvents()
            self.assertFalse(button.popup.isVisible())
            button.click()
            button.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
            app.processEvents()
            self.assertTrue(button.isChecked())
            self.assertTrue(button.popup.isVisible())
            button.click()
            self.assertFalse(button.popup.isVisible())
            builder.close()
        app.processEvents()

    def test_detail_dialog_save_persists_step_immediately(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.builder import BuilderPage
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("detail-save")
            payload = repository.load_macro("detail-save")
            payload["steps"] = [{"action": "wait", "duration": 100}]
            repository.save_macro("detail-save", payload)
            builder = BuilderPage(repository)
            builder.refresh("detail-save")
            dialog = mock.Mock()
            dialog.payload.return_value = {"action": "wait", "duration": 875}
            builder._apply_action_settings_dialog(dialog, "detail-save", 0)
            self.assertEqual(875, repository.load_macro("detail-save")["steps"][0]["duration"])
            builder.close()

    def test_offset_editor_supports_zoom_and_far_coordinates(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.action_editor import OffsetEditor, OffsetPointPickerDialog

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        editor = OffsetEditor()
        editor.set_value([850, -620])
        self.assertEqual([850, -620], editor.value())
        self.assertGreaterEqual(editor.canvas.view_range(), 850)
        self.assertEqual(850, editor.x_spin.value())
        self.assertEqual(-620, editor.y_spin.value())
        self.assertGreater(editor.zoom_combo.count(), 3)
        self.assertTrue(any(button.text() == "⌖ 화면에서 2점 지정" for button in editor.findChildren(QtWidgets.QPushButton)))
        picker = OffsetPointPickerDialog()
        picker.reference_point = QtCore.QPoint(120, -300)
        picker.click_point = QtCore.QPoint(185, -255)
        self.assertEqual([65, 45], picker.offset())
        picker.close()
        editor.close()

    def test_settings_restores_optional_component_management(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.repository import MacroRepository
        from macro_studio.settings_page import SettingsPage

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            page = SettingsPage(MacroRepository(Path(directory)))
            page.refresh()
            self.assertGreaterEqual(page.component_table.rowCount(), 6)
            labels = [page.component_table.item(row, 0).text() for row in range(page.component_table.rowCount())]
            self.assertIn("OpenCV 이미지 서치", labels)
            self.assertIn("Windows 자동화", labels)
            self.assertIn("Tesseract OCR", labels)
            page.close()

    def test_macro_folders_can_collapse_expand_and_search(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.builder import BuilderPage
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("folder-one")
            repository.create_macro("folder-two")
            repository.create_macro("outside")
            repository.assign_macro_group(["folder-one", "folder-two"], "업무")
            builder = BuilderPage(repository)
            builder.refresh("folder-one")
            heading = next(
                builder.macro_list.item(index)
                for index in range(builder.macro_list.count())
                if builder.macro_list.item(index).data(QtCore.Qt.UserRole + 3) == "group_header"
                and builder.macro_list.item(index).data(QtCore.Qt.UserRole + 2) == "업무"
            )
            builder._toggle_macro_group(heading)
            self.assertTrue(builder._find_macro_item("folder-one").isHidden())
            self.assertFalse(heading.isHidden())
            self.assertTrue(heading.text().startswith("▸"))
            builder._filter_macros("folder-two")
            self.assertFalse(builder._find_macro_item("folder-two").isHidden())
            builder._filter_macros("")
            builder._toggle_macro_group(heading)
            self.assertFalse(builder._find_macro_item("folder-one").isHidden())
            self.assertTrue(heading.text().startswith("▾"))
            builder.close()

    def test_image_auto_configuration_selects_matching_coordinate_basis(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.action_editor import ActionEditor
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            editor = ActionEditor(MacroRepository(Path(directory)))
            widgets = editor.widgets["image_search"]
            mode = widgets["region_mode"]
            coords = widgets["region_coords"]
            mode.setCurrentIndex(mode.findData("client"))
            self.assertEqual("relative", coords.currentData())
            mode.setCurrentIndex(mode.findData("screen"))
            self.assertEqual("screen", coords.currentData())

            editor._set_field_value("image_search", "region_window", "ahk_id 0x123")
            editor._set_field_value("image_search", "region_window_exe", "sample.exe")
            editor._set_field_value("image_search", "region.0", 10)
            editor._set_field_value("image_search", "region.1", 20)
            editor._set_field_value("image_search", "region.2", 300)
            editor._set_field_value("image_search", "region.3", 400)
            editor._use_full_virtual_screen()
            editor.set_action("image_search")
            full_screen = editor.build_step()
            self.assertEqual("screen", full_screen["region_mode"])
            self.assertEqual("screen", full_screen["region_coords"])
            self.assertNotIn("region", full_screen)
            self.assertNotIn("regions", full_screen)
            self.assertNotIn("region_window", full_screen)
            self.assertNotIn("region_window_exe", full_screen)

            editor._last_capture_target = {
                "window": "ahk_id 0x123",
                "exe": "sample.exe",
                "width": 800,
                "height": 600,
            }
            with mock.patch.object(editor, "_capture_register_asset", return_value="sample"), mock.patch(
                "macro_studio.action_editor.QtWidgets.QMessageBox.information", return_value=0
            ):
                editor._auto_configure_image_search()
            self.assertEqual("client", mode.currentData())
            self.assertEqual("relative", coords.currentData())
            self.assertEqual("sample.exe", widgets["region_window_exe"].text())
            self.assertEqual(799, widgets["region.2"].value())
            self.assertEqual(599, widgets["region.3"].value())
            editor.close()

    def test_run_current_saves_pending_step_before_emitting(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.builder import BuilderPage
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("run-save")
            payload = repository.load_macro("run-save")
            payload["steps"] = [{"action": "wait", "duration": 100}]
            repository.save_macro("run-save", payload)
            builder = BuilderPage(repository)
            builder.refresh("run-save")
            builder.repeat_spin.setValue(3)
            builder.repeat_var_edit.setText("$run_count")
            emitted: list[str] = []
            builder.run_macro.connect(emitted.append)
            builder._run_current()
            self.assertEqual(["run-save"], emitted)
            self.assertEqual(3, repository.load_macro("run-save")["steps"][0]["repeat"])
            self.assertEqual("run_count", repository.load_macro("run-save")["steps"][0]["repeat_var"])
            builder._open_logs()
            self.assertIsNotNone(builder._log_dialog)
            self.assertIn("실행 로그", builder._log_dialog.windowTitle())
            builder._log_dialog.close()
            builder.close()

    def test_smart_recording_notice_can_be_hidden_for_today(self) -> None:
        from PySide6 import QtCore
        from macro_studio.builder import BuilderPage

        settings = QtCore.QSettings("MacroRelay", "Studio")
        previous = settings.value("smart_recording/hide_notice_date", "")
        try:
            settings.setValue(
                "smart_recording/hide_notice_date",
                QtCore.QDate.currentDate().toString(QtCore.Qt.ISODate),
            )
            self.assertTrue(BuilderPage._recording_notice_hidden_today())
            settings.setValue("smart_recording/hide_notice_date", "2000-01-01")
            self.assertFalse(BuilderPage._recording_notice_hidden_today())
        finally:
            settings.setValue("smart_recording/hide_notice_date", previous)

    def test_run_current_blocks_missing_image_asset(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.builder import BuilderPage
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            repository = MacroRepository(Path(directory))
            repository.create_macro("missing-image")
            payload = repository.load_macro("missing-image")
            payload["steps"] = [{"action": "image_search", "asset": "선택 안 함"}]
            repository.save_macro("missing-image", payload)
            builder = BuilderPage(repository)
            builder.refresh("missing-image")
            emitted: list[str] = []
            builder.run_macro.connect(emitted.append)
            with mock.patch("macro_studio.builder.QtWidgets.QMessageBox.warning") as warning:
                builder._run_current()
            self.assertEqual([], emitted)
            warning.assert_called_once()
            self.assertIn("검색 이미지가 선택되지 않았습니다", warning.call_args.args[2])
            builder.close()

    def test_korean_initial_asset_search_and_vertical_canvas_range(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.action_editor import KoreanContainsProxyModel, korean_contains
        from macro_studio.node_editor import NodeCanvas

        self.assertTrue(korean_contains("ㅅㅎㅈㅇ", "상호작용"))
        model = QtCore.QStringListModel(["상호작용", "NAVER"])
        proxy = KoreanContainsProxyModel()
        proxy.setSourceModel(model)
        proxy.set_query("ㅅㅎ")
        self.assertEqual(1, proxy.rowCount())

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.resize(700, 420)
        canvas.show()
        canvas.set_macro({"steps": [{"action": "wait"}]})
        app.processEvents()
        self.assertFalse(hasattr(canvas.view, "minimap"))
        bar = canvas.view.verticalScrollBar()
        self.assertGreater(bar.maximum(), bar.minimum())
        canvas.close()

    def test_spin_boxes_ignore_wheel_changes(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.widgets import WheelSafeSpinBox

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        spin = WheelSafeSpinBox()
        spin.setRange(0, 100)
        spin.setValue(40)
        event = QtGui.QWheelEvent(
            QtCore.QPointF(5, 5),
            QtCore.QPointF(5, 5),
            QtCore.QPoint(),
            QtCore.QPoint(0, 120),
            QtCore.Qt.NoButton,
            QtCore.Qt.NoModifier,
            QtCore.Qt.NoScrollPhase,
            False,
        )
        app.sendEvent(spin, event)
        self.assertEqual(40, spin.value())

    def test_condition_edge_has_separate_label_and_style(self) -> None:
        from macro_studio.node_editor import NodeCanvas

        canvas = NodeCanvas()
        canvas.set_macro(
            {
                "steps": [
                    {
                        "action": "wait",
                        "on_success": 2,
                        "edge_conditions": [
                            {"kind": "success", "source": "edge_count", "operator": ">=", "value": 3, "target": 3}
                        ],
                    },
                    {"action": "wait"},
                    {"action": "wait"},
                ]
            }
        )
        conditional = next(edge for edge in canvas.edges if edge.is_condition)
        self.assertIn("횟수 >= 3", conditional.label.text())
        self.assertEqual(conditional.pen().style().name, "DashLine")
        self.assertTrue(conditional.label.flags() & conditional.label.GraphicsItemFlag.ItemIgnoresTransformations)
        canvas.close()

    def test_assets_reuse_existing_list_when_index_is_unchanged(self) -> None:
        from macro_studio.assets_page import AssetsPage
        from macro_studio.repository import MacroRepository

        page = AssetsPage(MacroRepository(ROOT))
        page.refresh()
        first = page.asset_list.item(0)
        page.refresh()
        self.assertIs(first, page.asset_list.item(0))
        page.close()

    def test_function_shortcuts_live_in_settings_not_quick_slots(self) -> None:
        from macro_studio.app import create_app
        from macro_studio.shortcuts import STUDIO_SHORTCUT_SPECS

        app, window = create_app(ROOT)
        settings = window.pages["settings"]
        quick_slots = window.pages["hotkeys"]
        self.assertEqual(len(STUDIO_SHORTCUT_SPECS), len(settings.shortcut_edits))
        self.assertEqual(1, quick_slots.tabs.count())
        self.assertIn("action_capture_cursor", settings.shortcut_edits)
        self.assertIn("action_smart_record", settings.shortcut_edits)
        self.assertIn("action_quick_automation", settings.shortcut_edits)
        self.assertIn("action_diagnose_automation", settings.shortcut_edits)
        self.assertIn("action_test_selected_step", settings.shortcut_edits)
        window.close()

    def test_delete_and_ctrl_z_restore_selected_macro(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("delete-me")
            app, window = create_app(root)
            window.show()
            builder = window.pages["builder"]
            builder.refresh("delete-me")
            app.processEvents()
            builder.macro_list.setFocus()
            app.processEvents()
            QTest.keyClick(builder.macro_list, QtCore.Qt.Key_Delete)
            app.processEvents()
            self.assertFalse(repository.macro_path("delete-me").exists())

            focus = QtWidgets.QApplication.focusWidget() or window
            QTest.keyClick(focus, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
            app.processEvents()
            self.assertTrue(repository.macro_path("delete-me").exists())
            window.close()

    def test_backward_edges_route_outside_nodes_without_overlapping(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        macro = {
            "steps": [
                {"action": "wait", "duration": 10, "on_success": 2},
                {"action": "wait", "duration": 20, "on_success": 1, "on_fail": 1},
            ],
            "graph_positions": {"1": [0, 0], "2": [360, 0]},
        }
        canvas.set_macro(macro)
        app.processEvents()
        forward = next(edge for edge in canvas.edges if edge.source == 1 and edge.kind == "success")
        success = next(edge for edge in canvas.edges if edge.source == 2 and edge.kind == "success")
        failure = next(edge for edge in canvas.edges if edge.source == 2 and edge.kind == "fail")
        top = min(node.sceneBoundingRect().top() for node in canvas.nodes.values())
        bottom = max(node.sceneBoundingRect().bottom() for node in canvas.nodes.values())
        self.assertEqual("", forward.route_side)
        self.assertEqual("top", success.route_side)
        self.assertEqual("bottom", failure.route_side)
        self.assertLess(success.path().boundingRect().top(), top - 40)
        self.assertGreater(failure.path().boundingRect().bottom(), bottom + 40)
        target_top = canvas.nodes[1].mapToScene(QtCore.QPointF(0, 0)).y()
        target_bottom = canvas.nodes[1].mapToScene(QtCore.QPointF(0, canvas.nodes[1].HEIGHT)).y()
        self.assertAlmostEqual(target_top, success.path().pointAtPercent(1).y(), delta=1.0)
        self.assertAlmostEqual(target_bottom, failure.path().pointAtPercent(1).y(), delta=1.0)
        canvas.close()

    def test_manual_edge_waypoint_is_restored_dragged_and_cleared(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.resize(900, 520)
        canvas.show()
        canvas.set_macro(
            {
                "steps": [{"action": "wait", "on_success": 2}, {"action": "wait"}],
                "graph_positions": {"1": [0, 0], "2": [500, 0]},
                "graph_routes": {"1:success:2:-1": [[360, -140]]},
            }
        )
        edge = canvas.edges[0]
        self.assertEqual([QtCore.QPointF(360, -140)], edge.manual_points)
        self.assertLess(edge.path().boundingRect().top(), -100)
        changed: list[dict] = []
        canvas.routes_changed.connect(changed.append)
        edge.add_manual_point(QtCore.QPointF(430, 120))
        self.assertEqual(2, len(changed[-1]["1:success:2:-1"]))
        edge.clear_manual_points()
        self.assertNotIn("1:success:2:-1", changed[-1])
        edge.setSelected(True)
        app.processEvents()
        self.assertEqual(1, len(edge._waypoint_handles))
        self.assertTrue(edge._waypoint_handles[0].seed)
        handle = edge._waypoint_handles[0]
        start = canvas.view.mapFromScene(handle.scenePos())
        finish = start + QtCore.QPoint(55, 35)
        QTest.mousePress(canvas.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
        QTest.mouseMove(canvas.view.viewport(), finish, 20)
        QTest.mouseRelease(canvas.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, finish)
        app.processEvents()
        self.assertIn("1:success:2:-1", canvas.manual_routes)
        self.assertFalse(edge._waypoint_handles[0].seed)
        edge.setSelected(False)
        app.processEvents()
        edge.setSelected(True)
        app.processEvents()
        self.assertEqual(1, len(edge._waypoint_handles))
        canvas.close()

    def test_manual_edge_routes_follow_node_reindex_and_drop_deleted_links(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.builder import BuilderPage
        from macro_studio.repository import MacroRepository

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            builder = BuilderPage(MacroRepository(Path(directory)))
            builder.current_macro = {
                "steps": [
                    {"action": "wait"},
                    {"action": "wait", "on_success": 1},
                ],
                "graph_routes": {
                    "1:success:2:-1": [[250, -80]],
                    "2:success:1:-1": [[300, 90]],
                },
            }
            builder._remap_graph_routes({1: 2, 2: 1})
            self.assertEqual({"2:success:1:-1": [[250, -80]]}, builder.current_macro["graph_routes"])

            builder.current_macro = {
                "steps": [{"action": "wait"}],
                "graph_routes": {"1:success:2:-1": [[250, -80]]},
            }
            builder._remap_graph_routes({1: 1})
            self.assertNotIn("graph_routes", builder.current_macro)
            builder.close()
        app.processEvents()

    def test_long_forward_edge_avoids_intermediate_node_and_graph_layout_keeps_chain_short(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro(
            {
                "steps": [
                    {"action": "wait", "on_success": 3},
                    {"action": "wait"},
                    {"action": "wait"},
                ],
                "graph_positions": {"1": [0, 0], "2": [350, 0], "3": [700, 0]},
            }
        )
        app.processEvents()
        crossing = next(edge for edge in canvas.edges if edge.source == 1 and edge.target == 3)
        self.assertEqual("top", crossing.route_side)
        self.assertLess(crossing.path().boundingRect().top(), canvas.nodes[2].sceneBoundingRect().top() - 40)

        canvas.set_macro(
            {
                "steps": [
                    {"action": "wait", "on_success": 2},
                    {"action": "wait", "on_success": 3},
                    {"action": "wait"},
                ]
            }
        )
        positions = canvas.positions()
        self.assertLess(positions["1"][0], positions["2"][0])
        self.assertLess(positions["2"][0], positions["3"][0])
        self.assertTrue(all(edge.route_side == "" for edge in canvas.edges))
        canvas.close()

    def test_close_forward_edges_do_not_turn_into_outer_loops(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro(
            {
                "steps": [
                    {"action": "wait", "on_success": 2},
                    {"action": "wait", "on_success": 1},
                    {"action": "wait", "on_success": 2},
                ],
                "graph_positions": {"1": [0, 0], "2": [310, 0], "3": [0, 150]},
            }
        )
        app.processEvents()
        first = next(edge for edge in canvas.edges if edge.source == 1)
        lower = next(edge for edge in canvas.edges if edge.source == 3)
        backward = next(edge for edge in canvas.edges if edge.source == 2)
        self.assertEqual("", first.route_side)
        self.assertEqual("", lower.route_side)
        self.assertEqual("top", backward.route_side)
        self.assertLess(first.path().boundingRect().width(), 45)
        self.assertLess(lower.path().boundingRect().width(), 45)
        self.assertNotEqual(first.target_offset_y, lower.target_offset_y)
        canvas.close()

    def test_drag_style_multi_selection_delete_and_restore_macros(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            for name in ("one", "two", "three"):
                repository.create_macro(name)
            app, window = create_app(root)
            window.show()
            builder = window.pages["builder"]
            builder.refresh("two")
            one = builder._find_macro_item("one")
            two = builder._find_macro_item("two")
            self.assertIsNotNone(one)
            self.assertIsNotNone(two)
            builder.macro_list.clearSelection()
            one.setSelected(True)
            two.setSelected(True)
            builder.macro_list.setFocus()
            app.processEvents()
            QTest.keyClick(builder.macro_list, QtCore.Qt.Key_Delete)
            app.processEvents()
            self.assertFalse(repository.macro_path("one").exists())
            self.assertFalse(repository.macro_path("two").exists())
            QTest.keyClick(QtWidgets.QApplication.focusWidget() or window, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
            app.processEvents()
            self.assertTrue(repository.macro_path("one").exists())
            self.assertTrue(repository.macro_path("two").exists())
            window.close()

    def test_delete_and_ctrl_z_restore_image_and_quick_slot(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from PySide6.QtTest import QTest
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("slot-macro")
            source = root / "selected.png"
            image = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#224466"))
            self.assertTrue(image.save(str(source)))
            repository.add_asset(source, "selected-image")

            app, window = create_app(root)
            window.show()
            window.switch_page("assets")
            assets = window.pages["assets"]
            assets.asset_list.setFocus()
            app.processEvents()
            QTest.keyClick(assets.asset_list, QtCore.Qt.Key_Delete)
            app.processEvents()
            self.assertNotIn("selected-image", repository.load_assets())
            QTest.keyClick(QtWidgets.QApplication.focusWidget() or window, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
            app.processEvents()
            self.assertIn("selected-image", repository.load_assets())

            window.switch_page("hotkeys")
            quick_slots = window.pages["hotkeys"]

            def fake_apply(payload):
                quick_slots.payload = payload
                return True

            quick_slots._save_and_apply = fake_apply
            quick_slots.slots[0] = {"macro": "slot-macro", "hotkey": "Alt+1", "mode": "hybrid"}
            quick_slots._select_slot(0)
            quick_slots.slot_buttons[0].setFocus()
            app.processEvents()
            QTest.keyClick(quick_slots.slot_buttons[0], QtCore.Qt.Key_Delete)
            app.processEvents()
            self.assertEqual("", quick_slots.slots[0]["macro"])
            QTest.keyClick(QtWidgets.QApplication.focusWidget() or window, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
            app.processEvents()
            self.assertEqual("slot-macro", quick_slots.slots[0]["macro"])
            self.assertEqual("Alt+1", quick_slots.slots[0]["hotkey"])
            window.close()

    def test_coordinate_picker_accepts_click_and_f4(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from PySide6.QtTest import QTest
        from macro_studio.action_editor import CoordinatePickerDialog

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        click_picker = CoordinatePickerDialog()
        click_picker.show()
        app.processEvents()
        QTest.mouseClick(click_picker, QtCore.Qt.LeftButton, pos=QtCore.QPoint(80, 90))
        app.processEvents()
        self.assertEqual(QtWidgets.QDialog.Accepted, click_picker.result())

        key_picker = CoordinatePickerDialog()
        key_picker.show()
        app.processEvents()
        QTest.keyClick(key_picker, QtCore.Qt.Key_F4)
        app.processEvents()
        self.assertEqual(QtWidgets.QDialog.Accepted, key_picker.result())

    def test_image_editor_restores_core_editing_tools(self) -> None:
        from PySide6 import QtGui
        from macro_studio.image_editor import ImageEditorDialog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sample.png"
            image = QtGui.QImage(12, 8, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#336699"))
            self.assertTrue(image.save(str(path)))
            dialog = ImageEditorDialog(path, "sample", root / ".history")
            dialog.rotate(90)
            self.assertEqual((8, 12), (dialog.image.width(), dialog.image.height()))
            dialog.undo()
            self.assertEqual((12, 8), (dialog.image.width(), dialog.image.height()))
            dialog.save()
            self.assertTrue(any((root / ".history" / "assets" / "sample").glob("*.png")))

    def test_image_editor_precision_brush_and_connected_colour_cutout(self) -> None:
        from PySide6 import QtCore, QtGui
        from macro_studio.image_editor import ImageEditorDialog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "precision.png"
            image = QtGui.QImage(24, 24, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#336699"))
            painter = QtGui.QPainter(image)
            painter.setPen(QtGui.QPen(QtGui.QColor("#EE3355"), 2))
            painter.setBrush(QtGui.QColor("#336699"))
            painter.drawRect(7, 7, 10, 10)
            painter.end()
            self.assertTrue(image.save(str(path)))

            dialog = ImageEditorDialog(path, "precision", root / ".history")
            dialog.picked_point = QtCore.QPoint(0, 0)
            dialog.picked_color = QtGui.QColor("#336699")
            dialog.tolerance.setValue(0)
            dialog.remove_connected_color()
            self.assertEqual(0, dialog.image.pixelColor(0, 0).alpha())
            self.assertEqual(255, dialog.image.pixelColor(12, 12).alpha())

            dialog.eraser.setChecked(True)
            dialog.eraser_size.setValue(3)
            dialog.brush_point = QtCore.QPoint(12, 12)
            dialog._stamp_brush()
            self.assertEqual(0, dialog.image.pixelColor(12, 12).alpha())
            key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Right, QtCore.Qt.NoModifier)
            dialog.keyPressEvent(key)
            self.assertEqual(QtCore.QPoint(13, 12), dialog.brush_point)
            self.assertFalse(dialog.pick_colour_shortcut.key().isEmpty())
            self.assertFalse(dialog.remove_colour_shortcut.key().isEmpty())
            self.assertFalse(dialog.remove_connected_shortcut.key().isEmpty())
            dialog.close()

    def test_builder_export_button_opens_export_page_with_current_macro(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("export-current")
            app, window = create_app(root)
            window.show()
            builder = window.pages["builder"]
            builder.refresh("export-current")
            buttons = builder.findChildren(QtWidgets.QPushButton)
            detail_button = next(button for button in buttons if button.text() == "상세 설정 창 열기")
            export_button = next(button for button in buttons if button.text() == "⇧ 내보내기")
            action_layout = detail_button.parentWidget().layout()
            self.assertEqual(action_layout.indexOf(detail_button) + 1, action_layout.indexOf(export_button))

            export_button.click()
            app.processEvents()
            export_page = window.pages["export"]
            self.assertIs(window.stack.currentWidget(), export_page)
            self.assertEqual("export-current", export_page.macro_combo.currentText())
            window.close()

    def test_export_path_dialog_starts_with_filename_and_normalizes_extension(self) -> None:
        from macro_studio.app import create_app
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = MacroRepository(root)
            repository.create_macro("path-test")
            app, window = create_app(root)
            export_page = window.pages["export"]
            window.switch_page("export")
            export_page.select_macro("path-test")
            chosen = root / "custom" / "saved-macro"
            with mock.patch(
                "macro_studio.export_page.QtWidgets.QFileDialog.getSaveFileName",
                return_value=(str(chosen), "AutoHotkey (*.ahk)"),
            ) as dialog:
                export_page._browse()
            initial_path = Path(dialog.call_args.args[2])
            self.assertEqual("path-test.ahk", initial_path.name)
            self.assertEqual(str(chosen.with_suffix(".ahk")), export_page.output_edit.text())
            export_page.portable_box.setChecked(True)
            self.assertTrue(export_page.compile_box.isChecked())
            self.assertFalse(export_page.compile_box.isEnabled())
            self.assertTrue(export_page.runtime_combo.isEnabled())
            export_page.runtime_combo.setCurrentIndex(1)
            self.assertEqual("ahk", export_page._runtime_mode())
            self.assertIn("Python은 포함하지", export_page.runtime_hint.text())
            export_page.portable_box.setChecked(False)
            self.assertTrue(export_page.compile_box.isEnabled())
            self.assertFalse(export_page.runtime_combo.isEnabled())
            export_page.single_file_box.setChecked(True)
            self.assertFalse(export_page.portable_box.isChecked())
            self.assertTrue(export_page.compile_box.isChecked())
            self.assertFalse(export_page.compile_box.isEnabled())
            self.assertEqual(".exe", export_page._output_path("single-output").suffix)
            self.assertTrue(export_page._default_output_path().name.endswith("-portable.exe"))
            window.close()


    # --- OCR Engine Tests ---

    def test_ocr_postprocess_similar_chars_correction(self):
        """유사 문자 보정이 올바르게 동작하는지 확인한다."""
        from ocr_postprocess import correct_similar_chars
        # Number mode: O -> 0, l -> 1
        assert correct_similar_chars("1O0l", is_number=True) == "1001"
        # Whitelist mode
        assert correct_similar_chars("AB0", whitelist="ABO") == "ABO"
        # Expect text hint
        assert correct_similar_chars("He1lo", expect_text="Hello") == "Hello"

    def test_ocr_postprocess_text_matching(self):
        """텍스트 매칭 모드가 올바르게 동작하는지 확인한다."""
        from ocr_postprocess import match_text
        assert match_text("Hello World", "Hello", "contains") is True
        assert match_text("Hello World", "hello", "contains") is True  # case insensitive
        assert match_text("Hello World", "Hello World", "exact") is True
        assert match_text("Hello World", "hello world", "exact") is True  # case insensitive
        assert match_text("Hello World", "Goodbye", "contains") is False
        assert match_text("Hello World", "Hello", "starts_with") is True
        assert match_text("Hello World", "World", "ends_with") is True
        assert match_text("Hello 123", r"\d+", "regex") is True
        assert match_text("Hello", r"^\d+$", "regex") is False

    def test_ocr_postprocess_number_extraction(self):
        """숫자 추출이 올바르게 동작하는지 확인한다."""
        from ocr_postprocess import extract_number, check_number_condition
        assert extract_number("가격: 12,500원") == 12500.0
        assert extract_number("레벨 42") == 42.0
        assert extract_number("HP: 85.5%") == 85.5
        assert extract_number("텍스트만") is None
        assert check_number_condition(100, "gte", 50) is True
        assert check_number_condition(100, "lte", 50) is False
        assert check_number_condition(100, "eq", 100) is True
        assert check_number_condition(None, "gte", 0) is False

    def test_ocr_postprocess_merge_results(self):
        """여러 OCR 결과 병합이 올바르게 동작하는지 확인한다."""
        from ocr_postprocess import merge_results, OcrCandidate, OcrBox
        c1 = OcrCandidate(
            text="확인", normalized_text="확인", confidence=0.95,
            boxes=[OcrBox(text="확인", confidence=0.95, rect=(10,10,50,30), center=(30,20))],
            engine="paddle", elapsed_ms=40.0, profile="auto",
        )
        c2 = OcrCandidate(
            text="확 인", normalized_text="확 인", confidence=0.80,
            boxes=[OcrBox(text="확 인", confidence=0.80, rect=(10,10,50,30), center=(30,20))],
            engine="tesseract", elapsed_ms=120.0, profile="auto",
        )
        result = merge_results([c1, c2])
        assert result.success is True
        assert result.confidence >= 0.9  # Should pick higher confidence
        assert result.engine == "paddle"

    def test_ocr_merge_prefers_complete_korean_sentence_over_short_fragment(self):
        from ocr_postprocess import OcrCandidate, merge_results

        fragment = OcrCandidate(
            text="한", normalized_text="한", confidence=0.99, boxes=[],
            engine="fragment", elapsed_ms=10.0, profile="precise",
        )
        sentence = OcrCandidate(
            text="한글 인식 테스트", normalized_text="한글 인식 테스트", confidence=0.88, boxes=[],
            engine="sentence", elapsed_ms=20.0, profile="precise",
        )
        result = merge_results([fragment, sentence], lang="eng+kor")
        self.assertEqual("sentence", result.engine)
        self.assertEqual("한글 인식 테스트", result.text)

    def test_ocr_merge_prefers_candidate_that_contains_requested_text(self):
        from ocr_postprocess import OcrCandidate, merge_results

        wrong = OcrCandidate("취소", "취소", 0.99, [], "wrong", 5.0, "auto")
        right = OcrCandidate("설정 저장", "설정 저장", 0.78, [], "right", 8.0, "auto")
        result = merge_results(
            [wrong, right], find_text="설정", match_mode="contains", lang="eng+kor"
        )
        self.assertTrue(result.success)
        self.assertEqual("right", result.engine)

    def test_ocr_postprocess_normalize_text(self):
        """텍스트 정규화가 올바르게 동작하는지 확인한다."""
        from ocr_postprocess import normalize_text
        assert normalize_text("  hello  \r\n  world  \r\n") == "hello\nworld"
        assert normalize_text("") == ""
        assert normalize_text("   \n   \n   ") == ""
        assert normalize_text("한글\u200b 인식") == "한글 인식"

    def test_ocr_engine_json_protocol(self):
        """OCR 엔진의 JSON 프로토콜 형식이 올바른지 확인한다."""
        from ocr_engine import EngineState, handle_ping, handle_status
        state = EngineState(idle_timeout=0)
        ping_resp = handle_ping(state, {})
        assert ping_resp["ok"] is True
        assert ping_resp["running"] is True
        status_resp = handle_status(state, {})
        assert status_resp["ok"] is True
        assert "uptime_seconds" in status_resp
        assert "request_count" in status_resp

    def test_ocr_engine_never_injects_packages_from_another_python_abi(self):
        import ocr_engine

        current_abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
        self.assertEqual(1, len(ocr_engine._pkg_candidates))
        self.assertEqual(current_abi, ocr_engine._pkg_candidates[0].parent.name)

    def test_ocr_render_engine_uses_legacy_for_browser_mode(self):
        """브라우저 모드 OCR은 레거시 렌더러를 사용하는지 확인한다."""
        import macro_tool
        step = {"action": "ocr", "mode": "browser", "selector": "#test", "ocr_action": "find_text"}
        # render_ocr_engine should delegate to render_ocr for browser mode
        if hasattr(macro_tool, 'render_ocr_engine'):
            lines = macro_tool.render_ocr_engine(step)
            script = "\n".join(lines)
            # Should contain browser-specific code from legacy renderer
            assert "selector" in script.lower() or "browser" in script.lower()

    def test_ocr_node_json_backward_compatibility(self):
        """기존 매크로 JSON의 OCR 노드가 새 코드에서도 정상 동작하는지 확인한다."""
        import macro_tool
        # Legacy OCR step (no ocr_action field)
        legacy_step = {
            "action": "ocr",
            "mode": "region",
            "lang": "eng+kor",
            "region": [100, 200, 400, 300],
        }
        lines = macro_tool.render_ocr(legacy_step)
        script = "\n".join(lines)
        assert "ocr_action.py" in script  # Uses legacy script
        assert "OCR_LastText" in script

    def test_ocr_portable_requirements_include_engine_files(self):
        """포터블 내보내기 요구사항에 OCR 엔진 파일이 포함되는지 확인한다."""
        # This test verifies the requirement detection logic
        import macro_tool
        steps = [{"action": "ocr", "mode": "region", "region": [0,0,100,100]}]
        # The export_macro_payload function copies ocr_action.py
        # Verify the ocr helper file would be included
        from pathlib import Path
        ocr_helper = Path(macro_tool.__file__).parent / "ocr_action.py"
        assert ocr_helper.exists(), "ocr_action.py should exist"
        ocr_engine = Path(macro_tool.__file__).parent / "ocr_engine.py"
        assert ocr_engine.exists(), "ocr_engine.py should exist"

    def test_ocr_postprocess_find_text_in_boxes(self):
        """박스에서 텍스트 찾기가 올바르게 동작하는지 확인한다."""
        from ocr_postprocess import find_text_in_boxes, OcrBox
        boxes = [
            OcrBox(text="확인", confidence=0.9, rect=(10,10,50,30), center=(30,20)),
            OcrBox(text="취소", confidence=0.85, rect=(60,10,100,30), center=(80,20)),
            OcrBox(text="확인", confidence=0.7, rect=(10,50,50,70), center=(30,60)),
        ]
        # Should find first "확인" by top-left priority
        found = find_text_in_boxes(boxes, "확인", "contains", position_priority="top_left")
        assert found is not None
        assert found.center == (30, 20)
        # Should find highest confidence "확인"
        found = find_text_in_boxes(boxes, "확인", "contains", position_priority="confidence")
        assert found is not None
        assert found.confidence == 0.9
        # Should not find nonexistent text
        found = find_text_in_boxes(boxes, "삭제", "contains")
        assert found is None

    def test_action_editor_has_test_ocr_method(self):
        """ActionEditor에 _test_ocr 메서드가 존재하고 step 조회가 빌드 가능한지 확인한다."""
        from macro_studio.action_editor import ActionEditor
        assert hasattr(ActionEditor, "_test_ocr")
        assert hasattr(ActionEditor, "build_step")

    def test_ocr_engine_returns_physical_screen_coordinates(self):
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("NumPy가 설치된 OCR 런타임에서 실행하는 테스트입니다.")
        import ocr_engine
        import ocr_postprocess

        image = np.zeros((40, 100, 3), dtype=np.uint8)
        capture = SimpleNamespace(
            region_to_image=lambda **_kwargs: (
                image,
                {"actual_region": [-1920, 120, -1820, 160]},
            )
        )
        preprocess = SimpleNamespace(preprocess=lambda *_args, **_kwargs: [SimpleNamespace(image=image, scale=1.0)])
        tess_result = SimpleNamespace(
            text="확인",
            normalized_text="확인",
            confidence=0.91,
            elapsed_ms=12.0,
            boxes=[SimpleNamespace(text="확인", confidence=0.91, rect=(10, 5, 50, 25), center=(30, 15))],
        )
        tesseract = SimpleNamespace(is_available=lambda: True, recognize_candidates=lambda *_args, **_kwargs: tess_result)
        state = ocr_engine.EngineState(idle_timeout=0)

        with mock.patch.object(ocr_engine, "_capture_mod", capture), mock.patch.object(
            ocr_engine, "_preprocess_mod", preprocess
        ), mock.patch.object(ocr_engine, "_tesseract_mod", tesseract), mock.patch.object(
            ocr_engine, "_postprocess_mod", ocr_postprocess
        ):
            result = ocr_engine.handle_ocr(
                state,
                {
                    "region": [-1920, 120, -1820, 160],
                    "engine_preference": "tesseract",
                    "find_text": "확인",
                    "ocr_action": "find_click",
                },
            )

        self.assertTrue(result["success"])
        self.assertEqual([-1890, 135], result["match_box"]["center"])
        self.assertEqual([30, 15], result["match_box"]["local_center"])

    def test_tesseract_scales_boxes_back_and_normalizes_confidence(self):
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("NumPy가 설치된 OCR 런타임에서 실행하는 테스트입니다.")
        import ocr_tesseract

        fake_data = {
            "level": [5],
            "text": ["OK"],
            "conf": ["92"],
            "left": [20],
            "top": [10],
            "width": [40],
            "height": [20],
            "page_num": [1],
            "block_num": [1],
            "par_num": [1],
            "line_num": [1],
        }
        fake_tesseract = SimpleNamespace(
            Output=SimpleNamespace(DICT="dict"),
            image_to_data=mock.Mock(return_value=fake_data),
        )
        fake_image = SimpleNamespace(fromarray=lambda _image: SimpleNamespace(size=(120, 60)))
        with mock.patch.object(ocr_tesseract, "pytesseract", fake_tesseract), mock.patch.object(
            ocr_tesseract, "Image", fake_image
        ), mock.patch.object(
            ocr_tesseract, "is_available", return_value=True
        ), mock.patch.object(
            ocr_tesseract, "ensure_tesseract"
        ):
            result = ocr_tesseract.recognize(np.zeros((60, 120), dtype=np.uint8), lang="eng", scale=2.0)

        self.assertAlmostEqual(0.92, result.confidence)
        self.assertEqual((10, 5, 30, 15), result.boxes[0].rect)
        self.assertEqual((20, 10), result.boxes[0].center)

    def test_ocr_nodes_branch_on_recognition_success_and_failure(self):
        import macro_tool

        macro = {
            "name": "ocr-branch",
            "steps": [
                {
                    "action": "ocr",
                    "ocr_action": "find_text",
                    "engine_preference": "auto",
                    "find_text": "확인",
                    "region": [0, 0, 100, 100],
                    "on_success": 2,
                    "on_fail": 3,
                },
                {"action": "wait", "duration": 1},
                {"action": "wait", "duration": 2},
            ],
        }
        script = macro_tool.render_macro_script(macro, {})
        first = script.split("Step1:", 1)[1].split("Step2:", 1)[0]
        self.assertIn("if (__ocr_success)", first)
        self.assertIn("Goto, Step2", first)
        self.assertIn("Goto, Step3", first)

    def test_ocr_export_copies_background_engine_modules(self):
        import macro_tool

        macro = {
            "name": "ocr-export",
            "steps": [{"action": "ocr", "ocr_action": "find_text", "find_text": "확인", "region": [0, 0, 100, 100]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ocr-export.ahk"
            macro_tool.export_macro_payload(macro, target)
            for name in ("ocr_engine.py", "ocr_capture.py", "ocr_preprocess.py", "ocr_tesseract.py", "ocr_postprocess.py"):
                self.assertTrue((target.parent / name).is_file(), name)


class RemoteFeatureTests(unittest.TestCase):
    def test_cloud_endpoint_migrates_loopback_config_and_enables_agent(self):
        from remote_common import load_config, save_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "remote").mkdir()
            (root / "remote" / "endpoint.json").write_text(
                json.dumps({"relay_url": "https://relay.example.workers.dev"}), encoding="utf-8"
            )
            config = {
                "enabled": False,
                "relay_url": "http://127.0.0.1:8765",
                "device_name": "Test PC",
                "device_id": "device",
                "device_secret": "secret",
                "allow_remote_run": True,
                "allow_remote_stop": True,
                "allowed_macros": [],
            }
            save_config(config, root)
            migrated = load_config(root)
            self.assertTrue(migrated["enabled"])
            self.assertEqual("https://relay.example.workers.dev", migrated["relay_url"])

    def test_remote_controller_recognizes_only_loopback_relay_as_local(self):
        from macro_studio.remote import RemoteController

        with tempfile.TemporaryDirectory() as directory:
            controller = RemoteController(Path(directory))
            self.assertTrue(controller.uses_local_relay({"relay_url": "http://127.0.0.1:8765"}))
            self.assertTrue(controller.uses_local_relay({"relay_url": "http://localhost:9000"}))
            self.assertFalse(controller.uses_local_relay({"relay_url": "https://relay.example.com"}))

    def test_remote_controller_keeps_local_relay_and_agent_alive_when_enabled(self):
        from macro_studio.remote import RemoteController

        with tempfile.TemporaryDirectory() as directory:
            controller = RemoteController(Path(directory))
            with mock.patch.object(controller, "load", return_value={
                "enabled": True, "relay_url": "http://127.0.0.1:9123",
            }), mock.patch.object(controller, "start_local_relay", return_value=True) as relay, mock.patch.object(
                controller, "start_agent", return_value=True
            ) as agent, mock.patch.object(controller, "status", return_value={"agent_running": True}):
                status = controller.ensure_running()
            relay.assert_called_once_with(9123)
            agent.assert_called_once_with()
            self.assertTrue(status["agent_running"])

    def test_relay_pair_status_command_and_event_roundtrip(self):
        from remote.relay_server import create_server
        from remote_common import request_json

        directory = tempfile.TemporaryDirectory()
        server = create_server("127.0.0.1", 0, Path(directory.name) / "relay.db")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            registered = request_json(base, "POST", "/api/agent/register", {
                "device_id": "pc-test", "device_secret": "secret", "device_name": "테스트 PC",
            })
            self.assertTrue(registered["ok"])
            paired = request_json(base, "POST", "/api/pair", {"code": registered["pairing_code"]})
            self.assertTrue(paired["ok"])
            agent_headers = {"X-MacroRelay-Device": "pc-test", "X-MacroRelay-Secret": "secret"}
            app_headers = {"Authorization": f"Bearer {paired['token']}"}
            self.assertTrue(request_json(base, "POST", "/api/agent/status", {"running": False}, agent_headers)["ok"])
            command = request_json(
                base, "POST", "/api/devices/pc-test/commands",
                {"action": "run_macro", "payload": {"name": "샘플"}}, app_headers,
            )
            self.assertTrue(command["ok"])
            polled = request_json(base, "GET", "/api/agent/commands?timeout=0", headers=agent_headers)
            self.assertEqual("run_macro", polled["commands"][0]["action"])
            self.assertTrue(request_json(
                base, "POST", "/api/agent/events",
                {"type": "notification", "message": "완료", "payload": {}}, agent_headers,
            )["ok"])
            events = request_json(base, "GET", "/api/devices/pc-test/events?after=0", headers=app_headers)
            self.assertEqual("완료", events["events"][0]["message"])
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()
            directory.cleanup()

    def test_remote_notification_action_is_rendered(self):
        import macro_tool

        script = macro_tool.render_macro_script({
            "name": "remote-notify-test",
            "steps": [{
                "action": "remote_notify", "title": "완료", "message": "작업 완료",
                "level": "success", "include_last_ocr": True, "wait_delivery": True,
            }],
        }, {})
        self.assertIn("remote_notify.py", script)
        self.assertIn("--message-file", script)
        self.assertIn("OCR_LastText", script)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "notify.ahk"
            macro_tool.export_macro_payload({
                "name": "notify", "steps": [{"action": "remote_notify", "message": "완료"}],
            }, target)
            self.assertTrue((target.parent / "remote_notify.py").is_file())
            self.assertTrue((target.parent / "remote_common.py").is_file())

    def test_remote_client_assets_exist(self):
        for name in ("index.html", "app.css", "app.js", "manifest.webmanifest", "service-worker.js", "icon.svg"):
            self.assertTrue((ROOT / "remote" / "mobile" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
