from __future__ import annotations

import json
import os
import queue
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class BrowserElementPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_element_command_is_delivered_only_to_selected_browser(self) -> None:
        from macro_studio.deck_browser_bridge import DeckBrowserBridge

        bridge = DeckBrowserBridge("secret", port=0)
        port = bridge.server.server_port
        headers = {"X-MacroRelay-Token": "secret"}
        try:
            command_id = bridge.request_element_command(
                "pick_element", "whale", page_url="https://example.com/")
            wrong = Request(f"http://127.0.0.1:{port}/next-command?browser=chrome", headers=headers)
            self.assertEqual(204, urlopen(wrong, timeout=2).status)
            right = Request(f"http://127.0.0.1:{port}/next-command?browser=whale", headers=headers)
            command = json.loads(urlopen(right, timeout=2).read())
            self.assertEqual(command_id, command["id"])
            self.assertEqual("pick_element", command["kind"])
            self.assertEqual("whale", command["browser"])
            self.assertIn(command_id, bridge.command_dispatched_at)
            result = {"id": command_id, "status": "ok", "result": {
                "selector": "#login", "count": 1, "tag": "button", "visible": True}}
            request = Request(f"http://127.0.0.1:{port}/command-result",
                              data=json.dumps(result).encode("utf-8"),
                              headers={**headers, "Content-Type": "application/json"})
            self.assertEqual(204, urlopen(request, timeout=2).status)
            self.assertEqual(result["result"], bridge.command_results.get_nowait()["result"])
            self.assertNotIn(command_id, bridge.command_dispatched_at)
        finally:
            bridge.close()

    def test_picker_shows_match_and_applies_selector(self) -> None:
        from macro_studio.action_editor import BrowserElementPickerDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            dialog = BrowserElementPickerDialog(MacroRepository(Path(directory)))
            try:
                dialog._pending_id = "chosen"
                dialog._bridge = mock.Mock()
                dialog._bridge.command_results.empty.return_value = False
                dialog._bridge.command_results.get_nowait.return_value = {
                    "id": "chosen", "status": "ok", "result": {
                        "selector": "#login", "count": 1, "tag": "button", "visible": True,
                        "interactive": True,
                        "text": "로그인", "url": "https://example.com/login", "title": "Example"}}
                dialog._poll_result()
                self.assertEqual("#login", dialog.selector_edit.text())
                self.assertIn("일치 1개", dialog.result_label.text())
                dialog.accept()
                self.assertEqual("https://example.com/login", dialog.selected["page_url"])
                self.assertEqual("whale", dialog.selected["browser"])
            finally:
                dialog.close()

    def test_picker_test_button_runs_saved_action_in_existing_browser(self) -> None:
        from macro_studio.action_editor import BrowserElementPickerDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            dialog = BrowserElementPickerDialog(MacroRepository(Path(directory)), selector="#login",
                                                page_url="https://example.com/login", action="click")
            bridge = mock.Mock()
            bridge.request_element_command.return_value = "test-1"
            bridge.command_results = queue.Queue()
            try:
                with mock.patch("macro_studio.browser_element_bridge.open_studio_bridge", return_value=bridge), \
                        mock.patch("macro_studio.browser_element_bridge.focus_browser", return_value=True):
                    dialog.test_button.click()
                bridge.request_element_command.assert_called_once_with(
                    "run_element_action", "whale", selector="#login",
                    page_url="https://example.com/login", action="click", value="", test_mode=True)
                bridge.command_results.put({"id": "test-1", "status": "ok", "result": {
                    "selector": "#login", "count": 1, "tag": "button", "visible": True,
                    "interactive": True,
                    "input_method": "browser_debugger"}})
                dialog._poll_result()
                self.assertIn("실제 마우스 입력", dialog.status_label.text())
                self.assertIn("CSS 일치 확인", dialog.result_label.text())
                self.assertIn("실제 입력 전송 확인", dialog.result_label.text())
                self.assertIn("사이트 반응: 자동 검증하지 않음", dialog.result_label.text())
                self.assertTrue(dialog.test_button.isEnabled())
            finally:
                dialog.close()

    def test_picker_reports_undelivered_command_without_full_action_timeout(self) -> None:
        from macro_studio.action_editor import BrowserElementPickerDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            dialog = BrowserElementPickerDialog(MacroRepository(Path(directory)), selector="#login")
            bridge = mock.Mock()
            bridge.command_results = queue.Queue()
            bridge.command_dispatched_at = {}
            dialog._bridge = bridge
            dialog._pending_id = "undelivered"
            dialog._pending_kind = "test_element_action"
            dialog._delivery_deadline = 0.0
            dialog._deadline = float("inf")
            try:
                dialog._poll_result()
                self.assertIn("명령을 받지 않았습니다", dialog.status_label.text())
                bridge.cancel_command.assert_called_once_with("undelivered")
            finally:
                dialog.close()

    def test_picker_identifies_old_extension_command_error(self) -> None:
        from macro_studio.action_editor import BrowserElementPickerDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            dialog = BrowserElementPickerDialog(MacroRepository(Path(directory)), selector="#login")
            bridge = mock.Mock()
            bridge.command_results = queue.Queue()
            bridge.command_results.put({"id": "old-extension", "status": "error",
                                        "error": "Error: Unsupported Studio browser command"})
            dialog._bridge = bridge
            dialog._pending_id = "old-extension"
            try:
                dialog._poll_result()
                self.assertIn("구버전", dialog.status_label.text())
                self.assertIn("다시 로드", dialog.status_label.text())
            finally:
                dialog.close()

    def test_page_body_selector_is_not_saved_as_a_click_target(self) -> None:
        from macro_studio.action_editor import BrowserElementPickerDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            dialog = BrowserElementPickerDialog(MacroRepository(Path(directory)), action="click")
            bridge = mock.Mock()
            bridge.command_results = queue.Queue()
            bridge.command_results.put({"id": "body", "status": "ok", "result": {
                "selector": "body.chart-page.unselectable", "count": 1, "tag": "body",
                "visible": True, "interactive": False}})
            dialog._bridge = bridge
            dialog._pending_id = "body"
            dialog._pending_kind = "pick_element"
            try:
                dialog._poll_result()
                self.assertEqual("", dialog.selector_edit.text())
                self.assertIn("저장하지 않았습니다", dialog.status_label.text())
                self.assertIn("페이지 전체라 지정 불가", dialog.result_label.text())
            finally:
                dialog.close()

    def test_picker_can_select_again_without_reopening_dialog(self) -> None:
        from macro_studio.action_editor import BrowserElementPickerDialog
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as directory:
            dialog = BrowserElementPickerDialog(MacroRepository(Path(directory)))
            bridge = mock.Mock()
            bridge.request_element_command.side_effect = ["pick-1", "pick-2"]
            bridge.command_results = queue.Queue()
            try:
                with mock.patch("macro_studio.browser_element_bridge.open_studio_bridge", return_value=bridge), \
                        mock.patch("macro_studio.browser_element_bridge.focus_browser", return_value=True):
                    for command_id, selector in (("pick-1", "#first"), ("pick-2", "#second")):
                        dialog.pick_button.click()
                        self.assertEqual(command_id, dialog._pending_id)
                        bridge.command_results.put({"id": command_id, "status": "ok", "result": {
                            "selector": selector, "count": 1, "tag": "button", "visible": True,
                            "interactive": True, "url": "https://example.com/"}})
                        dialog._poll_result()
                        self.assertTrue(dialog.pick_button.isEnabled())
                        self.assertEqual(selector, dialog.selector_edit.text())
                self.assertEqual(2, bridge.request_element_command.call_count)
            finally:
                dialog.close()

    def test_quick_add_browser_action_uses_picker(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio import automation

        selection = {"browser": "whale", "selector": "#login", "title": "Login",
                     "page_url": "https://example.com/login"}
        with mock.patch.object(automation, "BrowserElementPickerDialog") as picker:
            picker.return_value.exec.return_value = QtWidgets.QDialog.Accepted
            picker.return_value.selected = selection
            step = automation.QuickActionWizard.build("browser_action", mock.Mock())
        self.assertEqual("browser_action", step["action"])
        self.assertEqual("#login", step["selector"])
        self.assertTrue(step["prefer_active"])

    def test_extension_runtime_action_uses_selected_browser_and_returns_text(self) -> None:
        from browser_action import run_extension_action

        bridge = mock.Mock()
        bridge.request_element_command.return_value = "command-1"
        bridge.command_results = queue.Queue()
        bridge.command_results.put({"id": "command-1", "status": "ok",
                                    "result": {"text": "계정 이름"}})
        args = Namespace(browser="whale", selector="#account", page_url="https://example.com/login",
                         action="extract_text", value="", timeout=2000)
        with mock.patch("macro_studio.browser_element_bridge.open_studio_bridge", return_value=bridge), \
                mock.patch("macro_studio.browser_element_bridge.focus_browser", return_value=True):
            self.assertEqual("계정 이름", run_extension_action(args))
        bridge.request_element_command.assert_called_once_with(
            "run_element_action", "whale", selector="#account",
            page_url="https://example.com/login", action="extract_text", value="")
        bridge.close.assert_called_once()

    def test_old_extension_cannot_report_a_synthetic_click_as_success(self) -> None:
        from browser_action import run_extension_action

        bridge = mock.Mock()
        bridge.request_element_command.return_value = "command-1"
        bridge.command_results = queue.Queue()
        bridge.command_results.put({"id": "command-1", "status": "ok",
                                    "result": {"selector": "#login", "count": 1}})
        args = Namespace(browser="whale", selector="#login", page_url="https://example.com/login",
                         action="click", value="", timeout=2000)
        with mock.patch("macro_studio.browser_element_bridge.open_studio_bridge", return_value=bridge), \
                mock.patch("macro_studio.browser_element_bridge.focus_browser", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "구버전"):
                run_extension_action(args)
        bridge.close.assert_called_once()

    def test_node_hover_preview_dismisses_instead_of_staying_on_screen(self) -> None:
        from macro_studio.node_editor import NodeToolTipPopup

        popup = NodeToolTipPopup()
        try:
            popup.set_step_info(1, "브라우저 요소", "#login", "현재 위치: 1번 노드")
            popup.show()
            self.app.processEvents()
            self.assertTrue(popup._dismiss_timer.isActive())
            popup._dismiss_timer.timeout.emit()
            self.assertFalse(popup.isVisible())
        finally:
            popup.close()

    def test_rendered_browser_action_keeps_browser_and_page_address(self) -> None:
        from macro_tool import render_browser_action

        script = "\n".join(render_browser_action({
            "action": "browser_action", "browser": "whale", "selector": "#account",
            "page_url": "https://example.com/login", "browser_action": "click"}))
        self.assertIn('""browser"": ""whale""', script)
        self.assertIn("https://example.com/login", script)
        self.assertIn("--request-file", script)
        self.assertIn("RunWait,", script)
        self.assertNotIn('BrowserAction_Send("{""cmd"":""ping""}"', script)
        self.assertIn('""prefer_extension"": true', script)

    def test_exported_helper_finds_project_connection_config(self) -> None:
        import browser_action
        import sys

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "exports").mkdir()
            (root / "macro_studio").mkdir()
            (root / "macro_studio" / "browser_element_bridge.py").write_text("", encoding="utf-8")
            (root / ".quickslot_deck_config.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(browser_action, "__file__", str(root / "exports" / "browser_action.py")), \
                    mock.patch.dict(os.environ, {"MACRORELAY_HOME": ""}):
                self.assertEqual(root, browser_action.extension_project_root())
            sys.path.remove(str(root))

    def test_saved_browser_node_uses_same_extension_path_as_picker(self) -> None:
        from browser_action import BrowserSession

        args = Namespace(browser="whale", selector="#account", page_url="https://example.com/login",
                         action="click", value="", timeout=2000, prefer_extension=True)
        with mock.patch("browser_action.run_extension_action", return_value=None) as extension, \
                mock.patch.object(BrowserSession, "_run_action_once") as cdp:
            BrowserSession().run_action(args)
        extension.assert_called_once_with(args)
        cdp.assert_not_called()

    def test_rendered_node_uses_direct_helper_and_branches_on_real_response(self) -> None:
        from macro_tool import render_macro_script

        script = render_macro_script({"name": "browser-test", "steps": [
            {"action": "browser_action", "browser": "whale", "selector": "#account",
             "page_url": "https://example.com/login", "browser_action": "click"}
        ]}, {})
        self.assertIn('RunWait, "%PythonExe%" "%A_ScriptDir%\\browser_action.py" --request-file', script)
        self.assertNotIn('BrowserPing := BrowserAction_Send(', script)
        self.assertIn('BrowserActionOK := 1', script)
        self.assertIn('SetRunResult("FAILED", "BROWSER_ACTION_FAILED", BrowserActionError)', script)
        self.assertIn('if (BrowserActionOK)', script)
        self.assertIn('TraceStep(1, "browser_action", "FAIL")', script)

    def test_browser_cli_request_file_reports_success_and_failure(self) -> None:
        import browser_action
        import sys

        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "request.json"
            response = Path(directory) / "result.json"
            request.write_text(json.dumps({"selector": "span[data-name=\"TrendLine\"]",
                                           "browser": "whale", "page_url": "https://example.com/",
                                           "action": "click", "prefer_extension": True}), encoding="utf-8")
            argv = ["browser_action.py", "--request-file", str(request), "--result-file", str(response)]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(browser_action, "run_action") as action:
                action.return_value = None
                browser_action.main()
            self.assertTrue(json.loads(response.read_text(encoding="utf-8-sig"))["ok"])
            self.assertTrue(action.call_args.args[0].prefer_extension)
            self.assertEqual("span[data-name=\"TrendLine\"]", action.call_args.args[0].selector)
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(browser_action, "run_action", side_effect=RuntimeError("확장앱 응답 없음")):
                with self.assertRaises(SystemExit) as stopped:
                    browser_action.main()
            self.assertEqual(1, stopped.exception.code)
            self.assertEqual("확장앱 응답 없음", json.loads(response.read_text(encoding="utf-8-sig"))["error"])
            self.assertEqual("확장앱 응답 없음", Path(str(response) + ".error").read_text(encoding="utf-8-sig"))

    def test_extension_helper_import_does_not_require_playwright(self) -> None:
        import subprocess
        import sys

        project_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-S", "-c", "import browser_action"],
            cwd=project_root, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_legacy_cdp_node_has_bounded_server_probe(self) -> None:
        from macro_tool import render_browser_action

        script = "\n".join(render_browser_action({
            "action": "browser_action", "browser": "auto", "selector": "#login"}))
        self.assertIn('Loop, 4', script)
        self.assertIn('BrowserAction_Send("{""cmd"":""ping""}"', script)


if __name__ == "__main__":
    unittest.main()
