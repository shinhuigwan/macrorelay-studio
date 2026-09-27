"""Local Studio element-picking connection to the installed browser extension."""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .deck_browser_bridge import DeckBrowserBridge


STUDIO_ELEMENT_PORT = 18774
RUNTIME_ELEMENT_PORT = 18775
SUPPORTED_BROWSERS = {"whale": "whale.exe", "chrome": "chrome.exe", "edge": "msedge.exe"}


def load_connection_token(root: Path) -> str:
    """Reuse the Deck's local token without displaying or exporting it."""
    try:
        payload = json.loads((Path(root) / ".quickslot_deck_config.json").read_text(encoding="utf-8"))
        return str((payload.get("config") or {}).get("auto_preset_token") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def focus_browser(browser: str) -> bool:
    """Activate an already-open browser so its MV3 extension wakes and polls."""
    if os.name != "nt" or browser not in SUPPORTED_BROWSERS:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    expected = SUPPORTED_BROWSERS[browser]
    candidates: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                      wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    title_suffix = {"whale": "- Whale", "chrome": "- Google Chrome", "edge": "- Microsoft Edge"}[browser]

    def inspect(hwnd: int, _param: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        class_name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if class_name.value != "Chrome_WidgetWin_1":
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        matched = False
        if handle:
            try:
                path = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(path))
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    if Path(path.value).name.casefold() == expected:
                        matched = True
            finally:
                kernel32.CloseHandle(handle)
        if not matched:
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            matched = title.value.endswith(title_suffix)
        if matched:
            candidates.append(int(hwnd))
        return True

    user32.EnumWindows(callback_type(inspect), 0)
    if not candidates:
        return False
    foreground = int(user32.GetForegroundWindow() or 0)
    hwnd = foreground if foreground in candidates else candidates[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return int(user32.GetForegroundWindow() or 0) == hwnd


def open_studio_bridge(root: Path, *, port: int = STUDIO_ELEMENT_PORT) -> DeckBrowserBridge:
    token = load_connection_token(root)
    if not token:
        raise RuntimeError("덕덱 환경 설정 → 프리셋 자동 전환의 연결 코드를 확장앱에 먼저 저장해 주세요.")
    try:
        return DeckBrowserBridge(token, port=port)
    except OSError as exc:
        raise RuntimeError(f"다른 브라우저 요소 작업이 열려 있거나 로컬 포트 {port}를 사용 중입니다.") from exc
