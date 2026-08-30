from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .repository import MacroRepository


@dataclass(frozen=True)
class Issue:
    severity: str
    title: str
    detail: str
    macro: str = ""
    step: int = 0


class ProjectValidator:
    def __init__(self, repository: MacroRepository) -> None:
        self.repository = repository

    @staticmethod
    def _region_is_invalid(region: Any) -> bool:
        if not isinstance(region, list) or len(region) < 4:
            return False
        try:
            left, top, right, bottom = (int(value) for value in region[:4])
        except (TypeError, ValueError):
            return True
        return left == right or top == bottom

    def validate(self) -> list[Issue]:
        issues: list[Issue] = []
        assets = self.repository.load_assets()
        tables = self.repository.load_tables()
        for summary in self.repository.list_macros():
            try:
                macro = self.repository.load_macro(summary.name)
            except (OSError, ValueError) as exc:
                issues.append(Issue("error", "손상된 매크로", str(exc), summary.name))
                continue
            steps = macro.get("steps") or []
            if not isinstance(steps, list):
                issues.append(Issue("error", "잘못된 단계 목록", "steps가 배열이 아닙니다.", summary.name))
                continue
            if not steps:
                issues.append(Issue("warning", "빈 매크로", "실행할 노드가 없습니다.", summary.name))
            if bool((macro.get("meta") or {}).get("ai_draft")):
                pending = [
                    index
                    for index, step in enumerate(steps, start=1)
                    if isinstance(step, dict) and step.get("needs_setup")
                ]
                detail = "미완성 설정 계속하기에서 검토를 완료해야 정식 실행할 수 있습니다."
                if pending:
                    detail += f" 설정 필요 노드: {', '.join(map(str, pending[:12]))}"
                issues.append(Issue("warning", "AI 초안", detail, summary.name))
            if len(steps) > 1:
                first = steps[0] if isinstance(steps[0], dict) else {}
                if (
                    first.get("action") != "flow_control"
                    and not int(first.get("on_success") or 0)
                    and not int(first.get("on_fail") or 0)
                ):
                    issues.append(
                        Issue(
                            "warning",
                            "시작 단계 연결 없음",
                            "순차 실행 호환 모드로 동작하지만 그래프 연결을 확인하세요.",
                            summary.name,
                            1,
                        )
                    )
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    issues.append(Issue("error", "잘못된 단계", "단계가 객체가 아닙니다.", summary.name, index))
                    continue
                action = str(step.get("action") or "")
                if not action:
                    issues.append(Issue("error", "액션 누락", "action 값이 없습니다.", summary.name, index))
                repeat_var = str(step.get("repeat_var") or "").strip().lstrip("$")
                if repeat_var and (
                    not repeat_var.isascii()
                    or not (repeat_var[0].isalpha() or repeat_var[0] == "_")
                    or not all(char.isalnum() or char == "_" for char in repeat_var)
                ):
                    issues.append(
                        Issue("error", "반복 변수 이름 오류", f"'{repeat_var}'", summary.name, index)
                    )
                if action in {"image_search", "screen_condition"}:
                    aliases = [str(value) for value in step.get("assets") or [] if str(value).strip()] if isinstance(step.get("assets"), list) else []
                    primary = str(step.get("asset") or "")
                    if primary and primary not in aliases:
                        aliases.insert(0, primary)
                    if not aliases:
                        issues.append(
                            Issue("error", "이미지 누락", "검색 이미지가 선택되지 않았습니다.", summary.name, index)
                        )
                    for alias in aliases:
                        metadata = assets.get(alias)
                        if not isinstance(metadata, dict):
                            issues.append(Issue("error", "이미지 누락", f"'{alias}' 이미지 별칭이 없습니다.", summary.name, index))
                        else:
                            path = (self.repository.root / str(metadata.get("file") or "")).resolve()
                            if not path.exists():
                                issues.append(Issue("error", "이미지 파일 누락", str(path), summary.name, index))
                    regions = step.get("regions") or []
                    if step.get("region") is not None:
                        regions = list(regions) + [step.get("region")]
                    if any(self._region_is_invalid(region) for region in regions):
                        issues.append(
                            Issue(
                                "warning",
                                "검색 영역이 한 점입니다",
                                "0×0 영역은 화면 전체 또는 유효한 사각형으로 바꾸세요.",
                                summary.name,
                                index,
                            )
                        )
                    if str(step.get("region_mode") or "screen") in {"window", "client"} and not (
                        str(step.get("region_window") or "").strip()
                        or str(step.get("region_window_exe") or "").strip()
                    ):
                        issues.append(
                            Issue("error", "이미지 검색 대상 창 누락", "창/클라이언트 범위에는 대상 프로그램이 필요합니다.", summary.name, index)
                        )
                if action == "ocr":
                    region = step.get("region")
                    if str(step.get("mode") or "region") == "region" and self._region_is_invalid(region):
                        issues.append(Issue("error", "OCR 영역 오류", "OCR 인식 영역의 너비와 높이를 확인하세요.", summary.name, index))
                    if str(step.get("ocr_action") or "extract") in {"find_text", "find_click", "find_click_offset"} and not str(
                        step.get("find_text") or ""
                    ).strip():
                        issues.append(Issue("error", "OCR 검색어 누락", "찾을 텍스트를 입력하세요.", summary.name, index))
                if action == "mouse_click" and str(step.get("coordinate_scope") or "screen") == "client" and not (
                    str(step.get("window") or "").strip() or str(step.get("window_exe") or "").strip()
                ):
                    issues.append(Issue("error", "프로그램 기준 좌표 대상 누락", "대상 프로그램을 다시 지정하세요.", summary.name, index))
                if action == "inactive_click" and not (
                    str(step.get("window") or "").strip() or str(step.get("window_exe") or "").strip()
                ):
                    issues.append(Issue("error", "비활성 클릭 대상 누락", "대상 창 또는 프로그램을 지정하세요.", summary.name, index))
                if action == "call_submacro":
                    target_macro = str(step.get("macro") or "").strip()
                    if not target_macro:
                        issues.append(Issue("error", "서브플로우 누락", "호출할 매크로를 선택하세요.", summary.name, index))
                    elif not self.repository.macro_path(target_macro).is_file():
                        issues.append(Issue("error", "서브플로우 파일 누락", f"'{target_macro}' 매크로를 찾을 수 없습니다.", summary.name, index))
                    elif target_macro == summary.name:
                        issues.append(Issue("error", "서브플로우 순환 호출", "매크로가 자기 자신을 호출할 수 없습니다.", summary.name, index))
                if action in {"image_search", "screen_condition"} and bool(step.get("repeat_on_success")):
                    issues.append(
                        Issue(
                            "warning",
                            "이미지 성공 반복 확인",
                            "이미지가 계속 보이면 같은 노드가 계속 실행됩니다. 정지 조건이 있는지 확인하세요.",
                            summary.name,
                            index,
                        )
                    )
                if action == "flow_control":
                    jump_to = int(step.get("jump_to") or 0)
                    repeat_count = int(step.get("repeat_count") or 0)
                    if repeat_count == 0 and 0 < jump_to <= index:
                        issues.append(
                            Issue(
                                "warning",
                                "무한 반복 가능성",
                                f"{jump_to}번 노드로 제한 없이 되돌아갑니다.",
                                summary.name,
                                index,
                            )
                        )
                table = str(step.get("table") or "")
                if table and table not in tables:
                    issues.append(Issue("error", "데이터 테이블 누락", f"'{table}' 테이블이 없습니다.", summary.name, index))
                for field in ("on_success", "on_fail"):
                    target = int(step.get(field) or 0)
                    if target < 0 or target > len(steps):
                        issues.append(
                            Issue("error", "잘못된 단계 연결", f"{field}={target}", summary.name, index)
                        )
                conditions = step.get("edge_conditions") or []
                if conditions and not isinstance(conditions, list):
                    issues.append(Issue("error", "잘못된 조건 분기", "edge_conditions가 배열이 아닙니다.", summary.name, index))
                elif isinstance(conditions, list):
                    for rule_index, rule in enumerate(conditions, start=1):
                        if not isinstance(rule, dict):
                            issues.append(Issue("error", "잘못된 조건 분기", f"{rule_index}번 규칙이 객체가 아닙니다.", summary.name, index))
                            continue
                        target = int(rule.get("target") or 0)
                        if not 1 <= target <= len(steps):
                            issues.append(Issue("error", "조건 분기 목적지 오류", f"규칙 {rule_index}: target={target}", summary.name, index))
                        if str(rule.get("source") or "edge_count") == "variable":
                            variable = str(rule.get("variable") or "")
                            if not variable or not variable.replace("_", "a").isalnum() or variable[0].isdigit():
                                issues.append(Issue("error", "조건 분기 변수 오류", f"규칙 {rule_index}: '{variable}'", summary.name, index))
        call_graph: dict[str, set[str]] = {}
        for summary in self.repository.list_macros():
            try:
                macro = self.repository.load_macro(summary.name)
            except (OSError, ValueError):
                continue
            call_graph[summary.name] = {
                str(step.get("macro") or "").strip()
                for step in macro.get("steps") or []
                if isinstance(step, dict) and step.get("action") == "call_submacro" and str(step.get("macro") or "").strip()
            }
        reported_cycles: set[tuple[str, ...]] = set()

        def visit(origin: str, current: str, path: list[str]) -> None:
            for target in call_graph.get(current, set()):
                if target == origin:
                    if current == origin and not path:
                        continue
                    cycle = tuple(sorted(set([*path, current, target])))
                    if cycle not in reported_cycles:
                        reported_cycles.add(cycle)
                        issues.append(
                            Issue("error", "서브플로우 간접 순환", " → ".join([*path, current, target]), origin)
                        )
                    continue
                if target in path or target not in call_graph:
                    continue
                visit(origin, target, [*path, current])

        for macro_name in call_graph:
            visit(macro_name, macro_name, [])
        return issues

    def stats(self) -> dict[str, int]:
        macros = self.repository.list_macros()
        assets = self.repository.load_assets()
        steps = sum(item.steps for item in macros)
        exports_ahk = len(list(self.repository.exports_dir.glob("*.ahk")))
        exports_exe = len(list(self.repository.exports_dir.glob("*.exe")))
        return {
            "macros": len(macros),
            "steps": steps,
            "assets": len(assets),
            "exports": exports_ahk + exports_exe,
        }
