"""Bounded, session-local placement history, independent of executable actions."""


class LayoutHistory:
    def __init__(self, limit=100):
        self.limit = max(1, limit)
        self.clear()

    def clear(self):
        self.past = []
        self.future = []

    def record(self, before, after):
        if before == after:
            return
        self.past.append((dict(before), dict(after)))
        self.past = self.past[-self.limit:]
        self.future.clear()

    def undo(self, current):
        if not self.past:
            return None
        if current != self.past[-1][1]:
            self.clear()  # Never apply history to replaced presets or hotkeys.
            return None
        entry = self.past.pop()
        self.future.append(entry)
        return dict(entry[0])

    def redo(self, current):
        if not self.future:
            return None
        if current != self.future[-1][0]:
            self.clear()
            return None
        entry = self.future.pop()
        self.past.append(entry)
        return dict(entry[1])
