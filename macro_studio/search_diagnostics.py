from __future__ import annotations

from typing import Any, Iterable


INACTIVE_METHOD_LABELS = {
    "auto": "자동 (앱별 PostMessage/ControlClick)",
    "controlclick": "ControlClick",
    "postmessage": "PostMessage",
    "direct_postmessage": "최상위 창 PostMessage",
    "handle_probe": "저장된 자식 핸들",
}


def diagnose_capture_failure(description: str, step: dict[str, Any]) -> tuple[str, str]:
    """Return a short cause and an actionable explanation for capture failures."""
    text = str(description or "")
    target = str(step.get("region_window_exe") or step.get("region_window") or "").strip()
    if "찾지 못했습니다" in text:
        return "대상 창 없음", f"{target or '지정 프로그램'}이 실행 중인지 확인한 뒤 [프로그램 선택]으로 다시 지정하세요."
    if target:
        return "대상 캡처 실패", "창은 지정됐지만 화면을 가져오지 못했습니다. 최소화 상태·캡처 권한·창 크기를 확인하세요."
    return "화면 캡처 실패", "현재 모니터 화면을 가져오지 못했습니다. 화면 잠금 또는 캡처 권한을 확인하세요."


def diagnose_image_result(result: dict[str, Any]) -> tuple[str, str]:
    """Classify a visual-search result without hiding its measurable evidence."""
    if bool(result.get("found")):
        return "정상 탐지", f"지정 영역에서 {float(result.get('score') or 0):.1%}로 발견했습니다."
    explicit = str(result.get("reason") or "").strip()
    if explicit:
        if "파일 없음" in explicit:
            return "이미지 파일 없음", explicit
        if "디코딩" in explicit:
            return "이미지 읽기 실패", explicit
        if "보다 작음" in explicit:
            return "검색 영역이 너무 작음", explicit
        return "검색 설정 오류", explicit
    if bool(result.get("full_found")):
        return "검색 영역 이탈", "화면 전체에서는 발견했지만 지정 영역 밖에 있습니다. 실제 위치로 자동 맞춤하세요."
    score = float(result.get("full_score") or result.get("score") or 0)
    threshold = float(result.get("threshold") or 0)
    if threshold and score >= max(0.0, threshold - 0.05):
        return "정확도 기준 미달", f"최고 {score:.1%}, 기준 {threshold:.1%}입니다. 이미지 또는 기준값을 확인하세요."
    if score >= 0.35:
        return "화면 모양·크기 차이", f"최고 유사도 {score:.1%}입니다. 배율, 색상 변화, 투명 배경을 확인하세요."
    return "화면에서 이미지 없음", f"화면 전체 최고 유사도는 {score:.1%}입니다. 현재 화면과 등록 이미지를 확인하세요."


def inactive_click_preflight(step: dict[str, Any], target_hwnd: int, target_class: str = "") -> dict[str, Any]:
    click = step.get("click") if isinstance(step.get("click"), dict) else {}
    enabled = bool(step.get("click_enabled")) and str(step.get("click_target") or "").lower() != "none"
    if not enabled:
        return {"required": False, "valid": True, "title": "클릭 없음", "detail": "검색 결과만 판정합니다."}
    mode = str(click.get("mode") or "active").lower()
    if mode != "inactive":
        return {
            "required": True,
            "valid": True,
            "title": "활성 클릭",
            "detail": "실제 마우스 커서를 사용하는 설정입니다.",
        }
    method = str(click.get("method") or "auto").lower()
    method_label = INACTIVE_METHOD_LABELS.get(method, method or "자동")
    if method == "auto":
        cls = str(target_class or "")
        post_classes = ("EVA_", "Chrome_WidgetWin", "HwndWrapper", "Qt", "LDPlayer")
        if any(token in cls for token in post_classes):
            method_label = "PostMessage (앱 클래스 자동 선택)"
        elif cls:
            method_label = "ControlClick → 실패 시 PostMessage"
    target = str(click.get("window_exe") or click.get("window") or step.get("region_window_exe") or step.get("region_window") or "").strip()
    if not target:
        return {
            "required": True,
            "valid": False,
            "title": "비활성 대상 미지정",
            "detail": "활성 클릭으로 전환하지 않습니다. 대상 프로그램을 먼저 지정하세요.",
        }
    if not target_hwnd:
        return {
            "required": True,
            "valid": False,
            "title": "비활성 대상 창 없음",
            "detail": f"{target} 창을 찾지 못했습니다. 활성 클릭으로 대체하지 않고 저장을 차단합니다.",
        }
    return {
        "required": True,
        "valid": True,
        "title": "비활성 클릭 준비 완료",
        "detail": f"{method_label} · HWND 0x{int(target_hwnd):X} · 실제 커서 미사용",
    }


def predicted_click_points(
    aliases: Iterable[str],
    results: dict[str, dict[str, Any]],
    offsets: dict[str, list[int]],
    click_target: str,
    custom_point: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Build canvas-local predicted click points from visual matches."""
    target = str(click_target or "none").lower()
    if target == "none":
        return []
    if target == "custom_coord" and custom_point is not None:
        return [{"alias": "지정 좌표", "x": int(custom_point[0]), "y": int(custom_point[1]), "source": "custom"}]
    points: list[dict[str, Any]] = []
    for alias in aliases:
        result = results.get(alias) or {}
        if not result.get("found"):
            continue
        off = offsets.get(alias) or [0, 0]
        x = int(result.get("hit_x") or 0) + int(off[0] if len(off) > 0 else 0)
        y = int(result.get("hit_y") or 0) + int(off[1] if len(off) > 1 else 0)
        points.append({"alias": alias, "x": x, "y": y, "source": "match"})
        if target == "first_image":
            break
    return points
