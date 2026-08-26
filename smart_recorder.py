#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import sys
import time


if sys.platform != "win32":
    raise SystemExit("Smart recording is supported on Windows only.")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_MOUSEWHEEL = 0x020A
VK_F8 = 0x77
VK_F10 = 0x79
VK_OEM_3 = 0xC0  # ` / ~ key on standard Windows keyboard layouts
SHIFT_KEYS = {0x10, 0xA0, 0xA1}
LLKHF_INJECTED = 0x10


ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
HHOOK = ctypes.c_void_p
HWND = ctypes.c_void_p

user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.restype = LRESULT
user32.WindowFromPoint.restype = HWND
user32.GetForegroundWindow.restype = HWND
user32.GetAncestor.restype = HWND
kernel32.GetModuleHandleW.restype = ctypes.c_void_p


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.argtypes = [HWND, wintypes.UINT]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyboardState.argtypes = [ctypes.c_void_p]
user32.ToUnicodeEx.argtypes = [
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    wintypes.LPWSTR,
    ctypes.c_int,
    wintypes.UINT,
    ctypes.c_void_p,
]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetDC.argtypes = [HWND]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [HWND, ctypes.c_void_p]
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.BitBlt.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
gdi32.GetDIBits.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def enable_dpi_awareness() -> None:
    """Keep hook, window and capture coordinates in the same physical-pixel space."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def capture_click_sample(x: int, y: int, width: int = 360, height: int = 240) -> str:
    """Capture a small pre-click BMP through GDI; returned data only lives in the temporary recording."""
    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return ""
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height) if memory_dc else None
    old_object = None
    try:
        if not memory_dc or not bitmap:
            return ""
        old_object = gdi32.SelectObject(memory_dc, bitmap)
        left = int(x) - width // 2
        top = int(y) - height // 2
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, left, top, 0x40CC0020):
            return ""
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down pixels
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        pixel_size = width * height * 4
        pixels = ctypes.create_string_buffer(pixel_size)
        if gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            return ""
        file_header = struct.pack("<2sIHHI", b"BM", 14 + 40 + pixel_size, 0, 0, 54)
        dib_header = bytes(info.bmiHeader)
        return base64.b64encode(file_header + dib_header + pixels.raw).decode("ascii")
    finally:
        if old_object and memory_dc:
            gdi32.SelectObject(memory_dc, old_object)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def window_details(hwnd: int) -> dict[str, object]:
    if not hwnd:
        return {}
    root = int(user32.GetAncestor(hwnd, 2) or hwnd)
    title_buffer = ctypes.create_unicode_buffer(2048)
    user32.GetWindowTextW(root, title_buffer, len(title_buffer))
    class_buffer = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(root, class_buffer, len(class_buffer))
    pid = wintypes.DWORD()
    thread_id = int(user32.GetWindowThreadProcessId(root, ctypes.byref(pid)) or 0)
    executable = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        try:
            size = wintypes.DWORD(32768)
            path_buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
                executable = Path(path_buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    rect = wintypes.RECT()
    client = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    user32.GetWindowRect(root, ctypes.byref(rect))
    user32.GetClientRect(root, ctypes.byref(client))
    user32.ClientToScreen(root, ctypes.byref(origin))
    return {
        "hwnd": root,
        "pid": int(pid.value),
        "thread": thread_id,
        "title": title_buffer.value,
        "class": class_buffer.value,
        "exe": executable,
        "window_rect": [rect.left, rect.top, rect.right, rect.bottom],
        "client_origin": [origin.x, origin.y],
        "client_size": [client.right - client.left, client.bottom - client.top],
    }


SPECIAL_KEYS = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x1B: "Escape",
    0x20: " ",
    0x21: "PgUp",
    0x22: "PgDn",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2E: "Delete",
}
MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def key_value(vk: int, scan: int, thread_id: int) -> tuple[str, str]:
    if vk in SPECIAL_KEYS:
        value = SPECIAL_KEYS[vk]
        return (value if len(value) == 1 else "", value)
    state = (ctypes.c_ubyte * 256)()
    user32.GetKeyboardState(ctypes.byref(state))
    state[vk] |= 0x80
    buffer = ctypes.create_unicode_buffer(8)
    layout = user32.GetKeyboardLayout(thread_id)
    count = user32.ToUnicodeEx(vk, scan, ctypes.byref(state), buffer, len(buffer), 0, layout)
    if count > 0:
        return buffer.value[:count], buffer.value[:count]
    return "", f"VK_{vk:02X}"


class Recorder:
    def __init__(
        self,
        output: Path,
        exclude_pid: int,
        delay: float,
        capture_vk: int = VK_F8,
        stop_vk: int = VK_F10,
        hold_vk: int = VK_OEM_3,
    ) -> None:
        self.output = output
        self.exclude_pid = exclude_pid
        self.delay = max(0.0, delay)
        self.capture_vk = int(capture_vk)
        self.stop_vk = int(stop_vk)
        self.hold_vk = int(hold_vk)
        self.gate_down = False
        self.record_mode = "action"
        self._mode_key_down = False
        self._shift_down = False
        self.started = time.perf_counter()
        self.mouse_hook = None
        self.keyboard_hook = None
        self.mouse_callback = HOOKPROC(self._mouse_proc)
        self.keyboard_callback = HOOKPROC(self._keyboard_proc)
        self.handle = None

    def emit(self, payload: dict[str, object]) -> None:
        if not self.gate_down:
            return
        if time.perf_counter() - self.started < self.delay:
            return
        window = payload.get("window")
        if isinstance(window, dict) and int(window.get("pid") or 0) == self.exclude_pid:
            return
        payload["t"] = round((time.perf_counter() - self.started - self.delay) * 1000)
        payload["record_mode"] = self.record_mode
        assert self.handle is not None
        self.handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.handle.flush()

    def set_gate_active(self, active: bool) -> None:
        normalized = bool(active)
        if normalized == self.gate_down:
            return
        self.gate_down = normalized
        self.emit_control(
            {"type": "gate_state", "active": normalized, "mode": self.record_mode, "vk": self.hold_vk}
        )

    def set_record_mode(self, mode: str) -> None:
        normalized = "branch" if str(mode).lower() == "branch" else "action"
        if normalized == self.record_mode:
            return
        self.record_mode = normalized
        self.emit_control(
            {"type": "mode_state", "active": self.gate_down, "mode": normalized, "vk": self.hold_vk}
        )

    def cycle_record_mode(self) -> None:
        self.set_record_mode("branch" if self.record_mode == "action" else "action")

    def emit_control(self, payload: dict[str, object]) -> None:
        """Write recorder controls even during countdown or over Studio UI.

        F8 used to be discarded by the normal event filter when pressed during
        the first two seconds or while the recording bar had focus.
        """
        if self.handle is None:
            return
        payload["t"] = max(0, round((time.perf_counter() - self.started - self.delay) * 1000))
        payload["request_id"] = f"{time.perf_counter_ns()}-{int(payload.get('vk') or 0)}"
        self.handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.handle.flush()

    def _mouse_proc(self, code: int, message: int, data_ptr: int) -> int:
        if code == HC_ACTION and message in {WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL}:
            if not self.gate_down:
                return int(user32.CallNextHookEx(self.mouse_hook, code, message, data_ptr))
            data = ctypes.cast(data_ptr, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            hwnd = int(user32.WindowFromPoint(data.pt) or 0)
            details = window_details(hwnd)
            button = {
                WM_LBUTTONDOWN: "Left",
                WM_RBUTTONDOWN: "Right",
                WM_MBUTTONDOWN: "Middle",
            }.get(int(message), "WheelUp")
            if message == WM_MOUSEWHEEL:
                delta = ctypes.c_short((int(data.mouseData) >> 16) & 0xFFFF).value
                button = "WheelUp" if delta > 0 else "WheelDown"
            origin = details.get("client_origin") if isinstance(details, dict) else None
            client_x = data.pt.x - int(origin[0]) if isinstance(origin, list) and len(origin) >= 2 else data.pt.x
            client_y = data.pt.y - int(origin[1]) if isinstance(origin, list) and len(origin) >= 2 else data.pt.y
            sample_width, sample_height = 360, 240
            sample = (
                ""
                if message == WM_MOUSEWHEEL
                else capture_click_sample(int(data.pt.x), int(data.pt.y), sample_width, sample_height)
            )
            self.emit(
                {
                    "type": "mouse",
                    "button": button,
                    "x": int(data.pt.x),
                    "y": int(data.pt.y),
                    "client_x": int(client_x),
                    "client_y": int(client_y),
                    "window": details,
                    "image_sample_bmp": sample,
                    "image_sample_size": [sample_width, sample_height],
                    "image_anchor": [sample_width // 2, sample_height // 2],
                }
            )
        return int(user32.CallNextHookEx(self.mouse_hook, code, message, data_ptr))

    def _keyboard_proc(self, code: int, message: int, data_ptr: int) -> int:
        if code == HC_ACTION and message in {WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP}:
            data = ctypes.cast(data_ptr, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(data.vkCode)
            injected = bool(int(data.flags) & LLKHF_INJECTED)
            pressed = message in {WM_KEYDOWN, WM_SYSKEYDOWN}
            if vk in SHIFT_KEYS and not injected:
                self._shift_down = pressed
            if vk == self.hold_vk and not injected:
                if pressed and not self._mode_key_down:
                    if self._shift_down:
                        self.cycle_record_mode()
                    else:
                        self.set_gate_active(not self.gate_down)
                self._mode_key_down = pressed
                # Recorder control keys never reach the target app or become
                # recorded key actions.
                return 1
            if message not in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                return int(user32.CallNextHookEx(self.keyboard_hook, code, message, data_ptr))
            if vk == self.stop_vk:
                user32.PostQuitMessage(0)
                return 1
            if vk == self.capture_vk:
                if self.gate_down:
                    details = window_details(int(user32.GetForegroundWindow() or 0))
                    self.emit_control(
                        {
                            "type": "capture_request",
                            "window": details,
                            "mode": self.record_mode,
                            "vk": vk,
                        }
                    )
                return 1
            if self.gate_down and vk not in MODIFIERS and not injected:
                details = window_details(int(user32.GetForegroundWindow() or 0))
                char, token = key_value(vk, int(data.scanCode), int(details.get("thread") or 0))
                self.emit(
                    {
                        "type": "key",
                        "vk": vk,
                        "char": char,
                        "token": token,
                        "window": details,
                    }
                )
        return int(user32.CallNextHookEx(self.keyboard_hook, code, message, data_ptr))

    def run(self) -> int:
        enable_dpi_awareness()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.open("w", encoding="utf-8") as self.handle:
            module = kernel32.GetModuleHandleW(None)
            self.mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self.mouse_callback, module, 0)
            self.keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.keyboard_callback, module, 0)
            if not self.mouse_hook or not self.keyboard_hook:
                return 2
            self.handle.write(
                json.dumps(
                    {
                        "type": "meta",
                        "state": "ready",
                        "pid": os.getpid(),
                        "hold_vk": self.hold_vk,
                        "gate_active": self.gate_down,
                        "record_mode": self.record_mode,
                    }
                )
                + "\n"
            )
            self.handle.flush()
            message = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
            finally:
                if self.mouse_hook:
                    user32.UnhookWindowsHookEx(self.mouse_hook)
                if self.keyboard_hook:
                    user32.UnhookWindowsHookEx(self.keyboard_hook)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--exclude-pid", type=int, default=os.getpid())
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--capture-vk", type=int, default=VK_F8)
    parser.add_argument("--stop-vk", type=int, default=VK_F10)
    parser.add_argument("--hold-vk", type=int, default=VK_OEM_3)
    args = parser.parse_args()
    return Recorder(args.out.resolve(), args.exclude_pid, args.delay, args.capture_vk, args.stop_vk, args.hold_vk).run()


if __name__ == "__main__":
    raise SystemExit(main())
