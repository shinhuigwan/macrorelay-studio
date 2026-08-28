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
                if action == "image_search":
                    aliases = [str(value) for value in step.get("assets") or [] if str(value).strip()] if isinstance(step.get("assets"), list) else []
                    primary = str(step.get("asset") or "")
                    if primary and primary not in aliases:
                        aliases.insert(0, primary)
                    for alias in aliases or [primary]:
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
