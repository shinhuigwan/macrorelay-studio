from __future__ import annotations

import os
import unittest
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

    def test_preview_finds_edge_and_emits_splice_metadata(self) -> None:
        from PySide6 import QtWidgets

        app, canvas = self._canvas()
        edge = canvas.edges[0]
        scene_pos = edge.sceneBoundingRect().center()
        received: list[tuple[str, dict]] = []
        canvas.quick_node_drop_requested.connect(
            lambda kind, payload: received.append((kind, dict(payload)))
        )

        canvas.preview_quick_node_drop("wait", scene_pos)
        self.assertIs(canvas._quick_drop_candidate, edge)
        self.assertEqual(4, len(canvas._quick_drop_preview_items))
        self.assertTrue(
            all(isinstance(item, QtWidgets.QGraphicsPathItem) for item in canvas._quick_drop_preview_items)
        )
        canvas.commit_quick_node_drop("wait", scene_pos)
        app.processEvents()

        self.assertEqual("wait", received[0][0])
        self.assertEqual(1, received[0][1]["source"])
        self.assertEqual(2, received[0][1]["target"])
        self.assertEqual("success", received[0][1]["edge_kind"])
        self.assertEqual([], canvas._quick_drop_preview_items)
        canvas.close()

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
        bar.close()


if __name__ == "__main__":
    unittest.main()
