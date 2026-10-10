import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PySide6 import QtCore, QtTest, QtWidgets
from macro_studio.deck_instance import DeckSingleInstance


class DeckInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_duplicate_reveals_existing_window_and_release_allows_restart(self):
        with tempfile.TemporaryDirectory() as root:
            owner = DeckSingleInstance(Path(root))
            duplicate = DeckSingleInstance(Path(root))
            window = QtWidgets.QWidget()
            try:
                self.assertTrue(owner.acquire_or_notify())
                owner.bind_window(window)
                self.assertFalse(window.isVisible())
                self.assertFalse(duplicate.acquire_or_notify())
                self.assertFalse(duplicate.error)
                QtTest.QTest.qWait(80)
                self.assertTrue(window.isVisible())
                self.assertFalse(duplicate.owner)
                duplicate.close()
                self.assertTrue(owner.lock.isLocked())
                owner.close()
                self.assertTrue(duplicate.acquire_or_notify())
            finally:
                owner.close()
                duplicate.close()
                window.close()

    def test_early_activation_is_delivered_after_window_is_bound(self):
        with tempfile.TemporaryDirectory() as root:
            owner, duplicate = DeckSingleInstance(Path(root)), DeckSingleInstance(Path(root))
            window = QtWidgets.QWidget()
            try:
                self.assertTrue(owner.acquire_or_notify())
                self.assertFalse(duplicate.acquire_or_notify())
                QtTest.QTest.qWait(80)
                self.assertTrue(owner.activation_pending)
                owner.bind_window(window)
                self.assertTrue(window.isVisible())
            finally:
                owner.close()
                duplicate.close()
                window.close()

    def test_separate_roots_do_not_block_each_other_and_listen_failure_unlocks(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one, two = DeckSingleInstance(Path(first)), DeckSingleInstance(Path(second))
            try:
                self.assertTrue(one.acquire_or_notify())
                self.assertTrue(two.acquire_or_notify())
                self.assertNotEqual(one.name, two.name)
                two.close()
                with mock.patch.object(two.server, "listen", return_value=False), mock.patch.object(two.server, "errorString", return_value="listen failed"):
                    self.assertFalse(two.acquire_or_notify())
                self.assertFalse(two.lock.isLocked())
                self.assertEqual("listen failed", two.error)
            finally:
                one.close()
                two.close()

    def test_second_process_notifies_without_loading_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            owner = DeckSingleInstance(Path(root))
            window = QtWidgets.QWidget()
            try:
                self.assertTrue(owner.acquire_or_notify())
                owner.bind_window(window)
                environment = dict(os.environ, MACRORELAY_HOME=root, QT_QPA_PLATFORM="offscreen")
                script = Path(__file__).resolve().parents[1] / "quickslot_app.py"
                process = subprocess.Popen([sys.executable, str(script)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                _, errors = process.communicate(timeout=15)
                self.assertEqual(0, process.returncode, errors.decode(errors="replace"))
                QtTest.QTest.qWait(100)
                self.assertTrue(window.isVisible())
                self.assertFalse((Path(root) / ".quickslot_deck_config.json").exists())
                self.assertFalse((Path(root) / "hotkeys.json").exists())
            finally:
                owner.close()
                window.close()

    def test_crashed_owner_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as root:
            code = (
                "import os,sys; from pathlib import Path; from PySide6 import QtCore; "
                "from macro_studio.deck_instance import DeckSingleInstance; "
                "app=QtCore.QCoreApplication([]); owner=DeckSingleInstance(Path(sys.argv[1])); "
                "assert owner.acquire_or_notify(); os._exit(0)"
            )
            result = subprocess.run([sys.executable, "-c", code, root], capture_output=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
            owner = DeckSingleInstance(Path(root))
            try:
                self.assertTrue(owner.acquire_or_notify())
            finally:
                owner.close()
