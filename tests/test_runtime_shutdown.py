from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class RuntimeShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def test_active_run_markers_protect_shared_vision_engine(self) -> None:
        from macro_studio.repository import MacroRepository

        with tempfile.TemporaryDirectory() as folder:
            repository = MacroRepository(Path(folder))
            active = repository.exports_dir / ".run_results" / "active"
            active.mkdir(parents=True)
            live = active / "live.json"
            live.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            stale = active / "stale.json"
            stale.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")

            self.assertTrue(repository._has_running_macro_process())
            self.assertFalse(stale.exists())

            process = SimpleNamespace(macrorelay_run_marker=live)
            repository.release_macro_process(process)
            self.assertFalse(repository._has_running_macro_process())

    def test_vision_shutdown_only_runs_when_no_macro_is_alive(self) -> None:
        from macro_studio.repository import MacroRepository

        repository = object.__new__(MacroRepository)
        repository._has_running_macro_process = mock.Mock(return_value=True)
        repository._vision_request = mock.Mock(return_value={"ok": True})
        self.assertFalse(repository.shutdown_vision_engine_if_idle())
        repository._vision_request.assert_not_called()

        repository._has_running_macro_process.return_value = False
        self.assertTrue(repository.shutdown_vision_engine_if_idle())
        repository._vision_request.assert_called_once_with({"cmd": "shutdown"}, timeout=0.4)

    def test_main_window_shutdown_stops_tracked_processes(self) -> None:
        from macro_studio.main_window import MainWindow

        tracked = SimpleNamespace(
            _running_macro_processes={123: object()},
            stop_running_macros=mock.Mock(),
            repository=mock.Mock(),
        )
        MainWindow.shutdown_runtime(tracked)
        tracked.stop_running_macros.assert_called_once_with()
        tracked.repository.shutdown_vision_engine_if_idle.assert_not_called()

        idle = SimpleNamespace(
            _running_macro_processes={},
            stop_running_macros=mock.Mock(),
            repository=mock.Mock(),
        )
        MainWindow.shutdown_runtime(idle)
        idle.repository.shutdown_vision_engine_if_idle.assert_called_once_with()

    def test_player_shutdown_uses_taskkill_tree_before_direct_kill(self) -> None:
        from macro_studio.player import MacroPlayerWindow

        process = mock.Mock(pid=43210)
        repository = mock.Mock()
        fake = SimpleNamespace(
            is_running=True,
            current_process=process,
            is_paused=False,
            _stopwatch_timer=mock.Mock(),
            _monitor_timer=mock.Mock(),
            repository=repository,
            lbl_status=mock.Mock(),
            lbl_step_detail=mock.Mock(),
            btn_run=mock.Mock(),
            btn_pause=mock.Mock(),
            btn_stop=mock.Mock(),
            log_edit=mock.Mock(),
            _sync_control_button_labels=mock.Mock(),
        )
        completed = SimpleNamespace(returncode=0)
        with mock.patch("macro_studio.player.subprocess.run", return_value=completed) as taskkill:
            MacroPlayerWindow.stop_macro(fake)

        taskkill.assert_called_once()
        process.kill.assert_not_called()
        process.wait.assert_called_once_with(timeout=1.0)
        repository.release_macro_process.assert_called_once_with(process)
        repository.shutdown_vision_engine_if_idle.assert_called_once_with()

    def test_player_shutdown_is_idempotent(self) -> None:
        from macro_studio.player import MacroPlayerWindow

        fake = SimpleNamespace(
            _shutting_down=False,
            stop_macro=mock.Mock(),
            repository=mock.Mock(),
        )
        MacroPlayerWindow.shutdown_runtime(fake)
        MacroPlayerWindow.shutdown_runtime(fake)
        self.assertTrue(fake._shutting_down)
        self.assertEqual(2, fake.stop_macro.call_count)


if __name__ == "__main__":
    unittest.main()
