from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import uuid
import zipfile
from typing import Any, Iterable

from PySide6 import QtCore, QtGui


AI_SCHEMA_VERSION = "macrorelay-ai-1.0"
AI_PACKAGE_VERSION = "1.0"
AI_TRIGGER_DEFAULTS = {
    "poll_interval": 500,
    "stable_ms": 500,
    "search_scope": "target_client",
    "multi_scale": True,
    "fire_mode": "on_appear",
    "rearm_mode": "after_disappear",
}
ALLOWED_ACTIONS = {
    "mouse_click",
    "inactive_click",
    "image_search",
    "screen_condition",
    "datetime_condition",
    "type_text",
    "wait",
    "browser_action",
    "ocr",
    "table_store",
    "table_copy",
    "table_paste",
    "table_excel_read",
    "table_excel_write",
    "set_var",
    "vault_get",
    "calc_var",
    "coord_mode",
    "call_submacro",
    "flow_control",
    "text_condition",
    "run_program",
    "terminate_program",
    "remote_notify",
}

# These descriptions are shipped to the AI together with the mechanically
# generated FieldSpec catalog.  The catalog tells it *how* to configure every
# node; these rules tell it *when* each node is the correct tool.
ACTION_SEMANTICS: dict[str, dict[str, Any]] = {
    "mouse_click": {"use_when": "화면이 활성화되어도 되고 이미지 판정 없이 고정 위치를 클릭할 때", "result": "지정 좌표 클릭 완료"},
    "inactive_click": {"use_when": "대상 프로그램을 활성화하지 않고 좌표 클릭 또는 드래그를 전달할 때", "result": "대상 창 메시지 전송 완료"},
    "image_search": {"use_when": "PNG 이미지가 화면 어디에 있든 찾고 필요하면 중심·오프셋을 클릭할 때", "result": "탐지 성공/실패 분기", "requires": ["asset_ref 또는 assets"]},
    "screen_condition": {"use_when": "특정 화면·버튼의 존재 여부만 판정하고 클릭하지 않을 때", "result": "조건 성공/실패 분기", "requires": ["asset_ref"]},
    "datetime_condition": {"use_when": "날짜·요일·시간 조건을 검사하거나 조건 시각까지 대기할 때", "result": "시간 조건 성공/실패 분기"},
    "type_text": {"use_when": "활성 또는 비활성 창에 문자열과 기능키를 입력할 때", "result": "입력 전송 완료", "requires": ["params.text 또는 vault_get 결과 변수"]},
    "wait": {"use_when": "화면 판정 없이 정해진 시간만 대기해야 할 때", "result": "시간 경과"},
    "browser_action": {"use_when": "Chrome 계열 디버그 연결에서 CSS 선택자로 클릭·입력·추출할 때", "result": "브라우저 요소 동작 성공/실패", "requires": ["params.selector"]},
    "ocr": {"use_when": "화면이나 브라우저의 글자·숫자를 읽고 찾기·클릭·조건·변수 저장할 때", "result": "OCR_LastText/OCR_LastNumber 및 선택 변수 갱신"},
    "table_store": {"use_when": "상수·변수·OCR 값을 Studio 데이터 테이블 셀에 저장할 때", "result": "테이블 셀 갱신"},
    "table_copy": {"use_when": "테이블 범위를 클립보드로 복사하고 행·열 커서를 이동할 때", "result": "클립보드와 테이블 커서 갱신"},
    "table_paste": {"use_when": "테이블 범위를 대상 창에 활성/비활성 방식으로 순서대로 입력할 때", "result": "선택 범위 입력과 커서 갱신"},
    "table_excel_read": {"use_when": "Excel 파일 셀 또는 범위를 Studio 테이블로 읽을 때", "result": "테이블 갱신"},
    "table_excel_write": {"use_when": "Studio 테이블 값을 Excel 파일에 기록할 때", "result": "Excel 저장"},
    "set_var": {"use_when": "문자열·숫자·다른 변수 결과를 사용자 변수에 저장할 때", "result": "지정 변수 갱신", "requires": ["params.name"]},
    "vault_get": {"use_when": "비밀번호·API 키처럼 평문 저장하면 안 되는 값을 DPAPI 보관함에서 변수로 가져올 때", "result": "보안 값이 지정 변수에만 로드", "requires": ["params.name", "params.secret"]},
    "calc_var": {"use_when": "변수에 사칙연산·증감·수식을 적용할 때", "result": "숫자 변수 갱신", "requires": ["params.name"]},
    "coord_mode": {"use_when": "뒤따르는 좌표 액션의 화면/창/클라이언트 기준을 명시적으로 변경할 때", "result": "좌표 해석 기준 변경"},
    "call_submacro": {"use_when": "재사용 가능한 다른 매크로 행동을 호출하고 입력·출력 변수를 연결할 때", "result": "서브매크로 결과와 출력 변수 갱신", "requires": ["params.macro"]},
    "flow_control": {"use_when": "명시적 횟수 반복이나 카운터 기반 점프가 필요할 때만", "result": "지정 노드로 제한된 이동", "requires": ["params.jump_to와 유한 repeat_count 또는 counter_key"]},
    "text_condition": {"use_when": "OCR·클립보드·변수 문자열을 포함/일치/정규식으로 분기할 때", "result": "텍스트 일치/불일치 분기"},
    "run_program": {"use_when": "사용자가 지정한 실행 파일·문서·URL을 시작할 때", "result": "프로그램 시작 요청", "requires": ["params.command"]},
    "terminate_program": {"use_when": "프로세스 이름으로 대상 프로그램 종료를 요청할 때", "result": "프로세스 종료 요청", "requires": ["params.process"]},
    "remote_notify": {"use_when": "완료·실패·OCR 결과를 연결된 모바일에 알릴 때", "result": "원격 알림 전송"},
}
AI_TRIGGER_TYPES: dict[str, dict[str, Any]] = {
    "manual": {"label": "직접 실행", "use_when": "사용자가 실행 버튼·단축키·원격 명령으로 시작", "fields": {}},
    "process_start": {"label": "프로그램 시작", "use_when": "특정 프로세스가 실행된 순간 시작", "fields": {"process": "실행 파일 이름", "interval": "확인 간격(초)"}},
    "process_stop": {"label": "프로그램 종료", "use_when": "실행 중이던 특정 프로세스가 종료된 순간 시작", "fields": {"process": "실행 파일 이름", "interval": "확인 간격(초)"}},
    "window_appears": {"label": "창 나타남", "use_when": "제목에 특정 문자열을 포함한 창이 나타나면 시작", "fields": {"title": "창 제목 일부", "interval": "확인 간격(초)"}},
    "image_appears": {"label": "이미지 나타남", "use_when": "PNG가 화면 또는 대상 프로그램에 나타나면 시작", "fields": {"asset_ref": "asset-manifest id", "target_ref": "선택 대상 id", "params.threshold": "0.5~0.99", "params.poll_interval": "밀리초", "params.stable_ms": "안정 대기 밀리초", "params.search_scope": "target_client 또는 screen"}},
    "ocr_threshold": {"label": "OCR 숫자 조건", "use_when": "화면 범위의 OCR 숫자가 비교 조건을 만족하면 시작", "fields": {"region": "[left,top,right,bottom]", "operator": ">= <= > < == !=", "value": "기준 숫자", "profile": "number/auto/game_ui", "lang": "eng+kor", "interval": "확인 간격(초)"}},
    "schedule": {"label": "지정 시간·요일", "use_when": "선택 요일의 HH:mm에 한 번 시작", "fields": {"time": "HH:mm", "days": "월=0 ... 일=6 배열", "interval": "확인 간격(초)"}},
}
FORBIDDEN_KEYS = {
    "python",
    "python_code",
    "ahk",
    "ahk_code",
    "script",
    "shell",
    "powershell",
    "cmd",
    "eval",
    "exec",
}
SAFE_COMMON_STEP_KEYS = {
    "label",
    "on_success",
    "on_fail",
    "on_success_delay",
    "on_fail_delay",
    "sleep_after",
    "repeat",
    "repeat_var",
    "retry_count",
    "retry_delay",
    "edge_conditions",
    "needs_setup",
    "source_evidence",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _safe_component(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", str(value or "").strip()).strip(" .-")
    return cleaned[:96] or fallback


def _decode_image(value: Any) -> QtGui.QImage:
    if not isinstance(value, str) or not value:
        return QtGui.QImage()
    try:
        raw = base64.b64decode(value, validate=False)
    except (TypeError, ValueError):
        return QtGui.QImage()
    return QtGui.QImage.fromData(raw)


def _centered_rect(image: QtGui.QImage, anchor: QtCore.QPoint, size: QtCore.QSize) -> QtCore.QRect:
    width = min(max(1, size.width()), image.width())
    height = min(max(1, size.height()), image.height())
    left = min(max(0, anchor.x() - width // 2), max(0, image.width() - width))
    top = min(max(0, anchor.y() - height // 2), max(0, image.height() - height))
    return QtCore.QRect(left, top, width, height)


def _image_similarity(first: QtGui.QImage, second: QtGui.QImage) -> float:
    if first.isNull() or second.isNull() or first.size() != second.size():
        return 0.0
    one = first.convertToFormat(QtGui.QImage.Format_RGBA8888)
    two = second.convertToFormat(QtGui.QImage.Format_RGBA8888)
    a = bytes(one.bits())
    b = bytes(two.bits())
    if not a or len(a) != len(b):
        return 0.0
    # Sampling every fourth pixel is accurate enough for readiness scoring and
    # keeps large context candidates cheap during package generation.
    delta = 0
    samples = 0
    for offset in range(0, len(a), 16):
        delta += abs(a[offset] - b[offset])
        delta += abs(a[offset + 1] - b[offset + 1])
        delta += abs(a[offset + 2] - b[offset + 2])
        samples += 3
    return max(0.0, min(100.0, 100.0 - (delta / max(1, samples) / 255.0 * 100.0)))


def _visual_detail_score(image: QtGui.QImage) -> float:
    """Estimate whether a crop contains a searchable visual feature.

    Flat backgrounds and large nearly uniform panels are poor templates even
    though they match their source frame perfectly. This lightweight score is
    intentionally independent from the post-click frame, because a successful
    click often removes or recolors the target before that frame is captured.
    """
    if image.isNull() or image.width() < 8 or image.height() < 8:
        return 0.0
    probe = image.scaled(48, 32, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation).convertToFormat(
        QtGui.QImage.Format_RGB888
    )
    raw = bytes(probe.bits())
    stride = probe.bytesPerLine()
    luminance: list[float] = []
    edge_total = 0.0
    for y in range(probe.height()):
        for x in range(probe.width()):
            offset = y * stride + x * 3
            value = 0.2126 * raw[offset] + 0.7152 * raw[offset + 1] + 0.0722 * raw[offset + 2]
            luminance.append(value)
            if x:
                edge_total += abs(value - luminance[-2])
            if y:
                edge_total += abs(value - luminance[(y - 1) * probe.width() + x])
    if not luminance:
        return 0.0
    mean = sum(luminance) / len(luminance)
    variance = sum((value - mean) ** 2 for value in luminance) / len(luminance)
    contrast = min(100.0, (variance ** 0.5) / 64.0 * 100.0)
    edge = min(100.0, edge_total / max(1, len(luminance) * 38.0) * 100.0)
    return round(contrast * 0.55 + edge * 0.45, 2)


def _multiscale_search_score(scene: QtGui.QImage, template: QtGui.QImage) -> tuple[float, float]:
    """Return the best sampled template score and its scale.

    AI package generation cannot assume that the managed OpenCV runtime has
    already been installed. This small Qt-only matcher searches a quarter-size
    copy at several scales, so validation still works on a clean installation.
    Runtime image search can later perform the full OpenCV verification.
    """
    if scene.isNull() or template.isNull():
        return 0.0, 1.0
    reduced_scene = scene.scaled(
        max(1, scene.width() // 4),
        max(1, scene.height() // 4),
        QtCore.Qt.IgnoreAspectRatio,
        QtCore.Qt.SmoothTransformation,
    ).convertToFormat(QtGui.QImage.Format_RGB888)
    scene_bytes = bytes(reduced_scene.bits())
    scene_stride = reduced_scene.bytesPerLine()
    best_score, best_scale = 0.0, 1.0
    for scale in (0.85, 0.92, 1.0, 1.08, 1.15):
        width = max(4, round(template.width() * scale / 4))
        height = max(4, round(template.height() * scale / 4))
        if width > reduced_scene.width() or height > reduced_scene.height():
            continue
        probe = template.scaled(width, height, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation).convertToFormat(
            QtGui.QImage.Format_RGBA8888
        )
        probe_bytes = bytes(probe.bits())
        probe_stride = probe.bytesPerLine()
        sample_points = [
            (min(width - 1, round((column + 0.5) * width / 10)), min(height - 1, round((row + 0.5) * height / 8)))
            for row in range(8)
            for column in range(10)
        ]
        sample_points = [
            (sample_x, sample_y)
            for sample_x, sample_y in sample_points
            if probe_bytes[sample_y * probe_stride + sample_x * 4 + 3] >= 48
        ]
        if len(sample_points) < 8:
            continue
        step = 2 if reduced_scene.width() <= 480 else 3
        for top in range(0, reduced_scene.height() - height + 1, step):
            for left in range(0, reduced_scene.width() - width + 1, step):
                delta = 0
                for sample_x, sample_y in sample_points:
                    scene_offset = (top + sample_y) * scene_stride + (left + sample_x) * 3
                    probe_offset = sample_y * probe_stride + sample_x * 4
                    delta += abs(scene_bytes[scene_offset] - probe_bytes[probe_offset])
                    delta += abs(scene_bytes[scene_offset + 1] - probe_bytes[probe_offset + 1])
                    delta += abs(scene_bytes[scene_offset + 2] - probe_bytes[probe_offset + 2])
                    if delta > 14_000:
                        break
                score = max(0.0, 100.0 - delta / (len(sample_points) * 3 * 255) * 100.0)
                if score > best_score:
                    best_score, best_scale = score, scale
                if best_score >= 99.6:
                    return best_score, best_scale
    return best_score, best_scale


def _grayscale_candidate(image: QtGui.QImage) -> QtGui.QImage:
    return image.convertToFormat(QtGui.QImage.Format_Grayscale8).convertToFormat(QtGui.QImage.Format_ARGB32)


def _outline_candidate(image: QtGui.QImage) -> QtGui.QImage:
    gray = image.convertToFormat(QtGui.QImage.Format_Grayscale8)
    result = QtGui.QImage(gray.size(), QtGui.QImage.Format_ARGB32)
    result.fill(QtGui.QColor("#05070A"))
    for y in range(1, gray.height() - 1):
        for x in range(1, gray.width() - 1):
            center = QtGui.qGray(gray.pixel(x, y))
            horizontal = abs(QtGui.qGray(gray.pixel(x + 1, y)) - QtGui.qGray(gray.pixel(x - 1, y)))
            vertical = abs(QtGui.qGray(gray.pixel(x, y + 1)) - QtGui.qGray(gray.pixel(x, y - 1)))
            strength = min(255, horizontal + vertical + abs(center - QtGui.qGray(gray.pixel(x + 1, y + 1))))
            value = 255 if strength >= 46 else 0
            result.setPixelColor(x, y, QtGui.QColor(value, value, value, 255))
    return result


def _corner_cutout_candidate(image: QtGui.QImage) -> QtGui.QImage:
    source = image.convertToFormat(QtGui.QImage.Format_ARGB32)
    result = source.copy()
    if source.width() < 4 or source.height() < 4:
        return result
    corners = [
        source.pixelColor(0, 0), source.pixelColor(source.width() - 1, 0),
        source.pixelColor(0, source.height() - 1), source.pixelColor(source.width() - 1, source.height() - 1),
    ]
    reference = (
        sum(color.red() for color in corners) // 4,
        sum(color.green() for color in corners) // 4,
        sum(color.blue() for color in corners) // 4,
    )
    queue_points = [(x, 0) for x in range(source.width())] + [(x, source.height() - 1) for x in range(source.width())]
    queue_points += [(0, y) for y in range(1, source.height() - 1)] + [(source.width() - 1, y) for y in range(1, source.height() - 1)]
    seen = bytearray(source.width() * source.height())
    cursor = 0
    removed = 0
    while cursor < len(queue_points):
        x, y = queue_points[cursor]
        cursor += 1
        offset = y * source.width() + x
        if seen[offset]:
            continue
        seen[offset] = 1
        color = source.pixelColor(x, y)
        distance = (color.red() - reference[0]) ** 2 + (color.green() - reference[1]) ** 2 + (color.blue() - reference[2]) ** 2
        if distance > 38**2 * 3:
            continue
        result.setPixelColor(x, y, QtGui.QColor(color.red(), color.green(), color.blue(), 0))
        removed += 1
        if x > 0:
            queue_points.append((x - 1, y))
        if x + 1 < source.width():
            queue_points.append((x + 1, y))
        if y > 0:
            queue_points.append((x, y - 1))
        if y + 1 < source.height():
            queue_points.append((x, y + 1))
    ratio = removed / max(1, source.width() * source.height())
    return result if 0.04 <= ratio <= 0.92 else source


def _window_token(window: dict[str, Any]) -> str:
    exe = str(window.get("exe") or "").strip()
    title = str(window.get("title") or "").strip()
    window_class = str(window.get("class") or "").strip()
    if title and exe:
        return f"{title} ahk_exe {exe}"
    if window_class and exe:
        return f"ahk_class {window_class} ahk_exe {exe}"
    return f"ahk_exe {exe}" if exe else title or "A"


def _redact_screen_regions(image: QtGui.QImage, screen_left: int, screen_top: int, regions: list[list[int]]) -> QtGui.QImage:
    protected = image.copy()
    painter = QtGui.QPainter(protected)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("#080B10"))
    for values in regions:
        if len(values) < 4:
            continue
        left, top, right, bottom = (int(value) for value in values[:4])
        rect = QtCore.QRect(left - screen_left, top - screen_top, max(0, right - left), max(0, bottom - top))
        clipped = rect.intersected(protected.rect())
        if clipped.isValid() and not clipped.isEmpty():
            painter.drawRect(clipped.adjusted(-3, -3, 3, 3).intersected(protected.rect()))
    painter.end()
    return protected


def load_ai_recording(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    after_by_id = {
        str(item.get("event_id")): item
        for item in records
        if item.get("type") == "mouse_after" and item.get("event_id")
    }
    drag_sources = {
        str(item.get("source_event_id"))
        for item in records
        if item.get("type") == "mouse_drag" and item.get("source_event_id")
    }
    events: list[dict[str, Any]] = []
    for item in records:
        if item.get("type") not in {"mouse", "screen_condition", "screen_verification", "key", "mouse_drag", "workflow_branch"}:
            continue
        prepared = dict(item)
        if prepared.get("type") == "mouse" and str(prepared.get("event_id") or "") in drag_sources:
            continue
        followup = after_by_id.get(str(prepared.get("event_id") or ""))
        if followup and followup.get("image_after_bmp"):
            prepared["image_after_bmp"] = followup["image_after_bmp"]
        events.append(prepared)
    ordered = sorted(events, key=lambda item: int(item.get("t") or 0))
    coalesced: list[dict[str, Any]] = []
    for item in ordered:
        if item.get("type") == "mouse" and not str(item.get("button") or "").startswith("Wheel") and coalesced:
            previous = coalesced[-1]
            same_button = previous.get("type") == "mouse" and previous.get("button") == item.get("button")
            close_time = 0 <= int(item.get("t") or 0) - int(previous.get("t") or 0) <= 500
            close_point = abs(int(item.get("x") or 0) - int(previous.get("x") or 0)) <= 5 and abs(
                int(item.get("y") or 0) - int(previous.get("y") or 0)
            ) <= 5
            if same_button and close_time and close_point:
                combined = dict(item)
                combined["gesture"] = "double_click"
                combined["click_count"] = 2
                combined["double_click_start_t"] = int(previous.get("t") or 0)
                combined["source_event_ids"] = [previous.get("event_id"), item.get("event_id")]
                if previous.get("image_sample_bmp"):
                    combined["image_sample_bmp"] = previous["image_sample_bmp"]
                coalesced[-1] = combined
                continue
        coalesced.append(item)
    return coalesced


SHORT_CHATGPT_REQUEST = (
    "첨부한 MacroRelay 패키지의 START_HERE.txt를 먼저 읽고, 녹화 영상·액션·무손실 PNG를 함께 분석해 "
    "Studio에서 바로 가져올 수 있는 macrorelay-ai.json 파일을 만들어 주세요."
)


def chatgpt_prompt(
    package_id: str,
    packaged_trigger: dict[str, Any] | None = None,
    purpose: str = "",
) -> str:
    capabilities = ai_capabilities_document()
    field_count = sum(len(row.get("fields") or []) for row in capabilities.get("actions", {}).values())
    trigger_example = deepcopy(packaged_trigger) if packaged_trigger else {"id": "trigger-001", "type": "manual"}
    example = {
        "schema_version": AI_SCHEMA_VERSION,
        "source_package_id": package_id,
        "name": "자동화 이름",
        "description": "자동화 목적",
        "targets": [{
            "id": "target-01", "label": "대상 프로그램", "exe": "example.exe", "title": "",
            "class": "", "window_token": "ahk_exe example.exe", "coordinate_base": "client",
            "inactive_click_verified": False,
        }],
        "assets": [{
            "id": "recorded-image-001", "label": "버튼", "target_ref": "target-01",
            "candidate": "asset-candidates/click-001-button.png", "required": True,
            "click_purpose": "버튼 클릭",
        }],
        "variables": {},
        "workflows": [{"id": "workflow-01", "label": "로그인", "order": 1}],
        "triggers": [trigger_example],
        "steps": [{
            "id": "step-01", "action": "image_search", "target_ref": "target-01",
            "asset_ref": "recorded-image-001",
            "params": {"confidence": 86, "timeout": 1200, "click_enabled": True},
            "on_success": "end", "on_fail": "end", "retry_count": 2, "retry_delay": 250,
            "needs_setup": [],
            "workflow_id": "workflow-01", "workflow_label": "로그인",
            "source_evidence": {"timeline_id": "click-001", "frame": "frames/step-001-before.png"},
        }],
        "setup_requirements": [],
    }
    purpose_text = purpose.strip() or "녹화된 행동의 목적을 추론하여 안정적인 자동 매크로로 구성"
    return f"""당신은 MacroRelay Studio 자동화 설계 도우미입니다.

첨부된 AI 녹화 패키지 `{package_id}`를 분석하십시오. 이 패키지에는 사용자가 수행한 동작의 비식별 타임라인, 대상 프로그램 정보, 원본 PNG 이미지 후보와 선택적 동작 영상이 들어 있습니다.

사용자가 입력한 이번 녹화 목적: {purpose_text}

가장 먼저 패키지 최상단의 `studio-capabilities.json`, `node-reference.md`, `schema.json`, `generation-checklist.json`을 읽으십시오. 여기에는 현재 Studio가 실제로 지원하는 모든 노드와 {field_count}개 설정 필드의 경로·형식·기본값·허용값·사용 조건이 들어 있습니다. 녹화에 직접 나타나지 않은 OCR·변수·조건·반복·서브매크로·테이블·알림 기능도 목적 달성에 필요하면 스스로 선택하십시오. 명세에 없는 action, params 필드, 선택값은 추측하거나 만들지 마십시오.

중요 규칙:
1. 사용자가 평소처럼 수행한 기록을 그대로 자동화하는 것이 기본 목적입니다. 흐름이 명확하면 질문하지 말고 즉시 JSON을 만드십시오.
2. 기본값은 1회 실행 후 종료, 이미지 검색 3회 재시도, 재시도 간격 500ms, 최종 실패 시 정지, 알림 없음입니다. 이 기본값을 확인 질문으로 되묻지 마십시오.
3. 녹화에 문자 입력 이벤트가 없으면 이미 채워진 값 또는 프로그램의 자동 완성을 사용한 것입니다. ID·비밀번호 입력 노드를 새로 만들거나 보안 값 질문을 하지 마십시오.
4. F8 중요 화면 표시가 있으면 시작·성공·실패 판정 후보로 우선 사용하십시오. 표시가 없으면 마지막 동작 뒤 안정된 화면을 성공 후보로 사용하되, 성공 판정이 없어도 1회 흐름은 만들 수 있습니다.
5. 같은 exe와 창 class를 가진 제목 변화는 하나의 대상 프로그램으로 합치고, 실행할 때마다 현재 창과 핸들을 다시 찾도록 하십시오.
6. 이미지 후보·검색 범위·클릭 위치·클릭 방식은 패키지 메타데이터의 자동 선택값을 사용하십시오. 비활성 클릭은 `click.mode=inactive`, `method=auto`로 만들고 별도 확인 질문을 만들지 마십시오.
7. manifest.json의 실행 조건과 failure_policy는 그대로 보존하십시오. 시작 조건을 다시 질문하지 마십시오.
8. 정말로 매크로를 만들 수 없는 단 하나의 정보만 누락된 경우에만 질문하십시오. 여러 질문을 나열하지 말고 한 번에 하나만 질문하십시오. 그 외 불확실성은 안전한 기본값과 `needs_setup`으로 처리한 초안을 즉시 만드십시오.
9. 아이디·비밀번호·API 키를 평문으로 넣지 마십시오. 녹화에 실제 민감 입력 동작이 있을 때만 `vault_get`과 보안 보관함 이름을 사용하십시오.
10. 임의 Python, AutoHotkey, PowerShell, 셸 코드를 생성하지 마십시오. 허용 액션만 사용하십시오.
11. 설명이나 코드 블록을 채팅 본문에 출력하지 마십시오. 완성 결과는 다운로드 가능한 `macrorelay-ai.json` 파일 하나로만 첨부하십시오. 파일 생성이 지원되지 않는 환경에서만 완전한 JSON 객체 하나만 출력하십시오.
12. timeline의 `screen_condition_marker`는 사용자가 녹화 중 우클릭으로 명시한 중간 화면 조건입니다. 반드시 같은 asset_ref를 사용하는 `screen_condition` 노드로 만드십시오. 이 노드 다음의 일반 동작은 성공선에 연결하고, 실패선은 다음 `screen_condition_marker` 또는 안전한 종료로 연결하십시오. 우클릭 자체를 mouse_click으로 만들지 마십시오.
13. timeline의 `workflow_branch_marker`와 각 항목의 `workflow_id`는 사용자가 F7 또는 `다음 작업` 버튼으로 나눈 독립 작업입니다. 작업별로 짧은 한국어 이름을 추론해 `workflows`에 기록하고 모든 step에 `workflow_id`, `workflow_label`을 넣으십시오. 한 작업 내부의 선을 다른 작업 내부로 뒤섞지 말고, 작업 종료점에서만 다음 작업 시작점으로 연결하십시오. 각 작업의 우클릭 화면 조건은 그 작업의 진입·분기 조건입니다.
14. 작업 분기가 여러 개면 녹화 순서를 기본 실행 순서로 사용합니다. 앞 작업이 명시적으로 종료되어야 하는 경우를 제외하면 앞 작업의 정상 종료를 다음 작업 시작점에 연결하고, 조건 불충족은 그 작업을 건너뛰어 다음 작업으로 이동시킵니다.
15. timeline의 `screen_verification_marker`는 사용자가 F6으로 표시한 ‘이전 동작 결과 확인’입니다. 같은 asset_ref의 `screen_condition`을 만들고 성공하면 다음 동작으로 진행하십시오. 실패하면 해당 작업 안의 직전 실제 동작으로 돌아가 500ms 뒤 다시 시도하되 최대 3회로 제한하고, 모두 실패하면 작업을 안전하게 종료하십시오.
16. 작업 이름이나 작업 구분만을 위해 `flow_control` 노드를 만들지 마십시오. workflow_id와 workflow_label이 시각적 작업 레인을 만듭니다. 반복·카운터·명시적 점프가 실제로 녹화된 경우에만 flow_control을 사용하고, 녹화에 없는 대기 노드를 일괄 삽입하지 마십시오.
17. 반환 전 `generation-checklist.json`의 모든 항목을 자체 검사하십시오. 각 step의 `source_evidence`에 선택 이유와 근거 timeline/asset id를 기록하십시오.
18. `asset-manifest.json`에 없는 이미지 파일명이나 경로를 절대 만들지 마십시오. 녹화 자료에 없는 이미지가 있으면 가짜 `assets/*.png`를 참조하지 말고 candidate를 빈 문자열로 두고 해당 step의 `needs_setup`에 `select_asset`을 넣으십시오. 보조 판정이면 asset.required=false로 두어 가져오기를 막지 말고, 그 이미지 없이는 핵심 흐름을 실행할 수 없을 때만 required=true로 두십시오.
19. 종료를 별도 `flow_control` 노드나 `params.operation=end`로 만들지 마십시오. 흐름 종료는 `on_success` 또는 `on_fail`에 `end`를 넣거나 마지막 연결을 비워 표현하십시오.
20. 영상은 의도와 순서 판단용입니다. 이미지 서치 template은 반드시 `asset-manifest.json`에 등록된 무손실 PNG candidate만 사용하십시오. MP4 프레임을 잘라 만든 파일명을 반환하지 마십시오.

스키마 버전은 `{AI_SCHEMA_VERSION}`입니다. 최상위 필수 키는 `schema_version`, `source_package_id`, `name`, `description`, `targets`, `assets`, `variables`, `triggers`, `steps`, `setup_requirements`이며, 작업 분기가 있으면 `workflows`도 포함하십시오.

각 target은 `id`, `label`, `exe`, `title`, `class`, `window_token`, `coordinate_base`, `inactive_click_verified`를 사용합니다. 과거 hwnd 숫자는 저장하지 마십시오.

각 asset은 `id`, `label`, `target_ref`, `candidate`, `required`, `click_purpose`를 사용합니다. candidate는 asset-manifest.json에 있는 상대 PNG 경로만 사용하십시오.

각 trigger는 `manual`, `process_start`, `process_stop`, `window_appears`, `image_appears`, `ocr_threshold`, `schedule` 중 목적에 맞는 것을 선택합니다. 여러 자동 실행 조건이 필요하면 배열에 각각 추가합니다. 녹화 패키지에 지정된 이미지 실행 조건은 `target_ref`, `asset_ref`, `params`를 변경하지 마십시오.

각 step은 `id`, `action`, 선택적 `params`, `target_ref`, `asset_ref`, `on_success`, `on_fail`, `retry_count`, `retry_delay`, `needs_setup`, `source_evidence`를 사용합니다. 노드 연결은 step id를 참조합니다.

형식 기준은 패키지의 `schema.json`이며, 최소 예시는 다음과 같습니다. 이 예시의 값은 복사하지 말고 실제 패키지와 사용자 답변으로 채우십시오.
{json.dumps(example, ensure_ascii=False, indent=2)}

허용 액션:
{', '.join(sorted(ALLOWED_ACTIONS))}

이미지 클릭은 기본적으로 `image_search`, `engine=opencv`, `search_profile=balanced`, `click_enabled=true`, `click.mode=inactive`, `click.method=auto`를 사용하십시오. 화면 조건은 `screen_condition`을 사용하고 클릭을 수행하지 마십시오. 검색 범위는 대상 프로그램의 클라이언트 상대 좌표를 우선 사용하며 클릭 오프셋은 선택된 PNG 후보의 값을 그대로 사용하십시오.
"""


def _field_json_type(kind: str) -> str | list[str]:
    if kind in {"int", "duration"}:
        return "integer"
    if kind == "float":
        return "number"
    if kind == "bool":
        return "boolean"
    if kind in {"assets", "offset"}:
        return "array"
    return "string"


def ai_capabilities_document() -> dict[str, Any]:
    """Return the exact node contract generated from the editor itself.

    Keeping this derived from ACTION_FIELDS prevents the AI reference from
    drifting whenever a visible Studio option is added or renamed.
    """
    from . import __version__
    from .action_editor import ACTION_FIELDS, ACTION_LABELS, action_template

    actions: dict[str, Any] = {}
    for action in sorted(ALLOWED_ACTIONS):
        fields = []
        for spec in ACTION_FIELDS.get(action, []):
            field: dict[str, Any] = {
                "path": spec.key,
                "label": spec.label,
                "kind": spec.kind,
                "json_type": _field_json_type(spec.kind),
                "default": deepcopy(spec.default),
                "section": spec.section,
            }
            if spec.kind in {"int", "duration", "float"}:
                field.update(minimum=spec.minimum, maximum=spec.maximum)
            if spec.options:
                field["choices"] = [{"label": label, "value": value} for label, value in spec.options]
            if spec.tooltip:
                field["rule"] = spec.tooltip
            if spec.placeholder:
                field["example_hint"] = spec.placeholder
            fields.append(field)
        template = action_template(action)
        template.pop("action", None)
        semantics = deepcopy(ACTION_SEMANTICS.get(action) or {})
        actions[action] = {
            "label": ACTION_LABELS.get(action, action),
            **semantics,
            "params_location": "모든 설정은 step.params 안에 넣습니다. 연결·재시도 공통 필드는 step 최상위에 둡니다.",
            "default_params": template,
            "fields": fields,
            "allowed_field_paths": [field["path"] for field in fields],
        }
    return {
        "document_type": "macrorelay-studio-capabilities",
        "studio_version": __version__,
        "ai_schema_version": AI_SCHEMA_VERSION,
        "generated_from": "macro_studio.action_editor.ACTION_FIELDS",
        "contract": {
            "step_shape": {
                "required": ["id", "action"],
                "optional": [
                    "params", "target_ref", "asset_ref", "on_success", "on_fail", "retry_count",
                    "retry_delay", "needs_setup", "source_evidence", "workflow_id", "workflow_label",
                ],
                "connections": "on_success/on_fail은 존재하는 step.id 또는 end를 참조합니다.",
                "params": "해당 action의 fields.path만 사용합니다. 목록에 없는 필드는 만들지 않습니다.",
            },
            "common_step_fields": {
                "label": "노드 표시 이름",
                "on_success": "성공 시 step.id 또는 end",
                "on_fail": "실패 시 step.id 또는 end",
                "on_success_delay": "성공 연결 전 대기(ms)",
                "on_fail_delay": "실패 연결 전 대기(ms)",
                "sleep_after": "노드 완료 후 대기(ms)",
                "repeat": "현재 노드 자체 반복 횟수",
                "repeat_var": "반복 횟수를 읽을 변수 이름",
                "retry_count": "실패 후 추가 재시도 횟수",
                "retry_delay": "재시도 간격(ms)",
                "edge_conditions": "연결별 변수/횟수 비교 조건 배열",
                "needs_setup": "Studio에서 사용자 확인이 필요한 항목 배열",
                "source_evidence": "선택 근거·timeline id·asset id 객체",
                "workflow_id": "시각적 행동 그룹 id",
                "workflow_label": "시각적 행동 그룹 이름",
            },
            "execution": {
                "unlinked_success": "다음 순번 노드로 진행",
                "end": "해당 흐름 안전 종료",
                "retry": "retry_count는 추가 시도 횟수, retry_delay는 밀리초",
                "variables": "${name} 또는 Studio가 지원하는 변수 참조를 사용하며 민감 값은 vault_get으로만 로드",
            },
            "coordinates": {
                "preferred": "대상 프로그램의 client 상대 좌표",
                "reacquire": "저장된 HWND를 사용하지 않고 exe/title/class로 실행 때마다 현재 창 재탐색",
                "image_offset": "탐지된 PNG 중심 기준 [x,y]. 화면 위치가 바뀌어도 간격 유지",
            },
            "assets": {
                "candidate": "패키지 루트 기준 상대 PNG 경로만 허용",
                "video_rule": "JPG/MP4 프레임을 이미지 서치 자산으로 사용 금지",
                "screen_condition": "존재 여부만 판정하며 클릭 금지",
                "image_search": "찾으면 클릭이 필요한 경우 click_enabled=true",
            },
            "safety": {
                "forbidden_fields": sorted(FORBIDDEN_KEYS),
                "secrets": "평문 비밀번호·토큰 금지. vault_get과 보관함 이름만 사용",
                "program_commands": "run_program/terminate_program은 needs_setup에 확인 항목 추가",
                "loops": "무제한 flow_control 생성 금지. 종료 조건 또는 유한 반복 필수",
            },
        },
        "actions": actions,
        "triggers": deepcopy(AI_TRIGGER_TYPES),
    }


def ai_capability_reference_markdown(capabilities: dict[str, Any] | None = None) -> str:
    capabilities = capabilities or ai_capabilities_document()
    lines = [
        "# MacroRelay Studio 전체 노드 명세",
        "",
        f"Studio {capabilities['studio_version']} · AI schema {capabilities['ai_schema_version']}",
        "",
        "AI는 녹화 동작을 그대로 나열하는 데 그치지 말고 아래 모든 기능 중 목적에 가장 맞는 노드를 선택합니다.",
        "설정은 반드시 각 노드의 허용 필드 경로와 선택값을 사용하며 임의 필드를 만들지 않습니다.",
        "",
    ]
    for action, row in capabilities["actions"].items():
        lines.extend([
            f"## {action} · {row['label']}",
            "",
            f"사용 조건: {row.get('use_when', '')}",
            f"실행 결과: {row.get('result', '')}",
        ])
        if row.get("requires"):
            lines.append("필수 조건: " + ", ".join(row["requires"]))
        lines.extend(["", "| 필드 경로 | 화면 이름 | 형식 | 기본값/선택값 | 규칙 |", "|---|---|---|---|---|"])
        for field in row["fields"]:
            choices = ", ".join(str(choice["value"]) for choice in field.get("choices", []))
            default = json.dumps(field.get("default"), ensure_ascii=False)
            value_info = choices or default
            rule = str(field.get("rule") or "").replace("|", "\\|")
            lines.append(
                f"| `{field['path']}` | {field['label']} | {field['kind']} | `{value_info}` | {rule} |"
            )
        lines.extend(["", "기본 params:", "```json", json.dumps(row["default_params"], ensure_ascii=False, indent=2), "```", ""])
    lines.extend(["# 자동 실행 트리거", ""])
    for trigger, row in capabilities.get("triggers", {}).items():
        lines.append(f"- `{trigger}` · {row['label']}: {row['use_when']} · 필드 {json.dumps(row['fields'], ensure_ascii=False)}")
    return "\n".join(lines)


def relevant_ai_actions(timeline: list[dict[str, Any]] | None = None) -> list[str]:
    event_types = {str(row.get("type") or "") for row in timeline or [] if isinstance(row, dict)}
    actions = {"wait", "screen_condition", "flow_control", "set_var", "text_condition", "remote_notify"}
    if event_types & {"mouse", "mouse_drag"}:
        actions.update({"image_search", "mouse_click", "inactive_click"})
    if event_types & {"screen_condition_marker", "screen_verification_marker", "screen_condition", "screen_verification"}:
        actions.update({"image_search", "screen_condition"})
    if event_types & {"text_input", "shortcut", "key"}:
        actions.update({"type_text", "vault_get"})
    if "workflow_branch_marker" in event_types or "workflow_branch" in event_types:
        actions.update({"call_submacro", "flow_control"})
    return sorted(actions)


def write_ai_reference_files(stage: Path, timeline: list[dict[str, Any]] | None = None) -> None:
    capabilities = ai_capabilities_document()
    _write_json(stage / "schema.json", ai_schema_document())
    _write_json(stage / "studio-capabilities.json", capabilities)
    (stage / "node-reference.md").write_text(ai_capability_reference_markdown(capabilities), encoding="utf-8")
    _write_json(stage / "recommended-actions.json", {
        "advisory_only": True,
        "rule": "아래 목록을 우선 검토하되 전체 studio-capabilities.json에서 더 적합한 기능을 자유롭게 선택합니다.",
        "actions": relevant_ai_actions(timeline),
    })
    _write_json(stage / "generation-checklist.json", {
        "before_return": [
            "모든 step.action이 studio-capabilities.json에 존재",
            "모든 params 경로와 선택값이 해당 action 명세에 존재",
            "모든 on_success/on_fail 대상 step.id가 존재하거나 end",
            "모든 target_ref/asset_ref가 카탈로그에 존재",
            "screen_condition은 클릭하지 않음",
            "image_search 클릭은 recorded PNG 중심 또는 기록된 오프셋 사용",
            "모든 반복에는 유한 반복 횟수 또는 명확한 종료 조건 존재",
            "민감 값은 vault_get만 사용",
            "source_package_id가 입력 패키지 manifest의 package_id와 일치",
        ]
    })


def ai_schema_document() -> dict[str, Any]:
    identifier = {"type": "string", "minLength": 1, "pattern": "^[A-Za-z0-9_.-]+$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://macrorelay.local/schema/{AI_SCHEMA_VERSION}.json",
        "title": "MacroRelay AI automation document",
        "type": "object",
        "required": [
            "schema_version", "source_package_id", "name", "description", "targets", "assets",
            "variables", "triggers", "steps", "setup_requirements",
        ],
        "properties": {
            "schema_version": {"const": AI_SCHEMA_VERSION},
            "source_package_id": {"type": "string"},
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "targets": {"type": "array", "items": {"type": "object", "required": ["id"], "properties": {"id": identifier}}},
            "assets": {"type": "array", "items": {"type": "object", "required": ["id"], "properties": {"id": identifier}}},
            "variables": {"type": ["object", "array"]},
            "workflows": {
                "type": "array",
                "items": {
                    "type": "object", "required": ["id", "label", "order"],
                    "properties": {
                        "id": identifier, "label": {"type": "string", "minLength": 1},
                        "order": {"type": "integer", "minimum": 1},
                    },
                },
            },
            "triggers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "type"],
                    "properties": {
                        "id": identifier,
                        "type": {"enum": sorted(AI_TRIGGER_TYPES)},
                        "target_ref": {"type": "string"},
                        "asset_ref": {"type": "string"},
                        "params": {"type": "object"},
                        "enabled": {"type": "boolean"},
                        "interval": {"type": "number", "minimum": 0.5, "maximum": 3600},
                        "process": {"type": "string"},
                        "title": {"type": "string"},
                        "time": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
                        "days": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}},
                        "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                        "operator": {"enum": [">=", "<=", ">", "<", "==", "!="]},
                        "value": {"type": "number"},
                        "needs_setup": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "steps": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "object", "required": ["id", "action"],
                    "properties": {
                        "id": identifier,
                        "action": {"enum": sorted(ALLOWED_ACTIONS)},
                        "params": {"type": "object"},
                        "target_ref": {"type": "string"},
                        "asset_ref": {"type": "string"},
                        "on_success": {"type": ["string", "integer", "null"]},
                        "on_fail": {"type": ["string", "integer", "null"]},
                        "retry_count": {"type": "integer", "minimum": 0, "maximum": 1000},
                        "retry_delay": {"type": "integer", "minimum": 0, "maximum": 3600000},
                        "needs_setup": {"type": "array", "items": {"type": "string"}},
                        "source_evidence": {"type": "object"},
                        "workflow_id": {"type": "string"},
                        "workflow_label": {"type": "string"},
                    },
                },
            },
            "setup_requirements": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
        "x-macrorelay-reference-files": [
            "studio-capabilities.json", "node-reference.md", "recommended-actions.json", "generation-checklist.json"
        ],
    }


class AIRecordingPackageBuilder:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def build(
        self,
        events: list[dict[str, Any]],
        video_path: Path | None = None,
        package_id: str | None = None,
        trigger_config: dict[str, Any] | None = None,
        video_segments: list[dict[str, int]] | None = None,
        video_disabled: bool = False,
        purpose: str = "",
    ) -> tuple[Path, Path]:
        trigger_config = trigger_config or {}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        identifier = package_id or f"ai-{stamp}-{uuid.uuid4().hex[:8]}"
        stage = self.root / ".automation" / "ai-packages" / identifier
        if stage.exists():
            shutil.rmtree(stage)
        (stage / "frames").mkdir(parents=True, exist_ok=True)
        (stage / "asset-candidates").mkdir(parents=True, exist_ok=True)
        (stage / "trigger-assets").mkdir(parents=True, exist_ok=True)
        (stage / "marker-assets").mkdir(parents=True, exist_ok=True)

        targets, target_lookup = self._targets(events)
        timeline: list[dict[str, Any]] = []
        assets: list[dict[str, Any]] = []
        contact_rows: list[tuple[str, QtGui.QImage, str]] = []
        text_group = 0
        click_number = 0
        marker_number = 0
        previous_time = 0
        sensitive_regions = list(
            dict.fromkeys(
                tuple(int(value) for value in (event.get("window") or {}).get("focus_rect", [])[:4])
                for event in events
                if event.get("type") == "key"
                and str(event.get("char") or "") == "[REDACTED]"
                and isinstance(event.get("window"), dict)
                and len((event.get("window") or {}).get("focus_rect", [])) >= 4
            )
        )
        sensitive_regions = [list(values) for values in sensitive_regions]
        for event in events:
            current_time = int(event.get("t") or 0)
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            target_id = target_lookup.get(self._target_key(window), "")
            event_type = str(event.get("type") or "")
            if event_type == "workflow_branch":
                workflow_index = max(2, int(event.get("workflow_index") or 2))
                timeline.append({
                    "id": f"workflow-marker-{workflow_index:02d}",
                    "t": current_time,
                    "delay_from_previous_ms": max(0, current_time - previous_time),
                    "type": "workflow_branch_marker",
                    "workflow_id": str(event.get("workflow_id") or f"workflow-{workflow_index:02d}"),
                    "workflow_index": workflow_index,
                    "label": str(event.get("label") or f"작업 분기 {workflow_index}"),
                })
                previous_time = current_time
                continue
            if event_type == "capture":
                marker_number += 1
                marker_image = _decode_image(event.get("image_sample_bmp"))
                if not marker_image.isNull():
                    relative = f"marker-assets/marker-{marker_number:03d}.png"
                    marker_image.save(str(stage / relative), "PNG")
                    marker_id = f"marker-{marker_number:03d}"
                    assets.append({
                        "id": marker_id,
                        "label": f"사용자 중요 화면 {marker_number}",
                        "target_ref": target_id,
                        "required": False,
                        "purpose": "important_screen_marker",
                        "candidate": relative,
                        "selected_candidate": relative,
                        "readiness": "ready",
                        "validation": {"image_ready": True, "search_verified": True},
                        "needs_setup": [],
                    })
                    timeline.append({
                        "id": marker_id,
                        "t": current_time,
                        "delay_from_previous_ms": max(0, current_time - previous_time),
                        "type": "important_screen_marker",
                        "asset_ref": marker_id,
                        "target_ref": target_id,
                        "screen_rect": list(event.get("selected_screen_rect") or [])[:4],
                    })
                    contact_rows.append((marker_id, marker_image, "F8 중요 화면"))
                    previous_time = current_time
                continue
            if event_type == "key":
                token = str(event.get("token") or "")
                character = str(event.get("char") or "")
                modifiers = [str(value) for value in event.get("modifiers") or [] if str(value)]
                if {"Ctrl", "Alt", "Win"} & set(modifiers):
                    timeline.append(
                        {
                            "id": f"key-{len(timeline) + 1:03d}",
                            "t": current_time,
                            "delay_from_previous_ms": max(0, current_time - previous_time),
                            "type": "shortcut",
                            "token": token or "Unknown",
                            "modifiers": modifiers,
                            "target_ref": target_id,
                        }
                    )
                elif character and token not in {"Enter", "Tab", "Escape"}:
                    if (
                        timeline
                        and timeline[-1].get("type") == "text_input"
                        and timeline[-1].get("target_ref") == target_id
                        and current_time - int(timeline[-1].get("end_t") or timeline[-1].get("t") or 0) <= 1500
                    ):
                        timeline[-1]["character_count"] = int(timeline[-1].get("character_count") or 0) + 1
                        timeline[-1]["end_t"] = current_time
                        timeline[-1]["duration_ms"] = current_time - int(timeline[-1].get("t") or current_time)
                        previous_time = current_time
                        continue
                    text_group += 1
                    timeline.append(
                        {
                            "id": f"input-{text_group:03d}",
                            "t": current_time,
                            "delay_from_previous_ms": max(0, current_time - previous_time),
                            "type": "text_input",
                            "value": "[REDACTED]",
                            "character_count": 1,
                            "target_ref": target_id,
                            "control_class": str(window.get("focus_class") or ""),
                            "control_rect": list(window.get("focus_rect") or [])[:4],
                            "security_policy": "classify_then_use_literal_or_dpapi_vault",
                            "needs_setup": ["classify_sensitive_input", "provide_value_or_vault_name"],
                        }
                    )
                else:
                    timeline.append(
                        {
                            "id": f"key-{len(timeline) + 1:03d}",
                            "t": current_time,
                            "delay_from_previous_ms": max(0, current_time - previous_time),
                            "type": "key",
                            "token": token or "Unknown",
                            "target_ref": target_id,
                        }
                    )
                previous_time = current_time
                continue
            if event_type == "mouse_drag":
                timeline.append(
                    {
                        "id": f"drag-{len(timeline) + 1:03d}",
                        "t": current_time,
                        "delay_from_previous_ms": max(0, current_time - previous_time),
                        "type": "drag",
                        "button": str(event.get("button") or "Left"),
                        "from_screen": list(event.get("from_screen") or [])[:2],
                        "to_screen": list(event.get("to_screen") or [])[:2],
                        "target_ref": target_id,
                    }
                )
                previous_time = current_time
                continue
            if event_type in {"screen_condition", "screen_verification"}:
                click_number += 1
                asset = self._write_click_images(stage, event, click_number, target_id, sensitive_regions)
                if asset:
                    button_candidate = next(
                        (item for item in asset.get("candidates") or [] if item.get("kind") == "button"),
                        None,
                    )
                    if isinstance(button_candidate, dict):
                        asset["selected_candidate"] = button_candidate.get("file")
                        validation = asset.setdefault("validation", {})
                        detail_score = float(button_candidate.get("detail_score") or 0)
                        stability_score = float(button_candidate.get("stability_score") or 0)
                        pre_count = int(validation.get("pre_action_frame_count") or 0)
                        quality_ready = detail_score >= 10.0 and (not pre_count or stability_score >= 76.0)
                        validation["recording_quality_score"] = round(detail_score, 2)
                        validation["stability_score"] = round(stability_score, 2)
                        validation["recording_quality_verified"] = quality_ready
                    verification = event_type == "screen_verification"
                    asset["label"] = f"{'결과 확인' if verification else '화면 조건'} {click_number}"
                    asset["purpose"] = "screen_verification" if verification else "screen_condition"
                    asset["click_purpose"] = "F6으로 지정한 이전 동작 결과" if verification else "우클릭으로 지정한 화면 조건"
                    validation = asset.setdefault("validation", {})
                    quality_ready = bool(validation.get("recording_quality_verified"))
                    asset["readiness"] = "ready" if quality_ready else "needs_review"
                    asset["needs_setup"] = [] if quality_ready else ["choose_or_confirm_candidate", "verify_search"]
                    validation["image_ready"] = True
                    validation["search_verified"] = quality_ready
                    assets.append(asset)
                    preview = QtGui.QImage(str(stage / str(asset["selected_candidate"])))
                    contact_rows.append((
                        f"{'확인' if verification else '조건'}-{click_number:03d}", preview,
                        "F6 결과 확인" if verification else "우클릭 화면 조건",
                    ))
                    timeline.append({
                        "id": f"{'verification' if verification else 'condition'}-{click_number:03d}",
                        "t": current_time,
                        "delay_from_previous_ms": max(0, current_time - previous_time),
                        "type": "screen_verification_marker" if verification else "screen_condition_marker",
                        "asset_ref": asset["id"],
                        "target_ref": target_id,
                        "screen": [int(event.get("x") or 0), int(event.get("y") or 0)],
                        "client": [int(event.get("client_x") or 0), int(event.get("client_y") or 0)],
                        "branch_rule": (
                            "success_runs_following_actions; failure_retries_previous_action_3_times_then_stops_workflow"
                            if verification else
                            "success_runs_following_actions; failure_skips_to_next_screen_condition"
                        ),
                        "retry_count": 3 if verification else 0,
                        "retry_delay": 500 if verification else 0,
                    })
                    previous_time = current_time
                continue
            if event_type != "mouse":
                continue
            click_number += 1
            button = str(event.get("button") or "Left")
            row = {
                "id": f"click-{click_number:03d}",
                "t": current_time,
                "delay_from_previous_ms": max(0, current_time - previous_time),
                "type": "wheel" if button.startswith("Wheel") else str(event.get("gesture") or "click"),
                "button": button,
                "wheel_delta": int(event.get("wheel_delta") or 0),
                "screen": [int(event.get("x") or 0), int(event.get("y") or 0)],
                "client": [int(event.get("client_x") or 0), int(event.get("client_y") or 0)],
                "target_ref": target_id,
                "source_event_id": str(event.get("event_id") or ""),
            }
            if button.startswith("Wheel"):
                timeline.append(row)
                previous_time = current_time
                continue
            asset = self._write_click_images(stage, event, click_number, target_id, sensitive_regions)
            if asset:
                row["asset_ref"] = asset["id"]
                row["image_readiness"] = asset["readiness"]
                assets.append(asset)
                preview = QtGui.QImage(str(stage / str(asset["selected_candidate"])))
                contact_rows.append((row["id"], preview, str(window.get("exe") or window.get("title") or "화면")))
            timeline.append(row)
            previous_time = current_time

        # A recording always starts in workflow 1. F7 markers change the
        # active workflow for following rows, keeping the package easy to
        # reason about without duplicating the workflow id on every raw event.
        active_workflow = "workflow-01"
        active_index = 1
        workflow_rows: list[dict[str, Any]] = [
            {"id": active_workflow, "label": "작업 분기 1", "order": active_index}
        ]
        for row in timeline:
            if row.get("type") == "workflow_branch_marker":
                active_index = max(1, int(row.get("workflow_index") or active_index + 1))
                active_workflow = str(row.get("workflow_id") or f"workflow-{active_index:02d}")
                if not any(item["id"] == active_workflow for item in workflow_rows):
                    workflow_rows.append({
                        "id": active_workflow,
                        "label": str(row.get("label") or f"작업 분기 {active_index}"),
                        "order": active_index,
                    })
                continue
            row["workflow_id"] = active_workflow
            row["workflow_index"] = active_index

        packaged_trigger, trigger_asset = self._prepare_trigger(stage, trigger_config or {}, targets, target_lookup)
        if trigger_asset:
            assets.append(trigger_asset)
            preview = QtGui.QImage(str(stage / str(trigger_asset["selected_candidate"])))
            contact_rows.append(("실행 조건", preview, "특정 화면이 나타나면 자동 실행"))

        purpose = purpose.strip() or "녹화된 행동을 분석하여 MacroRelay 자동 매크로 생성"
        prompt = chatgpt_prompt(identifier, packaged_trigger, purpose)
        (stage / "prompt.txt").write_text(prompt, encoding="utf-8")
        (stage / "task-purpose.txt").write_text(purpose + "\n", encoding="utf-8")
        (stage / "short-request.txt").write_text(SHORT_CHATGPT_REQUEST + "\n", encoding="utf-8")
        (stage / "START_HERE.txt").write_text(
            "MacroRelay Studio AI 노드 생성 패키지\n\n"
            "1. prompt.txt의 고정 생성 규칙을 따릅니다. 사용자가 이 긴 프롬프트를 채팅에 다시 붙여넣을 필요는 없습니다.\n"
            "2. task-purpose.txt, recording.mp4, timeline.json을 함께 분석합니다.\n"
            "3. 이미지 검색에는 asset-manifest.json에 등록된 무손실 PNG만 사용합니다.\n"
            "4. studio-capabilities.json, node-reference.md, schema.json 밖의 노드·필드·선택값은 만들지 않습니다.\n"
            "5. generation-checklist.json을 통과하도록 자체 수정한 뒤 macrorelay-ai.json 파일 하나를 반환합니다.\n"
            "6. 자료에 없는 추가 이미지는 경로를 지어내지 않고 선택적 설정 항목으로 표시합니다.\n\n"
            f"이번 목적: {purpose}\n",
            encoding="utf-8",
        )
        _write_json(stage / "timeline.json", timeline)
        write_ai_reference_files(stage, timeline)
        _write_json(stage / "workflows.json", {"workflows": workflow_rows})
        _write_json(stage / "targets.json", targets)
        _write_json(stage / "asset-manifest.json", {"assets": assets})
        _write_json(stage / "video-segments.json", {"segments": video_segments or []})
        self._write_contact_sheet(stage / "contact-sheet.png", contact_rows)
        if video_path is not None and video_path.is_file():
            shutil.copy2(video_path, stage / "recording.mp4")
        else:
            (stage / "recording-unavailable.txt").write_text(
                "행동 학습 고속 모드에서는 MP4를 만들지 않습니다. timeline.json과 무손실 PNG가 정확한 자동화 근거입니다."
                if video_disabled else
                "이 환경에서는 동작 영상 인코딩을 완료하지 못했습니다. timeline.json과 PNG 프레임은 정상입니다.",
                encoding="utf-8",
            )
        manifest = {
            "package_version": AI_PACKAGE_VERSION,
            "schema_version": AI_SCHEMA_VERSION,
            "package_id": identifier,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "event_count": len(timeline),
            "workflow_count": len(workflow_rows),
            "target_count": len(targets),
            "asset_count": len(assets),
            "trigger": packaged_trigger,
            "failure_policy": deepcopy(trigger_config.get("failure_policy") or {
                "retry_count": 3, "retry_delay": 500, "after_failure": "stop", "notify": False,
            }),
            "video_available": (stage / "recording.mp4").is_file(),
            "video_mode": "disabled_structured_events" if video_disabled else "action_windows",
            "video_segment_count": len(video_segments or []),
            "text_policy": "All printable keyboard input is redacted. ChatGPT must ask for a vault name or value classification.",
            "image_policy": "Lossless native PNG candidates only; video frames are never used as search templates.",
            "purpose": purpose,
            "handoff": {
                "start_file": "START_HERE.txt",
                "short_request_file": "short-request.txt",
                "expected_output": "macrorelay-ai.json",
            },
        }
        _write_json(stage / "manifest.json", manifest)
        exports = self.root / "exports" / "ai-recordings"
        exports.mkdir(parents=True, exist_ok=True)
        archive = exports / f"MacroRelay-AI-Recording-{stamp}.zip"
        suffix = 2
        while archive.exists():
            archive = exports / f"MacroRelay-AI-Recording-{stamp}-{suffix}.zip"
            suffix += 1
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(stage).as_posix())
        _write_json(
            self.root / ".automation" / "last-ai-package.json",
            {"package_id": identifier, "stage": str(stage), "archive": str(archive), "prompt": str(stage / "prompt.txt")},
        )
        return archive, stage

    def _prepare_trigger(
        self,
        stage: Path,
        config: dict[str, Any],
        targets: list[dict[str, Any]],
        target_lookup: dict[tuple[str, str, str], str],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if str(config.get("type") or "manual") != "image_appear":
            return {"id": "trigger-001", "type": "manual"}, None
        source = config.get("image")
        image = source.copy() if isinstance(source, QtGui.QImage) else QtGui.QImage()
        if image.isNull():
            return {
                "id": "trigger-001",
                "type": "image_appears",
                "params": deepcopy(AI_TRIGGER_DEFAULTS),
                "needs_setup": ["capture_trigger_image"],
            }, None
        window = config.get("window") if isinstance(config.get("window"), dict) else {}
        target_ref = target_lookup.get(self._target_key(window), "")
        if not target_ref and len(targets) == 1:
            target_ref = str(targets[0].get("id") or "")
        candidate_path = "trigger-assets/trigger-001.png"
        image.save(str(stage / candidate_path), "PNG")
        raw_scene = config.get("scene")
        scene = raw_scene.copy() if isinstance(raw_scene, QtGui.QImage) and not raw_scene.isNull() else image
        score, matched_scale = _multiscale_search_score(scene, image)
        ready = image.width() >= 8 and image.height() >= 8 and score >= 84 and bool(target_ref)
        trigger_asset = {
            "id": "trigger-image-001",
            "label": "자동 실행 시작 화면",
            "target_ref": target_ref,
            "required": True,
            "purpose": "trigger",
            "candidate": candidate_path,
            "selected_candidate": candidate_path,
            "candidates": [{
                "kind": "trigger",
                "file": candidate_path,
                "rect": [0, 0, image.width(), image.height()],
                "validation_score": round(score, 2),
                "matched_scale": round(matched_scale, 2),
            }],
            "readiness": "ready" if ready else "needs_review",
            "validation": {
                "image_ready": True,
                "search_verified": ready,
                "score": round(score, 2),
                "matched_scale": round(matched_scale, 2),
                "ambiguous": False,
            },
            "needs_setup": [] if ready else ["verify_trigger_image"],
        }
        params = deepcopy(AI_TRIGGER_DEFAULTS)
        trigger = {
            "id": "trigger-001",
            "type": "image_appears",
            "target_ref": target_ref,
            "asset_ref": "trigger-image-001",
            "params": params,
        }
        if not ready:
            trigger["needs_setup"] = ["verify_trigger_image"]
        return trigger, trigger_asset

    @staticmethod
    def _target_key(window: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(window.get("exe") or "").strip().casefold(),
            str(window.get("class") or "").strip().casefold(),
            str(window.get("title") or "").strip().casefold(),
        )

    def _targets(self, events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str], str]]:
        targets: list[dict[str, Any]] = []
        lookup: dict[tuple[str, str, str], str] = {}
        grouped: dict[tuple[str, ...], dict[str, Any]] = {}
        for event in events:
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            key = self._target_key(window)
            if not any(key) or key in lookup:
                continue
            exe, window_class, title = key
            group_key: tuple[str, ...] = ("app", exe, window_class) if exe and window_class else ("window", *key)
            existing = grouped.get(group_key)
            if existing is not None:
                target_id = str(existing.get("id") or "")
                lookup[key] = target_id
                titles = existing.setdefault("observed_titles", [])
                raw_title = str(window.get("title") or "")
                if raw_title and raw_title not in titles:
                    titles.append(raw_title)
                if len(titles) > 1:
                    existing["title"] = ""
                    existing["window_token"] = _window_token({"exe": window.get("exe"), "class": window.get("class")})
                continue
            target_id = f"target-{len(targets) + 1:02d}"
            lookup[key] = target_id
            target = {
                    "id": target_id,
                    "label": str(window.get("exe") or window.get("title") or f"대상 {len(targets) + 1}"),
                    "exe": str(window.get("exe") or ""),
                    "title": str(window.get("title") or ""),
                    "class": str(window.get("class") or ""),
                    "window_token": _window_token(window),
                    "window_rect": list(window.get("window_rect") or [])[:4],
                    "client_origin": list(window.get("client_origin") or [])[:2],
                    "client_size": list(window.get("client_size") or [])[:2],
                    "observed_handles": {
                        "root": int(window.get("root_hwnd") or window.get("hwnd") or 0),
                        "child": int(window.get("child_hwnd") or 0),
                    },
                    "dpi": int(window.get("dpi") or 96),
                    "scale_percent": int(window.get("scale_percent") or 100),
                    "virtual_screen": list(window.get("virtual_screen") or [])[:4],
                    "coordinate_base": "client",
                    "reacquire_each_run": True,
                    "inactive_click_verified": False,
                    "needs_setup": [],
                    "observed_titles": [str(window.get("title") or "")] if str(window.get("title") or "") else [],
                }
            targets.append(target)
            grouped[group_key] = target
        return targets, lookup

    def _write_click_images(
        self,
        stage: Path,
        event: dict[str, Any],
        click_number: int,
        target_id: str,
        sensitive_regions: list[list[int]],
    ) -> dict[str, Any] | None:
        before = _decode_image(event.get("image_sample_bmp"))
        if before.isNull():
            return None
        after = _decode_image(event.get("image_after_bmp"))
        previous_frames = [
            frame for frame in (
                _decode_image(value) for value in event.get("image_previous_bmps") or []
            ) if not frame.isNull()
        ]
        anchor_values = event.get("image_anchor") if isinstance(event.get("image_anchor"), list) else []
        anchor = QtCore.QPoint(
            int(anchor_values[0]) if len(anchor_values) >= 2 else before.width() // 2,
            int(anchor_values[1]) if len(anchor_values) >= 2 else before.height() // 2,
        )
        sample_left = int(event.get("x") or 0) - before.width() // 2
        sample_top = int(event.get("y") or 0) - before.height() // 2
        before = _redact_screen_regions(before, sample_left, sample_top, sensitive_regions)
        if not after.isNull():
            after = _redact_screen_regions(after, sample_left, sample_top, sensitive_regions)
        previous_frames = [
            _redact_screen_regions(frame, sample_left, sample_top, sensitive_regions)
            for frame in previous_frames
        ]
        before_name = f"frames/step-{click_number:03d}-before.png"
        before.save(str(stage / before_name), "PNG")
        after_name = ""
        if not after.isNull():
            after_name = f"frames/step-{click_number:03d}-after.png"
            after.save(str(stage / after_name), "PNG")
        definitions = [
            ("small", QtCore.QSize(96, 64)),
            ("button", QtCore.QSize(160, 96)),
            ("wide", QtCore.QSize(280, 160)),
        ]
        candidates: list[dict[str, Any]] = []
        base_candidates: dict[str, tuple[QtGui.QImage, QtCore.QRect]] = {}
        for kind, size in definitions:
            rect = _centered_rect(before, anchor, size)
            image = before.copy(rect)
            base_candidates[kind] = (image, rect)
            relative = f"asset-candidates/click-{click_number:03d}-{kind}.png"
            image.save(str(stage / relative), "PNG")
            score, matched_scale = _multiscale_search_score(after, image) if not after.isNull() else (0.0, 1.0)
            stability_scores = [_multiscale_search_score(frame, image)[0] for frame in previous_frames]
            candidates.append(
                {
                    "kind": kind,
                    "file": relative,
                    "rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                    "image_anchor": [anchor.x() - rect.x(), anchor.y() - rect.y()],
                    "click_offset": [anchor.x() - rect.center().x(), anchor.y() - rect.center().y()],
                    "validation_score": round(score, 2),
                    "detail_score": _visual_detail_score(image),
                    "pre_action_scores": [round(value, 2) for value in stability_scores],
                    "stability_score": round(min(stability_scores), 2) if stability_scores else 0.0,
                    "matched_scale": round(matched_scale, 2),
                }
            )
        button_image, button_rect = base_candidates["button"]
        variants = [
            ("button-grayscale", _grayscale_candidate(button_image), _grayscale_candidate(after) if not after.isNull() else QtGui.QImage(), "grayscale"),
            # The structural outline is generated for manual fallback. A full
            # Python-side outline of a multi-monitor frame would be too slow,
            # so final verification is deferred to the managed OpenCV test.
            ("button-outline", _outline_candidate(button_image), QtGui.QImage(), "outline"),
            ("button-cutout", _corner_cutout_candidate(button_image), after, "corner_background_cutout"),
        ]
        for kind, image, validation_scene, preprocessing in variants:
            relative = f"asset-candidates/click-{click_number:03d}-{kind}.png"
            image.save(str(stage / relative), "PNG")
            score, matched_scale = _multiscale_search_score(validation_scene, image) if not validation_scene.isNull() else (0.0, 1.0)
            # Processed variants are manual fallbacks; automatic selection is
            # intentionally limited to the original-color crops above.
            stability_scores = []
            candidates.append(
                {
                    "kind": kind,
                    "file": relative,
                    "rect": [button_rect.x(), button_rect.y(), button_rect.width(), button_rect.height()],
                    "image_anchor": [anchor.x() - button_rect.x(), anchor.y() - button_rect.y()],
                    "click_offset": [anchor.x() - button_rect.center().x(), anchor.y() - button_rect.center().y()],
                    "preprocessing": preprocessing,
                    "validation_score": round(score, 2),
                    "detail_score": _visual_detail_score(image),
                    "pre_action_scores": [round(value, 2) for value in stability_scores],
                    "stability_score": round(min(stability_scores), 2) if stability_scores else 0.0,
                    "matched_scale": round(matched_scale, 2),
                }
            )
        raw_candidates = [item for item in candidates if item.get("kind") in {"small", "button", "wide"}]
        size_penalty = {"small": 1.5, "button": 0.0, "wide": 6.0}
        selected = max(
            raw_candidates,
            key=lambda item: (
                float(item.get("detail_score") or 0)
                + min(100.0, float(item.get("stability_score") or 0)) * (0.55 if previous_frames else 0.0)
                + min(100.0, float(item.get("validation_score") or 0)) * 0.08
                - size_penalty.get(str(item.get("kind") or ""), 0.0)
            ),
        )
        candidates.sort(
            key=lambda item: (
                item is not selected,
                -float(item.get("detail_score") or 0),
                -float(item.get("validation_score") or 0),
            )
        )
        top_score = float(selected.get("validation_score") or 0)
        detail_score = float(selected.get("detail_score") or 0)
        stability_score = float(selected.get("stability_score") or 0)
        quality_ready = detail_score >= 10.0 and (not previous_frames or stability_score >= 76.0)
        readiness = "ready" if quality_ready else "needs_review"
        return {
            "id": f"recorded-image-{click_number:03d}",
            "label": f"녹화 클릭 이미지 {click_number}",
            "target_ref": target_id,
            "required": True,
            "click_purpose": "녹화된 클릭 위치",
            "before_frame": before_name,
            "after_frame": after_name,
            "candidates": candidates,
            "selected_candidate": selected["file"],
            "readiness": readiness,
            "validation": {
                "image_ready": True,
                "search_verified": quality_ready,
                "recording_quality_verified": quality_ready,
                "recording_quality_score": round(detail_score, 2),
                "pre_action_frame_count": len(previous_frames),
                "stability_score": round(stability_score, 2),
                "score": round(top_score, 2),
                "matched_scale": selected.get("matched_scale", 1.0),
                "ambiguous": not quality_ready,
                "inactive_click_verified": False,
            },
            "needs_setup": ([] if readiness == "ready" else ["choose_or_confirm_candidate", "verify_search"])
            + ["verify_inactive_click"],
        }

    @staticmethod
    def _write_contact_sheet(path: Path, rows: list[tuple[str, QtGui.QImage, str]]) -> None:
        if not rows:
            image = QtGui.QImage(960, 180, QtGui.QImage.Format_ARGB32)
            image.fill(QtGui.QColor("#0D1119"))
            painter = QtGui.QPainter(image)
            painter.setPen(QtGui.QColor("#DCE5F3"))
            painter.setFont(QtGui.QFont("Malgun Gothic", 15, QtGui.QFont.Bold))
            painter.drawText(image.rect(), QtCore.Qt.AlignCenter, "기록된 클릭 이미지가 없습니다")
            painter.end()
            image.save(str(path), "PNG")
            return
        columns = 3
        card_width, card_height = 310, 230
        sheet = QtGui.QImage(columns * card_width, ((len(rows) + columns - 1) // columns) * card_height, QtGui.QImage.Format_ARGB32)
        sheet.fill(QtGui.QColor("#0D1119"))
        painter = QtGui.QPainter(sheet)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        for index, (label, source, target) in enumerate(rows):
            x = (index % columns) * card_width
            y = (index // columns) * card_height
            painter.setPen(QtGui.QPen(QtGui.QColor("#2B394D"), 1))
            painter.setBrush(QtGui.QColor("#151C27"))
            painter.drawRoundedRect(QtCore.QRect(x + 8, y + 8, card_width - 16, card_height - 16), 12, 12)
            pixmap = QtGui.QPixmap.fromImage(source).scaled(270, 150, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            painter.drawPixmap(x + (card_width - pixmap.width()) // 2, y + 20, pixmap)
            painter.setPen(QtGui.QColor("#F0F4FA"))
            painter.setFont(QtGui.QFont("Malgun Gothic", 10, QtGui.QFont.Bold))
            painter.drawText(QtCore.QRect(x + 18, y + 176, 274, 22), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
            painter.setPen(QtGui.QColor("#92A0B5"))
            painter.setFont(QtGui.QFont("Malgun Gothic", 8))
            painter.drawText(QtCore.QRect(x + 18, y + 199, 274, 20), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, target)
        painter.end()
        sheet.save(str(path), "PNG")


@dataclass(frozen=True)
class AIImportIssue:
    severity: str
    code: str
    detail: str
    step_id: str = ""


def _items_by_id(value: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            if isinstance(item, dict):
                prepared = dict(item)
                prepared.setdefault("id", str(key))
                rows.append(prepared)
    elif isinstance(value, list):
        rows = [dict(item) for item in value if isinstance(item, dict)]
    else:
        rows = []
    lookup = {str(item.get("id") or ""): item for item in rows if str(item.get("id") or "")}
    return rows, lookup


def normalize_ai_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Repair harmless formatting drift without inventing business logic."""
    from .action_editor import ACTION_LABELS

    normalized = deepcopy(payload)
    normalized.setdefault("schema_version", AI_SCHEMA_VERSION)
    normalized.setdefault("source_package_id", str(normalized.pop("package_id", "") or ""))
    normalized.setdefault("name", "AI 자동화 초안")
    normalized.setdefault("description", "AI 녹화 패키지에서 생성한 자동화")
    normalized.setdefault("variables", {})
    normalized.setdefault("triggers", [{"id": "trigger-001", "type": "manual"}])
    normalized.setdefault("setup_requirements", [])

    for collection in ("targets", "assets", "workflows", "steps"):
        rows, _lookup = _items_by_id(normalized.get(collection))
        normalized[collection] = rows

    aliases: dict[str, str] = {}
    for action in ALLOWED_ACTIONS:
        aliases[action.casefold()] = action
        aliases[action.replace("_", " ").casefold()] = action
        aliases[action.replace("_", "-").casefold()] = action
    for action, label in ACTION_LABELS.items():
        aliases[str(label).strip().casefold()] = action
    aliases.update({
        "이미지 검색": "image_search", "멀티 이미지 서치": "image_search", "멀티 이미지 검색": "image_search",
        "화면 확인": "screen_condition", "조건": "screen_condition", "반복": "flow_control",
        "서브 매크로": "call_submacro", "알림": "remote_notify", "프로그램 시작": "run_program",
    })

    steps = normalized["steps"]
    seen_ids: set[str] = set()
    for index, step in enumerate(steps, 1):
        requested_id = str(step.get("id") or "").strip()
        if not requested_id:
            requested_id = f"step-{index:03d}"
        step["id"] = requested_id
        seen_ids.add(requested_id)
        raw_action = str(step.get("action") or "").strip()
        step["action"] = aliases.get(raw_action.casefold(), raw_action)
        if "success" in step and "on_success" not in step:
            step["on_success"] = step.pop("success")
        if "failure" in step and "on_fail" not in step:
            step["on_fail"] = step.pop("failure")
        if "parameters" in step and "params" not in step and isinstance(step.get("parameters"), dict):
            step["params"] = step.pop("parameters")
        step.setdefault("params", {})

    targets = normalized["targets"]
    assets = normalized["assets"]
    only_target = str(targets[0].get("id") or "") if len(targets) == 1 else ""
    only_asset = str(assets[0].get("id") or "") if len(assets) == 1 else ""
    target_actions = {
        "mouse_click", "inactive_click", "image_search", "screen_condition", "type_text", "browser_action",
        "ocr", "run_program", "terminate_program",
    }
    for step in steps:
        if only_target and step.get("action") in target_actions and not str(step.get("target_ref") or ""):
            step["target_ref"] = only_target
        if only_asset and step.get("action") in {"image_search", "screen_condition"} and not str(step.get("asset_ref") or ""):
            step["asset_ref"] = only_asset
    return normalized


def validate_ai_document(payload: Any) -> list[AIImportIssue]:
    issues: list[AIImportIssue] = []
    if not isinstance(payload, dict):
        return [AIImportIssue("error", "root_type", "JSON 최상위 값은 객체여야 합니다.")]
    required = {"schema_version", "name", "description", "targets", "assets", "variables", "triggers", "steps", "setup_requirements"}
    allowed_top = required | {"source_package_id", "workflows"}
    for key in sorted(required - set(payload)):
        issues.append(AIImportIssue("error", "missing_top_level", f"필수 항목 `{key}`가 없습니다."))
    for key in sorted(set(payload) - allowed_top):
        issues.append(AIImportIssue("error", "unknown_top_level", f"허용되지 않은 최상위 항목 `{key}`가 있습니다."))
    if str(payload.get("schema_version") or "") != AI_SCHEMA_VERSION:
        issues.append(AIImportIssue("error", "schema_version", f"지원 버전은 {AI_SCHEMA_VERSION}입니다."))
    steps, lookup = _items_by_id(payload.get("steps"))
    if not steps:
        issues.append(AIImportIssue("error", "empty_steps", "생성할 노드가 없습니다."))
    if len(lookup) != len(steps):
        issues.append(AIImportIssue("error", "duplicate_step_id", "노드 ID가 비어 있거나 중복되었습니다."))
    target_rows, target_lookup = _items_by_id(payload.get("targets"))
    asset_rows, asset_lookup = _items_by_id(payload.get("assets"))
    if len(target_rows) != len(target_lookup):
        issues.append(AIImportIssue("error", "duplicate_target_id", "대상 프로필 ID가 비어 있거나 중복되었습니다."))
    if len(asset_rows) != len(asset_lookup):
        issues.append(AIImportIssue("error", "duplicate_asset_id", "이미지 자산 ID가 비어 있거나 중복되었습니다."))
    trigger_rows = payload.get("triggers") if isinstance(payload.get("triggers"), list) else []
    for trigger in trigger_rows:
        if not isinstance(trigger, dict):
            issues.append(AIImportIssue("error", "trigger_type", "실행 조건 형식이 올바르지 않습니다."))
            continue
        kind = str(trigger.get("type") or "")
        if kind not in set(AI_TRIGGER_TYPES) | {"image_appear"}:
            issues.append(AIImportIssue("error", "unsupported_trigger", f"지원하지 않는 실행 조건: {kind or '(없음)'}"))
            continue
        if kind in {"image_appear", "image_appears"}:
            target_ref = str(trigger.get("target_ref") or "")
            asset_ref = str(trigger.get("asset_ref") or "")
            if target_ref and target_ref not in target_lookup:
                issues.append(AIImportIssue("error", "unknown_trigger_target", f"실행 조건 대상 `{target_ref}`를 찾을 수 없습니다."))
            if not asset_ref or asset_ref not in asset_lookup:
                issues.append(AIImportIssue("warning", "missing_trigger_asset", "시작 화면 이미지를 확인해야 합니다."))
        elif kind in {"process_start", "process_stop"} and not str(trigger.get("process") or "").strip():
            issues.append(AIImportIssue("warning", "missing_trigger_process", "프로그램 트리거의 실행 파일 이름을 확인해야 합니다."))
        elif kind == "window_appears" and not str(trigger.get("title") or "").strip():
            issues.append(AIImportIssue("warning", "missing_trigger_title", "창 트리거의 제목 문자열을 확인해야 합니다."))
        elif kind == "schedule" and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(trigger.get("time") or "")):
            issues.append(AIImportIssue("warning", "invalid_trigger_time", "시간 트리거는 HH:mm 형식으로 확인해야 합니다."))
    for step in steps:
        step_id = str(step.get("id") or "")
        action = str(step.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            issues.append(AIImportIssue("error", "unsupported_action", f"허용되지 않은 액션: {action or '(없음)'}", step_id))
        flattened = {str(key).casefold() for key in step}
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        if "params" in step and not isinstance(step.get("params"), dict):
            issues.append(AIImportIssue("error", "params_type", "params는 JSON 객체여야 합니다.", step_id))
        if action in ALLOWED_ACTIONS and isinstance(params, dict):
            from .action_editor import ACTION_FIELDS, get_path

            specs = ACTION_FIELDS.get(action, [])
            allowed_roots = {spec.key.split(".", 1)[0] for spec in specs}
            for key in sorted(set(params) - allowed_roots - {"data_classification", "sensitive"}):
                issues.append(AIImportIssue(
                    "warning", "ignored_param",
                    f"`{action}`에 없는 params 필드 `{key}`는 가져올 때 무시됩니다.", step_id,
                ))
            missing = object()
            for spec in specs:
                value = get_path(params, spec.key, missing)
                if value is missing:
                    continue
                valid_type = True
                if spec.kind in {"int", "duration"}:
                    valid_type = isinstance(value, int) and not isinstance(value, bool)
                elif spec.kind == "float":
                    valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
                elif spec.kind == "bool":
                    valid_type = isinstance(value, bool)
                elif spec.kind in {"assets", "offset"}:
                    valid_type = isinstance(value, list)
                elif spec.kind not in {"choice"}:
                    valid_type = isinstance(value, str) or spec.kind in {"table"}
                if not valid_type:
                    issues.append(AIImportIssue(
                        "warning", "param_type",
                        f"`{action}.{spec.key}` 값 형식은 {spec.kind}이어야 하며 기본값으로 보정될 수 있습니다.", step_id,
                    ))
                if spec.options and value not in {choice_value for _label, choice_value in spec.options}:
                    allowed = ", ".join(str(choice_value) for _label, choice_value in spec.options)
                    issues.append(AIImportIssue(
                        "warning", "param_choice",
                        f"`{action}.{spec.key}` 허용값은 {allowed}입니다.", step_id,
                    ))
        flattened.update(str(key).casefold() for key in params)
        forbidden = sorted(flattened & FORBIDDEN_KEYS)
        if forbidden:
            issues.append(AIImportIssue("error", "raw_code", f"실행 코드 필드는 허용되지 않습니다: {', '.join(forbidden)}", step_id))
        target_ref = str(step.get("target_ref") or "")
        if target_ref and target_ref not in target_lookup:
            issues.append(AIImportIssue("error", "unknown_target", f"대상 프로필 `{target_ref}`를 찾을 수 없습니다.", step_id))
        asset_ref = str(step.get("asset_ref") or "")
        if asset_ref and asset_ref not in asset_lookup:
            issues.append(AIImportIssue("error", "unknown_asset", f"이미지 `{asset_ref}`를 찾을 수 없습니다.", step_id))
        if action in {"image_search", "screen_condition"} and not asset_ref:
            issues.append(AIImportIssue("warning", "missing_asset", "이미지 서치 자산을 설정해야 합니다.", step_id))
        for edge in ("on_success", "on_fail"):
            target = str(step.get(edge) or "")
            if target and target not in lookup and target not in {"0", "end", "END"}:
                issues.append(AIImportIssue("error", "bad_edge", f"{edge} 목적지 `{target}`가 없습니다.", step_id))
        if action == "run_program":
            command = str(params.get("command") or step.get("command") or "").strip()
            if command:
                issues.append(AIImportIssue("warning", "program_confirmation", "외부 프로그램 실행 경로는 가져온 뒤 사용자가 확인해야 합니다.", step_id))
        if action in {"type_text", "set_var"}:
            value = str(params.get("text") or params.get("value") or step.get("text") or step.get("value") or "")
            if value and value != "[REDACTED]" and re.search(r"password|passwd|api[_-]?key|secret", value, re.I):
                issues.append(AIImportIssue("error", "plaintext_secret", "민감정보로 보이는 평문이 포함되어 있습니다. 보안 보관함을 사용하세요.", step_id))
            classification = str(params.get("data_classification") or step.get("data_classification") or "").casefold()
            sensitive = bool(params.get("sensitive", step.get("sensitive", False))) or classification in {"secret", "credential", "password"}
            if value and value != "[REDACTED]" and sensitive:
                issues.append(AIImportIssue("error", "plaintext_secret", "민감 입력값은 평문 대신 vault_get 보안 보관함 참조를 사용해야 합니다.", step_id))
            elif action == "type_text" and value and value != "[REDACTED]" and classification not in {"public", "non_sensitive"}:
                issues.append(AIImportIssue("warning", "classify_text", "입력 텍스트가 민감정보인지 가져온 뒤 확인해야 합니다.", step_id))
        if action in {"image_search", "screen_condition"}:
            click = params.get("click") if isinstance(params.get("click"), dict) else step.get("click") if isinstance(step.get("click"), dict) else {}
            mode = str(click.get("mode") or "inactive").casefold()
            if mode not in {"active", "inactive", "none"}:
                issues.append(AIImportIssue("error", "unsupported_click_mode", f"지원하지 않는 클릭 방식: {mode}", step_id))
        if action == "flow_control":
            jump = str(params.get("jump_to") or step.get("jump_to") or "")
            count = int(params.get("repeat_count") or step.get("repeat_count") or 0)
            counter = str(params.get("counter_key") or step.get("counter_key") or "").strip()
            if not jump and not count and not counter:
                issues.append(AIImportIssue(
                    "warning", "workflow_placeholder",
                    "작업 구분용 반복 이동 노드는 필요하지 않습니다. 작업 레인으로 표시되므로 삭제를 권장합니다.",
                    step_id,
                ))
            if jump and jump in lookup and count == 0:
                issues.append(AIImportIssue("warning", "possible_infinite_loop", "반복 제한이 없는 이동 노드입니다.", step_id))
    return issues


def _merge_safe_step(action: str, raw: dict[str, Any]) -> dict[str, Any]:
    from .action_editor import ACTION_FIELDS, action_template, get_path, set_path

    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    source = {**params, **{key: value for key, value in raw.items() if key not in {"params", "id", "action", "target_ref", "asset_ref"}}}
    step = action_template(action)
    allowed_paths = {spec.key for spec in ACTION_FIELDS.get(action, [])} | SAFE_COMMON_STEP_KEYS
    allowed_roots = {path.split(".", 1)[0] for path in allowed_paths}
    for key, value in source.items():
        if key in FORBIDDEN_KEYS or key not in allowed_roots:
            continue
        if key in allowed_paths:
            step[key] = deepcopy(value)
            continue
        nested_paths = [path for path in allowed_paths if path.startswith(key + ".")]
        if isinstance(value, (dict, list)) and nested_paths:
            for path in nested_paths:
                nested = get_path({key: value}, path, None)
                if nested is not None:
                    set_path(step, path, deepcopy(nested))
    return step


def materialize_ai_document(
    payload: dict[str, Any],
    repository,
    package_stage: Path | None = None,
) -> tuple[dict[str, Any], list[AIImportIssue]]:
    issues = validate_ai_document(payload)
    if any(issue.severity == "error" for issue in issues):
        return {}, issues
    raw_targets, _raw_target_lookup = _items_by_id(payload.get("targets"))
    targets: list[dict[str, Any]] = []
    target_lookup: dict[str, dict[str, Any]] = {}
    target_id_remap: dict[str, str] = {}
    grouped_targets: dict[tuple[str, ...], dict[str, Any]] = {}
    for source in raw_targets:
        target = deepcopy(source)
        target_id = str(target.get("id") or "")
        exe = str(target.get("exe") or "").strip().casefold()
        window_class = str(target.get("class") or "").strip().casefold()
        title = str(target.get("title") or "").strip().casefold()
        group_key: tuple[str, ...] = ("app", exe, window_class) if exe and window_class else ("window", exe, window_class, title)
        existing = grouped_targets.get(group_key)
        if existing is None:
            target["needs_setup"] = [
                value for value in target.get("needs_setup") or [] if value != "verify_inactive_click"
            ]
            targets.append(target)
            grouped_targets[group_key] = target
            target_lookup[target_id] = target
            target_id_remap[target_id] = target_id
            continue
        target_lookup[target_id] = existing
        target_id_remap[target_id] = str(existing.get("id") or target_id)
        if str(existing.get("title") or "") != str(target.get("title") or ""):
            existing["title"] = ""
            if existing.get("class") and existing.get("exe"):
                existing["window_token"] = f"ahk_class {existing['class']} ahk_exe {existing['exe']}"
        existing["inactive_click_verified"] = bool(
            existing.get("inactive_click_verified") or target.get("inactive_click_verified")
        )
    assets, asset_lookup = _items_by_id(payload.get("assets"))
    asset_aliases: dict[str, str] = {}
    asset_states: dict[str, dict[str, Any]] = {}
    existing_assets = repository.load_assets()
    manifest_lookup: dict[str, dict[str, Any]] = {}
    if package_stage is not None:
        try:
            manifest = json.loads((package_stage / "asset-manifest.json").read_text(encoding="utf-8-sig"))
        except (OSError, TypeError, ValueError):
            manifest = {}
        manifest_lookup = {
            str(item.get("id") or ""): item
            for item in manifest.get("assets", [])
            if isinstance(item, dict) and str(item.get("id") or "")
        }
    for item in assets:
        asset_id = str(item.get("id") or "")
        alias = str(item.get("alias") or item.get("label") or asset_id).strip()
        source_item = manifest_lookup.get(asset_id, {})
        asset_states[asset_id] = {**source_item, **item}
        if alias in existing_assets:
            asset_aliases[asset_id] = alias
            continue
        candidate = str(item.get("candidate") or "")
        if not candidate:
            candidate = str(source_item.get("selected_candidate") or "")
        if package_stage is None or not candidate:
            continue
        source = (package_stage / candidate).resolve()
        try:
            source.relative_to(package_stage.resolve())
        except ValueError:
            continue
        image = QtGui.QImage(str(source)) if source.is_file() else QtGui.QImage()
        if image.isNull():
            continue
        base = alias or asset_id
        unique = base
        suffix = 2
        while repository.asset_path(unique) is not None:
            unique = f"{base}-{suffix}"
            suffix += 1
        repository.add_asset_image(image, unique)
        asset_aliases[asset_id] = unique

    raw_steps, step_lookup = _items_by_id(payload.get("steps"))
    workflow_rows, _workflow_lookup = _items_by_id(payload.get("workflows"))
    workflow_labels = {
        str(item.get("id") or ""): str(item.get("label") or item.get("id") or "작업")
        for item in workflow_rows
        if str(item.get("id") or "")
    }
    index_by_id = {str(step.get("id")): index for index, step in enumerate(raw_steps, start=1)}
    steps: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for raw in raw_steps:
        step_id = str(raw.get("id") or "")
        action = str(raw.get("action") or "")
        step = _merge_safe_step(action, raw)
        target_ref = target_id_remap.get(str(raw.get("target_ref") or ""), str(raw.get("target_ref") or ""))
        target = target_lookup.get(target_ref, {})
        window_token = str(target.get("window_token") or "")
        exe = str(target.get("exe") or "")
        if action in {"image_search", "screen_condition"}:
            asset_ref = str(raw.get("asset_ref") or "")
            alias = asset_aliases.get(asset_ref, "")
            asset_state = asset_states.get(asset_ref, {})
            validation = asset_state.get("validation") if isinstance(asset_state.get("validation"), dict) else {}
            step["asset"] = alias
            step["engine"] = "opencv"
            step["search_profile"] = "balanced"
            step["confidence"] = max(78, min(88, int(step.get("confidence") or 82)))
            step["timeout"] = max(5000 if action == "screen_condition" else 2000, int(step.get("timeout") or 0))
            step.setdefault("poll_delay", 40)
            step["region_mode"] = "client" if target else "screen"
            step["region_coords"] = "relative" if target else "screen"
            step["region_window"] = window_token
            step["region_window_exe"] = exe
            step["fallback_full_region"] = bool(target)
            click = step.get("click") if isinstance(step.get("click"), dict) else {}
            click.update({"mode": "inactive", "method": "auto", "window": window_token, "window_exe": exe})
            selected_candidate = str(asset_state.get("selected_candidate") or asset_state.get("candidate") or "")
            candidate_rows = asset_state.get("candidates") if isinstance(asset_state.get("candidates"), list) else []
            selected_row = next(
                (
                    candidate
                    for candidate in candidate_rows
                    if isinstance(candidate, dict) and str(candidate.get("file") or "") == selected_candidate
                ),
                {},
            )
            offset = selected_row.get("click_offset") if isinstance(selected_row, dict) else None
            if isinstance(offset, (list, tuple)) and len(offset) >= 2:
                click.update({
                    "click_image": int(offset[0]) == 0 and int(offset[1]) == 0,
                    "click_offset": int(offset[0]) != 0 or int(offset[1]) != 0,
                    "offset": [int(offset[0]), int(offset[1])],
                })
            step["click"] = click
            step["click_enabled"] = action == "image_search"
            if action == "screen_condition":
                step.pop("click", None)
                step.setdefault("label", f"화면 조건 · {alias or '이미지 확인'}")
            if not alias:
                step.setdefault("needs_setup", []).append("select_asset")
            elif str(asset_state.get("readiness") or "") not in {"ready", "verified"} or not bool(
                validation.get("search_verified", asset_state.get("search_verified", False))
            ):
                step.setdefault("needs_setup", []).append("verify_search")
        elif action in {"inactive_click", "mouse_click", "type_text"} and target:
            step["window"] = window_token
            step["window_exe"] = exe
            if action == "mouse_click":
                step["coordinate_scope"] = "client"
            if action == "type_text":
                step.setdefault("mode", "inactive")
        elif action == "ocr" and target:
            step["capture_mode"] = "client"
            step["coord_base"] = "client"
            step["window_title"] = window_token
        # The runtime's automatic click method reacquires the current target
        # every run. A manual handle-lab profile remains an optional advanced
        # override; it must not block an otherwise usable AI draft.
        for edge in ("on_success", "on_fail"):
            target_id = str(raw.get(edge) or "")
            if target_id in index_by_id:
                step[edge] = index_by_id[target_id]
            elif target_id in {"0", "end", "END", ""}:
                step.pop(edge, None)
        if "retry_count" in raw:
            step["retry_count"] = max(0, int(raw.get("retry_count") or 0))
        if "retry_delay" in raw:
            step["retry_delay"] = max(0, int(raw.get("retry_delay") or 0))
        needs = list(dict.fromkeys(str(value) for value in step.get("needs_setup", []) if str(value)))
        if action == "type_text":
            text_value = str(step.get("text") or "")
            raw_params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
            classification = str(raw.get("data_classification") or raw_params.get("data_classification") or "").casefold()
            if text_value in {"", "[REDACTED]"}:
                needs.append("provide_text_or_vault_reference")
            elif classification not in {"public", "non_sensitive"}:
                needs.append("classify_sensitive_input")
        if action in {"run_program", "terminate_program"}:
            needs.append("confirm_program_command")
        if action == "flow_control":
            raw_params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
            if str(raw.get("jump_to") or raw_params.get("jump_to") or "") and int(
                raw.get("repeat_count") or raw_params.get("repeat_count") or 0
            ) == 0:
                needs.append("confirm_loop_limit")
        if action == "vault_get" and not str(step.get("secret") or "").strip():
            needs.append("select_dpapi_vault_secret")
        step["needs_setup"] = list(dict.fromkeys(needs))
        if step["needs_setup"]:
            unresolved.append({"step": len(steps) + 1, "id": step_id, "items": step["needs_setup"]})
        step["_ai"] = {
            "source_id": step_id,
            "target_ref": target_ref,
            "asset_ref": str(raw.get("asset_ref") or ""),
            "source_evidence": deepcopy(raw.get("source_evidence") or {}),
            "asset_validation": deepcopy(validation) if action in {"image_search", "screen_condition"} else {},
        }
        workflow_id = str(raw.get("workflow_id") or "").strip()
        workflow_label = str(raw.get("workflow_label") or workflow_labels.get(workflow_id) or "").strip()
        if workflow_id:
            step["workflow_id"] = workflow_id
            step["workflow_label"] = workflow_label or workflow_id
        steps.append(step)
    top_requirements = [str(value) for value in payload.get("setup_requirements", []) if str(value)] if isinstance(payload.get("setup_requirements"), list) else []
    stored_targets: list[dict[str, Any]] = []
    for target in targets:
        clean_target = deepcopy(target)
        # Observed handles are useful evidence inside the recording package,
        # but never become runtime configuration. A fresh handle is resolved
        # from exe/title/class every run.
        clean_target.pop("observed_handles", None)
        for key in ("hwnd", "root_hwnd", "child_hwnd", "target_hwnd"):
            clean_target.pop(key, None)
        clean_target["reacquire_each_run"] = True
        stored_targets.append(clean_target)
    runtime_triggers: list[dict[str, Any]] = []
    for raw_trigger in payload.get("triggers") if isinstance(payload.get("triggers"), list) else []:
        if not isinstance(raw_trigger, dict):
            continue
        kind = str(raw_trigger.get("type") or "manual")
        params = raw_trigger.get("params") if isinstance(raw_trigger.get("params"), dict) else {}
        if kind == "manual":
            runtime_triggers.append({
                "id": str(raw_trigger.get("id") or "trigger-001"), "type": "manual",
                "enabled": bool(raw_trigger.get("enabled", True)),
            })
            continue
        if kind in {"process_start", "process_stop"}:
            runtime_triggers.append({
                "id": str(raw_trigger.get("id") or "trigger-001"), "type": kind,
                "enabled": bool(raw_trigger.get("enabled", True)),
                "process": str(raw_trigger.get("process") or params.get("process") or ""),
                "interval": max(0.5, float(raw_trigger.get("interval") or params.get("interval") or 1)),
            })
            continue
        if kind == "window_appears":
            runtime_triggers.append({
                "id": str(raw_trigger.get("id") or "trigger-001"), "type": kind,
                "enabled": bool(raw_trigger.get("enabled", True)),
                "title": str(raw_trigger.get("title") or params.get("title") or ""),
                "interval": max(0.5, float(raw_trigger.get("interval") or params.get("interval") or 1)),
            })
            continue
        if kind == "schedule":
            days = raw_trigger.get("days", params.get("days", list(range(7))))
            runtime_triggers.append({
                "id": str(raw_trigger.get("id") or "trigger-001"), "type": kind,
                "enabled": bool(raw_trigger.get("enabled", True)),
                "time": str(raw_trigger.get("time") or params.get("time") or "00:00"),
                "days": [int(day) for day in days if str(day).lstrip("-").isdigit() and 0 <= int(day) <= 6]
                if isinstance(days, list) else list(range(7)),
                "interval": max(0.5, float(raw_trigger.get("interval") or params.get("interval") or 1)),
            })
            continue
        if kind == "ocr_threshold":
            region = raw_trigger.get("region", params.get("region", [0, 0, 0, 0]))
            runtime_triggers.append({
                "id": str(raw_trigger.get("id") or "trigger-001"), "type": kind,
                "enabled": bool(raw_trigger.get("enabled", True)),
                "region": [int(value) for value in list(region)[:4]] if isinstance(region, (list, tuple)) else [0, 0, 0, 0],
                "operator": str(raw_trigger.get("operator") or params.get("operator") or ">="),
                "value": float(raw_trigger.get("value", params.get("value", 0)) or 0),
                "profile": str(raw_trigger.get("profile") or params.get("profile") or "number"),
                "lang": str(raw_trigger.get("lang") or params.get("lang") or "eng+kor"),
                "engine": str(raw_trigger.get("engine") or params.get("engine") or "auto"),
                "interval": max(0.5, float(raw_trigger.get("interval") or params.get("interval") or 3)),
            })
            continue
        if kind not in {"image_appear", "image_appears"}:
            continue
        target_ref = target_id_remap.get(
            str(raw_trigger.get("target_ref") or ""), str(raw_trigger.get("target_ref") or "")
        )
        target = target_lookup.get(target_ref, {})
        asset_ref = str(raw_trigger.get("asset_ref") or "")
        alias = asset_aliases.get(asset_ref, "")
        asset_state = asset_states.get(asset_ref, {})
        validation = asset_state.get("validation") if isinstance(asset_state.get("validation"), dict) else {}
        needs = [str(value) for value in raw_trigger.get("needs_setup") or [] if str(value)]
        if not alias or not bool(validation.get("search_verified", asset_state.get("search_verified", False))):
            needs.append("verify_trigger_image")
        runtime_triggers.append({
            "id": str(raw_trigger.get("id") or "trigger-001"),
            "type": "image_appears",
            "enabled": bool(raw_trigger.get("enabled", True)),
            "asset": alias,
            "asset_ref": asset_ref,
            "target_ref": target_ref,
            "window": str(target.get("window_token") or ""),
            "window_exe": str(target.get("exe") or ""),
            "search_scope": "target_client" if target else "screen",
            "interval": max(0.5, int(params.get("poll_interval") or 500) / 1000.0),
            "stable_ms": max(0, int(params.get("stable_ms") or 500)),
            "multi_scale": bool(params.get("multi_scale", True)),
            "fire_mode": "on_appear",
            "rearm_mode": "after_disappear",
            "threshold": max(0.5, min(0.99, float(params.get("threshold") or 0.86))),
            "needs_setup": list(dict.fromkeys(needs)),
        })
    if not runtime_triggers:
        runtime_triggers = [{"id": "trigger-001", "type": "manual", "enabled": True}]

    graph_positions: dict[str, list[float]] = {}
    workflow_order: list[str] = []
    workflow_columns: dict[str, int] = {}
    for index, step in enumerate(steps, start=1):
        workflow_id = str(step.get("workflow_id") or "workflow-01")
        if workflow_id not in workflow_order:
            workflow_order.append(workflow_id)
        column = workflow_columns.get(workflow_id, 0)
        workflow_columns[workflow_id] = column + 1
        graph_positions[str(index)] = [column * 340.0, workflow_order.index(workflow_id) * 210.0]

    macro = {
        "name": str(payload.get("name") or "AI 자동화 초안").strip() or "AI 자동화 초안",
        "description": str(payload.get("description") or ""),
        "meta": {
            "coord_mode": "Client",
            "release_channel": "test",
            # Every imported document starts as a draft. Even a structurally
            # complete ChatGPT response must be explicitly reviewed before a
            # real click/input run is allowed.
            "ai_draft": True,
            "ai_schema_version": AI_SCHEMA_VERSION,
            "ai_source_package": str(payload.get("source_package_id") or ""),
            "ai_imported_at": datetime.now(timezone.utc).isoformat(),
        },
        "steps": steps,
        "graph_positions": graph_positions,
        "triggers": runtime_triggers,
        "variables": deepcopy(payload.get("variables") if isinstance(payload.get("variables"), (list, dict)) else {}),
        "ai_setup": {
            "targets": stored_targets,
            "assets": assets,
            "requirements": top_requirements,
            "unresolved": unresolved,
        },
    }
    return macro, issues


def ai_draft_readiness(macro: dict[str, Any]) -> tuple[int, int, list[str]]:
    checks: list[tuple[str, bool]] = []
    setup = macro.get("ai_setup") if isinstance(macro.get("ai_setup"), dict) else {}
    targets = setup.get("targets") if isinstance(setup.get("targets"), list) else []
    steps = macro.get("steps") if isinstance(macro.get("steps"), list) else []
    triggers = macro.get("triggers") if isinstance(macro.get("triggers"), list) else []
    automatic_triggers = [
        trigger for trigger in triggers
        if isinstance(trigger, dict) and str(trigger.get("type") or "manual") != "manual"
    ]
    image_triggers = [
        trigger for trigger in automatic_triggers
        if str(trigger.get("type") or "") in {"image_appear", "image_appears"}
    ]
    checks.append((
        "자동 실행 조건",
        all(not trigger.get("needs_setup") for trigger in automatic_triggers)
        and all(str(trigger.get("asset") or "") for trigger in image_triggers),
    ))
    target_actions = {"mouse_click", "inactive_click", "image_search", "screen_condition", "type_text", "browser_action", "ocr", "run_program", "terminate_program"}
    needs_target = any(isinstance(step, dict) and step.get("action") in target_actions for step in steps)
    checks.append(("대상 프로그램", (not needs_target) or (bool(targets) and all(str(item.get("exe") or item.get("window_token") or "") for item in targets if isinstance(item, dict)))))
    image_steps = [step for step in steps if isinstance(step, dict) and step.get("action") in {"image_search", "screen_condition"}]
    checks.append(("이미지 자산", all(str(step.get("asset") or "") for step in image_steps)))
    inactive_steps = [step for step in steps if isinstance(step, dict) and step.get("action") in {"image_search", "screen_condition", "inactive_click", "type_text"}]
    checks.append(("비활성 클릭", all("verify_inactive_click" not in (step.get("needs_setup") or []) for step in inactive_steps)))
    ocr_steps = [step for step in steps if isinstance(step, dict) and step.get("action") == "ocr"]
    checks.append(("OCR 영역", all(not step.get("needs_setup") for step in ocr_steps)))
    vault_steps = [step for step in steps if isinstance(step, dict) and step.get("action") == "vault_get"]
    checks.append(("보안 값", all(str(step.get("secret") or "") for step in vault_steps)))
    checks.append(("노드 연결", all(int(step.get("on_success") or 0) <= len(steps) and int(step.get("on_fail") or 0) <= len(steps) for step in steps if isinstance(step, dict))))
    requirements = setup.get("requirements") if isinstance(setup.get("requirements"), list) else []
    checks.append(("가져오기 질문", not any(str(value).strip() for value in requirements)))
    checks.append(("필수 설정", not any(step.get("needs_setup") for step in steps if isinstance(step, dict))))
    complete = sum(1 for _label, okay in checks if okay)
    pending = [label for label, okay in checks if not okay]
    return complete, len(checks), pending
