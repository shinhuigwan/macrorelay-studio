from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from macro_studio.repository import MacroRepository
from macro_studio.validation import ProjectValidator


class ScopedValidationTests(unittest.TestCase):
    def test_current_macro_validation_does_not_scan_macro_list(self) -> None:
        repository = SimpleNamespace(
            root=Path(tempfile.gettempdir()),
            load_assets=mock.Mock(return_value={}),
            load_tables=mock.Mock(return_value={}),
            list_macros=mock.Mock(side_effect=AssertionError("전체 목록을 읽으면 안 됩니다.")),
            load_macro=mock.Mock(side_effect=AssertionError("전달된 매크로를 다시 읽으면 안 됩니다.")),
        )

        issues = ProjectValidator(repository).validate(
            "current",
            {"name": "current", "steps": [{"action": "wait", "duration": 10}]},
        )

        self.assertEqual([], issues)
        repository.list_macros.assert_not_called()
        repository.load_macro.assert_not_called()

    def test_asset_path_reuses_preloaded_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "assets" / "preview.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"preview")
            repository = SimpleNamespace(
                root=root,
                load_assets=mock.Mock(side_effect=AssertionError("인덱스를 다시 읽으면 안 됩니다.")),
            )

            path = MacroRepository.asset_path(
                repository,
                "preview",
                {"preview": {"file": "assets/preview.png"}},
            )

            self.assertEqual(asset.resolve(), path)
            repository.load_assets.assert_not_called()

    def test_current_macro_validation_checks_only_reachable_cycle(self) -> None:
        child_payloads = {
            "child-a": {"name": "child-a", "steps": [{"action": "call_submacro", "macro": "child-b"}]},
            "child-b": {"name": "child-b", "steps": [{"action": "call_submacro", "macro": "child-a"}]},
        }
        repository = SimpleNamespace(
            root=Path(tempfile.gettempdir()),
            load_assets=mock.Mock(return_value={}),
            load_tables=mock.Mock(return_value={}),
            list_macros=mock.Mock(side_effect=AssertionError("전체 목록을 읽으면 안 됩니다.")),
            load_macro=mock.Mock(side_effect=lambda name: child_payloads[name]),
            macro_path=mock.Mock(return_value=SimpleNamespace(is_file=lambda: True)),
        )

        issues = ProjectValidator(repository).validate(
            "current",
            {"name": "current", "steps": [{"action": "call_submacro", "macro": "child-a"}]},
        )

        self.assertTrue(any(issue.title == "서브플로우 간접 순환" for issue in issues))
        repository.list_macros.assert_not_called()


if __name__ == "__main__":
    unittest.main()
