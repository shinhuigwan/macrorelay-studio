"""Ordered atomic config writes away from the GUI thread during layout editing."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def write_deck_config(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class DeckConfigWriter:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deck-config")
        self.pending = None

    def save(self, path, snapshot, *, wait=False):
        # A single queue orders async edits and synchronous saves. A backup or
        # close cannot be overwritten afterwards by an older background write.
        if self.pending is not None:
            self.pending.cancel()  # Coalesce a queued (not yet started) edit.
        self.pending = self.executor.submit(write_deck_config, path, snapshot)
        if wait:
            self.pending.result()
        return self.pending

    def close(self):
        self.executor.shutdown(wait=True)
