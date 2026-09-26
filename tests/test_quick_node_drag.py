from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class QuickNodeDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def _canvas(self):
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro(
            {
                "steps": [
                    {"action": "wait", "label": "앞", "duration": 1, "on_success": 2},
                    {"action": "wait", "label": "뒤", "duration": 1},
                ],
                "graph_positions": {"1": [0, 0], "2": [360, 0]},
            }
        )
        app.processEvents()
        return app, canvas

    def test_drop_on_edge_stays_disconnected_and_has_no_preview_line(self) -> None:
        app, canvas = self._canvas()
        edge = canvas.edges[0]
        scene_pos = edge.sceneBoundingRect().center()
        received: list[tuple[str, dict]] = []
        canvas.quick_node_drop_requested.connect(
            lambda kind, payload: received.append((kind, dict(payload)))
        )

        canvas.preview_quick_node_drop("wait", scene_pos)
        self.assertIsNone(canvas._quick_drop_candidate)
        self.assertEqual([], canvas._quick_drop_preview_items)
        canvas.commit_quick_node_drop("wait", scene_pos)
        app.processEvents()

        self.assertEqual("wait", received[0][0])
        self.assertEqual(0, received[0][1]["source"])
        self.assertEqual(0, received[0][1]["target"])
        self.assertEqual("success", received[0][1]["edge_kind"])
        self.assertEqual([], canvas._quick_drop_preview_items)
        canvas.close()

    def test_independent_live_drop_does_not_change_existing_routes(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.automation import RecordingBar

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        bar = RecordingBar(repository=None)
        events = [
            {"type": "wait", "event_id": "source", "t": 1, "duration": 1},
            {"type": "wait", "event_id": "tail", "t": 2, "duration": 1},
        ]
        bar._live_events = list(events)
        bar.update_live_events(events, force=True)
        bar.apply_quick_node_drop("new", {"x": 180, "y": 30}, rebuild=False)

        self.assertEqual({}, bar._live_link_overrides)
        self.assertIn("new", bar._event_positions)
        bar.close()

    def test_canvas_double_click_pans_and_ctrl_double_click_requests_action_picker(self) -> None:
        from PySide6 import QtCore, QtTest

        app, canvas = self._canvas()
        canvas.resize(900, 620)
        canvas.show()
        app.processEvents()
        point = QtCore.QPoint(canvas.view.viewport().width() - 24, canvas.view.viewport().height() - 24)
        requested: list[tuple[str, object]] = []
        canvas.node_add_at_requested.connect(lambda action, pos: requested.append((action, pos)))

        QtTest.QTest.mouseDClick(
            canvas.view.viewport(),
            QtCore.Qt.LeftButton,
            QtCore.Qt.ControlModifier,
            point,
        )
        app.processEvents()
        self.assertEqual(1, len(requested))
        self.assertEqual("", requested[0][0])

        QtTest.QTest.mouseDClick(canvas.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        app.processEvents()
        self.assertEqual(1, len(requested))
        canvas.close()

    def test_quick_wizards_restore_two_stage_image_and_pixel_regions(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio import automation

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        pixmap = QtGui.QPixmap(1000, 800)
        pixmap.fill(QtGui.QColor("#203040"))
        geometry = QtCore.QRect(0, 0, 1000, 800)
        target = {
            "window": "Game ahk_exe game.exe",
            "exe": "game.exe",
            "capture_scope": "client",
            "capture_origin": [50, 50],
            "capture_size": [500, 500],
        }

        class Repository:
            @staticmethod
            def add_asset_image(_image, alias):
                return alias

        class CaptureDialog:
            def __init__(self, rect, image=None):
                self.rect = rect
                self.image = image or QtGui.QImage(20, 20, QtGui.QImage.Format_RGB32)

            @staticmethod
            def exec():
                return QtWidgets.QDialog.Accepted

            def selected_native_screen_rect(self):
                return QtCore.QRect(self.rect)

            def captured_image(self):
                return self.image

            @staticmethod
            def deleteLater():
                return None

        image_dialogs = [
            CaptureDialog(QtCore.QRect(150, 150, 20, 20)),
            CaptureDialog(QtCore.QRect(100, 100, 300, 300)),
        ]
        with mock.patch.object(automation, "capture_virtual_desktop", return_value=(pixmap, geometry)), \
             mock.patch.object(automation, "ScreenCaptureDialog", side_effect=image_dialogs), \
             mock.patch.object(automation.ActionEditor, "_window_target_at", return_value=target), \
             mock.patch.object(automation.QtCore.QThread, "msleep"):
            image_step = automation.QuickActionWizard.build("image_search", Repository())

        self.assertEqual([50, 50, 350, 350], image_step["region"])
        self.assertEqual("client", image_step["region_mode"])
        self.assertEqual("relative", image_step["region_coords"])
        self.assertEqual("game.exe", image_step["region_window_exe"])
        self.assertEqual("inactive", image_step["click"]["mode"])

        class PixelPicker:
            @staticmethod
            def exec():
                return QtWidgets.QDialog.Accepted

            @staticmethod
            def selected_color():
                return QtGui.QColor("#44AA77")

            @staticmethod
            def selected_point():
                return QtCore.QPoint(200, 210)

        with mock.patch.object(automation, "capture_virtual_desktop", return_value=(pixmap, geometry)), \
             mock.patch.object(automation, "PixelColorPickerDialog", return_value=PixelPicker()), \
             mock.patch.object(automation, "ScreenCaptureDialog", return_value=CaptureDialog(QtCore.QRect(90, 80, 200, 180))), \
             mock.patch.object(automation.ActionEditor, "_window_target_at", return_value=target), \
             mock.patch.object(automation.QtCore.QThread, "msleep"):
            pixel_step = automation.QuickActionWizard.build("pixel_search", Repository())

        self.assertEqual([40, 30, 240, 210], pixel_step["search_region"])
        self.assertEqual([150, 160], [pixel_step["x"], pixel_step["y"]])
        self.assertEqual("game.exe", pixel_step["region_window_exe"])
        self.assertEqual("inactive", pixel_step["click"]["mode"])
        app.processEvents()

    def test_live_splice_replaces_only_selected_connection(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.automation import RecordingBar

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        bar = RecordingBar(repository=None)
        old_events = [
            {"type": "wait", "event_id": "source", "t": 1, "duration": 1},
            {"type": "wait", "event_id": "target", "t": 2, "duration": 1},
            {"type": "wait", "event_id": "tail", "t": 3, "duration": 1},
        ]
        bar._live_events = list(old_events)
        bar.update_live_events(old_events, force=True)
        bar.apply_quick_node_drop(
            "new",
            {
                "source_event_id": "source",
                "target_event_id": "target",
                "edge_kind": "success",
                "x": 180,
                "y": 30,
            },
            rebuild=False,
        )
        new_events = [
            *old_events,
            {"type": "wait", "event_id": "new", "t": 4, "duration": 1},
        ]
        bar._live_events = list(new_events)
        bar.update_live_events(new_events, force=True)
        app.processEvents()

        self.assertEqual("new", bar._live_link_overrides[("source", "success")])
        self.assertEqual("target", bar._live_link_overrides[("new", "success")])
        self.assertIsNone(bar._live_link_overrides[("tail", "success")])
        source_pos = bar._event_positions["source"]
        new_pos = bar._event_positions["new"]
        target_pos = bar._event_positions["target"]
        self.assertGreaterEqual(new_pos[0], source_pos[0] + 196.0 + 68.0)
        self.assertGreaterEqual(target_pos[0], new_pos[0] + 196.0 + 68.0)
        self.assertEqual(source_pos[1], new_pos[1])
        tail_index = next(
            index for index, step in enumerate(bar.live_canvas.steps, start=1)
            if step.get("_event_id") == "tail"
        )
        self.assertEqual(0, int(bar.live_canvas.steps[tail_index - 1].get("on_success") or 0))
        bar.close()

    def test_toolbar_contains_coordinate_click_and_live_edge_settings_persist(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.automation import DraggableIconWrapper, RecordingBar

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        bar = RecordingBar(repository=None)
        kinds = [
            widget.kind
            for widget in bar.icon_toolbar.findChildren(DraggableIconWrapper)
        ]
        self.assertIn("mouse_click", kinds)

        events = [
            {"type": "wait", "event_id": "source", "t": 1, "duration": 1},
            {"type": "wait", "event_id": "target", "t": 2, "duration": 1},
        ]
        bar._live_events = list(events)
        bar.update_live_events(events, force=True)

        class FakeSpin:
            @staticmethod
            def value() -> int:
                return 275

        class FakeDialog:
            delay_spin = FakeSpin()
            rules = [
                {
                    "kind": "success",
                    "label": "두 번 후 분기",
                    "source": "edge_count",
                    "operator": ">=",
                    "value": 2,
                    "target": 2,
                }
            ]

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            @staticmethod
            def exec() -> int:
                return QtWidgets.QDialog.Accepted

        with mock.patch("macro_studio.builder.EdgeSettingsDialog", FakeDialog):
            bar.live_canvas.edge_delay_requested.emit(1, 2, "success")
        app.processEvents()

        payload = bar.step_payloads()["source"]
        self.assertEqual(275, payload["on_success_delay"])
        self.assertEqual("두 번 후 분기", payload["edge_conditions"][0]["label"])
        self.assertEqual(275, bar.live_canvas.steps[0]["on_success_delay"])

        class FakeConditionDialog:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            @staticmethod
            def exec() -> int:
                return QtWidgets.QDialog.Accepted

            @staticmethod
            def payload(kind: str) -> dict:
                return {
                    "kind": kind,
                    "label": "우클릭 직접 분기",
                    "source": "edge_count",
                    "operator": ">=",
                    "value": 1,
                    "target": 2,
                }

        with mock.patch("macro_studio.builder.EdgeConditionDialog", FakeConditionDialog):
            bar.live_canvas.edge_condition_add_requested.emit(1, 2, "success")
        app.processEvents()
        payload = bar.step_payloads()["source"]
        self.assertEqual(2, len(payload["edge_conditions"]))
        self.assertEqual("우클릭 직접 분기", payload["edge_conditions"][1]["label"])
        bar.close()

    def test_nested_edge_condition_dialog_stays_above_recording_window(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.builder import EdgeConditionDialog, EdgeSettingsDialog

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        host = QtWidgets.QDialog()
        host.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint)
        settings = EdgeSettingsDialog(3, "success", 0, [], host)
        condition = EdgeConditionDialog(3, "success", parent=settings)

        self.assertTrue(bool(settings.windowFlags() & QtCore.Qt.WindowStaysOnTopHint))
        self.assertTrue(bool(condition.windowFlags() & QtCore.Qt.WindowStaysOnTopHint))
        self.assertEqual(QtCore.Qt.WindowModal, condition.windowModality())
        condition.close()
        settings.close()
        host.close()
        app.processEvents()

    def test_live_name_survives_rebuild_and_deleted_node_stays_removed(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.automation import RecordingBar

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        bar = RecordingBar(repository=None)
        events = [
            {"type": "wait", "event_id": "source", "t": 1, "duration": 1},
            {"type": "wait", "event_id": "target", "t": 2, "duration": 1},
        ]
        bar.update_live_events(events, force=True)
        bar.live_canvas.node_title_changed.emit(1, "사용자 지정 이름")
        bar.live_canvas.link_requested.emit(1, 2, "success")
        app.processEvents()
        app.processEvents()

        self.assertEqual("사용자 지정 이름", bar.live_canvas.steps[0]["label"])
        self.assertEqual("사용자 지정 이름", bar.step_payloads()["source"]["label"])

        deleted: list[str] = []
        bar.events_deleted.connect(lambda values: deleted.extend(values))
        bar.live_canvas.node_delete_requested.emit(2)
        app.processEvents()
        self.assertEqual(["target"], deleted)
        self.assertEqual(1, len(bar.live_canvas.steps))
        self.assertEqual("🗑 선택 삭제  Del", bar.delete_button.text())
        bar.close()

    def test_live_preview_click_applies_detailed_image_edit(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.automation import RecordingBar, _encoded_png

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        original = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
        original.fill(QtGui.QColor("#FF0000"))
        edited = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
        edited.fill(QtGui.QColor("#00FF00"))
        event = {
            "type": "capture",
            "event_id": "image",
            "t": 1,
            "image_sample_bmp": _encoded_png(original),
        }
        bar = RecordingBar(repository=None)
        bar.update_live_events([event], force=True)

        class FakeImageDialog:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            @staticmethod
            def exec() -> int:
                return QtWidgets.QDialog.Accepted

            @staticmethod
            def edited_image() -> QtGui.QImage:
                return edited

            @staticmethod
            def precise_search_enabled() -> bool:
                return True

            @staticmethod
            def click_offset() -> QtCore.QPoint:
                return QtCore.QPoint(2, 3)

        with mock.patch("macro_studio.automation.RecordedImageDetailDialog", FakeImageDialog):
            bar.live_canvas.image_edit_requested.emit(1)
        app.processEvents()
        self.assertTrue(bool(event.get("_review_edited_image_bmp")))
        self.assertEqual([2, 3], event.get("_review_detail_click_offset"))
        self.assertEqual("#00ff00", bar.live_canvas._asset_pixmaps["1"].toImage().pixelColor(0, 0).name())
        bar.close()

    def test_pixel_search_exposes_multi_color_and_inactive_click(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.action_editor import ACTION_FIELDS, ActionEditor
        from macro_studio.repository import MacroRepository
        from macro_tool import render_pixel_search

        keys = {spec.key for spec in ACTION_FIELDS["pixel_search"]}
        self.assertIn("colors_text", keys)
        self.assertIn("click.mode", keys)
        script = "\n".join(
            render_pixel_search(
                {
                    "action": "pixel_search",
                    "color": "#FF0000",
                    "colors": ["#FF0000", "#00FF00"],
                    "match_condition": "all_matched",
                    "region_mode": "client",
                    "region_window_exe": "dnplayer.exe",
                    "action_on_found": "click",
                    "click": {"mode": "inactive", "method": "postmessage", "button": "Left"},
                },
                4,
            )
        )
        self.assertEqual(2, script.count("    PixelSearch,"))
        self.assertIn("inactive click target", script)
        self.assertIn("PostMessage", script)

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            editor = ActionEditor(MacroRepository(Path(directory)))
            editor.load_step(
                {
                    "action": "pixel_search",
                    "color": "#FF0000",
                    "colors": ["#FF0000", "#00FF00"],
                    "region_window_exe": "dnplayer.exe",
                    "click": {"mode": "inactive", "method": "postmessage"},
                }
            )
            built = editor.build_step()
            self.assertEqual(["#FF0000", "#00FF00"], built["colors"])
            self.assertEqual("inactive", built["click"]["mode"])
            self.assertEqual("dnplayer.exe", built["click"]["window_exe"])
            editor.close()
        app.processEvents()

    def test_inactive_image_click_waits_for_browser_release(self) -> None:
        from macro_tool import render_inactive_click_from_hit

        script = "\n".join(render_inactive_click_from_hit({
            "mode": "inactive",
            "method": "auto",
            "window_exe": "whale.exe",
            "button": "left",
            "count": 1,
        }))
        self.assertIn('UseSynchronousClick := 1', script)
        down = script.index('DownOk := DllCall("SendMessageTimeoutW"')
        release = script.index('UpOk := DllCall("SendMessageTimeoutW"')
        cleanup = script.index('DllCall("SendMessageTimeoutW", "Ptr", ClickHwnd, "UInt", 0x200', release)
        self.assertNotIn("Sleep,", script[down:release])
        self.assertGreater(cleanup, release)
        self.assertIn("ClickOk := DownOk && UpOk", script)
        self.assertIn("if (ClickOk && !RetryPost)", script)

    def test_explicit_postmessage_click_remains_available(self) -> None:
        from macro_tool import render_inactive_click_from_hit

        script = "\n".join(render_inactive_click_from_hit({
            "mode": "inactive",
            "method": "postmessage",
            "window_exe": "whale.exe",
            "button": "left",
        }))
        self.assertIn('if ("postmessage" != "auto")', script)
        self.assertIn("UseSynchronousClick := 0", script)
        self.assertIn("PostMessage, %DownMessage%", script)


if __name__ == "__main__":
    unittest.main()
