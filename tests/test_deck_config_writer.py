import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from macro_studio.deck_config_writer import DeckConfigWriter, write_deck_config


class DeckConfigWriterTests(unittest.TestCase):
    def test_atomic_unicode_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "deck.json"
            write_deck_config(path, {"프리셋": "트레이딩", "positions": {"0": 3}})
            self.assertEqual("트레이딩", json.loads(path.read_text(encoding="utf-8"))["프리셋"])
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_background_write_coalesces_and_sync_flush_preserves_newest_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        writes, threads = [], []
        def slow_write(path, snapshot):
            if snapshot["revision"] == 1:
                entered.set()
                release.wait(5)
            writes.append(snapshot["revision"])
            threads.append(threading.get_ident())
        writer = DeckConfigWriter()
        try:
            with mock.patch("macro_studio.deck_config_writer.write_deck_config", side_effect=slow_write):
                writer.save(Path("unused"), {"revision": 1})
                self.assertTrue(entered.wait(2))
                writer.save(Path("unused"), {"revision": 2})
                writer.save(Path("unused"), {"revision": 3})
                release.set()
                writer.save(Path("unused"), {"revision": 4}, wait=True)
                self.assertEqual(1, writes[0])
                self.assertEqual(4, writes[-1])
                self.assertNotIn(2, writes)
                self.assertTrue(all(value != threading.get_ident() for value in threads))
        finally:
            release.set()
            writer.close()

    def test_sync_save_reports_failed_write(self):
        writer = DeckConfigWriter()
        try:
            with mock.patch("macro_studio.deck_config_writer.write_deck_config", side_effect=OSError("full")):
                with self.assertRaises(OSError):
                    writer.save(Path("unused"), {}, wait=True)
        finally:
            writer.close()
