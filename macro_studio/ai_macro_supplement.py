"""Copy into macro_studio. Extends local evidence; never runs a macro.

Supplement images are check-only. The base record supplies the target window
and search settings; users must choose a record for the same client/region.
Original record IDs and original files are retained across revisions.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid
import zipfile

from .ai_macro_plan import VERSION, PROMPT, PlanError, VISUAL, CONTROL_FIELDS

MAX_IMAGE = 20_000_000
MAX_PACKAGE = 150_000_000


def requests_from_plan(plan, private):
    if not isinstance(plan, dict) or plan.get("schema") != VERSION:
        raise PlanError("지원하지 않는 계획 형식입니다.")
    if plan.get("recording_id") != private["recording_id"]:
        raise PlanError("다른 녹화의 계획입니다.")
    requests = plan.get("missing_images", [])
    if not isinstance(requests, list) or len(requests) > 50:
        raise PlanError("추가 이미지 요청은 최대 50개입니다.")
    result = []
    for index, item in enumerate(requests, 1):
        if not isinstance(item, dict):
            raise PlanError("이미지 요청 형식이 잘못되었습니다.")
        label, reason = item.get("label"), item.get("reason", "")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise PlanError("이미지 요청 이름이 잘못되었습니다.")
        if not isinstance(reason, str) or len(reason) > 4000:
            raise PlanError("이미지 요청 설명이 너무 깁니다.")
        result.append({"id": f"request-{index}", "label": label, "reason": reason})
    return result


def add_check_record(private, base_record, alias, label, asset_path, search_region=None):
    """Pure operation: returns a copy; uses an asset already registered locally."""
    records = private["records"]
    if len(records) >= 200:
        raise PlanError("기록은 최대 200개입니다.")
    base = records.get(base_record, {}).get("step", {})
    if base.get("action") not in VISUAL:
        raise PlanError("같은 대상 프로그램의 이미지 기록을 선택하세요.")
    resolved = asset_path(alias)
    if resolved is None:
        raise PlanError("이미지 자산이 없습니다.")
    path = Path(resolved)
    if not path.is_file() or path.stat().st_size > MAX_IMAGE:
        raise PlanError("PNG 파일이 없거나 20MB를 초과합니다.")
    content = path.read_bytes()
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PlanError("PNG 이미지를 선택하세요.")
    result = deepcopy(private)
    number = 1
    while f"r{number:03d}" in records:
        number += 1
    rid = f"r{number:03d}"
    step = deepcopy(base)
    for key in list(step):
        if key in CONTROL_FIELDS or key.startswith("_") or key in {
            "click", "assets", "asset_offsets", "asset_options", "offsets"
        }:
            step.pop(key, None)
    step.update(action="screen_condition", asset=alias, click_enabled=False,
                label=label, repeat=1)
    if search_region is not None and isinstance(search_region, (list, tuple)) and len(search_region) == 4:
        step["search_region"] = [int(x) for x in search_region]
    result["records"][rid] = {"step": step, "images": [{
        "alias": alias, "file": f"images/{rid}-1.png",
        "sha256": hashlib.sha256(content).hexdigest(),
    }]}
    return result, rid


def export_revision(private, requests, responses, purpose, notes, parent_dir, asset_path):
    """Writes a NEW revision directory. Private input never enters the ZIP.

    responses: request ID -> {records: [record ID, ...], note: str}.
    An unanswered request is retained, not silently marked resolved.
    """
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 10000:
        raise PlanError("목적을 1~10000자로 입력하세요.")
    if not isinstance(notes, str) or len(notes) > 20000:
        raise PlanError("보완 설명은 20000자 이내로 입력하세요.")
    records = private["records"]
    evidence, images, total = [], {}, 0
    for rid, saved in records.items():
        refs = []
        for index, ref in enumerate(saved["images"], 1):
            resolved = asset_path(ref["alias"])
            if resolved is None:
                raise PlanError(f"{rid}: 원본 이미지가 없습니다.")
            path = Path(resolved)
            if not path.is_file() or path.stat().st_size > MAX_IMAGE:
                raise PlanError(f"{rid}: 이미지가 없거나 너무 큽니다.")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != ref["sha256"]:
                raise PlanError(f"{rid}: 기존 이미지가 변경되었습니다. 새 이미지로 추가하세요.")
            total += len(data)
            if total > MAX_PACKAGE:
                raise PlanError("이미지 합계가 150MB를 초과합니다.")
            # Construct archive paths ourselves; never trust paths in local JSON.
            filename = f"images/record-{len(evidence)+1}-{index}.png"
            images[filename] = data
            refs.append({"file": filename, "sha256": ref["sha256"]})
        step = saved["step"]
        item = {"record": rid, "action": step["action"],
                "label": "기록된 텍스트 입력" if step["action"] == "type_text" else step.get("label", ""),
                "duration_ms": step.get("duration"), "images": refs}
        if "search_region" in step and isinstance(step["search_region"], (list, tuple)) and len(step["search_region"]) == 4:
            item["search_region"] = [int(x) for x in step["search_region"]]
        evidence.append(item)
    answers = []
    for request in requests:
        response = responses.get(request["id"], {})
        supplied = response.get("records", [])
        if not isinstance(supplied, list) or any(r not in records for r in supplied):
            raise PlanError("보완 기록 참조가 잘못되었습니다.")
        note = response.get("note", "")
        if not isinstance(note, str) or len(note) > 4000:
            raise PlanError("항목 설명은 4000자 이내여야 합니다.")
        answers.append({**request, "supplied_records": supplied, "user_note": note,
                        "status": "provided_for_review" if supplied else "unresolved"})
    manifest = {"schema": VERSION, "recording_id": private["recording_id"],
                "purpose": purpose.strip(), "records": evidence,
                "supplement_requests": answers, "user_revision_notes": notes}
    prompt = PROMPT + "\n" + (
        "이번 ZIP은 같은 녹화의 보완본이며 필요한 원본 이미지도 모두 포함합니다.\n"
        "recording_id와 기존 record ID를 그대로 사용하세요. 새 record도 사용할 수 있습니다.\n"
        "supplement_requests의 이유, supplied_records 및 사용자 보완 설명을 검토하세요.\n"
        "이미지가 제공됐다는 이유만으로 문제가 해결됐다고 판단하지 마세요.\n"
        "추가된 screen_condition 기록은 클릭 없는 확인용입니다.\n"
        "기존 목적을 반영한 완전한 plan.json을 다시 작성하세요. 부분 패치는 금지합니다.\n"
        "미해결 조건은 missing_images에 남기세요. 자료로 판정할 수 없는 숫자 비교나\n"
        "여러 속성의 AND 조건을 단일 이미지 하나로 검증 가능하다고 가장하지 마세요.\n"
    )
    local_text = json.dumps(private, ensure_ascii=False, indent=2)
    if len(local_text.encode("utf-8")) > 5_000_000:
        raise PlanError("로컬 녹화 파일이 5MB를 초과합니다.")
    destination = Path(parent_dir) / f"recording-revision-{uuid.uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    local_path = destination / "private-recording.json"
    local_path.write_text(local_text, encoding="utf-8")
    # Recoverable local answers; not used as executable instructions.
    (destination / "supplement.json").write_text(json.dumps(
        {"requests": requests, "responses": responses, "purpose": purpose, "notes": notes},
        ensure_ascii=False, indent=2), encoding="utf-8")
    package = destination / "gpt-package.zip"
    with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("START_HERE.txt", prompt)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for filename, data in images.items():
            archive.writestr(filename, data)
    return package, local_path
