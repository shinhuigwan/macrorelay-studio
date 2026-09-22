from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from macro_studio.player import (
    PLAYER_ICON_FILENAME,
    PLAYER_RUN_BUTTON_MIN_HEIGHT,
    MacroPlayerWindow,
)


class PlayerProgressTests(unittest.TestCase):
    def test_player_branding_and_primary_action_size(self) -> None:
        self.assertEqual("macrorelay-player.ico", PLAYER_ICON_FILENAME)
        self.assertGreaterEqual(PLAYER_RUN_BUTTON_MIN_HEIGHT, 56)

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

    def test_compact_mode_only_shows_macro_name_and_controls(self) -> None:
        hidden_widgets = [mock.Mock() for _ in range(5)]
        compact_header = mock.Mock()
        window = SimpleNamespace(
            _compact_mode=False,
            header_widget=hidden_widgets[0],
            select_card=hidden_widgets[1],
            opts_card=hidden_widgets[2],
            dash_card=hidden_widgets[3],
            log_edit=hidden_widgets[4],
            compact_header=compact_header,
            main_layout=mock.Mock(),
            btn_run=mock.Mock(),
            btn_pause=mock.Mock(),
            btn_stop=mock.Mock(),
            is_paused=False,
            setMinimumSize=mock.Mock(),
            setMaximumHeight=mock.Mock(),
            resize=mock.Mock(),
            width=mock.Mock(return_value=460),
            setWindowTitle=mock.Mock(),
            _save_settings=mock.Mock(),
        )
        window._sync_control_button_labels = lambda: MacroPlayerWindow._sync_control_button_labels(window)

        MacroPlayerWindow._toggle_compact_mode(window, True)

        for widget in hidden_widgets:
            widget.hide.assert_called_once_with()
        compact_header.show.assert_called_once_with()
        window.resize.assert_called_once_with(460, 118)
        window.btn_run.setText.assert_called_once_with("▶ 실행")
        window.btn_pause.setText.assert_called_once_with("Ⅱ 일시정지")
        window.btn_stop.setText.assert_called_once_with("■ 종료")

        MacroPlayerWindow._toggle_compact_mode(window, False)

        for widget in hidden_widgets:
            widget.show.assert_called_once_with()
        compact_header.hide.assert_called_once_with()
        window.setWindowTitle.assert_called_with("⚡ Macro Player")


if __name__ == "__main__":
    unittest.main()
