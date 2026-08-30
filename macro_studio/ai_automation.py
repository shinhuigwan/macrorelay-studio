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
        if item.get("type") not in {"mouse", "key", "mouse_drag"}:
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


def chatgpt_prompt(package_id: str, packaged_trigger: dict[str, Any] | None = None) -> str:
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
        "triggers": [trigger_example],
        "steps": [{
            "id": "step-01", "action": "image_search", "target_ref": "target-01",
            "asset_ref": "recorded-image-001",
            "params": {"confidence": 86, "timeout": 1200, "click_enabled": True},
            "on_success": "end", "on_fail": "end", "retry_count": 2, "retry_delay": 250,
            "needs_setup": [],
            "source_evidence": {"timeline_id": "click-001", "frame": "frames/step-001-before.png"},
        }],
        "setup_requirements": [],
    }
    return f"""당신은 MacroRelay Studio 자동화 설계 도우미입니다.

첨부된 AI 녹화 패키지 `{package_id}`를 분석하십시오. 이 패키지에는 사용자가 수행한 동작의 비식별 타임라인, 대상 프로그램 정보, 원본 PNG 이미지 후보와 선택적 동작 영상이 들어 있습니다.

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
11. 마지막 답변에는 설명과 JSON을 분리하고, JSON은 하나의 완전한 코드 블록으로 출력하십시오.

스키마 버전은 `{AI_SCHEMA_VERSION}`입니다. 최상위 필수 키는 `schema_version`, `source_package_id`, `name`, `description`, `targets`, `assets`, `variables`, `triggers`, `steps`, `setup_requirements`입니다.

각 target은 `id`, `label`, `exe`, `title`, `class`, `window_token`, `coordinate_base`, `inactive_click_verified`를 사용합니다. 과거 hwnd 숫자는 저장하지 마십시오.

각 asset은 `id`, `label`, `target_ref`, `candidate`, `required`, `click_purpose`를 사용합니다. candidate는 asset-manifest.json에 있는 상대 PNG 경로만 사용하십시오.

각 trigger는 `manual` 또는 `image_appear`만 사용합니다. `image_appear`는 패키지의 `target_ref`, `asset_ref`, `params`를 변경하지 마십시오.

각 step은 `id`, `action`, 선택적 `params`, `target_ref`, `asset_ref`, `on_success`, `on_fail`, `retry_count`, `retry_delay`, `needs_setup`, `source_evidence`를 사용합니다. 노드 연결은 step id를 참조합니다.

형식 기준은 패키지의 `schema.json`이며, 최소 예시는 다음과 같습니다. 이 예시의 값은 복사하지 말고 실제 패키지와 사용자 답변으로 채우십시오.
{json.dumps(example, ensure_ascii=False, indent=2)}

허용 액션:
{', '.join(sorted(ALLOWED_ACTIONS))}

이미지 클릭은 기본적으로 `image_search`, `click.mode=inactive`, `click.method=auto`를 사용하십시오. 검색 범위는 대상 프로그램의 클라이언트 상대 좌표를 우선 사용하며 클릭 오프셋은 선택된 PNG 후보의 값을 그대로 사용하십시오.
"""


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
            "triggers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "type"],
                    "properties": {
                        "id": identifier,
                        "type": {"enum": ["manual", "image_appear"]},
                        "target_ref": {"type": "string"},
                        "asset_ref": {"type": "string"},
                        "params": {"type": "object"},
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
                    },
                },
            },
            "setup_requirements": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
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

        packaged_trigger, trigger_asset = self._prepare_trigger(stage, trigger_config or {}, targets, target_lookup)
        if trigger_asset:
            assets.append(trigger_asset)
            preview = QtGui.QImage(str(stage / str(trigger_asset["selected_candidate"])))
            contact_rows.append(("실행 조건", preview, "특정 화면이 나타나면 자동 실행"))

        prompt = chatgpt_prompt(identifier, packaged_trigger)
        (stage / "prompt.txt").write_text(prompt, encoding="utf-8")
        _write_json(stage / "schema.json", ai_schema_document())
        _write_json(stage / "timeline.json", timeline)
        _write_json(stage / "targets.json", targets)
        _write_json(stage / "asset-manifest.json", {"assets": assets})
        _write_json(stage / "video-segments.json", {"segments": video_segments or []})
        self._write_contact_sheet(stage / "contact-sheet.png", contact_rows)
        if video_path is not None and video_path.is_file():
            shutil.copy2(video_path, stage / "recording.mp4")
        else:
            (stage / "recording-unavailable.txt").write_text(
                "이 환경에서는 동작 영상 인코딩을 완료하지 못했습니다. timeline.json과 PNG 프레임은 정상입니다.",
                encoding="utf-8",
            )
        manifest = {
            "package_version": AI_PACKAGE_VERSION,
            "schema_version": AI_SCHEMA_VERSION,
            "package_id": identifier,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "event_count": len(timeline),
            "target_count": len(targets),
            "asset_count": len(assets),
            "trigger": packaged_trigger,
            "failure_policy": deepcopy(trigger_config.get("failure_policy") or {
                "retry_count": 3, "retry_delay": 500, "after_failure": "stop", "notify": False,
            }),
            "video_available": (stage / "recording.mp4").is_file(),
            "video_mode": "action_windows",
            "video_segment_count": len(video_segments or []),
            "text_policy": "All printable keyboard input is redacted. ChatGPT must ask for a vault name or value classification.",
            "image_policy": "Lossless native PNG candidates only; video frames are never used as search templates.",
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
                "type": "image_appear",
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
            "type": "image_appear",
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
            candidates.append(
                {
                    "kind": kind,
                    "file": relative,
                    "rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                    "image_anchor": [anchor.x() - rect.x(), anchor.y() - rect.y()],
                    "click_offset": [anchor.x() - rect.center().x(), anchor.y() - rect.center().y()],
                    "validation_score": round(score, 2),
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
            candidates.append(
                {
                    "kind": kind,
                    "file": relative,
                    "rect": [button_rect.x(), button_rect.y(), button_rect.width(), button_rect.height()],
                    "image_anchor": [anchor.x() - button_rect.x(), anchor.y() - button_rect.y()],
                    "click_offset": [anchor.x() - button_rect.center().x(), anchor.y() - button_rect.center().y()],
                    "preprocessing": preprocessing,
                    "validation_score": round(score, 2),
                    "matched_scale": round(matched_scale, 2),
                }
            )
        candidates.sort(key=lambda item: float(item.get("validation_score") or 0), reverse=True)
        selected = candidates[0]
        top_score = float(selected.get("validation_score") or 0)
        tied = len(candidates) > 1 and abs(top_score - float(candidates[1].get("validation_score") or 0)) <= 0.5
        readiness = "ready" if top_score >= 84 and not tied else "needs_review"
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
                "search_verified": readiness == "ready",
                "score": round(top_score, 2),
                "matched_scale": selected.get("matched_scale", 1.0),
                "ambiguous": tied,
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


def validate_ai_document(payload: Any) -> list[AIImportIssue]:
    issues: list[AIImportIssue] = []
    if not isinstance(payload, dict):
        return [AIImportIssue("error", "root_type", "JSON 최상위 값은 객체여야 합니다.")]
    required = {"schema_version", "name", "description", "targets", "assets", "variables", "triggers", "steps", "setup_requirements"}
    allowed_top = required | {"source_package_id"}
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
    if len(trigger_rows) > 1:
        issues.append(AIImportIssue("error", "multiple_triggers", "현재 AI 자동 매크로는 실행 조건을 하나만 사용할 수 있습니다."))
    for trigger in trigger_rows:
        if not isinstance(trigger, dict):
            issues.append(AIImportIssue("error", "trigger_type", "실행 조건 형식이 올바르지 않습니다."))
            continue
        kind = str(trigger.get("type") or "")
        if kind not in {"manual", "image_appear", "image_appears"}:
            issues.append(AIImportIssue("error", "unsupported_trigger", f"지원하지 않는 실행 조건: {kind or '(없음)'}"))
            continue
        if kind in {"image_appear", "image_appears"}:
            target_ref = str(trigger.get("target_ref") or "")
            asset_ref = str(trigger.get("asset_ref") or "")
            if target_ref and target_ref not in target_lookup:
                issues.append(AIImportIssue("error", "unknown_trigger_target", f"실행 조건 대상 `{target_ref}`를 찾을 수 없습니다."))
            if not asset_ref or asset_ref not in asset_lookup:
                issues.append(AIImportIssue("warning", "missing_trigger_asset", "시작 화면 이미지를 확인해야 합니다."))
    for step in steps:
        step_id = str(step.get("id") or "")
        action = str(step.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            issues.append(AIImportIssue("error", "unsupported_action", f"허용되지 않은 액션: {action or '(없음)'}", step_id))
        flattened = {str(key).casefold() for key in step}
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        if "params" in step and not isinstance(step.get("params"), dict):
            issues.append(AIImportIssue("error", "params_type", "params는 JSON 객체여야 합니다.", step_id))
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
        if action == "image_search" and not asset_ref:
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
        if action == "image_search":
            click = params.get("click") if isinstance(params.get("click"), dict) else step.get("click") if isinstance(step.get("click"), dict) else {}
            mode = str(click.get("mode") or "inactive").casefold()
            if mode not in {"active", "inactive", "none"}:
                issues.append(AIImportIssue("error", "unsupported_click_mode", f"지원하지 않는 클릭 방식: {mode}", step_id))
        if action == "flow_control":
            jump = str(params.get("jump_to") or step.get("jump_to") or "")
            count = int(params.get("repeat_count") or step.get("repeat_count") or 0)
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
        if action == "image_search":
            asset_ref = str(raw.get("asset_ref") or "")
            alias = asset_aliases.get(asset_ref, "")
            asset_state = asset_states.get(asset_ref, {})
            validation = asset_state.get("validation") if isinstance(asset_state.get("validation"), dict) else {}
            step["asset"] = alias
            step.setdefault("engine", "opencv")
            step.setdefault("search_profile", "fast")
            step.setdefault("confidence", 84)
            step.setdefault("timeout", 800)
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
            step["click_enabled"] = True
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
        }
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
        if kind == "manual":
            runtime_triggers.append({"id": str(raw_trigger.get("id") or "trigger-001"), "type": "manual", "enabled": True})
            continue
        if kind not in {"image_appear", "image_appears"}:
            continue
        params = raw_trigger.get("params") if isinstance(raw_trigger.get("params"), dict) else {}
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
            "enabled": True,
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
        if isinstance(trigger, dict) and str(trigger.get("type") or "") in {"image_appear", "image_appears"}
    ]
    checks.append((
        "시작 화면",
        all(str(trigger.get("asset") or "") and not trigger.get("needs_setup") for trigger in automatic_triggers),
    ))
    target_actions = {"mouse_click", "inactive_click", "image_search", "type_text", "browser_action", "ocr", "run_program", "terminate_program"}
    needs_target = any(isinstance(step, dict) and step.get("action") in target_actions for step in steps)
    checks.append(("대상 프로그램", (not needs_target) or (bool(targets) and all(str(item.get("exe") or item.get("window_token") or "") for item in targets if isinstance(item, dict)))))
    image_steps = [step for step in steps if isinstance(step, dict) and step.get("action") == "image_search"]
    checks.append(("이미지 자산", all(str(step.get("asset") or "") for step in image_steps)))
    inactive_steps = [step for step in steps if isinstance(step, dict) and step.get("action") in {"image_search", "inactive_click", "type_text"}]
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
