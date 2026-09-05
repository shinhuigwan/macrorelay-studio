from __future__ import annotations

import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__


SENSITIVE_PARTS = ("secret", "token", "password", "authorization", "key_password", "store_password")
TEXT_SECRET_PATTERNS = (
    re.compile(r'(?i)("(?:device_secret|token|password|authorization)"\s*:\s*")[^"]*(")'),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
)


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).casefold()
    return any(part in lowered for part in SENSITIVE_PARTS)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if _is_sensitive_key(key) else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def redact_text(text: str) -> str:
    result = text
    for pattern in TEXT_SECRET_PATTERNS:
        result = pattern.sub(lambda match: match.group(1) + "[REDACTED]" + (match.group(2) if match.lastindex and match.lastindex >= 2 else ""), result)
    return result


def _read_tail(path: Path, limit: int = 512 * 1024) -> str:
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - limit))
        raw = handle.read()
    return redact_text(raw.decode("utf-8-sig", errors="replace"))


def _existing(paths: Iterable[Path]) -> list[Path]:
    return [path for path in paths if path.is_file()]


def build_diagnostic_bundle(root: Path, destination: Path, screen_info: list[dict[str, Any]] | None = None) -> Path:
    """Create a privacy-filtered support bundle without macro or image assets."""
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    logs = _existing(
        (
            root / "exports" / "studio_run.log",
            root / "exports" / "macro_log.txt",
            root / "exports" / "execution_trace.log",
            root / "exports" / "browser_action_log.txt",
            root / "exports" / "inactive_click_test.log",
            root / "runtime" / "vision_engine.log",
            root / "runtime" / "ocr_engine.log",
            root / "runtime" / "runner.log",
        )
    )
    result_dir = root / "exports" / ".run_results"
    recent_results = (
        sorted(result_dir.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)[:20]
        if result_dir.is_dir()
        else []
    )
    system = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "studio_version": __version__,
        "python": sys.version,
        "python_executable": Path(sys.executable).name,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "screens": screen_info or [],
        "components": {
            "opencv_runtime": (root / "runtime" / "opencv").is_dir(),
            "ocr_engine": (root / "ocr_engine.py").is_file(),
            "vision_engine": (root / "vision_engine.py").is_file(),
            "autohotkey_configured": (root / "ahk_path.txt").is_file(),
        },
    }
    remote_config: dict[str, Any] = {}
    try:
        loaded = json.loads((root / "remote_config.json").read_text(encoding="utf-8-sig"))
        if isinstance(loaded, dict):
            remote_config = sanitize(loaded)
    except (OSError, ValueError):
        pass

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("system.json", json.dumps(system, ensure_ascii=False, indent=2))
            archive.writestr("remote_config.sanitized.json", json.dumps(remote_config, ensure_ascii=False, indent=2))
            for path in logs:
                archive.writestr(f"logs/{path.name}", _read_tail(path))
            for index, path in enumerate(recent_results, start=1):
                archive.writestr(f"run_results/{index:02d}-{path.name}", _read_tail(path, 64 * 1024))
            archive.writestr(
                "README.txt",
                "MacroRelay 진단 자료입니다. 매크로 정의와 이미지 자산은 포함하지 않았으며 "
                "비밀 키와 인증 토큰은 자동으로 제거했습니다.\n",
            )
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination
