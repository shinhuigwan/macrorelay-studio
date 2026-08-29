from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from PySide6 import QtWidgets


@dataclass(frozen=True)
class TestCaseResult:
    name: str
    passed: bool
    messages: tuple[str, ...]
    visited: tuple[int, ...]
    predicted_clicks: int


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def simulate_macro(macro: dict[str, Any], case: dict[str, Any]) -> TestCaseResult:
    steps = macro.get("steps") if isinstance(macro.get("steps"), list) else []
    fixtures = case.get("fixtures") if isinstance(case.get("fixtures"), dict) else {}
    image_states = fixtures.get("images") if isinstance(fixtures.get("images"), dict) else {}
    ocr_states = fixtures.get("ocr") if isinstance(fixtures.get("ocr"), dict) else {}
    variables = dict(fixtures.get("variables") or {})
    visited: list[int] = []
    clicks = 0
    current = int(macro.get("graph_start_step") or 1)
    last_ocr = ""
    loop_counts: dict[int, int] = {}
    transition_by_step: dict[int, int] = {}
    for _guard in range(1000):
        if not 1 <= current <= len(steps):
            break
        step_index = current
        step = steps[step_index - 1] if isinstance(steps[step_index - 1], dict) else {}
        action = str(step.get("action") or "")
        visited.append(step_index)
        success = True
        if action == "image_search":
            aliases = step.get("assets") if isinstance(step.get("assets"), list) else [step.get("asset")]
            success = any(bool(image_states.get(str(alias), False)) for alias in aliases if alias)
            if success and isinstance(step.get("click"), dict):
                click = step["click"]
                clicks += int(bool(click.get("click_image", True))) + int(bool(click.get("click_offset", False)))
        elif action == "ocr":
            raw = ocr_states.get(str(step_index), ocr_states.get(str(step.get("label") or ""), ""))
            last_ocr = str(raw or "")
            success = bool(last_ocr)
            store = str(step.get("store_var") or "").strip().lstrip("$")
            if store:
                match = re.search(r"[+-]?\d+(?:\.\d+)?", last_ocr)
                variables[store] = _number(match.group(0)) if match else last_ocr
        elif action == "text_condition":
            needle = str(step.get("needle") or "")
            success = needle in last_ocr
        elif action == "set_var":
            variables[str(step.get("name") or "value")] = step.get("value")
        elif action in {"mouse_click", "inactive_click"}:
            clicks += 1
        repeat_var = str(step.get("repeat_var") or "").strip().lstrip("$")
        repeat_limit = max(1, int(_number(variables.get(repeat_var)))) if repeat_var else max(1, int(step.get("repeat") or 1))
        loop_counts[step_index] = loop_counts.get(step_index, 0) + 1
        if loop_counts[step_index] < repeat_limit:
            next_step = step_index
        else:
            loop_counts[step_index] = 0
            route_value = step.get("on_success") if success else step.get("on_fail")
            if action == "text_condition":
                route_value = step.get("on_match") if success else step.get("on_no_match")
            next_step = int(route_value or 0)
            if next_step <= 0:
                next_step = step_index + 1
        transition_by_step[step_index] = next_step
        current = next_step
    expected = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    messages: list[str] = []
    expected_clicks = expected.get("click_count")
    if expected_clicks is not None and clicks != int(expected_clicks):
        messages.append(f"예상 클릭 {expected_clicks}회, 실제 예측 {clicks}회")
    expected_repeat = expected.get("repeat") if isinstance(expected.get("repeat"), dict) else {}
    for raw_step, count in expected_repeat.items():
        actual = visited.count(int(raw_step))
        if actual != int(count):
            messages.append(f"{raw_step}번 노드 예상 {count}회, 실제 {actual}회")
    expected_next = expected.get("next_by_step") if isinstance(expected.get("next_by_step"), dict) else {}
    for raw_step, target in expected_next.items():
        actual = transition_by_step.get(int(raw_step), 0)
        if actual != int(target):
            messages.append(f"{raw_step}번 다음 노드 예상 {target}, 실제 {actual}")
    expected_visited = expected.get("visited")
    if isinstance(expected_visited, list) and list(map(int, expected_visited)) != visited:
        messages.append(f"방문 순서 불일치: {visited}")
    return TestCaseResult(str(case.get("name") or "이름 없는 테스트"), not messages, tuple(messages), tuple(visited), clicks)


def run_test_cases(macro: dict[str, Any]) -> list[TestCaseResult]:
    cases = macro.get("test_cases") if isinstance(macro.get("test_cases"), list) else []
    return [simulate_macro(macro, case) for case in cases if isinstance(case, dict)]


class MacroTestCaseDialog(QtWidgets.QDialog):
    def __init__(self, macro: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.macro = macro
        self.setWindowTitle("매크로 테스트 케이스")
        self.resize(900, 700)
        root = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("이미지·OCR·변수 입력과 기대 경로를 JSON으로 저장합니다. 실행은 시뮬레이션이므로 실제 클릭이 발생하지 않습니다.")
        hint.setWordWrap(True); root.addWidget(hint)
        self.editor = QtWidgets.QPlainTextEdit()
        cases = macro.get("test_cases") if isinstance(macro.get("test_cases"), list) else []
        self.editor.setPlainText(json.dumps(cases, ensure_ascii=False, indent=2)); root.addWidget(self.editor, 1)
        self.results = QtWidgets.QPlainTextEdit(); self.results.setReadOnly(True); self.results.setMaximumHeight(180); root.addWidget(self.results)
        row = QtWidgets.QHBoxLayout()
        sample = QtWidgets.QPushButton("예제 추가"); run = QtWidgets.QPushButton("▷ 전체 회귀 검사")
        sample.clicked.connect(self._sample); run.clicked.connect(self._run)
        row.addWidget(sample); row.addWidget(run); row.addStretch(1); root.addLayout(row)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _parse(self) -> list[dict[str, Any]]:
        payload = json.loads(self.editor.toPlainText() or "[]")
        if not isinstance(payload, list): raise ValueError("테스트 케이스는 JSON 배열이어야 합니다.")
        return [item for item in payload if isinstance(item, dict)]

    def _sample(self) -> None:
        try:
            cases = self._parse()
        except (ValueError, json.JSONDecodeError) as exc:
            QtWidgets.QMessageBox.warning(self, "JSON 확인", str(exc))
            return
        cases.append({"name": "이미지 성공 경로", "fixtures": {"images": {"이미지A": True}}, "expect": {"next_by_step": {"1": 3}, "click_count": 0}})
        self.editor.setPlainText(json.dumps(cases, ensure_ascii=False, indent=2))

    def _run(self) -> None:
        try: cases = self._parse()
        except (ValueError, json.JSONDecodeError) as exc: QtWidgets.QMessageBox.warning(self, "JSON 확인", str(exc)); return
        probe = dict(self.macro); probe["test_cases"] = cases
        report = run_test_cases(probe)
        self.results.setPlainText("\n".join(("✓ " if item.passed else "✗ ") + item.name + ("" if item.passed else " · " + "; ".join(item.messages)) for item in report) or "저장된 테스트가 없습니다.")

    def _accept(self) -> None:
        try: self.cases = self._parse()
        except (ValueError, json.JSONDecodeError) as exc: QtWidgets.QMessageBox.warning(self, "JSON 확인", str(exc)); return
        self.accept()
