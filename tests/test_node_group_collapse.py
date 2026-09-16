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
        self.assertTrue(canvas.dump_comments()[0]["collapsed"])

        group.set_collapsed(False)
        app.processEvents()
        self.assertTrue(all(node.isVisible() for node in canvas.nodes.values()))
        self.assertTrue(all(edge.isVisible() for edge in canvas.edges))
        self.assertFalse(canvas.dump_comments()[0]["collapsed"])
        canvas.close()

    def test_removing_collapsed_group_reveals_member_nodes(self) -> None:
        app, canvas = self._canvas()
        group = canvas.comments[0]

        canvas.remove_comment_box(group)
        app.processEvents()
        self.assertTrue(all(node.isVisible() for node in canvas.nodes.values()))
        self.assertEqual([], canvas.dump_comments())
        canvas.close()


if __name__ == "__main__":
    unittest.main()
