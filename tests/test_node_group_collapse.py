from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock


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
        proxy = next(item for item in canvas._group_proxy_edges if item.source_edge is external)
        self.assertEqual(external.pen().style(), proxy.pen().style())
        self.assertAlmostEqual(external.pen().widthF(), proxy.pen().widthF())
        self.assertEqual(external.pen().color(), proxy.pen().color())
        self.assertAlmostEqual(external.opacity(), proxy.opacity())
        self.assertEqual(external.label.text(), proxy.proxy_label.text())
        self.assertFalse(proxy.proxy_arrow.polygon().isEmpty())
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

    def test_folded_workflow_lane_becomes_a_compact_header_card(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas, WorkflowLaneItem

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [
                {"action": "wait", "workflow_id": "branch-lane-1", "workflow_label": "1번 분기"},
                {"action": "wait", "workflow_id": "branch-lane-1", "workflow_label": "1번 분기"},
            ],
            "graph_positions": {"1": [0, 0], "2": [700, 0]},
        })
        lane = canvas.workflow_items[0]
        self.assertGreater(lane._rect.width(), WorkflowLaneItem.COLLAPSED_WIDTH)

        lane.toggle_fold()
        app.processEvents()
        self.assertTrue(lane.folded)
        self.assertAlmostEqual(WorkflowLaneItem.COLLAPSED_WIDTH, lane._rect.width())
        self.assertTrue(all(not node.isVisible() for node in canvas.nodes.values()))
        canvas.close()

    def test_folded_branch_hides_connected_legacy_nodes_group_and_edges(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [
                {"action": "wait", "workflow_id": "branch-1", "workflow_label": "1번 분기", "on_success": 2},
                {"action": "wait", "workflow_id": "branch-1", "workflow_label": "1번 분기", "on_success": 3},
                {"action": "wait", "on_success": 4},
                {"action": "wait", "workflow_id": "branch-2", "workflow_label": "2번 분기"},
            ],
            "graph_positions": {"1": [0, 0], "2": [280, 0], "3": [560, 0], "4": [840, 0]},
            "graph_comments": [{
                "id": "branch-child", "title": "하위 그룹", "color": "yellow", "node_indexes": [2, 3],
            }],
        })
        app.processEvents()

        first_lane = next(lane for lane in canvas.workflow_items if lane.workflow_id == "branch-1")
        first_lane.toggle_fold()
        app.processEvents()

        self.assertFalse(canvas.nodes[1].isVisible())
        self.assertFalse(canvas.nodes[2].isVisible())
        self.assertFalse(canvas.nodes[3].isVisible())
        self.assertTrue(canvas.nodes[4].isVisible())
        self.assertFalse(canvas.comments[0].isVisible())
        self.assertTrue(all(not edge.isVisible() for edge in canvas.edges if edge.source in {1, 2, 3}))

        first_lane.toggle_fold()
        app.processEvents()
        self.assertTrue(all(canvas.nodes[index].isVisible() for index in (1, 2, 3, 4)))
        self.assertTrue(canvas.comments[0].isVisible())
        canvas.close()

    def test_branch_drag_moves_lane_child_group_nodes_and_edges_together(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [
                {"action": "wait", "workflow_id": "branch-1", "workflow_label": "1번 분기", "on_success": 2},
                {"action": "wait", "workflow_id": "branch-1", "workflow_label": "1번 분기", "on_success": 3},
                {"action": "wait"},
            ],
            "graph_positions": {"1": [0, 0], "2": [280, 0], "3": [560, 0]},
            "graph_comments": [{
                "id": "branch-child", "title": "하위 그룹", "color": "green", "node_indexes": [2, 3],
            }],
        })
        app.processEvents()

        lane = canvas.workflow_items[0]
        group = canvas.comments[0]
        node_origin = QtCore.QPointF(canvas.nodes[3].pos())
        group_origin = QtCore.QPointF(group.pos())
        lane_origin = QtCore.QRectF(lane._rect)
        edge = next(edge for edge in canvas.edges if edge.source == 2 and edge.target == 3)
        edge_origin = QtCore.QRectF(edge.path().boundingRect())
        delta = QtCore.QPointF(90, 55)

        lane._begin_hierarchy_drag(QtCore.QPointF(100, 100))
        lane._move_hierarchy_drag(QtCore.QPointF(100, 100) + delta)

        self.assertEqual(node_origin + delta, canvas.nodes[3].pos())
        self.assertEqual(group_origin + delta, group.pos())
        self.assertEqual(lane_origin.topLeft() + delta, lane._rect.topLeft())
        self.assertEqual(edge_origin.topLeft() + delta, edge.path().boundingRect().topLeft())

        lane._dragging = False
        canvas.finish_node_move()
        canvas.close()

    def test_branch_drag_updates_folded_group_proxy_edge_in_same_frame(self) -> None:
        from PySide6 import QtCore, QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [
                {"action": "wait", "workflow_id": "branch-1", "workflow_label": "1번 분기", "on_success": 2},
                {"action": "wait", "workflow_id": "branch-1", "workflow_label": "1번 분기", "on_success": 3},
                {"action": "wait"},
            ],
            "graph_positions": {"1": [0, 0], "2": [280, 0], "3": [560, 0]},
            "graph_comments": [{
                "id": "folded-child", "title": "접힌 하위 그룹", "color": "yellow",
                "node_indexes": [2], "collapsed": True,
            }],
        })
        app.processEvents()

        lane = canvas.workflow_items[0]
        proxy = next(
            item for item in canvas._group_proxy_edges
            if item.source_edge.source == 2 and item.source_edge.target == 3
        )
        proxy_origin = QtCore.QRectF(proxy.path().boundingRect())
        delta = QtCore.QPointF(75, 45)

        lane._begin_hierarchy_drag(QtCore.QPointF(50, 50))
        lane._move_hierarchy_drag(QtCore.QPointF(50, 50) + delta)

        self.assertIn(proxy, canvas._group_proxy_edges)
        self.assertEqual(proxy_origin.topLeft() + delta, proxy.path().boundingRect().topLeft())
        lane._dragging = False
        canvas.finish_node_move()
        canvas.close()

    def test_alignment_keeps_first_selected_node_as_anchor(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [{"action": "wait"}, {"action": "wait"}, {"action": "wait"}],
            "graph_positions": {"1": [400, 220], "2": [0, 0], "3": [100, 500]},
        })
        canvas.nodes[1].setSelected(True)
        canvas.nodes[2].setSelected(True)
        canvas.nodes[3].setSelected(True)
        app.processEvents()
        anchor_pos = canvas.nodes[1].pos()

        canvas.align_selected_nodes("top")

        self.assertEqual(anchor_pos, canvas.nodes[1].pos())
        self.assertEqual(anchor_pos.y(), canvas.nodes[2].pos().y())
        self.assertEqual(anchor_pos.y(), canvas.nodes[3].pos().y())
        self.assertGreater(canvas.nodes[2].pos().x(), canvas.nodes[1].pos().x())
        self.assertGreater(canvas.nodes[3].pos().x(), canvas.nodes[2].pos().x())
        canvas.close()

    def test_low_zoom_edge_paint_never_fills_open_path(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets
        from macro_studio.node_editor import EdgeItem

        edge = SimpleNamespace(
            pen=lambda: QtGui.QPen(QtGui.QColor("#38E7FF"), 2.6),
            path=lambda: QtGui.QPainterPath(QtCore.QPointF(0, 0)),
        )
        painter = mock.Mock()
        painter.worldTransform.return_value = QtGui.QTransform.fromScale(0.5, 0.5)
        option = QtWidgets.QStyleOptionGraphicsItem()

        EdgeItem.paint(edge, painter, option)

        painter.setBrush.assert_called_once_with(QtCore.Qt.NoBrush)

    def test_interactive_drag_updates_only_connected_edges_until_release(self) -> None:
        from PySide6 import QtWidgets
        from macro_studio.node_editor import NodeCanvas

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        canvas = NodeCanvas()
        canvas.set_macro({
            "steps": [
                {"action": "wait", "on_success": 2},
                {"action": "wait", "on_success": 3},
                {"action": "wait"},
            ],
            "graph_positions": {"1": [0, 0], "2": [300, 0], "3": [600, 0]},
        })
        first_edge = next(edge for edge in canvas.edges if edge.source == 1)
        second_edge = next(edge for edge in canvas.edges if edge.source == 2)
        first_edge.update_path = mock.Mock()
        second_edge.update_path = mock.Mock()

        with mock.patch.object(canvas, "_route_edges") as full_route:
            canvas.begin_node_move()
            canvas.node_moved(1)
            canvas._move_frame_timer.stop()
            canvas._flush_node_move_frame()
            full_route.assert_not_called()
            first_edge.update_path.assert_called_once_with()
            second_edge.update_path.assert_not_called()

            canvas.finish_node_move()
            full_route.assert_called_once_with()
        canvas.close()


if __name__ == "__main__":
    unittest.main()
