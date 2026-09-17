from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
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

    def validate(
        self,
        macro_name: str | None = None,
        macro_payload: dict[str, Any] | None = None,
    ) -> list[Issue]:
        issues: list[Issue] = []
        assets = self.repository.load_assets()
        tables = self.repository.load_tables()
        summaries = (
            [SimpleNamespace(name=macro_name)]
            if macro_name
            else self.repository.list_macros()
        )
        for summary in summaries:
            try:
                macro = (
                    macro_payload
                    if macro_name and summary.name == macro_name and isinstance(macro_payload, dict)
                    else self.repository.load_macro(summary.name)
                )
            except (OSError, ValueError) as exc:
                issues.append(Issue("error", "손상된 매크로", str(exc), summary.name))
                continue
            steps = macro.get("steps") or []
            if not isinstance(steps, list):
                issues.append(Issue("error", "잘못된 단계 목록", "steps가 배열이 아닙니다.", summary.name))
                continue
            if not steps:
                issues.append(Issue("warning", "빈 매크로", "실행할 노드가 없습니다.", summary.name))
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
                asset_routes = step.get("asset_routes")
                if asset_routes and not isinstance(asset_routes, dict):
                    issues.append(Issue("error", "잘못된 이미지별 분기", "asset_routes가 객체가 아닙니다.", summary.name, index))
                elif isinstance(asset_routes, dict):
                    known_assets = {
                        str(value) for value in step.get("assets") or [] if str(value).strip()
                    } if isinstance(step.get("assets"), list) else set()
                    primary_asset = str(step.get("asset") or "").strip()
                    if primary_asset:
                        known_assets.add(primary_asset)
                    for alias, route in asset_routes.items():
                        if str(alias) not in known_assets:
                            issues.append(Issue("warning", "분기 이미지 누락", f"'{alias}' 이미지가 현재 노드에 없습니다.", summary.name, index))
                        if not isinstance(route, dict):
                            issues.append(Issue("error", "잘못된 이미지별 분기", f"'{alias}' 분기 값이 객체가 아닙니다.", summary.name, index))
                            continue
                        for outcome in ("true", "fail"):
                            try:
                                target = int(route.get(outcome) or 0)
                            except (TypeError, ValueError):
                                issues.append(
                                    Issue(
                                        "error",
                                        "잘못된 이미지별 분기",
                                        f"{alias}.{outcome} 값이 노드 번호가 아닙니다.",
                                        summary.name,
                                        index,
                                    )
                                )
                                continue
                            if target and not 1 <= target <= len(steps):
                                issues.append(Issue("error", "이미지별 분기 목적지 오류", f"{alias}.{outcome}={target}", summary.name, index))
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
        # 빌더에서 매크로 하나를 선택할 때는 현재 매크로와 그 매크로에서
        # 실제로 도달할 수 있는 서브플로우만 따라가 순환을 검사합니다.
        if macro_name:
            call_graph: dict[str, set[str]] = {}
            payloads: dict[str, dict[str, Any]] = {
                macro_name: macro if isinstance(macro, dict) else {}
            }
            pending = [macro_name]
            while pending:
                current = pending.pop()
                current_payload = payloads.get(current) or {}
                targets = {
                    str(step.get("macro") or "").strip()
                    for step in current_payload.get("steps") or []
                    if isinstance(step, dict)
                    and step.get("action") == "call_submacro"
                    and str(step.get("macro") or "").strip()
                }
                call_graph[current] = targets
                for target in targets:
                    if target in payloads or not self.repository.macro_path(target).is_file():
                        continue
                    try:
                        child = self.repository.load_macro(target)
                    except (OSError, ValueError):
                        continue
                    if isinstance(child, dict):
                        payloads[target] = child
                        pending.append(target)

            states: dict[str, int] = {}
            stack: list[str] = []
            reported_cycles: set[tuple[str, ...]] = set()

            def visit_reachable(current: str) -> None:
                states[current] = 1
                stack.append(current)
                for target in call_graph.get(current, set()):
                    if target not in call_graph:
                        continue
                    if states.get(target, 0) == 0:
                        visit_reachable(target)
                    elif states.get(target) == 1:
                        cycle = tuple([*stack[stack.index(target) :], target])
                        if len(cycle) == 2 and target == macro_name:
                            continue
                        if cycle not in reported_cycles:
                            reported_cycles.add(cycle)
                            issues.append(
                                Issue("error", "서브플로우 간접 순환", " → ".join(cycle), macro_name)
                            )
                stack.pop()
                states[current] = 2

            visit_reachable(macro_name)
            return issues

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
