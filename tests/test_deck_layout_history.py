import unittest
from macro_studio.deck_layout_history import LayoutHistory


class LayoutHistoryTests(unittest.TestCase):
    def test_undo_redo_new_edit_and_noop(self):
        history = LayoutHistory()
        first, second, third = {0: 0, 1: 1}, {0: 1, 1: 0}, {0: 3, 1: 0}
        history.record(first, first)
        self.assertIsNone(history.undo(first))
        history.record(first, second)
        history.record(second, third)
        self.assertEqual(second, history.undo(third))
        self.assertEqual(first, history.undo(second))
        self.assertIsNone(history.undo(first))
        self.assertEqual(second, history.redo(first))
        self.assertEqual(third, history.redo(second))
        self.assertIsNone(history.redo(third))
        history.undo(third)
        history.record(second, {0: 4, 1: 0})
        self.assertIsNone(history.redo({0: 4, 1: 0}))

    def test_bounds_snapshot_and_replaced_layout_guard(self):
        history = LayoutHistory(limit=2)
        for index in range(3):
            before, after = {0: index}, {0: index + 1}
            history.record(before, after)
            before[0] = -1
            after[0] = -1
        self.assertEqual({0: 2}, history.undo({0: 3}))
        self.assertEqual({0: 1}, history.undo({0: 2}))
        self.assertIsNone(history.undo({0: 1}))
        self.assertIsNone(history.redo({0: 99}))
        self.assertFalse(history.future)
