from __future__ import annotations

import os
import unittest


class NodeGroupCollapseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def _canvas(self):
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        macro = {
            "steps": [
                {"action": "wait", "label": "첫 단계", "duration": 1, "on_success": 2},
                {"action": "wait", "label": "둘째 단계", "duration": 1, "on_success": 3},
                {"action": "flow_control", "label": "완료", "jump_to": 0, "repeat_count": 0},
            ],
            "graph_positions": {"1": [0, 0], "2": [300, 0], "3": [600, 0]},
            "graph_comments": [
                {
                    "id": "group-a",
                    "title": "준비",
                    "color": "blue",
                    "x": -30,
                    "y": -45,
                    "w": 560,
                    "h": 200,
                    "node_indexes": [1, 2],
                    "collapsed": True,
                }
            ],
        }
        canvas.set_macro(macro)
        app.processEvents()
        return app, canvas

    def test_saved_collapsed_group_hides_members_and_restores_them(self) -> None:
        app, canvas = self._canvas()
        group = canvas.comments[0]

        self.assertTrue(group.collapsed)
        self.assertFalse(canvas.nodes[1].isVisible())
        self.assertFalse(canvas.nodes[2].isVisible())
        self.assertTrue(canvas.nodes[3].isVisible())
        self.assertGreaterEqual(len(canvas._group_proxy_edges), 1)
        external = next(edge for edge in canvas.edges if edge.source == 2 and edge.target == 3)
        self.assertFalse(external.isVisible())
        self.assertTrue(canvas.dump_comments()[0]["collapsed"])

        group.set_collapsed(False)
        app.processEvents()
        self.assertTrue(all(node.isVisible() for node in canvas.nodes.values()))
        self.assertTrue(all(edge.isVisible() for edge in canvas.edges))
        self.assertFalse(canvas.dump_comments()[0]["collapsed"])
        canvas.close()

    def test_group_flow_routes_unconnected_results_without_overwriting_explicit_edges(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        macro = {
            "steps": [
                {"action": "wait", "duration": 1, "on_fail": 3},
                {"action": "wait", "duration": 1},
                {"action": "wait", "duration": 1},
            ],
            "graph_start_step": 1,
            "graph_positions": {"1": [0, 0], "2": [300, 0], "3": [600, 0]},
            "graph_comments": [
                {"id": "a", "title": "A", "color": "yellow", "node_indexes": [1], "next_group_id": "b"},
                {"id": "b", "title": "B", "color": "blue", "node_indexes": [2], "next_group_id": "c"},
                {"id": "c", "title": "C", "color": "green", "node_indexes": [3]},
            ],
        }
        canvas.set_macro(macro)
        app.processEvents()

        self.assertEqual(2, canvas.steps[0]["on_success"])
        self.assertEqual(3, canvas.steps[0]["on_fail"])
        self.assertEqual(3, canvas.steps[1]["on_success"])
        self.assertEqual(3, canvas.steps[1]["on_fail"])
        self.assertTrue(canvas.steps[2]["stop_on_success"])
        self.assertTrue(canvas.steps[2]["abort_on_fail"])
        self.assertEqual("#F59E0B", canvas.node_group_theme(1)["border"])
        canvas.close()

    def test_execution_numbers_follow_graph_and_collapsing_compacts_row(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [
                {"action": "wait", "duration": 1},
                {"action": "wait", "duration": 1, "on_success": 4},
                {"action": "wait", "duration": 1, "on_success": 2},
                {"action": "wait", "duration": 1, "on_success": 3},
            ],
            "graph_start_step": 2,
            "graph_positions": {"1": [0, 0], "2": [300, 0], "3": [900, 0], "4": [600, 0]},
        })
        app.processEvents()
        self.assertEqual(1, canvas.display_number(2))
        self.assertEqual(2, canvas.display_number(4))
        self.assertEqual(3, canvas.display_number(3))

        canvas.set_nodes_collapsed([1], True)
        app.processEvents()
        self.assertAlmostEqual(160.0, canvas.nodes[2].pos().x())
        canvas.close()

    def test_removing_collapsed_group_reveals_member_nodes(self) -> None:
        app, canvas = self._canvas()
        group = canvas.comments[0]

        canvas.remove_comment_box(group)
        app.processEvents()
        self.assertTrue(all(node.isVisible() for node in canvas.nodes.values()))
        self.assertEqual([], canvas.dump_comments())
        canvas.close()

    def test_dragging_node_out_of_smart_workflow_detaches_without_changing_edges(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [
                {"action": "wait", "workflow_id": "smart-1", "workflow_label": "스마트 작업 1", "on_success": 2},
                {"action": "wait", "workflow_id": "smart-1", "workflow_label": "스마트 작업 1"},
            ],
            "graph_positions": {"1": [0, 0], "2": [300, 0]},
        })
        app.processEvents()
        original = QtCore.QRectF(canvas.workflow_items[0]._rect)
        canvas.nodes[1].setPos(original.right() + 200, original.bottom() + 200)

        changed = []
        canvas.workflow_membership_changed.connect(lambda: changed.append(True))
        canvas.check_node_workflow_membership(canvas.nodes[1], original)

        self.assertNotIn("workflow_id", canvas.steps[0])
        self.assertEqual("smart-1", canvas.steps[1]["workflow_id"])
        self.assertEqual([2], canvas.workflow_items[0].indexes)
        self.assertTrue(any(edge.source == 1 and edge.target == 2 for edge in canvas.edges))
        self.assertEqual([True], changed)
        canvas.close()

    def test_branch_workflows_follow_success_edges_but_leave_shared_merge_outside(self) -> None:
        from macro_studio.builder import _assign_connected_branch_workflows

        steps = [
            {"action": "wait", "on_success": 2, "on_fail": 3},
            {"action": "wait", "on_success": 5},
            {"action": "wait", "on_success": 4},
            {"action": "wait", "on_success": 6},
            {"action": "wait", "on_success": 6},
            {"action": "wait", "stop_on_success": True},
        ]
        assigned = _assign_connected_branch_workflows(steps, [1, 3])

        self.assertEqual([1, 2, 5], assigned[1])
        self.assertEqual([3, 4], assigned[3])
        self.assertEqual("branch-lane-1", steps[1]["workflow_id"])
        self.assertEqual("branch-lane-3", steps[3]["workflow_id"])
        self.assertNotIn("workflow_id", steps[5])

    def test_branch_target_picker_highlights_only_eligible_workflow_nodes(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({"steps": [
            {"action": "wait"},
            {"action": "wait", "workflow_id": "branch-lane-2", "workflow_label": "2번 분기"},
        ]})
        picked = []
        canvas.node_target_picked.connect(picked.append)
        canvas.begin_node_target_pick(eligible_indexes={2}, prompt="분기 선택")

        self.assertLess(canvas.nodes[1].opacity(), canvas.nodes[2].opacity())
        canvas.complete_node_target_pick(1)
        self.assertEqual([], picked)
        canvas.complete_node_target_pick(2)
        app.processEvents()
        self.assertEqual([2], picked)
        canvas.close()


if __name__ == "__main__":
    unittest.main()
