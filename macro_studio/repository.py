from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class MacroSummary:
    name: str
    description: str
    steps: int
    modified: datetime
    path: Path


@dataclass(frozen=True)
class PortableExportResult:
    folder: Path
    archive: Path
    executable: Path
    script: Path
    features: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class SingleFileExportResult:
    executable: Path
    features: tuple[str, ...]
    notes: tuple[str, ...]


class MacroRepository:
    """Single data gateway for macros, assets, tables, hotkeys and exports."""

    def __init__(self, root: Path | None = None) -> None:
        configured = os.environ.get("MACRORELAY_HOME", "").strip() or os.environ.get("MACRO_STUDIO_HOME", "").strip()
        self.root = Path(configured).expanduser().resolve() if configured else (
            root.resolve() if root else Path(__file__).resolve().parents[1]
        )
        self.macros_dir = self.root / "macros"
        self.assets_dir = self.root / "assets"
        self.exports_dir = self.root / "exports"
        self.history_dir = self.root / ".history"
        self.tables_path = self.root / "data_tables.json"
        self.hotkeys_path = self.root / "hotkeys.json"
        self.hotkey_actions_path = self.root / "hotkey_actions.json"
        self.macro_order_path = self.root / "macro_order.json"
        self.macro_tags_path = self.root / "macro_tags.json"
        self.automation_blocks_path = self.root / "automation_blocks.json"
        self.assets_index_path = self.assets_dir / "index.json"
        self._opencv_probe_signature: tuple[int, int, int] | None = None
        self._opencv_probe_ok = False
        self._opencv_runtime: tuple[Path, Path] | None = None
        for path in (self.macros_dir, self.assets_dir, self.exports_dir, self.history_dir):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp, path)

    @staticmethod
    def safe_name(name: str) -> str:
        cleaned = "".join("-" if ch in '<>:"/\\|?*' else ch for ch in name.strip())
        return cleaned.strip(" .") or "새 매크로"

    def macro_path(self, name: str) -> Path:
        return self.macros_dir / f"{self.safe_name(name)}.json"

    def list_macros(self) -> list[MacroSummary]:
        summaries: list[MacroSummary] = []
        for path in self.macros_dir.glob("*.json"):
            try:
                payload = self._read_json(path, {})
            except (OSError, ValueError):
                continue
            summaries.append(
                MacroSummary(
                    name=path.stem,
                    description=str(payload.get("description") or ""),
                    steps=len(payload.get("steps") or []),
                    modified=datetime.fromtimestamp(path.stat().st_mtime),
                    path=path,
                )
            )
        by_name = {item.name: item for item in summaries}
        saved_order = self.load_macro_order()
        ordered_names = [name for name in saved_order if name in by_name]
        missing = sorted(
            (item for item in summaries if item.name not in ordered_names),
            key=lambda item: (item.path.stat().st_ctime_ns, item.name.casefold()),
        )
        ordered_names.extend(item.name for item in missing)
        if missing:
            self.save_macro_order([*saved_order, *(item.name for item in missing)])
        return [by_name[name] for name in ordered_names]

    def load_macro_order(self) -> list[str]:
        payload = self._read_json(self.macro_order_path, [])
        if not isinstance(payload, list):
            return []
        return [str(value).strip() for value in payload if str(value).strip()]

    def save_macro_order(self, names: Iterable[str]) -> None:
        ordered: list[str] = []
        for value in names:
            name = str(value).strip()
            if name and name not in ordered:
                ordered.append(name)
        self._write_json(self.macro_order_path, ordered)

    def _append_macro_order(self, name: str) -> None:
        order = self.load_macro_order()
        if name not in order:
            order.append(name)
            self.save_macro_order(order)

    def load_macro_tags(self) -> dict[str, str]:
        payload = self._read_json(self.macro_tags_path, {})
        if not isinstance(payload, dict):
            return {}
        return {
            str(name).strip(): str(group).strip()
            for name, group in payload.items()
            if str(name).strip() and str(group).strip()
        }

    def save_macro_tags(self, tags: dict[str, str]) -> None:
        cleaned = {
            str(name).strip(): str(group).strip()
            for name, group in tags.items()
            if str(name).strip() and str(group).strip()
        }
        self._write_json(self.macro_tags_path, cleaned)

    def assign_macro_group(self, names: Iterable[str], group: str) -> None:
        tags = self.load_macro_tags()
        target = group.strip()
        for name in names:
            key = str(name).strip()
            if not key:
                continue
            if target:
                tags[key] = target
            else:
                tags.pop(key, None)
        self.save_macro_tags(tags)

    def load_macro(self, name: str) -> dict[str, Any]:
        payload = self._read_json(self.macro_path(name), {})
        if not isinstance(payload, dict):
            raise ValueError(f"{name}: 매크로 JSON 형식이 올바르지 않습니다.")
        payload.setdefault("name", name)
        payload.setdefault("description", "")
        payload.setdefault("meta", {"coord_mode": "Screen"})
        payload.setdefault("steps", [])
        self._upgrade_smart_image_steps(payload)
        return payload

    @staticmethod
    def _upgrade_smart_image_steps(payload: dict[str, Any]) -> None:
        """Repair settings produced by early smart-recording test builds."""
        for step in payload.get("steps") or []:
            if not isinstance(step, dict) or step.get("action") != "image_search":
                continue
            alias = str(step.get("asset") or "")
            if not alias.startswith("자동녹화-"):
                continue
            step.setdefault("fallback_full_region", True)
            if str(step.get("engine") or "ahk").casefold() == "ahk":
                # Hybrid OpenCV steps perform AHK's exact-size probe first and
                # only launch OpenCV when the native probe misses.
                step["engine"] = "opencv"
                step["search_profile"] = "fast"
                step["timeout"] = min(800, max(0, int(step.get("timeout") or 800)))
                step["poll_delay"] = 40
            if str(step.get("region_window_exe") or "").casefold() in {"python.exe", "pythonw.exe"}:
                # F8 used to bind the capture to Studio when Studio owned focus.
                # The old target is certainly wrong; a screen search is the
                # only safe recoverable interpretation for that saved step.
                step["region_mode"] = "screen"
                step["region_coords"] = "screen"
                step.pop("region_window", None)
                step.pop("region_window_exe", None)
                step.pop("region", None)
                step.pop("regions", None)
                click = dict(step.get("click") or {})
                click["mode"] = "active"
                click.pop("window", None)
                click.pop("window_exe", None)
                step["click"] = click

    def _backup_macro(self, path: Path) -> None:
        if not path.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.history_dir / path.stem / f"{stamp}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        backups = sorted(target.parent.glob("*.json"), key=lambda item: item.stat().st_mtime)
        for stale in backups[:-20]:
            stale.unlink(missing_ok=True)

    def list_macro_versions(self, name: str) -> list[dict[str, Any]]:
        history_root = (self.history_dir / self.safe_name(name)).resolve()
        versions: list[dict[str, Any]] = []
        if not history_root.is_dir():
            return versions
        for path in sorted(history_root.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
            try:
                payload = self._read_json(path, {})
                if not isinstance(payload, dict):
                    continue
                modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            except (OSError, ValueError):
                continue
            versions.append(
                {
                    "path": path,
                    "modified": modified,
                    "steps": len(payload.get("steps") or []),
                    "description": str(payload.get("description") or ""),
                    "channel": str((payload.get("meta") or {}).get("release_channel") or "test"),
                    "payload": payload,
                }
            )
        return versions

    def restore_macro_version(self, name: str, version_path: Path) -> Path:
        history_root = (self.history_dir / self.safe_name(name)).resolve()
        source = version_path.resolve()
        try:
            source.relative_to(history_root)
        except ValueError as exc:
            raise ValueError("해당 매크로의 버전 기록 밖에 있는 파일은 복구할 수 없습니다.") from exc
        if not source.is_file():
            raise FileNotFoundError(source)
        payload = self._read_json(source, {})
        if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
            raise ValueError("선택한 버전 기록의 형식이 올바르지 않습니다.")
        payload = deepcopy(payload)
        payload["name"] = name
        return self.save_macro(name, payload)

    def set_macro_release_channel(self, name: str, channel: str) -> Path:
        normalized = str(channel or "test").strip().casefold()
        if normalized not in {"stable", "test"}:
            raise ValueError("버전 채널은 stable 또는 test만 사용할 수 있습니다.")
        payload = self.load_macro(name)
        payload.setdefault("meta", {})["release_channel"] = normalized
        payload["meta"]["release_marked_at"] = datetime.now().astimezone().isoformat()
        return self.save_macro(name, payload)

    def save_macro(self, name: str, payload: dict[str, Any]) -> Path:
        path = self.macro_path(name)
        self._backup_macro(path)
        payload = dict(payload)
        payload["name"] = payload.get("name") or name
        payload.setdefault("meta", {})["last_modified"] = datetime.utcnow().isoformat() + "Z"
        payload.setdefault("steps", [])
        self._write_json(path, payload)
        return path

    def create_macro(self, name: str, description: str = "") -> Path:
        path = self.macro_path(name)
        if path.exists():
            raise FileExistsError(f"'{path.stem}' 매크로가 이미 있습니다.")
        payload = {
            "name": name.strip() or path.stem,
            "description": description.strip(),
            "meta": {"coord_mode": "Screen"},
            "steps": [],
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        self._write_json(path, payload)
        self._append_macro_order(path.stem)
        return path

    def duplicate_macro(self, source: str, target: str) -> Path:
        payload = self.load_macro(source)
        payload["name"] = target
        payload["created_at"] = datetime.utcnow().isoformat() + "Z"
        path = self.macro_path(target)
        if path.exists():
            raise FileExistsError(f"'{path.stem}' 매크로가 이미 있습니다.")
        self._write_json(path, payload)
        self._append_macro_order(path.stem)
        return path

    def archive_macro(self, name: str) -> Path:
        source = self.macro_path(name)
        if not source.exists():
            raise FileNotFoundError(source)
        archive = self.root / ".archive" / "macros"
        archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = archive / f"{source.stem}-{stamp}.json"
        shutil.move(str(source), str(target))
        return target

    def restore_macro(self, archived_path: Path, original_name: str) -> Path:
        archive_root = (self.root / ".archive" / "macros").resolve()
        source = archived_path.resolve()
        try:
            source.relative_to(archive_root)
        except ValueError as exc:
            raise ValueError("매크로 보관함 밖의 파일은 복원할 수 없습니다.") from exc
        if not source.is_file():
            raise FileNotFoundError(source)
        restored_name = original_name
        target = self.macro_path(restored_name)
        suffix = 2
        while target.exists():
            restored_name = f"{original_name}-복구-{suffix}"
            target = self.macro_path(restored_name)
            suffix += 1
        shutil.move(str(source), str(target))
        if restored_name != original_name:
            payload = self._read_json(target, {})
            if isinstance(payload, dict):
                payload["name"] = restored_name
                self._write_json(target, payload)
        return target

    def load_assets(self) -> dict[str, dict[str, Any]]:
        payload = self._read_json(self.assets_index_path, {})
        return payload if isinstance(payload, dict) else {}

    def asset_path(self, alias: str) -> Path | None:
        metadata = self.load_assets().get(alias)
        if not isinstance(metadata, dict):
            return None
        relative = Path(str(metadata.get("file") or ""))
        candidate = (self.root / relative).resolve()
        return candidate if candidate.exists() else None

    def add_asset(self, source: Path, alias: str | None = None) -> str:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        key = (alias or source.stem).strip() or source.stem
        target_name = self.safe_name(key) + source.suffix.lower()
        target = self.assets_dir / target_name
        shutil.copy2(source, target)
        index = self.load_assets()
        index[key] = {
            "file": str(target.relative_to(self.root)),
            "source": str(source),
            "size": target.stat().st_size,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        self._write_json(self.assets_index_path, index)
        return key

    def add_asset_image(self, image: Any, alias: str) -> str:
        key = alias.strip() or datetime.now().strftime("capture-%Y%m%d-%H%M%S")
        target = self.assets_dir / f"{self.safe_name(key)}.png"
        if target.exists():
            raise FileExistsError(f"'{key}' 이미지가 이미 있습니다.")
        if not image.save(str(target), "PNG"):
            raise OSError("캡처 이미지를 저장하지 못했습니다.")
        index = self.load_assets()
        index[key] = {
            "file": str(target.relative_to(self.root)),
            "source": "screen-capture",
            "size": target.stat().st_size,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        self._write_json(self.assets_index_path, index)
        return key

    def refresh_asset_metadata(self, alias: str) -> None:
        index = self.load_assets()
        metadata = index.get(alias)
        if not isinstance(metadata, dict):
            return
        path = (self.root / str(metadata.get("file") or "")).resolve()
        if path.exists():
            metadata["size"] = path.stat().st_size
            metadata["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self._write_json(self.assets_index_path, index)

    def update_asset_organization(
        self,
        aliases: Iterable[str],
        group: str = "",
        tags: Iterable[str] = (),
        variant_group: str = "",
        variant_kind: str = "",
    ) -> None:
        index = self.load_assets()
        normalized_tags = list(dict.fromkeys(str(value).strip() for value in tags if str(value).strip()))
        target_group = str(group or "").strip()
        target_variant_group = str(variant_group or "").strip()
        target_variant_kind = str(variant_kind or "").strip()
        changed = False
        for alias in aliases:
            metadata = index.get(str(alias))
            if not isinstance(metadata, dict):
                continue
            if target_group:
                metadata["group"] = target_group
            else:
                metadata.pop("group", None)
            if normalized_tags:
                metadata["tags"] = normalized_tags
            else:
                metadata.pop("tags", None)
            if target_variant_group:
                metadata["variant_group"] = target_variant_group
            else:
                metadata.pop("variant_group", None)
            if target_variant_kind:
                metadata["variant_kind"] = target_variant_kind
            else:
                metadata.pop("variant_kind", None)
            changed = True
        if changed:
            self._write_json(self.assets_index_path, index)

    def analyze_assets(self) -> dict[str, Any]:
        """Return macro references, missing files and exact duplicate groups."""
        index = self.load_assets()
        references: dict[str, list[str]] = {alias: [] for alias in index}

        def walk(value: Any, macro_name: str) -> None:
            if isinstance(value, dict):
                asset = value.get("asset")
                if isinstance(asset, str) and asset in references and macro_name not in references[asset]:
                    references[asset].append(macro_name)
                assets = value.get("assets")
                if isinstance(assets, list):
                    for alias in assets:
                        key = str(alias)
                        if key in references and macro_name not in references[key]:
                            references[key].append(macro_name)
                for nested in value.values():
                    walk(nested, macro_name)
            elif isinstance(value, list):
                for nested in value:
                    walk(nested, macro_name)

        for summary in self.list_macros():
            try:
                walk(self.load_macro(summary.name).get("steps") or [], summary.name)
            except (OSError, ValueError):
                continue

        missing: list[str] = []
        by_digest: dict[str, list[str]] = {}
        for alias, metadata in index.items():
            path = (self.root / str(metadata.get("file") or "")).resolve() if isinstance(metadata, dict) else None
            if path is None or not path.is_file():
                missing.append(alias)
                continue
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                missing.append(alias)
                continue
            by_digest.setdefault(digest.hexdigest(), []).append(alias)
        duplicates = [aliases for aliases in by_digest.values() if len(aliases) > 1]
        duplicate_aliases = sorted(alias for group in duplicates for alias in group)
        perceptual: dict[str, tuple[int, tuple[float, float, float]]] = {}
        try:
            from PySide6 import QtCore, QtGui

            for alias, metadata in index.items():
                path = (self.root / str(metadata.get("file") or "")).resolve() if isinstance(metadata, dict) else None
                image = QtGui.QImage(str(path)) if path and path.is_file() else QtGui.QImage()
                if image.isNull():
                    continue
                color_tiny = image.scaled(8, 8, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
                tiny = color_tiny.convertToFormat(QtGui.QImage.Format_Grayscale8)
                values = [QtGui.qGray(tiny.pixel(x, y)) for y in range(8) for x in range(8)]
                average = sum(values) / max(1, len(values))
                bits = 0
                for value in values:
                    bits = (bits << 1) | int(value >= average)
                colors = [color_tiny.pixelColor(x, y) for y in range(8) for x in range(8)]
                mean_color = (
                    sum(color.red() for color in colors) / 64,
                    sum(color.green() for color in colors) / 64,
                    sum(color.blue() for color in colors) / 64,
                )
                perceptual[alias] = bits, mean_color
        except (ImportError, RuntimeError):
            perceptual = {}
        parents = {alias: alias for alias in perceptual}

        def find(alias: str) -> str:
            while parents[alias] != alias:
                parents[alias] = parents[parents[alias]]
                alias = parents[alias]
            return alias

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        perceptual_items = list(perceptual.items())
        for position, (left_alias, (left_hash, left_color)) in enumerate(perceptual_items):
            for right_alias, (right_hash, right_color) in perceptual_items[position + 1 :]:
                color_distance = sum((left_color[index] - right_color[index]) ** 2 for index in range(3)) ** 0.5
                if (left_hash ^ right_hash).bit_count() <= 8 and color_distance <= 90:
                    union(left_alias, right_alias)
        similar_map: dict[str, list[str]] = {}
        for alias in perceptual:
            similar_map.setdefault(find(alias), []).append(alias)
        similar = [aliases for aliases in similar_map.values() if len(aliases) > 1]
        similar_aliases = sorted(alias for group in similar for alias in group)
        return {
            "references": references,
            "unused": sorted(alias for alias, macros in references.items() if not macros),
            "missing": sorted(missing),
            "duplicates": duplicates,
            "duplicate_aliases": duplicate_aliases,
            "similar": similar,
            "similar_aliases": similar_aliases,
        }

    def list_asset_versions(self, alias: str) -> list[Path]:
        root = (self.history_dir / "assets" / self.safe_name(alias)).resolve()
        if not root.is_dir():
            return []
        return sorted(
            (path for path in root.iterdir() if path.is_file() and path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".bmp"}),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )

    def restore_asset_version(self, alias: str, version_path: Path) -> Path:
        target = self.asset_path(alias)
        if target is None:
            raise FileNotFoundError(f"'{alias}' 이미지 파일이 없습니다.")
        history_root = (self.history_dir / "assets" / self.safe_name(alias)).resolve()
        source = version_path.resolve()
        try:
            source.relative_to(history_root)
        except ValueError as exc:
            raise ValueError("해당 이미지의 버전 기록 밖에 있는 파일은 복구할 수 없습니다.") from exc
        if not source.is_file():
            raise FileNotFoundError(source)
        history_root.mkdir(parents=True, exist_ok=True)
        backup = history_root / f"before-restore-{datetime.now():%Y%m%d-%H%M%S-%f}{target.suffix}"
        shutil.copy2(target, backup)
        shutil.copy2(source, target)
        self.refresh_asset_metadata(alias)
        return target

    def sync_assets(self) -> int:
        index = self.load_assets()
        changed = 0
        known_files = {Path(str(item.get("file") or "")).name.casefold() for item in index.values() if isinstance(item, dict)}
        for path in self.assets_dir.iterdir():
            if not path.is_file() or path.name == self.assets_index_path.name or path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
                continue
            if path.name.casefold() in known_files:
                continue
            key = path.stem
            suffix = 2
            while key in index:
                key = f"{path.stem}-{suffix}"
                suffix += 1
            index[key] = {
                "file": str(path.relative_to(self.root)),
                "source": "asset-folder-sync",
                "size": path.stat().st_size,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
            changed += 1
        if changed:
            self._write_json(self.assets_index_path, index)
        return changed

    def archive_asset(self, alias: str) -> Path | None:
        index = self.load_assets()
        metadata = index.pop(alias, None)
        if not isinstance(metadata, dict):
            return None
        source = (self.root / str(metadata.get("file") or "")).resolve()
        target: Path | None = None
        if source.exists():
            trash = self.assets_dir / ".trash"
            trash.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            target = trash / f"{source.stem}-{stamp}{source.suffix}"
            shutil.move(str(source), str(target))
        self._write_json(self.assets_index_path, index)
        return target

    def restore_asset(self, alias: str, metadata: dict[str, Any], archived_path: Path | None) -> str:
        index = self.load_assets()
        restored_alias = alias
        suffix = 2
        while restored_alias in index:
            restored_alias = f"{alias}-복구-{suffix}"
            suffix += 1
        restored_metadata = dict(metadata)
        original_relative = Path(str(restored_metadata.get("file") or f"assets/{self.safe_name(restored_alias)}.png"))
        target = (self.root / original_relative).resolve()
        assets_root = self.assets_dir.resolve()
        try:
            target.relative_to(assets_root)
        except ValueError as exc:
            raise ValueError("이미지 폴더 밖의 파일은 복원할 수 없습니다.") from exc
        if target.exists() or restored_alias != alias:
            extension = target.suffix or (archived_path.suffix if archived_path else ".png")
            target = self.assets_dir / f"{self.safe_name(restored_alias)}{extension}"
        if archived_path is not None:
            trash_root = (self.assets_dir / ".trash").resolve()
            source = archived_path.resolve()
            try:
                source.relative_to(trash_root)
            except ValueError as exc:
                raise ValueError("이미지 보관함 밖의 파일은 복원할 수 없습니다.") from exc
            if not source.is_file():
                raise FileNotFoundError(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        elif not target.exists():
            raise FileNotFoundError("복원할 이미지 파일이 없습니다.")
        restored_metadata["file"] = str(target.relative_to(self.root))
        restored_metadata["size"] = target.stat().st_size
        restored_metadata["updated_at"] = datetime.utcnow().isoformat() + "Z"
        index[restored_alias] = restored_metadata
        self._write_json(self.assets_index_path, index)
        return restored_alias

    def load_tables(self) -> dict[str, list[list[str]]]:
        payload = self._read_json(self.tables_path, {})
        return payload if isinstance(payload, dict) else {}

    def save_tables(self, payload: dict[str, list[list[str]]]) -> None:
        if self.tables_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self.history_dir / "data_tables" / f"{stamp}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.tables_path, backup)
        self._write_json(self.tables_path, payload)

    def load_hotkeys(self) -> dict[str, Any]:
        payload = self._read_json(self.hotkeys_path, {"rows": 3, "cols": 5, "slots": []})
        return payload if isinstance(payload, dict) else {"rows": 3, "cols": 5, "slots": []}

    def save_hotkeys(self, payload: dict[str, Any]) -> None:
        self._write_json(self.hotkeys_path, payload)

    def load_hotkey_actions(self) -> dict[str, str]:
        payload = self._read_json(self.hotkey_actions_path, {})
        normalized = {str(key): str(value) for key, value in payload.items()} if isinstance(payload, dict) else {}
        # One-time migration from the former F9/F10 navigation defaults to the
        # dedicated smart-recording controls requested for the automation UI.
        if not normalized.get("action_smart_record") and (
            normalized.get("tab_export") == "F9" or normalized.get("tab_hotkey_settings") == "F10"
        ):
            changed = False
            if normalized.get("tab_export") == "F9":
                normalized["tab_export"] = ""
                changed = True
            if normalized.get("tab_hotkey_settings") == "F10":
                normalized["tab_hotkey_settings"] = ""
                changed = True
            normalized["action_smart_record"] = "F9"
            changed = True
            if changed:
                self._write_json(self.hotkey_actions_path, normalized)
        return normalized

    def save_hotkey_actions(self, payload: dict[str, str]) -> None:
        self._write_json(self.hotkey_actions_path, payload)

    def load_automation_blocks(self) -> dict[str, dict[str, Any]]:
        payload = self._read_json(self.automation_blocks_path, {})
        if not isinstance(payload, dict):
            return {}
        return {str(name): value for name, value in payload.items() if isinstance(value, dict)}

    def save_automation_block(self, name: str, steps: list[dict[str, Any]], description: str = "") -> None:
        key = name.strip()
        if not key:
            raise ValueError("자동화 블록 이름이 비어 있습니다.")
        blocks = self.load_automation_blocks()
        blocks[key] = {
            "description": description.strip(),
            "steps": deepcopy(steps),
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        self._write_json(self.automation_blocks_path, blocks)

    def remove_automation_block(self, name: str) -> None:
        blocks = self.load_automation_blocks()
        if name in blocks:
            blocks.pop(name)
            self._write_json(self.automation_blocks_path, blocks)

    def quick_slots_runner(self):
        from .runner import QuickSlotsRunner

        return QuickSlotsRunner(self)

    def engine(self):
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        import macro_tool

        return macro_tool

    def render(self, name: str, browser_fast: bool = False, runtime_mode: str = "auto") -> str:
        engine = self.engine()
        payload = engine.prepare_macro_for_runtime(self.load_macro(name), runtime_mode)
        return engine.render_macro_script(payload, engine.read_assets(), browser_fast)

    def export(
        self,
        name: str,
        output: Path | None = None,
        browser_fast: bool = False,
        runtime_mode: str = "auto",
    ) -> Path:
        engine = self.engine()
        target = output or self.exports_dir / f"{self.safe_name(name)}.ahk"
        # Repository filenames preserve spaces and Korean characters, while
        # the legacy CLI slugifies display names. Export the already loaded
        # payload directly so e.g. '자동화 테스트.json' is never looked up as
        # '자동화-테스트.json'.
        payload = engine.prepare_macro_for_runtime(self.load_macro(name), runtime_mode)
        script = engine.render_macro_script(payload, engine.read_assets(), browser_fast)
        engine.export_macro_payload(payload, target, browser_fast, rendered_script=script)
        return target

    def compile(self, script: Path) -> Path:
        compiler_path = self._read_text_path("ahk2exe_path.txt")
        if not compiler_path or not compiler_path.exists():
            raise FileNotFoundError("Ahk2Exe 경로를 찾을 수 없습니다.")
        output = script.with_suffix(".exe")
        result = subprocess.run(
            [str(compiler_path), "/in", str(script), "/out", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not output.exists():
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "EXE 컴파일 실패")
        return output

    def export_portable(
        self,
        name: str,
        output: Path | None = None,
        browser_fast: bool = False,
        runtime_mode: str = "auto",
    ) -> PortableExportResult:
        """Create an installation-free folder and ZIP without overwriting an older bundle."""
        requested = output or self.exports_dir / f"{self.safe_name(name)}.ahk"
        requested = requested.expanduser().resolve()
        requested.parent.mkdir(parents=True, exist_ok=True)
        bundle_name = f"{requested.stem}-portable"
        folder = self._unique_portable_path(requested.parent / bundle_name)
        staging = requested.parent / f".{folder.name}.building-{os.getpid()}-{datetime.now():%H%M%S%f}"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            script = self.export(name, staging / f"{requested.stem}.ahk", browser_fast, runtime_mode)
            executable = self.compile(script)
            engine = self.engine()
            payload = engine.prepare_macro_for_runtime(self.load_macro(name), runtime_mode)
            steps = list(payload.get("steps") or [])
            requirements = self._portable_requirements(steps)
            if str(runtime_mode or "auto").casefold() == "python" and not requirements["python"]:
                requirements = dict(requirements)
                requirements["python"] = True
                requirements["features"] = tuple((*requirements["features"], "Python 런타임"))
            features = list(requirements["features"])
            notes = list(requirements["notes"])
            python_abi: str | None = None
            if requirements["python"]:
                python_abi = self._copy_portable_python(staging / "runtime")
                self._copy_portable_packages(staging / "runtime_packages", requirements["packages"], python_abi)
                self._validate_portable_python(staging, requirements["imports"])
            if requirements["tables"] and self.tables_path.exists():
                shutil.copy2(self.tables_path, staging / "data_tables.json")
            if requirements["tesseract"]:
                self._copy_portable_tesseract(staging)
            if requirements.get("ocr_engine"):
                self._copy_portable_ocr_engine(staging)
            if requirements.get("remote_notify"):
                for module_name in ("remote_notify.py", "remote_common.py"):
                    source = self.root / module_name
                    if source.is_file():
                        shutil.copy2(source, staging / module_name)
            manifest = {
                "format": 1,
                "macro": name,
                "created_at": datetime.now().astimezone().isoformat(),
                "entrypoint": executable.name,
                "script": script.name,
                "features": features,
                "notes": notes,
                "autohotkey_install_required": False,
                "python_install_required": False,
                "python_abi": python_abi,
                "runtime_mode": str(runtime_mode or "auto").casefold(),
            }
            self._write_json(staging / "portable_manifest.json", manifest)
            self._write_portable_readme(staging, executable.name, features, notes)
            staging.rename(folder)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        archive = Path(shutil.make_archive(str(folder), "zip", root_dir=folder.parent, base_dir=folder.name))
        return PortableExportResult(
            folder=folder,
            archive=archive,
            executable=folder / executable.name,
            script=folder / script.name,
            features=tuple(features),
            notes=tuple(notes),
        )

    def export_single_file(
        self,
        name: str,
        output: Path | None = None,
        browser_fast: bool = False,
        runtime_mode: str = "auto",
    ) -> SingleFileExportResult:
        requested = output or self.exports_dir / f"{self.safe_name(name)}-portable.exe"
        requested = requested.expanduser().resolve()
        if requested.suffix.casefold() != ".exe":
            requested = requested.with_suffix(".exe")
        requested.parent.mkdir(parents=True, exist_ok=True)
        target = self._unique_output_file(requested)
        with tempfile.TemporaryDirectory(prefix="macrorelay-onefile-") as directory:
            temporary_root = Path(directory)
            portable = self.export_portable(
                name,
                temporary_root / f"{self.safe_name(name)}.ahk",
                browser_fast,
                runtime_mode,
            )
            entrypoint = str(Path(portable.folder.name) / portable.executable.name)
            self._compile_single_file_launcher(portable.archive, target, entrypoint, name)
            features = tuple(dict.fromkeys((*portable.features, "단일 파일 자동 압축 해제")))
            notes = tuple(
                dict.fromkeys(
                    (
                        *portable.notes,
                        "첫 실행 시 사용자 로컬 캐시에 자동 압축 해제되며 이후 실행에서는 캐시를 재사용합니다.",
                    )
                )
            )
        if not target.is_file() or target.stat().st_size <= 0:
            raise RuntimeError("단일 포터블 EXE가 생성되지 않았습니다.")
        return SingleFileExportResult(executable=target, features=features, notes=notes)

    @staticmethod
    def _unique_output_file(path: Path) -> Path:
        if not path.exists():
            return path
        suffix = 2
        while True:
            candidate = path.with_name(f"{path.stem}-{suffix}{path.suffix}")
            if not candidate.exists():
                return candidate
            suffix += 1

    def _compile_single_file_launcher(self, payload: Path, output: Path, entrypoint: str, macro_name: str) -> None:
        framework_roots = [
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319",
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319",
        ]
        compiler = next((root / "csc.exe" for root in framework_roots if (root / "csc.exe").is_file()), None)
        if compiler is None:
            raise FileNotFoundError("단일 EXE 생성에 필요한 Windows .NET C# 컴파일러를 찾을 수 없습니다.")
        compression = self._find_dotnet_assembly("System.IO.Compression.dll")
        forms = self._find_dotnet_assembly("System.Windows.Forms.dll")
        if compression is None or forms is None:
            raise FileNotFoundError("단일 EXE 생성에 필요한 Windows .NET 구성요소를 찾을 수 없습니다.")
        source = self._single_file_launcher_source(entrypoint, macro_name)
        with tempfile.TemporaryDirectory(prefix="macrorelay-launcher-") as directory:
            source_path = Path(directory) / "MacroRelayPortableLauncher.cs"
            source_path.write_text(source, encoding="utf-8-sig")
            command = [
                str(compiler),
                "/nologo",
                "/target:winexe",
                "/platform:anycpu",
                "/optimize+",
                f"/out:{output}",
                f"/resource:{payload},MacroRelay.Payload",
                f"/reference:{compression}",
                f"/reference:{forms}",
            ]
            icon = self.root / "branding" / "macrorelay-studio.ico"
            if icon.is_file():
                command.append(f"/win32icon:{icon}")
            command.append(str(source_path))
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if result.returncode != 0 or not output.is_file():
            detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
            raise RuntimeError("단일 포터블 EXE 컴파일 실패: " + detail)

    @staticmethod
    def _find_dotnet_assembly(filename: str) -> Path | None:
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        candidates = [
            windows / "Microsoft.NET" / "Framework64" / "v4.0.30319" / filename,
            windows / "Microsoft.NET" / "Framework" / "v4.0.30319" / filename,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        assembly_root = windows / "Microsoft.NET" / "assembly" / "GAC_MSIL" / Path(filename).stem
        matches = sorted(assembly_root.glob(f"**/{filename}"), reverse=True) if assembly_root.is_dir() else []
        return matches[0] if matches else None

    @staticmethod
    def _single_file_launcher_source(entrypoint: str, macro_name: str) -> str:
        def csharp(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        template = r'''using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("MacroRelay Portable")]
[assembly: AssemblyProduct("MacroRelay")]
[assembly: AssemblyCompany("MacroRelay")]

internal static class Program
{
    private const string ResourceName = "MacroRelay.Payload";
    private const string Entrypoint = "__ENTRYPOINT__";
    private const string MacroName = "__MACRO_NAME__";

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string hash = PayloadHash();
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (String.IsNullOrWhiteSpace(local))
                local = Path.GetTempPath();
            string cacheRoot = Path.Combine(local, "MacroRelay", "Cache");
            string cache = Path.Combine(cacheRoot, hash.Substring(0, 20));
            string marker = Path.Combine(cache, ".complete");
            Directory.CreateDirectory(cacheRoot);
            using (var mutex = new Mutex(false, "Local\\MacroRelay_" + hash.Substring(0, 20)))
            {
                if (!mutex.WaitOne(TimeSpan.FromMinutes(5)))
                    throw new TimeoutException("포터블 파일 준비 시간이 초과되었습니다.");
                try
                {
                    string expected = Path.Combine(cache, Entrypoint);
                    if (!File.Exists(marker) || !File.Exists(expected))
                    {
                        if (Directory.Exists(cache))
                            Directory.Delete(cache, true);
                        string staging = cache + ".building-" + Process.GetCurrentProcess().Id;
                        if (Directory.Exists(staging))
                            Directory.Delete(staging, true);
                        Directory.CreateDirectory(staging);
                        try
                        {
                            ExtractPayload(staging);
                            File.WriteAllText(Path.Combine(staging, ".complete"), hash, Encoding.UTF8);
                            Directory.Move(staging, cache);
                        }
                        finally
                        {
                            if (Directory.Exists(staging))
                                Directory.Delete(staging, true);
                        }
                    }
                    File.SetLastWriteTimeUtc(marker, DateTime.UtcNow);
                }
                finally
                {
                    mutex.ReleaseMutex();
                }
            }
            CleanupOldCaches(cacheRoot, cache);
            string executable = Path.Combine(cache, Entrypoint);
            var start = new ProcessStartInfo(executable)
            {
                WorkingDirectory = Path.GetDirectoryName(executable),
                UseShellExecute = true,
                Arguments = JoinArguments(args)
            };
            Process.Start(start);
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(
                "단일 포터블 실행 준비에 실패했습니다.\n\n" + error.Message,
                "MacroRelay · " + MacroName,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }
    }

    private static Stream PayloadStream()
    {
        Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(ResourceName);
        if (stream == null)
            throw new InvalidDataException("내장 포터블 데이터가 없습니다.");
        return stream;
    }

    private static string PayloadHash()
    {
        using (Stream stream = PayloadStream())
        using (SHA256 sha = SHA256.Create())
        {
            byte[] hash = sha.ComputeHash(stream);
            var text = new StringBuilder(hash.Length * 2);
            foreach (byte value in hash)
                text.Append(value.ToString("x2"));
            return text.ToString();
        }
    }

    private static void ExtractPayload(string destination)
    {
        string root = Path.GetFullPath(destination) + Path.DirectorySeparatorChar;
        using (Stream stream = PayloadStream())
        using (var archive = new ZipArchive(stream, ZipArchiveMode.Read, false))
        {
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string relative = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                string target = Path.GetFullPath(Path.Combine(destination, relative));
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("안전하지 않은 압축 경로가 감지되었습니다.");
                if (String.IsNullOrEmpty(entry.Name))
                {
                    Directory.CreateDirectory(target);
                    continue;
                }
                string parent = Path.GetDirectoryName(target);
                if (!String.IsNullOrEmpty(parent))
                    Directory.CreateDirectory(parent);
                using (Stream input = entry.Open())
                using (var output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None))
                    input.CopyTo(output);
            }
        }
    }

    private static string JoinArguments(string[] args)
    {
        if (args == null || args.Length == 0)
            return String.Empty;
        var values = new string[args.Length];
        for (int index = 0; index < args.Length; index++)
            values[index] = "\"" + args[index].Replace("\"", "\\\"") + "\"";
        return String.Join(" ", values);
    }

    private static void CleanupOldCaches(string cacheRoot, string current)
    {
        try
        {
            foreach (string directory in Directory.GetDirectories(cacheRoot))
            {
                if (String.Equals(directory, current, StringComparison.OrdinalIgnoreCase))
                    continue;
                string marker = Path.Combine(directory, ".complete");
                DateTime used = File.Exists(marker) ? File.GetLastWriteTimeUtc(marker) : Directory.GetLastWriteTimeUtc(directory);
                if (used < DateTime.UtcNow.AddDays(-30))
                    Directory.Delete(directory, true);
            }
        }
        catch
        {
            // Cache cleanup must never block macro startup.
        }
    }
}
'''
        return template.replace("__ENTRYPOINT__", csharp(entrypoint)).replace("__MACRO_NAME__", csharp(macro_name))

    @staticmethod
    def _unique_portable_path(path: Path) -> Path:
        if not path.exists() and not path.with_suffix(".zip").exists():
            return path
        suffix = 2
        while True:
            candidate = path.with_name(f"{path.name}-{suffix}")
            if not candidate.exists() and not candidate.with_suffix(".zip").exists():
                return candidate
            suffix += 1

    @staticmethod
    def _portable_requirements(steps: list[dict[str, Any]]) -> dict[str, Any]:
        actions = {str(step.get("action") or "") for step in steps if isinstance(step, dict)}
        opencv = any(
            step.get("action") == "image_search" and str(step.get("engine") or "ahk").casefold() == "opencv"
            for step in steps
            if isinstance(step, dict)
        )
        ocr_steps = [step for step in steps if isinstance(step, dict) and step.get("action") == "ocr"]
        browser = "browser_action" in actions or any(str(step.get("mode") or "").casefold() == "browser" for step in ocr_steps)
        excel_steps = [step for step in steps if isinstance(step, dict) and step.get("action") in {"table_excel_read", "table_excel_write"}]
        excel_file = bool(excel_steps) or any(str(step.get("excel_mode") or "none").casefold() == "file" for step in ocr_steps)
        excel_com = any(str(step.get("excel_mode") or "").casefold() in {"active", "com"} for step in excel_steps + ocr_steps)
        remote_notify = "remote_notify" in actions
        python_needed = opencv or bool(ocr_steps) or browser or bool(excel_steps) or remote_notify
        packages: set[str] = set()
        imports: set[str] = set()
        features = ["AutoHotkey EXE"]
        notes: list[str] = []
        if opencv:
            packages.update({"cv2", "numpy", "numpy.libs", "mss"})
            imports.update({"cv2", "numpy", "mss"})
            features.append("OpenCV 이미지 서치")
        ocr_engine_needed = any(
            step.get("engine_preference") in ("auto", "paddle") or step.get("ocr_action")
            for step in ocr_steps
        )
        if ocr_steps:
            packages.update({"PIL", "pytesseract", "packaging"})
            imports.update({"PIL", "pytesseract"})
            features.append("OCR")
        if ocr_engine_needed:
            packages.update({"cv2", "numpy", "numpy.libs", "mss"})
            imports.update({"cv2", "numpy", "mss"})
            if any(str(step.get("engine_preference") or "auto").casefold() in {"auto", "paddle"} for step in ocr_steps):
                packages.update(
                    {
                        "rapidocr_onnxruntime",
                        "rapidocr",
                        "onnxruntime",
                        "pyclipper",
                        "shapely",
                        "shapely.libs",
                        "yaml",
                        "tqdm",
                        "flatbuffers",
                        "google",
                        "six.py",
                        "colorama",
                        "colorlog",
                        "omegaconf",
                        "antlr4",
                        "requests",
                        "urllib3",
                        "charset_normalizer",
                        "idna",
                        "certifi",
                    }
                )
                imports.update({"rapidocr", "onnxruntime"})
            features.append("OCR 엔진 (PaddleOCR)")
        if browser:
            packages.update({"playwright", "greenlet", "pyee", "typing_extensions.py"})
            imports.update({"greenlet", "playwright.sync_api", "pyee"})
            features.append("브라우저 자동화")
            notes.append("브라우저 자동화는 대상 PC의 Chromium 계열 브라우저와 원격 디버깅 연결이 필요합니다.")
        if excel_file:
            packages.update({"openpyxl", "et_xmlfile"})
            imports.add("openpyxl")
            features.append("Excel 파일 처리")
        if excel_com:
            packages.update(
                {
                    "win32com",
                    "win32comext",
                    "win32",
                    "pythonwin",
                    "pywin32_system32",
                    "pythoncom.py",
                    "pywintypes.py",
                }
            )
            features.append("실행 중 Excel 제어")
            imports.add("win32com.client")
            notes.append("실행 중 Excel 제어 기능은 대상 PC에 Microsoft Excel이 설치되어 있어야 합니다.")
        if "run_program" in actions:
            notes.append("프로그램 실행 단계에 지정한 외부 프로그램은 대상 PC에도 존재해야 합니다.")
        if remote_notify:
            features.append("MacroRelay 모바일 알림")
            notes.append("모바일 알림은 해당 PC에서 생성한 remote_config.json 연결 설정이 필요합니다.")
        return {
            "python": python_needed,
            "packages": packages,
            "imports": imports,
            "features": tuple(dict.fromkeys(features)),
            "notes": tuple(dict.fromkeys(notes)),
            "tables": bool(actions.intersection({"table_store", "table_copy", "table_paste", "table_excel_read", "table_excel_write"}))
            or any(step.get("table") for step in ocr_steps),
            "tesseract": bool(ocr_steps),
            "ocr_engine": ocr_engine_needed,
            "remote_notify": remote_notify,
        }

    def _copy_portable_python(self, destination: Path) -> str:
        candidates = [Path(sys.executable).resolve().parent, Path(sys.base_prefix).resolve()]
        base: Path | None = None
        abi_tag = ""
        for candidate in dict.fromkeys(candidates):
            executable = candidate / "python.exe"
            if not executable.is_file() or not (candidate / "Lib").is_dir():
                continue
            environment = os.environ.copy()
            for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
                environment.pop(key, None)
            probe = subprocess.run(
                [str(executable), "-S", "-c", "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            detected = probe.stdout.strip()
            if probe.returncode == 0 and detected.startswith("cp") and detected[2:].isdigit():
                base = candidate
                abi_tag = detected
                break
        if base is None:
            raise FileNotFoundError("포터블 패키지에 포함할 Python 런타임을 찾을 수 없습니다.")
        destination.mkdir(parents=True, exist_ok=False)
        for pattern in ("python.exe", "python3.dll", "python3*.dll", "vcruntime*.dll", "LICENSE.txt"):
            for source in base.glob(pattern):
                if source.is_file():
                    shutil.copy2(source, destination / source.name)
        dlls = base / "DLLs"
        if dlls.is_dir():
            shutil.copytree(dlls, destination / "DLLs", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(
            base / "Lib",
            destination / "Lib",
            ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "test", "tests"),
        )
        return abi_tag

    def _portable_package_roots(self) -> list[Path]:
        application_root = Path(__file__).resolve().parents[1]
        exported_roots = sorted(
            self.exports_dir.glob("*-portable/runtime_packages"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        candidates = [
            *sorted((application_root / "runtime_packages").glob("cp*"), reverse=True),
            application_root / "runtime_packages",
            *sorted((self.root / "runtime_packages").glob("cp*"), reverse=True),
            self.root / "runtime_packages",
            *sorted((self.root / "runtime" / "opencv").glob("cp*/packages"), reverse=True),
            self.root / ".venv" / "Lib" / "site-packages",
            Path(sys.base_prefix) / "Lib" / "site-packages",
            *exported_roots,
        ]
        roots: list[Path] = []
        for path in candidates:
            resolved = path.resolve()
            if resolved.is_dir() and resolved not in roots:
                roots.append(resolved)
        return roots

    def _copy_portable_packages(self, destination: Path, packages: set[str], abi_tag: str | None = None) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        roots = self._portable_package_roots()
        missing: list[str] = []
        abi_tag = abi_tag or f"cp{sys.version_info.major}{sys.version_info.minor}"
        for package in sorted(packages, key=str.casefold):
            copied = False
            for root in roots:
                source = root / package
                try:
                    if source.is_dir():
                        if package == "greenlet" and not list(source.glob(f"_greenlet.{abi_tag}-*.pyd")):
                            continue
                        tagged_binaries = [binary for binary in source.rglob("*.pyd") if ".cp3" in binary.name]
                        if tagged_binaries and not any(f".{abi_tag}-" in binary.name for binary in tagged_binaries):
                            continue
                        shutil.copytree(
                            source,
                            destination / package,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "*.pyc", "test", "tests", "doc", "docs", "*.lib", "*.pdb"
                            ),
                        )
                        for binary in (destination / package).rglob("*.pyd"):
                            if ".cp3" in binary.name and f".{abi_tag}-" not in binary.name:
                                binary.unlink()
                        copied = True
                        break
                    if source.is_file():
                        shutil.copy2(source, destination / source.name)
                        copied = True
                        break
                    if package == "pywintypes.py":
                        matches = list(root.glob("pywintypes*.dll"))
                        if matches:
                            for match in matches:
                                shutil.copy2(match, destination / match.name)
                            copied = True
                            break
                except OSError:
                    shutil.rmtree(destination / package, ignore_errors=True)
                    continue
            if not copied:
                missing.append(package)
        if missing:
            raise FileNotFoundError("포터블 구성요소가 설치되어 있지 않습니다: " + ", ".join(missing))

    @staticmethod
    def _validate_portable_python(bundle: Path, imports: set[str]) -> None:
        if not imports:
            return
        python = bundle / "runtime" / "python.exe"
        packages = bundle / "runtime_packages"
        statements = "; ".join(f"import {module}" for module in sorted(imports))
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(packages)
        result = subprocess.run(
            [str(python), "-c", statements],
            cwd=bundle,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
            raise RuntimeError("포터블 Python 구성요소 호환성 검사 실패: " + detail)

    def _copy_portable_tesseract(self, destination: Path) -> None:
        configured = self._read_text_path("tesseract_path.txt")
        candidates = [
            configured,
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ]
        executable = next((path for path in candidates if path and path.is_file()), None)
        if executable is None:
            raise FileNotFoundError("OCR 포터블 내보내기에 필요한 Tesseract가 연결되어 있지 않습니다.")
        target_dir = destination / "tesseract"
        shutil.copytree(executable.parent, target_dir)
        (destination / "tesseract_path.txt").write_text("tesseract\\tesseract.exe\n", encoding="utf-8")

    def _copy_portable_ocr_engine(self, destination: Path) -> None:
        """OCR 엔진 모듈과 모델 파일을 포터블 패키지에 복사한다."""
        engine_modules = [
            "ocr_engine.py",
            "ocr_capture.py",
            "ocr_preprocess.py",
            "ocr_paddle.py",
            "ocr_tesseract.py",
            "ocr_postprocess.py",
        ]
        for module_name in engine_modules:
            src = self.root / module_name
            if src.exists():
                shutil.copy2(src, destination / module_name)
        # Copy ONNX models directory
        models_src = self.root / "models"
        if models_src.is_dir():
            models_dst = destination / "models"
            if models_dst.exists():
                shutil.rmtree(models_dst)
            shutil.copytree(models_src, models_dst)
        # Copy tessdata if not already copied
        tessdata_src = self.root / "tessdata"
        tessdata_dst = destination / "tessdata"
        if tessdata_src.is_dir() and not tessdata_dst.exists():
            shutil.copytree(tessdata_src, tessdata_dst)
        tessdata_best_src = self.root / "tessdata_best"
        tessdata_best_dst = destination / "tessdata_best"
        if tessdata_best_src.is_dir() and not tessdata_best_dst.exists():
            shutil.copytree(tessdata_best_src, tessdata_best_dst)

    @staticmethod
    def _write_portable_readme(destination: Path, executable: str, features: list[str], notes: list[str]) -> None:
        lines = [
            "MacroRelay 포터블 패키지",
            "",
            f"실행: {executable}",
            "이 폴더의 파일과 하위 폴더를 분리하지 말고 폴더째 복사하세요.",
            "대상 PC에는 AutoHotkey와 Python을 설치할 필요가 없습니다.",
            "",
            "포함 기능:",
            *(f"- {feature}" for feature in features),
        ]
        if notes:
            lines.extend(["", "주의:", *(f"- {note}" for note in notes)])
        (destination / "사용방법.txt").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    def run_macro(self, name: str) -> subprocess.Popen[Any]:
        payload = deepcopy(self.load_macro(name))
        if any(
            step.get("action") == "image_search" and str(step.get("engine") or "ahk").lower() == "opencv"
            for step in payload.get("steps", [])
        ):
            self._ensure_opencv_runtime()
        # Full execution must use the already loaded payload. Passing the
        # display name back through the legacy CLI path slugified Korean names
        # with spaces (e.g. '자동화 테스트') and could report macro not found.
        engine = self.engine()
        script = self.exports_dir / f".{self.safe_name(name)}-run.ahk"
        engine.export_macro_payload(payload, script)
        return self._launch_macro_payload(name, payload, script)

    def run_macro_step(self, name: str, step_index: int) -> subprocess.Popen[Any]:
        payload = deepcopy(self.load_macro(name))
        steps = payload.get("steps") or []
        if not 1 <= int(step_index) <= len(steps):
            raise IndexError(f"테스트할 {step_index}번 단계를 찾을 수 없습니다.")
        payload["name"] = f"{name} · {step_index}번 단계 테스트"
        payload["graph_start_step"] = int(step_index)
        payload["graph_end_step"] = int(step_index)
        engine = self.engine()
        script = self.exports_dir / f".{self.safe_name(name)}-step-{int(step_index)}-test.ahk"
        engine.export_macro_payload(payload, script)
        return self._launch_macro_payload(f"{name} · {step_index}번 단계", payload, script)

    def _launch_macro_payload(self, name: str, payload: dict[str, Any], script: Path) -> subprocess.Popen[Any]:
        environment = os.environ.copy()
        has_opencv = any(
            step.get("action") == "image_search" and str(step.get("engine") or "ahk").lower() == "opencv"
            for step in payload.get("steps", [])
        )
        has_ocr = any(step.get("action") == "ocr" for step in payload.get("steps", []))
        if has_opencv or has_ocr:
            python, packages = self._ensure_ocr_runtime() if has_ocr else self._ensure_opencv_runtime()
            environment["MACRORELAY_PYTHON_EXE"] = str(python)
            environment["MACRORELAY_PYTHON_PACKAGES"] = str(packages)
        result_dir = self.exports_dir / ".run_results"
        result_dir.mkdir(parents=True, exist_ok=True)
        for stale in sorted(result_dir.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)[99:]:
            stale.unlink(missing_ok=True)
        result_path = result_dir / f"{self.safe_name(name)}-{uuid.uuid4().hex}.txt"
        progress_path = result_dir / f"{self.safe_name(name)}-{uuid.uuid4().hex}.progress.txt"
        click_path = result_dir / f"{self.safe_name(name)}-{uuid.uuid4().hex}.click.txt"
        control_path = result_dir / f"{self.safe_name(name)}-{uuid.uuid4().hex}.control.txt"
        variable_path = result_dir / f"{self.safe_name(name)}-{uuid.uuid4().hex}.variables.ini"
        trace_path = self.exports_dir / "execution_trace.log"
        trace_path.write_text("", encoding="utf-8")
        control_path.write_text("RUN", encoding="utf-8")
        variable_path.write_text("[variables]\n", encoding="utf-8-sig")
        environment["MACRORELAY_RESULT_FILE"] = str(result_path)
        environment["MACRORELAY_PROGRESS_FILE"] = str(progress_path)
        environment["MACRORELAY_CLICK_FILE"] = str(click_path)
        environment["MACRORELAY_TRACE_FILE"] = str(trace_path)
        environment["MACRORELAY_CONTROL_FILE"] = str(control_path)
        environment["MACRORELAY_VARIABLE_FILE"] = str(variable_path)
        executable = self._read_text_path("ahk_path.txt")
        if not executable or not executable.exists():
            raise FileNotFoundError("AutoHotkey 실행 파일을 찾을 수 없습니다.")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen([str(executable), str(script)], env=environment, creationflags=flags)
        process.macrorelay_result_path = result_path  # type: ignore[attr-defined]
        process.macrorelay_progress_path = progress_path  # type: ignore[attr-defined]
        process.macrorelay_click_path = click_path  # type: ignore[attr-defined]
        process.macrorelay_trace_path = trace_path  # type: ignore[attr-defined]
        process.macrorelay_control_path = control_path  # type: ignore[attr-defined]
        process.macrorelay_variable_path = variable_path  # type: ignore[attr-defined]
        return process

    def _ensure_ocr_runtime(self) -> tuple[Path, Path]:
        """Select one ABI-compatible Python environment for the OCR server."""
        marker = self.root / ".component-installing"
        if marker.exists():
            try:
                installing = marker.read_text(encoding="utf-8-sig").strip()
            except OSError:
                installing = "구성요소"
            if "OCR" in installing or "OpenCV" in installing:
                raise RuntimeError(f"{installing}을 설치하고 있습니다. 완료 후 다시 실행하세요.")

        studio_python = Path(sys.executable)
        if studio_python.name.casefold() == "pythonw.exe":
            console_python = studio_python.with_name("python.exe")
            if console_python.is_file():
                studio_python = console_python
        abi_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
        candidates = [
            (studio_python, self.root / "runtime_packages" / abi_tag),
            (studio_python, self.root / "runtime_packages"),
            (studio_python, self.root / ".venv" / "Lib" / "site-packages"),
        ]
        candidates.extend(self._opencv_runtime_candidates())
        probe = "import cv2,numpy,mss; from PIL import Image; import pytesseract"
        failures: list[str] = []
        for python, packages in candidates:
            if not python.is_file() or not packages.is_dir():
                continue
            environment = os.environ.copy()
            for key in ("PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
                environment.pop(key, None)
            environment["PYTHONPATH"] = str(packages)
            try:
                result = subprocess.run(
                    [str(python), "-c", probe],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append(str(exc))
                continue
            if result.returncode == 0:
                return python.resolve(), packages.resolve()
            failures.append((result.stderr or result.stdout or "OCR 모듈 로드 실패").strip().splitlines()[-1])
        detail = failures[-1] if failures else "사용 가능한 OCR Python 환경 없음"
        raise RuntimeError(
            "OCR 실행 구성요소가 완전히 설치되지 않았습니다. "
            "설정 > 구성요소 설치에서 OpenCV와 고속 OCR 엔진을 점검하세요. "
            f"({detail})"
        )

    def _managed_opencv_runtime(self, abi_tag: str | None = None) -> tuple[Path, Path]:
        base = self.root / "runtime" / "opencv"
        if abi_tag:
            base = base / abi_tag
        return base / "python" / "python.exe", base / "packages"

    @staticmethod
    def _python_abi(executable: Path) -> str | None:
        environment = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
            environment.pop(key, None)
        try:
            result = subprocess.run(
                [str(executable), "-S", "-c", "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = result.stdout.strip()
        return value if result.returncode == 0 and value.startswith("cp") and value[2:].isdigit() else None

    @staticmethod
    def _copy_python_runtime(source: Path, destination: Path) -> None:
        base = source.parent
        destination.mkdir(parents=True, exist_ok=False)
        for pattern in ("python.exe", "python3.dll", "python3*.dll", "vcruntime*.dll", "LICENSE.txt"):
            for item in base.glob(pattern):
                if item.is_file():
                    shutil.copy2(item, destination / item.name)
        dlls = base / "DLLs"
        if dlls.is_dir():
            shutil.copytree(dlls, destination / "DLLs", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        library = base / "Lib"
        if not library.is_dir():
            raise FileNotFoundError(f"Python 표준 라이브러리를 찾을 수 없습니다: {library}")
        shutil.copytree(
            library,
            destination / "Lib",
            ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "test", "tests", "idlelib", "ensurepip"),
        )

    @staticmethod
    def _copy_opencv_packages(source: Path, destination: Path, abi_tag: str) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        required = ("cv2", "numpy", "numpy.libs", "mss")
        for package in required:
            item = source / package
            if not item.exists():
                raise FileNotFoundError(f"OpenCV 런타임 패키지가 없습니다: {package}")
            target = destination / package
            if item.is_dir():
                shutil.copytree(
                    item,
                    target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test", "tests", "doc", "docs"),
                )
                for binary in target.rglob("*.pyd"):
                    if ".cp3" in binary.name and f".{abi_tag}-" not in binary.name:
                        binary.unlink(missing_ok=True)
            else:
                shutil.copy2(item, target)

    def _promote_opencv_runtime(self, python: Path, packages: Path) -> tuple[Path, Path] | None:
        """Copy a proven runtime into Studio-owned, ABI-scoped storage."""
        abi_tag = self._python_abi(python)
        if not abi_tag:
            return None
        managed_python, managed_packages = self._managed_opencv_runtime(abi_tag)
        managed_root = managed_python.parents[1]
        if managed_python.is_file() and managed_packages.is_dir():
            return managed_python, managed_packages
        parent = managed_root.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".{abi_tag}.building-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        previous = parent / f".{abi_tag}.invalid-{datetime.now():%Y%m%d%H%M%S}"
        try:
            staging.mkdir(parents=True, exist_ok=False)
            self._copy_python_runtime(python, staging / "python")
            self._copy_opencv_packages(packages, staging / "packages", abi_tag)
            self._validate_opencv_pair(staging / "python" / "python.exe", staging / "packages")
            if managed_root.exists():
                managed_root.rename(previous)
            staging.rename(managed_root)
            shutil.rmtree(previous, ignore_errors=True)
            return managed_python, managed_packages
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            if previous.exists() and not managed_root.exists():
                previous.rename(managed_root)
            return None

    @staticmethod
    def _validate_opencv_pair(python: Path, packages: Path) -> None:
        environment = os.environ.copy()
        for key in ("PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
            environment.pop(key, None)
        environment["PYTHONPATH"] = str(packages)
        probe = (
            "import cv2,numpy,mss; "
            "assert callable(getattr(cv2,'imdecode',None)); "
            "assert callable(getattr(cv2,'matchTemplate',None)); "
            "assert numpy.zeros((1,1)).shape == (1,1)"
        )
        result = subprocess.run(
            [str(python), "-c", probe],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "cv2 모듈 로드 실패").strip().splitlines()[-1]
            raise RuntimeError(detail)

    def _opencv_runtime_candidates(self) -> list[tuple[Path, Path]]:
        candidates: list[tuple[Path, Path]] = []
        managed_root = self.root / "runtime" / "opencv"
        for abi_dir in sorted(managed_root.glob("cp*"), reverse=True):
            candidates.append((abi_dir / "python" / "python.exe", abi_dir / "packages"))
        exported = sorted(
            self.exports_dir.glob("*-portable"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        for bundle in exported:
            candidates.append((bundle / "runtime" / "python.exe", bundle / "runtime_packages"))
        python = Path(sys.executable)
        if python.name.casefold() == "pythonw.exe":
            console = python.with_name("python.exe")
            if console.is_file():
                python = console
        candidates.extend(
            [
                (python, self.root / "runtime_packages" / f"cp{sys.version_info.major}{sys.version_info.minor}"),
                (python, self.root / "runtime_packages"),
                (python, self.root / ".venv" / "Lib" / "site-packages"),
                (python, Path(sys.base_prefix) / "Lib" / "site-packages"),
            ]
        )
        unique: list[tuple[Path, Path]] = []
        for executable, packages in candidates:
            pair = (executable.resolve(), packages.resolve())
            if executable.is_file() and packages.is_dir() and pair not in unique:
                unique.append(pair)
        return unique

    def _ensure_opencv_runtime(self) -> tuple[Path, Path]:
        marker = self.root / ".component-installing"
        if marker.exists():
            try:
                installing = marker.read_text(encoding="utf-8-sig").strip()
            except OSError:
                installing = "구성요소"
            if "OpenCV" in installing:
                raise RuntimeError("OpenCV를 설치하고 있습니다. 설치 완료 메시지가 나온 뒤 다시 실행하세요.")

        if self._opencv_runtime is not None:
            python, packages = self._opencv_runtime
            if python.is_file() and packages.is_dir():
                return self._opencv_runtime

        probe = (
            "import cv2,numpy,mss; "
            "assert callable(getattr(cv2,'imdecode',None)); "
            "assert callable(getattr(cv2,'matchTemplate',None)); "
            "assert numpy.zeros((1,1)).shape == (1,1)"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        failures: list[str] = []
        for python, packages in self._opencv_runtime_candidates():
            environment = os.environ.copy()
            for key in ("PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
                environment.pop(key, None)
            environment["PYTHONPATH"] = str(packages)
            try:
                result = subprocess.run(
                    [str(python), "-c", probe],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=12,
                    creationflags=flags,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append(f"{python}: {exc}")
                continue
            if result.returncode == 0:
                self._opencv_probe_ok = True
                managed_root = (self.root / "runtime" / "opencv").resolve()
                try:
                    python.resolve().relative_to(managed_root)
                    is_managed = True
                except ValueError:
                    is_managed = False
                promoted = None if is_managed else self._promote_opencv_runtime(python, packages)
                self._opencv_runtime = promoted or (python, packages)
                return self._opencv_runtime
            detail = (result.stderr or result.stdout or "cv2 모듈 로드 실패").strip().splitlines()[-1]
            failures.append(f"{python}: {detail}")
        self._opencv_probe_ok = False
        detail = failures[-1] if failures else "사용 가능한 Python/OpenCV 런타임 없음"
        raise RuntimeError(
            "OpenCV 구성요소가 완전히 설치되지 않았습니다. "
            "설정 > 구성요소 설치에서 OpenCV를 다시 설치한 뒤 완료 메시지를 확인하세요. "
            f"({detail})"
        )

    def _read_text_path(self, filename: str) -> Path | None:
        path = self.root / filename
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8-sig").strip()
        return Path(value) if value else None

    def referenced_tables(self, steps: Iterable[dict[str, Any]]) -> set[str]:
        return {str(step.get("table")) for step in steps if step.get("table")}
