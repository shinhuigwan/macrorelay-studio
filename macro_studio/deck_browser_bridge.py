"""Opt-in, loopback-only URL events for QuickSlot preset switching."""

from __future__ import annotations

import ipaddress
import ctypes
import json
import os
import queue
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import urlsplit


BRIDGE_PORT = 18773


def normalize_site_rule(value: str) -> str:
    """Accept a hostname (optionally with a path), never a substring match."""
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("사이트 주소가 비어 있습니다.")
    parsed = urlsplit(raw if "://" in raw else "https://" + raw)
    host = (parsed.hostname or "").rstrip(".")
    if not host or any(char.isspace() for char in host) or parsed.username or parsed.password:
        raise ValueError("올바른 사이트 주소를 입력해 주세요.")
    if parsed.port or parsed.query or parsed.fragment:
        raise ValueError("포트·검색어·# 조각 없이 도메인과 경로만 입력해 주세요.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or any(not part or not part.replace("-", "").isalnum() for part in host.split(".")):
            raise ValueError("도메인 형식이 올바르지 않습니다.") from None
    path = parsed.path.rstrip("/")
    return host + path


def preset_for_url(url: str, rules: list[dict[str, Any]]) -> str:
    """Return the most specific exact-host/path rule; ambiguity keeps current preset."""
    try:
        parsed = urlsplit(str(url or ""))
        if parsed.scheme not in {"http", "https"}:
            return ""
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path or "/"
    except ValueError:
        return ""
    matches: list[tuple[int, str]] = []
    for rule in rules:
        try:
            pattern = normalize_site_rule(str(rule.get("site") or ""))
        except (ValueError, AttributeError):
            continue
        rule_host, _, rule_path = pattern.partition("/")
        if host != rule_host and not host.endswith("." + rule_host):
            continue
        rule_path = "/" + rule_path if rule_path else ""
        if rule_path and path != rule_path and not path.startswith(rule_path + "/"):
            continue
        preset_id = str(rule.get("preset_id") or "")
        if preset_id:
            matches.append((len(rule_host) + len(rule_path), preset_id))
    if not matches:
        return ""
    best = max(score for score, _ in matches)
    winners = {preset_id for score, preset_id in matches if score == best}
    return next(iter(winners)) if len(winners) == 1 else ""


def normalize_program_rule(value: str) -> str:
    name = PureWindowsPath(str(value or "").strip()).name.casefold()
    if not name.endswith(".exe") or not name[:-4] or any(char.isspace() for char in name):
        raise ValueError("프로그램 파일명은 whale.exe처럼 입력해 주세요.")
    return name


def preset_for_program(executable: str, rules: list[dict[str, Any]]) -> str:
    try:
        name = normalize_program_rule(executable)
    except ValueError:
        return ""
    matches = {str(rule.get("preset_id") or "") for rule in rules
               if isinstance(rule, dict) and str(rule.get("exe") or "").casefold() == name}
    matches.discard("")
    return next(iter(matches)) if len(matches) == 1 else ""


def foreground_program() -> tuple[str, int]:
    """Return the foreground executable basename and PID without activation."""
    if os.name != "nt":
        return "", 0
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return "", 0
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return "", pid.value
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return "", pid.value
            return PureWindowsPath(buffer.value).name.casefold(), pid.value
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return "", 0


class DeckBrowserBridge:
    """Tiny HTTP receiver; handler threads never touch Qt widgets."""

    def __init__(self, token: str, port: int = BRIDGE_PORT):
        self.token = token
        self.events: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=32)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_OPTIONS(self) -> None:
                self._reply(204)

            def _reply(self, status: int) -> None:
                origin = self.headers.get("Origin", "")
                self.send_response(status)
                if origin.startswith("chrome-extension://"):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MacroRelay-Token")
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:
                origin = self.headers.get("Origin", "")
                if (self.path != "/active-tab" or
                        (origin and not origin.startswith("chrome-extension://")) or
                        self.headers.get("X-MacroRelay-Token") != bridge.token):
                    self._reply(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("invalid length")
                    payload = json.loads(self.rfile.read(length))
                    url = str(payload.get("url") or "")
                    if urlsplit(url).scheme not in {"http", "https"}:
                        raise ValueError("invalid URL")
                    observed_at = int(payload.get("observedAt") or int(time.time() * 1000))
                    try:
                        bridge.events.put_nowait((observed_at, url))
                    except queue.Full:
                        try:
                            bridge.events.get_nowait()
                        except queue.Empty:
                            pass
                        bridge.events.put_nowait((observed_at, url))
                    self._reply(204)
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._reply(400)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="DeckBrowserBridge", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
