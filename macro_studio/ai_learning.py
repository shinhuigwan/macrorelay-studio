from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .ai_automation import write_ai_reference_files
from .widgets import Card, danger_button, primary_button


LEARNING_SCHEMA_VERSION = "macrorelay-behavior-learning-1.0"
DEMO_KIND_LABELS = {
    "normal": "기본 시연",
    "variation": "변형 시연",
    "recovery": "실패·복구 시연",
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _read_json_value(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError):
        return default


class BehaviorLearningStore:
    """Persist behavior demonstrations and build a self-contained AI package."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.base = self.root / ".automation" / "ai-learning"
        self.path = self.base / "learning-project.json"
        self.data = self.load()

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, TypeError, ValueError):
            payload = {}
        behaviors = payload.get("behaviors") if isinstance(payload, dict) else []
        return {
            "schema_version": LEARNING_SCHEMA_VERSION,
            "project_name": str(payload.get("project_name") or "AI 플레이 자동화") if isinstance(payload, dict) else "AI 플레이 자동화",
            "behaviors": [dict(row) for row in behaviors if isinstance(row, dict)] if isinstance(behaviors, list) else [],
        }

    def save(self) -> None:
        _write_json(self.path, self.data)

    def set_project_name(self, name: str) -> None:
        self.data["project_name"] = name.strip() or "AI 플레이 자동화"
        self.save()

    def behavior(self, behavior_id: str) -> dict[str, Any] | None:
        return next((row for row in self.data["behaviors"] if row.get("id") == behavior_id), None)

    def add_behavior(self, name: str, purpose: str, priority: int = 1) -> dict[str, Any]:
        row = {
            "id": f"behavior-{uuid.uuid4().hex[:10]}",
            "name": name.strip() or "새 행동",
            "purpose": purpose.strip(),
            "priority": max(1, int(priority)),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "demos": [],
        }
        self.data["behaviors"].append(row)
        self.save()
        return row

    def update_behavior(self, behavior_id: str, name: str, purpose: str, priority: int) -> None:
        row = self.behavior(behavior_id)
        if row is None:
            raise ValueError("행동을 찾을 수 없습니다.")
        row.update(name=name.strip() or "새 행동", purpose=purpose.strip(), priority=max(1, int(priority)))
        self.save()

    def remove_behavior(self, behavior_id: str) -> None:
        self.data["behaviors"] = [row for row in self.data["behaviors"] if row.get("id") != behavior_id]
        self.save()

    def add_demo(
        self,
        behavior_id: str,
        archive: str,
        stage: str,
        event_count: int,
        kind: str = "normal",
        note: str = "",
    ) -> dict[str, Any]:
        row = self.behavior(behavior_id)
        if row is None:
            raise ValueError("시연을 추가할 행동을 찾을 수 없습니다.")
        demo = {
            "id": f"demo-{uuid.uuid4().hex[:10]}",
            "kind": kind if kind in DEMO_KIND_LABELS else "normal",
            "note": note.strip(),
            "archive": str(Path(archive).resolve()),
            "stage": str(Path(stage).resolve()),
            "event_count": max(0, int(event_count)),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        row.setdefault("demos", []).append(demo)
        self.save()
        return demo

    def remove_demo(self, behavior_id: str, demo_id: str) -> None:
        row = self.behavior(behavior_id)
        if row is None:
            return
        row["demos"] = [demo for demo in row.get("demos", []) if demo.get("id") != demo_id]
        self.save()

    def readiness(self, behavior: dict[str, Any]) -> tuple[str, str]:
        count = len(behavior.get("demos") or [])
        if count < 2:
            return "needs_more", f"시연 {count}/2 · 한 번 더 필요"
        if count == 2:
            return "ready", "분석 가능 · 변형 시연 1회 권장"
        return "strong", f"학습 준비 완료 · {count}회"

    def _copy_demo(self, demo: dict[str, Any], destination: Path) -> str:
        source_stage = Path(str(demo.get("stage") or ""))
        source_archive = Path(str(demo.get("archive") or ""))
        if source_stage.is_dir():
            shutil.copytree(source_stage, destination)
            return destination.name
        if source_archive.is_file():
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / source_archive.name
            shutil.copy2(source_archive, target)
            return f"{destination.name}/{target.name}"
        raise FileNotFoundError(f"시연 원본을 찾을 수 없습니다: {demo.get('id', '')}")

    def build_package(self, behavior_ids: list[str] | None = None) -> tuple[Path, Path]:
        requested = set(behavior_ids or [])
        behaviors = [row for row in self.data["behaviors"] if not requested or row.get("id") in requested]
        if not behaviors:
            raise ValueError("패키지에 포함할 행동이 없습니다.")
        missing = [str(row.get("name") or "행동") for row in behaviors if not row.get("demos")]
        if missing:
            raise ValueError("시연이 없는 행동: " + ", ".join(missing))

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        package_id = f"learning-{stamp}-{uuid.uuid4().hex[:8]}"
        stage = self.base / "packages" / package_id
        stage.mkdir(parents=True, exist_ok=False)
        manifest_behaviors: list[dict[str, Any]] = []
        asset_catalog: list[dict[str, Any]] = []
        target_catalog: list[dict[str, Any]] = []
        timeline_catalog: list[dict[str, Any]] = []
        combined_timeline: list[dict[str, Any]] = []
        for behavior_index, behavior in enumerate(behaviors, 1):
            safe_folder = f"behavior-{behavior_index:02d}"
            demo_rows: list[dict[str, Any]] = []
            for demo_index, demo in enumerate(behavior.get("demos") or [], 1):
                destination = stage / "behaviors" / safe_folder / f"demo-{demo_index:02d}"
                source = self._copy_demo(demo, destination)
                # The outer package contains one authoritative current Studio
                # contract. Removing nested prompts/schemas avoids conflicting
                # instructions and keeps multi-demo packages small and fast.
                for redundant in (
                    "prompt.txt", "schema.json", "studio-capabilities.json", "node-reference.md",
                    "recommended-actions.json", "generation-checklist.json", "README.txt",
                ):
                    (destination / redundant).unlink(missing_ok=True)
                prefix = f"{safe_folder}-demo-{demo_index:02d}"
                relative_demo = f"behaviors/{safe_folder}/demo-{demo_index:02d}"
                target_id_map: dict[str, str] = {}
                raw_targets = _read_json_value(destination / "targets.json", [])
                if isinstance(raw_targets, dict):
                    raw_targets = raw_targets.get("targets") or []
                if not isinstance(raw_targets, list):
                    raw_targets = []
                for target in raw_targets:
                    if not isinstance(target, dict):
                        continue
                    old_id = str(target.get("id") or f"target-{len(target_id_map) + 1:02d}")
                    new_id = f"{prefix}-{old_id}"
                    target_id_map[old_id] = new_id
                    target_catalog.append({
                        **target,
                        "id": new_id,
                        "source_target_id": old_id,
                        "behavior_id": behavior.get("id"),
                        "behavior_name": behavior.get("name"),
                        "demo_id": demo.get("id"),
                    })
                raw_assets = _read_json(destination / "asset-manifest.json").get("assets")
                for asset in raw_assets if isinstance(raw_assets, list) else []:
                    if not isinstance(asset, dict):
                        continue
                    old_id = str(asset.get("id") or f"asset-{len(asset_catalog) + 1:03d}")
                    prepared = dict(asset)
                    prepared["id"] = f"{prefix}-{old_id}"
                    prepared["source_asset_id"] = old_id
                    prepared["behavior_id"] = behavior.get("id")
                    prepared["behavior_name"] = behavior.get("name")
                    prepared["demo_id"] = demo.get("id")
                    prepared["target_ref"] = target_id_map.get(str(asset.get("target_ref") or ""), "")
                    for key in ("candidate", "selected_candidate"):
                        if str(prepared.get(key) or ""):
                            prepared[key] = f"{relative_demo}/{prepared[key]}"
                    candidates = []
                    for candidate in prepared.get("candidates") if isinstance(prepared.get("candidates"), list) else []:
                        if not isinstance(candidate, dict):
                            continue
                        item = dict(candidate)
                        if str(item.get("file") or ""):
                            item["file"] = f"{relative_demo}/{item['file']}"
                        candidates.append(item)
                    prepared["candidates"] = candidates
                    asset_catalog.append(prepared)
                parsed_timeline = _read_json_value(destination / "timeline.json", [])
                if isinstance(parsed_timeline, dict):
                    parsed_timeline = parsed_timeline.get("timeline") or []
                if not isinstance(parsed_timeline, list):
                    parsed_timeline = []
                for item in parsed_timeline:
                    if isinstance(item, dict):
                        combined_timeline.append(dict(item))
                timeline_catalog.append({
                    "behavior_id": behavior.get("id"),
                    "behavior_name": behavior.get("name"),
                    "demo_id": demo.get("id"),
                    "kind": demo.get("kind", "normal"),
                    "timeline": f"{relative_demo}/timeline.json",
                    "asset_manifest": f"{relative_demo}/asset-manifest.json",
                    "targets": f"{relative_demo}/targets.json",
                })
                demo_rows.append({
                    "id": demo.get("id"),
                    "kind": demo.get("kind", "normal"),
                    "kind_label": DEMO_KIND_LABELS.get(str(demo.get("kind")), "기본 시연"),
                    "note": demo.get("note", ""),
                    "event_count": demo.get("event_count", 0),
                    "source": f"behaviors/{safe_folder}/{source}",
                })
            manifest_behaviors.append({
                "id": behavior.get("id"),
                "name": behavior.get("name"),
                "purpose": behavior.get("purpose"),
                "priority": behavior.get("priority", behavior_index),
                "demonstrations": demo_rows,
            })

        manifest = {
            "schema_version": LEARNING_SCHEMA_VERSION,
            "package_id": package_id,
            "project_name": self.data.get("project_name"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "behavior_count": len(manifest_behaviors),
            "behaviors": manifest_behaviors,
        }
        _write_json(stage / "learning-manifest.json", manifest)
        _write_json(stage / "asset-manifest.json", {"assets": asset_catalog})
        _write_json(stage / "targets.json", target_catalog)
        _write_json(stage / "learning-evidence-index.json", {"demonstrations": timeline_catalog})
        write_ai_reference_files(stage, combined_timeline)
        prompt = self._prompt(manifest)
        (stage / "prompt.txt").write_text(prompt, encoding="utf-8")
        (stage / "README.txt").write_text(
            "prompt.txt와 이 ZIP 전체를 ChatGPT에 함께 전달하세요. 각 demo 폴더의 PNG는 실제 이미지 검색용 무손실 원본입니다.\n",
            encoding="utf-8",
        )
        archive_dir = self.root / "exports" / "ai-learning"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive = archive_dir / f"MacroRelay-AI-Learning-{stamp}.zip"
        suffix = 2
        while archive.exists():
            archive = archive_dir / f"MacroRelay-AI-Learning-{stamp}-{suffix}.zip"
            suffix += 1
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(stage).as_posix())
        _write_json(self.base / "last-learning-package.json", {
            "package_id": package_id,
            "archive": str(archive),
            "stage": str(stage),
            "prompt": str(stage / "prompt.txt"),
        })
        return archive, stage

    @staticmethod
    def _prompt(manifest: dict[str, Any]) -> str:
        behavior_lines = "\n".join(
            f"- {row['priority']}순위 · {row['name']}: {row.get('purpose') or '시연에서 목적 추론'} "
            f"({len(row['demonstrations'])}회 시연)"
            for row in manifest["behaviors"]
        )
        return f"""당신은 MacroRelay Studio 행동 학습 분석기입니다.

첨부된 `{manifest['package_id']}` ZIP은 사용자가 행동별로 나누어 녹화한 실제 시연 묶음입니다.
사용자에게 노드 연결법을 다시 묻지 말고, 같은 행동의 여러 시연에서 공통 규칙과 우연한 동작을 구분하여 자동화 흐름을 추론하십시오.

분석 전에 반드시 최상단의 다음 파일을 모두 읽으십시오.
- `studio-capabilities.json`: 현재 Studio의 모든 노드와 실제 설정 필드, 기본값, 허용값, 사용 조건
- `node-reference.md`: 전체 기능의 사람이 읽을 수 있는 상세 설명과 노드별 기본 params
- `schema.json`: 반환 JSON의 정확한 구조
- `asset-manifest.json`: 모든 시연 PNG를 충돌 없는 고유 ID와 패키지 기준 경로로 합친 카탈로그
- `targets.json`: 시연별 대상 프로그램 카탈로그
- `learning-evidence-index.json`: 행동·시연과 타임라인 파일의 대응표
- `generation-checklist.json`: 반환 전 필수 자체 검증 목록

녹화에 직접 나타난 클릭만 복사하지 말고, 위 전체 기능 중 목적 달성에 필요한 OCR·변수·조건·반복·서브매크로·테이블·알림 기능을 스스로 판단해 사용하십시오. 단, 명세에 없는 action·params 경로·선택값은 만들지 마십시오.

프로젝트: {manifest.get('project_name')}
행동 목록:
{behavior_lines}

분석 규칙:
1. 각 행동마다 진입 조건, 실제 동작, 완료 확인, 재시도, 실패 복구 지점을 추론합니다.
2. 여러 시연에 공통으로 나타난 화면과 동작을 핵심 규칙으로 우선합니다. 한 번만 나타난 불필요한 커서 이동·대기는 제거합니다.
3. `기본 시연`, `변형 시연`, `실패·복구 시연`의 의도를 구분합니다.
4. PNG 자산만 이미지 서치 템플릿으로 사용합니다. JPG/MP4 프레임을 검색 이미지로 만들지 않습니다.
5. 화면 위치가 바뀌어도 동작하도록 대상 프로그램의 클라이언트 상대 좌표와 이미지 기준 오프셋을 사용합니다.
6. 여러 행동은 우선순위가 높은 것부터 현재 화면의 진입 조건을 검사하고, 실행 가능한 행동 하나만 수행한 뒤 다시 판단하도록 구성합니다.
7. 한 행동의 성공·실패 흐름이 다른 행동의 내부 노드와 뒤섞이지 않도록 명확한 그룹과 표시 이름을 부여합니다.
8. 로그인 정보나 비밀번호는 실제 값을 만들지 말고 MacroRelay Vault 변수로 남깁니다.
9. 정말로 결과를 바꾸는 업무 규칙이 누락된 경우만 한 번 질문합니다. 영상으로 판단 가능하면 질문하지 않습니다.
10. 반환 JSON의 `source_package_id`는 반드시 `{manifest['package_id']}`로 지정합니다.
11. 이미지 asset의 id와 candidate는 최상단 `asset-manifest.json` 값을 그대로 사용합니다. demo 내부의 짧은 원본 id를 사용하지 않습니다.
12. 각 step의 `source_evidence`에 선택한 행동·시연·timeline id와 해당 노드를 선택한 이유를 기록합니다.
13. 반환 전에 `generation-checklist.json`을 모두 검사하고, 실패한 항목이 있으면 스스로 수정한 뒤 결과를 냅니다.

결과는 MacroRelay Studio가 가져올 수 있는 `macrorelay-ai.json` 파일 하나로만 제공하십시오.
설명용 코드 블록이나 긴 해설은 출력하지 말고, 다운로드 가능한 JSON 첨부 파일로 답하십시오.
"""


class BehaviorEditDialog(QtWidgets.QDialog):
    def __init__(self, behavior: dict[str, Any] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("학습할 행동 설정")
        self.setMinimumWidth(520)
        behavior = behavior or {}
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("행동 자체를 설명하세요. 세부 클릭 순서는 시연에서 AI가 추론합니다.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit(str(behavior.get("name") or ""))
        self.name_edit.setPlaceholderText("예: 골드가 모이면 공격력·방어력 강화")
        self.purpose_edit = QtWidgets.QPlainTextEdit(str(behavior.get("purpose") or ""))
        self.purpose_edit.setPlaceholderText("예: 업그레이드 가능한 항목을 확인하고 골드를 효율적으로 사용한다")
        self.purpose_edit.setMaximumHeight(90)
        self.priority_spin = QtWidgets.QSpinBox()
        self.priority_spin.setRange(1, 999)
        self.priority_spin.setValue(int(behavior.get("priority") or 1))
        form.addRow("행동 이름", self.name_edit)
        form.addRow("목적", self.purpose_edit)
        form.addRow("판단 우선순위", self.priority_spin)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("저장")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QtWidgets.QMessageBox.information(self, "행동 이름", "학습할 행동 이름을 입력하세요.")
            return
        self.accept()


class DemoKindDialog(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("시연 종류")
        self.setMinimumWidth(440)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("이번 시연이 어떤 상황인지 선택하세요."))
        self.kind_combo = QtWidgets.QComboBox()
        for key, label in DEMO_KIND_LABELS.items():
            self.kind_combo.addItem(label, key)
        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setPlaceholderText("선택 사항 · 예: 골드 부족 상태부터 시작")
        layout.addWidget(self.kind_combo)
        layout.addWidget(self.note_edit)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("2초 뒤 녹화 시작")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class BehaviorLearningDialog(QtWidgets.QDialog):
    record_requested = QtCore.Signal(str, str, str)
    import_requested = QtCore.Signal()
    status = QtCore.Signal(str)

    def __init__(self, store: BehaviorLearningStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._current_id = ""
        self._package_message: QtWidgets.QMessageBox | None = None
        self.setWindowTitle("AI 플레이 학습")
        self.resize(1120, 720)
        self.setMinimumSize(900, 600)
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("행동을 나눠 시연하면 AI가 플레이 규칙을 조립합니다")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)
        guide = QtWidgets.QLabel(
            "① 행동 등록  →  ② 같은 행동을 2~3회 시연  →  ③ 학습 패키지 생성  →  "
            "④ ChatGPT가 만든 JSON 가져오기\n"
            "클릭 순서나 노드는 작성하지 않아도 됩니다. 변형 상황과 실패 복구를 한 번씩 보여주면 결과가 더 안정적입니다."
        )
        guide.setObjectName("Muted")
        guide.setWordWrap(True)
        root.addWidget(guide)

        project_row = QtWidgets.QHBoxLayout()
        project_row.addWidget(QtWidgets.QLabel("자동화 프로젝트"))
        self.project_edit = QtWidgets.QLineEdit(str(store.data.get("project_name") or "AI 플레이 자동화"))
        self.project_edit.editingFinished.connect(lambda: self.store.set_project_name(self.project_edit.text()))
        project_row.addWidget(self.project_edit, 1)
        root.addLayout(project_row)

        splitter = QtWidgets.QSplitter()
        left = Card()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(QtWidgets.QLabel("학습할 행동"))
        self.behavior_list = QtWidgets.QListWidget()
        self.behavior_list.currentItemChanged.connect(self._selection_changed)
        left_layout.addWidget(self.behavior_list, 1)
        behavior_buttons = QtWidgets.QHBoxLayout()
        add_btn = primary_button("＋ 행동 추가")
        add_btn.clicked.connect(self._add_behavior)
        edit_btn = QtWidgets.QPushButton("편집")
        edit_btn.clicked.connect(self._edit_behavior)
        delete_btn = danger_button("삭제")
        delete_btn.clicked.connect(self._remove_behavior)
        behavior_buttons.addWidget(add_btn)
        behavior_buttons.addWidget(edit_btn)
        behavior_buttons.addWidget(delete_btn)
        left_layout.addLayout(behavior_buttons)

        right = Card()
        right_layout = QtWidgets.QVBoxLayout(right)
        self.behavior_title = QtWidgets.QLabel("왼쪽에서 행동을 추가하세요")
        self.behavior_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.behavior_purpose = QtWidgets.QLabel("")
        self.behavior_purpose.setWordWrap(True)
        self.behavior_purpose.setObjectName("Muted")
        self.readiness_label = QtWidgets.QLabel("")
        right_layout.addWidget(self.behavior_title)
        right_layout.addWidget(self.behavior_purpose)
        right_layout.addWidget(self.readiness_label)
        self.demo_table = QtWidgets.QTableWidget(0, 4)
        self.demo_table.setHorizontalHeaderLabels(["종류", "기록 시각", "액션", "메모"])
        self.demo_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.demo_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.demo_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.demo_table.verticalHeader().hide()
        right_layout.addWidget(self.demo_table, 1)
        demo_buttons = QtWidgets.QHBoxLayout()
        self.record_btn = primary_button("● 이 행동 시연 녹화 추가")
        self.record_btn.clicked.connect(self._request_recording)
        self.remove_demo_btn = danger_button("선택 시연 제거")
        self.remove_demo_btn.clicked.connect(self._remove_demo)
        demo_buttons.addWidget(self.record_btn)
        demo_buttons.addWidget(self.remove_demo_btn)
        demo_buttons.addStretch(1)
        right_layout.addLayout(demo_buttons)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([330, 760])
        root.addWidget(splitter, 1)

        footer = QtWidgets.QHBoxLayout()
        self.selected_package_btn = QtWidgets.QPushButton("선택 행동만 패키지")
        self.selected_package_btn.clicked.connect(lambda: self._build_package(False))
        self.all_package_btn = primary_button("전체 행동 학습 패키지 생성")
        self.all_package_btn.clicked.connect(lambda: self._build_package(True))
        import_btn = QtWidgets.QPushButton("받은 JSON 가져오기")
        import_btn.clicked.connect(self.import_requested.emit)
        close_btn = QtWidgets.QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        footer.addWidget(self.selected_package_btn)
        footer.addWidget(self.all_package_btn)
        footer.addWidget(import_btn)
        footer.addStretch(1)
        footer.addWidget(close_btn)
        root.addLayout(footer)
        self.refresh()

    def refresh(self, select_id: str = "") -> None:
        wanted = select_id or self._current_id
        self.behavior_list.blockSignals(True)
        self.behavior_list.clear()
        selected_item = None
        for behavior in sorted(self.store.data["behaviors"], key=lambda row: (int(row.get("priority") or 1), str(row.get("name") or ""))):
            _state, readiness = self.store.readiness(behavior)
            item = QtWidgets.QListWidgetItem(f"{behavior.get('priority', 1)} · {behavior.get('name')}\n{readiness}")
            item.setData(QtCore.Qt.UserRole, behavior.get("id"))
            item.setToolTip(str(behavior.get("purpose") or ""))
            self.behavior_list.addItem(item)
            if behavior.get("id") == wanted:
                selected_item = item
        self.behavior_list.blockSignals(False)
        if selected_item is not None:
            self.behavior_list.setCurrentItem(selected_item)
        elif self.behavior_list.count():
            self.behavior_list.setCurrentRow(0)
        else:
            self._current_id = ""
            self._render_current()

    def _selection_changed(self, current, _previous) -> None:
        self._current_id = str(current.data(QtCore.Qt.UserRole) or "") if current else ""
        self._render_current()

    def _render_current(self) -> None:
        behavior = self.store.behavior(self._current_id)
        enabled = behavior is not None
        self.record_btn.setEnabled(enabled)
        self.remove_demo_btn.setEnabled(enabled)
        self.selected_package_btn.setEnabled(enabled and bool(behavior.get("demos")))
        self.demo_table.setRowCount(0)
        if behavior is None:
            self.behavior_title.setText("왼쪽에서 행동을 추가하세요")
            self.behavior_purpose.setText("예: 골드 강화, 퀘스트 진행, 유물 합성처럼 목적별로 나눕니다.")
            self.readiness_label.clear()
            return
        self.behavior_title.setText(str(behavior.get("name") or "행동"))
        self.behavior_purpose.setText(str(behavior.get("purpose") or "목적 설명 없음"))
        state, readiness = self.store.readiness(behavior)
        colors = {"needs_more": "#F0B35A", "ready": "#5DD8C3", "strong": "#7C65FF"}
        self.readiness_label.setText(readiness)
        self.readiness_label.setStyleSheet(f"color: {colors[state]}; font-weight: 700;")
        for row_index, demo in enumerate(behavior.get("demos") or []):
            self.demo_table.insertRow(row_index)
            values = [
                DEMO_KIND_LABELS.get(str(demo.get("kind")), "기본 시연"),
                str(demo.get("created_at") or "")[:19].replace("T", " "),
                f"{int(demo.get('event_count') or 0)}개",
                str(demo.get("note") or ""),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, demo.get("id"))
                self.demo_table.setItem(row_index, column, item)

    def _add_behavior(self) -> None:
        dialog = BehaviorEditDialog(parent=self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        behavior = self.store.add_behavior(dialog.name_edit.text(), dialog.purpose_edit.toPlainText(), dialog.priority_spin.value())
        self.refresh(str(behavior["id"]))

    def _edit_behavior(self) -> None:
        behavior = self.store.behavior(self._current_id)
        if behavior is None:
            return
        dialog = BehaviorEditDialog(behavior, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self.store.update_behavior(self._current_id, dialog.name_edit.text(), dialog.purpose_edit.toPlainText(), dialog.priority_spin.value())
        self.refresh(self._current_id)

    def _remove_behavior(self) -> None:
        behavior = self.store.behavior(self._current_id)
        if behavior is None:
            return
        answer = QtWidgets.QMessageBox.question(self, "행동 삭제", f"'{behavior.get('name')}'과 시연 목록을 삭제할까요?")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.store.remove_behavior(self._current_id)
        self._current_id = ""
        self.refresh()

    def _request_recording(self) -> None:
        behavior = self.store.behavior(self._current_id)
        if behavior is None:
            return
        dialog = DemoKindDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self.record_requested.emit(
            self._current_id,
            str(dialog.kind_combo.currentData() or "normal"),
            dialog.note_edit.text(),
        )

    def _remove_demo(self) -> None:
        row = self.demo_table.currentRow()
        if row < 0 or self.demo_table.item(row, 0) is None:
            return
        demo_id = str(self.demo_table.item(row, 0).data(QtCore.Qt.UserRole) or "")
        self.store.remove_demo(self._current_id, demo_id)
        self.refresh(self._current_id)

    def _build_package(self, all_behaviors: bool) -> None:
        try:
            archive, stage = self.store.build_package(None if all_behaviors else [self._current_id])
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "학습 패키지 생성 실패", str(exc))
            return
        prompt = stage / "prompt.txt"
        QtWidgets.QApplication.clipboard().setText(prompt.read_text(encoding="utf-8-sig"))
        self.status.emit(f"AI 행동 학습 패키지를 생성했습니다 · {archive.name}")
        message = QtWidgets.QMessageBox(self)
        self._package_message = message
        message.setWindowTitle("AI 행동 학습 패키지 준비 완료")
        message.setIcon(QtWidgets.QMessageBox.Information)
        message.setText("프롬프트를 자동 복사했습니다. ZIP을 ChatGPT에 첨부한 뒤 붙여넣으세요.")
        message.setDetailedText(f"ZIP: {archive}\n작업 폴더: {stage}")
        folder_button = message.addButton("저장 폴더 열기", QtWidgets.QMessageBox.ActionRole)
        message.addButton(QtWidgets.QMessageBox.Ok)
        message.setModal(False)
        message.buttonClicked.connect(
            lambda button: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(archive.parent)))
            if button is folder_button else None
        )
        message.finished.connect(
            lambda _result: setattr(self, "_package_message", None) if self._package_message is message else None
        )
        message.show()
