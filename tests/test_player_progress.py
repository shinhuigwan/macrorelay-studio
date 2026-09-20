from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from macro_studio.player import MacroPlayerWindow


class PlayerProgressTests(unittest.TestCase):
    def test_reads_ahk_utf8_bom_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.progress.txt"
            path.write_text("7", encoding="utf-8-sig")
            self.assertEqual(7, MacroPlayerWindow._read_progress_file(path))

    def test_invalid_or_missing_progress_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.progress.txt"
            self.assertEqual(0, MacroPlayerWindow._read_progress_file(path))
            path.write_text("waiting", encoding="utf-8")
            self.assertEqual(0, MacroPlayerWindow._read_progress_file(path))


if __name__ == "__main__":
    unittest.main()
