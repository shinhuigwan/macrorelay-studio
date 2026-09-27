"""Opt-in, loopback-only URL events for QuickSlot preset switching."""

from __future__ import annotations

import ipaddress
import ctypes
import json
import os
import queue
import secrets
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import parse_qs, urlsplit


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
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=16)
        self.command_results: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=16)
        self.cancelled_commands: set[str] = set()
        self.command_dispatched_at: dict[str, float] = {}
        self.last_command_poll_at = 0.0
        self.last_active_tab_at = 0.0
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_OPTIONS(self) -> None:
                self._reply(204)

            def _reply(self, status: int, payload: bytes = b"") -> None:
                origin = self.headers.get("Origin", "")
                self.send_response(status)
                if origin.startswith("chrome-extension://"):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MacroRelay-Token")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                if payload:
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            def _authorized(self) -> bool:
                origin = self.headers.get("Origin", "")
                return (not origin or origin.startswith("chrome-extension://")) and self.headers.get("X-MacroRelay-Token") == bridge.token

            def do_GET(self) -> None:
                parsed_path = urlsplit(self.path)
                if parsed_path.path != "/next-command" or not self._authorized():
                    self._reply(403)
                    return
                bridge.last_command_poll_at = time.monotonic()
                requester = parse_qs(parsed_path.query).get("browser", [""])[0]
                while True:
                    try:
                        command = bridge.commands.get_nowait()
                    except queue.Empty:
                        self._reply(204)
                        return
                    if command["id"] not in bridge.cancelled_commands:
                        break
                    bridge.cancelled_commands.discard(command["id"])
                if command.get("browser") and command["browser"] != requester:
                    bridge.commands.put_nowait(command)
                    self._reply(204)
                    return
                bridge.command_dispatched_at[command["id"]] = time.monotonic()
                self._reply(200, json.dumps(command).encode("utf-8"))

            def do_POST(self) -> None:
                if self.path not in {"/active-tab", "/command-result"} or not self._authorized():
                    self._reply(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16384:
                        raise ValueError("invalid length")
                    payload = json.loads(self.rfile.read(length))
                    if self.path == "/command-result":
                        result = {"id": str(payload.get("id") or ""),
                                  "status": str(payload.get("status") or ""),
                                  "url": str(payload.get("url") or ""),
                                  "error": str(payload.get("error") or "")[:300],
                                  "result": payload.get("result") if isinstance(payload.get("result"), dict) else {}}
                        if not result["id"] or result["status"] not in {"ok", "error"}:
                            raise ValueError("invalid result")
                        bridge.command_results.put_nowait(result)
                        bridge.command_dispatched_at.pop(result["id"], None)
                        self._reply(204)
                        return
                    url = str(payload.get("url") or "")
                    if urlsplit(url).scheme not in {"http", "https"}:
                        raise ValueError("invalid URL")
                    observed_at = int(payload.get("observedAt") or int(time.time() * 1000))
                    bridge.last_active_tab_at = time.monotonic()
                    try:
                        bridge.events.put_nowait((observed_at, url))
                    except queue.Full:
                        try:
                            bridge.events.get_nowait()
                        except queue.Empty:
                            pass
                        bridge.events.put_nowait((observed_at, url))
                    self._reply(204)
                except (ValueError, TypeError, json.JSONDecodeError, queue.Full):
                    self._reply(400)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="DeckBrowserBridge", daemon=True)
        self.thread.start()

    def request_tab(self, url: str, match: str = "origin_path") -> str:
        return self._queue_tab_command("activate_tab", url, match)

    def request_navigation(self, url: str, match: str = "domain") -> str:
        """Navigate an existing site tab without creating another tab."""
        return self._queue_tab_command("navigate_tab", url, match)

    def request_element_command(self, kind: str, browser: str, *, selector: str = "",
                                page_url: str = "", action: str = "", value: str = "",
                                test_mode: bool = False) -> str:
        if kind not in {"pick_element", "probe_element", "test_element_action", "run_element_action"}:
            raise ValueError("지원하지 않는 브라우저 요소 명령입니다.")
        if browser not in {"whale", "chrome", "edge"}:
            raise ValueError("브라우저를 선택해 주세요.")
        if kind in {"probe_element", "test_element_action", "run_element_action"} and not str(selector).strip():
            raise ValueError("검사할 CSS 선택자가 비어 있습니다.")
        if len(selector) > 2048 or len(page_url) > 2048 or len(value) > 4096:
            raise ValueError("선택자 또는 페이지 주소가 너무 깁니다.")
        if kind in {"test_element_action", "run_element_action"} and action not in {"click", "double_click", "type_text", "extract_text", "hover"}:
            raise ValueError("지원하지 않는 브라우저 요소 동작입니다.")
        command_id = secrets.token_urlsafe(12)
        self.commands.put_nowait({"id": command_id, "kind": kind, "browser": browser,
                                  "selector": selector, "page_url": page_url,
                                  "action": action, "value": value, "test_mode": test_mode})
        return command_id

    def _queue_tab_command(self, kind: str, url: str, match: str) -> str:
        parsed = urlsplit(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or len(url) > 2048:
            raise ValueError("브라우저 주소는 http 또는 https URL이어야 합니다.")
        if match not in {"domain", "origin_path"}:
            raise ValueError("지원하지 않는 탭 일치 방식입니다.")
        command_id = secrets.token_urlsafe(12)
        self.commands.put_nowait({"id": command_id, "kind": kind, "url": url, "match": match})
        return command_id

    def cancel_command(self, command_id: str) -> None:
        self.cancelled_commands.add(str(command_id))
        self.command_dispatched_at.pop(str(command_id), None)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
