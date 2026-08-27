"""Shared configuration and HTTP helpers for MacroRelay Remote."""

from __future__ import annotations

import json
import os
import platform
import secrets
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


DEFAULT_RELAY_URL = "http://127.0.0.1:8765"


def bundled_cloud_url(root: Path | None = None) -> str:
    path = (root or Path(__file__).resolve().parent) / "remote" / "endpoint.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    value = str(payload.get("relay_url") or "").strip().rstrip("/") if isinstance(payload, dict) else ""
    return value if value.startswith("https://") else ""


def config_path(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parent) / "remote_config.json"


def default_config() -> dict[str, Any]:
    return {
        # New installations connect as soon as Studio opens. Users can still
        # turn mobile access off explicitly in Settings.
        "enabled": True,
        "relay_url": DEFAULT_RELAY_URL,
        "prefer_cloud": True,
        "device_name": platform.node() or "MacroRelay PC",
        "device_id": uuid.uuid4().hex,
        "device_secret": secrets.token_urlsafe(32),
        "allow_remote_run": True,
        "allow_remote_stop": True,
        "allowed_macros": [],
    }


def load_config(root: Path | None = None, create: bool = True) -> dict[str, Any]:
    path = config_path(root)
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError):
            payload = {}
    changed = False
    for key, value in default_config().items():
        if key not in payload:
            payload[key] = value
            changed = True
    cloud_url = bundled_cloud_url(root)
    configured_url = str(payload.get("relay_url") or "").rstrip("/")
    if payload.get("prefer_cloud", True) and cloud_url and configured_url in {"", DEFAULT_RELAY_URL}:
        payload["relay_url"] = cloud_url
        payload["enabled"] = True
        changed = True
    if create and (changed or not path.exists()):
        save_config(payload, root)
    return payload


def save_config(payload: dict[str, Any], root: Path | None = None) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return path


def request_json(
    relay_url: str,
    method: str,
    route: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    base = str(relay_url or DEFAULT_RELAY_URL).rstrip("/")
    url = base + (route if route.startswith("/") else "/" + route)
    data = None
    merged_headers = {"Accept": "application/json", "User-Agent": "MacroRelay-Remote/1"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        merged_headers["Content-Type"] = "application/json; charset=utf-8"
    merged_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=merged_headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {"ok": False, "error": "invalid_response"}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"error": raw or str(exc)}
        parsed.setdefault("ok", False)
        parsed.setdefault("status", exc.code)
        return parsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"ok": False, "error": "connection_failed", "detail": str(reason)}


def agent_headers(config: dict[str, Any]) -> dict[str, str]:
    return {
        "X-MacroRelay-Device": str(config.get("device_id") or ""),
        "X-MacroRelay-Secret": str(config.get("device_secret") or ""),
    }


def post_agent_event(
    config: dict[str, Any],
    event_type: str,
    message: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    return request_json(
        str(config.get("relay_url") or DEFAULT_RELAY_URL),
        "POST",
        "/api/agent/events",
        {"type": event_type, "message": message, "payload": payload or {}},
        agent_headers(config),
        timeout,
    )
