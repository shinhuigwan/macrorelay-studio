#!/usr/bin/env python3
"""AutoHotkey macro builder for Ulando Stream Deck buttons."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parent
MACRO_DIR = BASE_DIR / "macros"
ASSET_DIR = BASE_DIR / "assets"
EXPORT_DIR = BASE_DIR / "exports"
ASSET_INDEX = ASSET_DIR / "index.json"
DATA_TABLES_FILE = BASE_DIR / "data_tables.json"

ACTIONS = [
    "mouse_click",
    "inactive_click",
    "image_search",
    "browser_action",
    "ocr",
    "flow_control",
    "table_store",
    "table_copy",
    "table_paste",
    "table_excel_read",
    "table_excel_write",
    "calc_var",
    "type_text",
    "wait",
    "set_var",
    "coord_mode",
    "run_program",
    "terminate_program",
    "remote_notify",
    "call_submacro",
    "text_condition",
]


def col_to_index(col: str) -> int:
    col = (col or "").strip().upper()
    if not col:
        return 0
    total = 0
    for char in col:
        if not ("A" <= char <= "Z"):
            return 0
        total = total * 26 + (ord(char) - ord("A") + 1)
    return total


def parse_col_list(text: str) -> List[int]:
    raw = (text or "").replace(" ", "")
    parts = [part for part in raw.split(",") if part]
    cols: List[int] = []
    for part in parts:
        idx = col_to_index(part)
        if idx:
            cols.append(idx)
    return cols


def col_range(start: str, end: str) -> List[int]:
    start_idx = col_to_index(start)
    end_idx = col_to_index(end)
    if start_idx <= 0:
        return []
    if end_idx <= 0:
        end_idx = start_idx
    if end_idx < start_idx:
        start_idx, end_idx = end_idx, start_idx
    return list(range(start_idx, end_idx + 1))


def _selection_key(table: str) -> str:
    raw = (table or "").strip()
    if not raw:
        return "default"
    safe = "".join(ch if (ch.isascii() and ch.isalnum()) else "_" for ch in raw)
    safe_base = safe.strip("_")
    if safe_base and safe_base == raw and safe_base.isascii():
        return safe_base
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    if not safe_base:
        return f"t_{digest}"
    return f"{safe_base}_{digest}"


def ensure_environment() -> None:
    """Create expected directories and placeholder files."""
    for folder in (MACRO_DIR, ASSET_DIR, EXPORT_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    if not ASSET_INDEX.exists():
        ASSET_INDEX.write_text("{}", encoding="utf-8")


def slugify(name: str) -> str:
    """Map a display name to a filesystem-safe identifier."""
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in name.strip())
    cleaned = cleaned.strip("-")
    return cleaned or "macro"


def macro_path(name: str) -> Path:
    candidate = MACRO_DIR / f"{slugify(name)}.json"
    if candidate.exists():
        return candidate
    direct = Path(name)
    if direct.exists():
        return direct
    return candidate


def load_data_tables() -> Dict[str, List[List[str]]]:
    if not DATA_TABLES_FILE.exists():
        return {}
    try:
        data = json.loads(DATA_TABLES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    tables: Dict[str, List[List[str]]] = {}
    for key, value in data.items():
        if not isinstance(value, list):
            continue
        if value and all(not isinstance(item, list) for item in value):
            rows = [[str(item)] for item in value]
        else:
            rows = []
            for row in value:
                if isinstance(row, list):
                    rows.append([str(cell) for cell in row])
                else:
                    rows.append([str(row)])
        tables[str(key)] = rows
    return tables


def load_json_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_file(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_param_list(params: Optional[Iterable[str]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for entry in params or []:
        if "=" not in entry:
            raise ValueError(f"invalid param {entry!r}, format key=value is required")
        key, raw_value = entry.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        lowered = raw_value.lower()
        if lowered in ("true", "false"):
            value: Any = lowered == "true"
        else:
            try:
                value = ast.literal_eval(raw_value)
            except (ValueError, SyntaxError):
                value = raw_value
        result[key] = value
    return result


def read_assets() -> Dict[str, Dict[str, Any]]:
    if not ASSET_INDEX.exists():
        return {}
    return load_json_file(ASSET_INDEX)


def write_assets(index: Dict[str, Dict[str, Any]]) -> None:
    save_json_file(ASSET_INDEX, index)


def register_asset(source: Path, alias: Optional[str], force: bool) -> str:
    index = read_assets()
    alias_final = alias or slugify(source.stem)
    suffix = source.suffix or ".png"
    target = ASSET_DIR / f"{alias_final}{suffix}"
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists, use --force to overwrite")
    shutil.copy2(source, target)
    record = {
        "file": str(target.relative_to(BASE_DIR)),
        "original": str(source),
        "added_at": datetime.utcnow().isoformat() + "Z",
        "size": target.stat().st_size,
    }
    index[alias_final] = record
    write_assets(index)
    return alias_final


def remove_asset(alias: str) -> None:
    index = read_assets()
    record = index.pop(alias, None)
    if not record:
        raise FileNotFoundError(f"asset {alias} not found")
    target = BASE_DIR / record["file"]
    if target.exists():
        target.unlink()
    write_assets(index)


def create_macro(name: str, description: str, coord_mode: str) -> Path:
    target = macro_path(name)
    if target.exists():
        raise FileExistsError(f"{target.name} already exists")
    payload = {
        "name": name,
        "description": description or "",
        "meta": {"coord_mode": coord_mode},
        "steps": [],
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    save_json_file(target, payload)
    return target


def add_step_to_macro(macro_file: Path, action: str, step_data: Dict[str, Any], label: Optional[str]) -> None:
    payload = load_json_file(macro_file)
    step = {"action": action}
    step.update(step_data)
    if label:
        step["label"] = label
    payload.setdefault("steps", []).append(step)
    payload.setdefault("meta", {})["last_modified"] = datetime.utcnow().isoformat() + "Z"
    save_json_file(macro_file, payload)


def list_macros() -> List[Path]:
    return sorted(MACRO_DIR.glob("*.json"))


def build_macro_header(macro: Dict[str, Any]) -> List[str]:
    meta = macro.get("meta", {})
    coord_mode = meta.get("coord_mode", "Screen")
    name = macro.get("name", "macro")
    python_path = Path(sys.executable) if sys.executable else None
    if python_path is not None and python_path.name.lower() == "pythonw.exe":
        console_python = python_path.with_name("python.exe")
        if console_python.exists():
            python_path = console_python
    python_cmd = str(python_path or "py -3")
    return [
        "; Auto-generated by macro_tool",
        "#SingleInstance Force",
        "#NoEnv",
        "#MaxThreadsPerHotkey 1",
        "ListLines, Off",
        "SetBatchLines, -1",
        "DetectHiddenWindows, On",
        "DetectHiddenText, On",
        "SetWinDelay, 0",
        "SetControlDelay, 0",
        "SetMouseDelay, -1",
        "SendMode Input",
        "FileEncoding, UTF-8",
        "SetWorkingDir %A_ScriptDir%",
        '; Keep screen, window and client coordinates in physical pixels on mixed-DPI monitors.',
        'DllCall("SetThreadDpiAwarenessContext", "ptr", -4, "ptr")',
        f"CoordMode, Mouse, {coord_mode}",
        f'MacroMouseCoordMode := "{coord_mode}"',
        "CoordMode, Pixel, Screen",
        "SysGet, VirtualLeft, 76",
        "SysGet, VirtualTop, 77",
        "SysGet, VirtualWidth, 78",
        "SysGet, VirtualHeight, 79",
        "VirtualRight := VirtualLeft + VirtualWidth - 1",
        "VirtualBottom := VirtualTop + VirtualHeight - 1",
        'PythonExe := A_ScriptDir . "\\runtime\\python.exe"',
        "EnvGet, ManagedPythonExe, MACRORELAY_PYTHON_EXE",
        "if (ManagedPythonExe != \"\" and FileExist(ManagedPythonExe))",
        "    PythonExe := ManagedPythonExe",
        "if !FileExist(PythonExe)",
        f'    PythonExe := "{python_cmd.replace(chr(34), chr(34) * 2)}"',
        'MacroPackages := A_ScriptDir . "\\runtime_packages"',
        'if !FileExist(MacroPackages . "\\numpy")',
        '    MacroPackages := A_ScriptDir . "\\..\\runtime_packages"',
        "EnvGet, ManagedPythonPackages, MACRORELAY_PYTHON_PACKAGES",
        "if (ManagedPythonPackages != \"\" and FileExist(ManagedPythonPackages . \"\\numpy\"))",
        "    MacroPackages := ManagedPythonPackages",
        'if FileExist(MacroPackages . "\\cv2")',
        "{",
        "    EnvGet, ExistingPythonPath, PYTHONPATH",
        '    MacroPythonPath := MacroPackages . (ExistingPythonPath != "" ? ";" . ExistingPythonPath : "")',
        "    EnvSet, PYTHONPATH, %MacroPythonPath%",
        "}",
        "LogFile := A_ScriptDir . \"\\macro_log.txt\"",
        "Log(msg) {",
        "    global LogFile",
        "    FormatTime, ts,, yyyy-MM-dd HH:mm:ss",
        "    FileAppend, % ts \" | \" msg \"`n\", %LogFile%",
        "}",
        "EnvGet, MacroRunResultFile, MACRORELAY_RESULT_FILE",
        "EnvGet, MacroRunProgressFile, MACRORELAY_PROGRESS_FILE",
        "EnvGet, MacroRunClickFile, MACRORELAY_CLICK_FILE",
        'MacroRunStatus := "RUNNING"',
        "SetRunProgress(step) {",
        "    global MacroRunProgressFile",
        "    if (MacroRunProgressFile = \"\")",
        "        return",
        "    FileDelete, %MacroRunProgressFile%",
        "    FileAppend, %step%, %MacroRunProgressFile%, UTF-8",
        "}",
        "SetRunResult(status, code, message) {",
        "    global MacroRunResultFile, MacroRunStatus",
        "    MacroRunStatus := status",
        "    if (MacroRunResultFile = \"\")",
        "        return",
        "    StringReplace, cleanMessage, message, |, /, All",
        "    StringReplace, cleanMessage, cleanMessage, `r, %A_Space%, All",
        "    StringReplace, cleanMessage, cleanMessage, `n, %A_Space%, All",
        "    FileDelete, %MacroRunResultFile%",
        '    FileAppend, % status . "|" . code . "|" . cleanMessage, %MacroRunResultFile%, UTF-8',
        "}",
        "SetLastClick(screenX, screenY, clickKind := \"click\") {",
        "    global MacroRunClickFile",
        "    if (MacroRunClickFile = \"\")",
        "        return",
        "    FileDelete, %MacroRunClickFile%",
        '    FileAppend, % screenX . "|" . screenY . "|" . clickKind, %MacroRunClickFile%, UTF-8',
        "}",
        "MacroRelayFinalize(exitReason, exitCode) {",
        "    global MacroRunStatus",
        "    SetRunProgress(0)",
        '    if (MacroRunStatus = "RUNNING")',
        '        SetRunResult("SUCCESS", "COMPLETED", "매크로 실행 완료")',
        "}",
        'SetRunResult("RUNNING", "STARTED", "매크로 실행 중")',
        'OnExit("MacroRelayFinalize")',
        f'Log("macro start: {name}")',
        "",
    ]


def browser_action_helpers() -> List[str]:
    return [
        "BrowserAction_Send(payload, server_port) {",
        "    static wsainit := 0",
        "    if (!wsainit) {",
        "        VarSetCapacity(wsaData, 32, 0)",
        "        if (DllCall(\"Ws2_32\\WSAStartup\", \"UShort\", 0x202, \"Ptr\", &wsaData) != 0)",
        "            return \"\"",
        "        wsainit := 1",
        "    }",
        "    sock := DllCall(\"Ws2_32\\socket\", \"Int\", 2, \"Int\", 1, \"Int\", 6, \"Ptr\")",
        "    if (sock = -1)",
        "        return \"\"",
        "    VarSetCapacity(addr, 16, 0)",
        "    NumPut(2, addr, 0, \"UShort\")",
        "    NumPut(DllCall(\"Ws2_32\\htons\", \"UShort\", server_port, \"UShort\"), addr, 2, \"UShort\")",
        "    NumPut(DllCall(\"Ws2_32\\inet_addr\", \"AStr\", \"127.0.0.1\", \"UInt\"), addr, 4, \"UInt\")",
        "    if (DllCall(\"Ws2_32\\connect\", \"Ptr\", sock, \"Ptr\", &addr, \"Int\", 16) != 0) {",
        "        DllCall(\"Ws2_32\\closesocket\", \"Ptr\", sock)",
        "        return \"\"",
        "    }",
        "    size := StrPut(payload, \"UTF-8\")",
        "    VarSetCapacity(sendbuf, size, 0)",
        "    StrPut(payload, &sendbuf, size, \"UTF-8\")",
        "    DllCall(\"Ws2_32\\send\", \"Ptr\", sock, \"Ptr\", &sendbuf, \"Int\", size - 1, \"Int\", 0)",
        "    ; Signal end-of-request so the Python server doesn't wait on a read timeout.",
        "    DllCall(\"Ws2_32\\shutdown\", \"Ptr\", sock, \"Int\", 1)",
        "    VarSetCapacity(buf, 16384, 0)",
        "    recv := DllCall(\"Ws2_32\\recv\", \"Ptr\", sock, \"Ptr\", &buf, \"Int\", 16384, \"Int\", 0)",
        "",
    ]


def browser_action_helpers() -> List[str]:
    return [
        "BrowserAction_Send(payload, server_port) {",
        "    static wsainit := 0",
        "    if (!wsainit) {",
        "        VarSetCapacity(wsaData, 32, 0)",
        "        if (DllCall(\"Ws2_32\\WSAStartup\", \"UShort\", 0x202, \"Ptr\", &wsaData) != 0)",
        "            return \"\"",
        "        wsainit := 1",
        "    }",
        "    sock := DllCall(\"Ws2_32\\socket\", \"Int\", 2, \"Int\", 1, \"Int\", 6, \"Ptr\")",
        "    if (sock = -1)",
        "        return \"\"",
        "    VarSetCapacity(addr, 16, 0)",
        "    NumPut(2, addr, 0, \"UShort\")",
        "    NumPut(DllCall(\"Ws2_32\\htons\", \"UShort\", server_port, \"UShort\"), addr, 2, \"UShort\")",
        "    NumPut(DllCall(\"Ws2_32\\inet_addr\", \"AStr\", \"127.0.0.1\", \"UInt\"), addr, 4, \"UInt\")",
        "    if (DllCall(\"Ws2_32\\connect\", \"Ptr\", sock, \"Ptr\", &addr, \"Int\", 16) != 0) {",
        "        DllCall(\"Ws2_32\\closesocket\", \"Ptr\", sock)",
        "        return \"\"",
        "    }",
        "    size := StrPut(payload, \"UTF-8\")",
        "    VarSetCapacity(sendbuf, size, 0)",
        "    StrPut(payload, &sendbuf, size, \"UTF-8\")",
        "    DllCall(\"Ws2_32\\send\", \"Ptr\", sock, \"Ptr\", &sendbuf, \"Int\", size - 1, \"Int\", 0)",
        "    ; Signal end-of-request so the Python server doesn't wait on a read timeout.",
        "    DllCall(\"Ws2_32\\shutdown\", \"Ptr\", sock, \"Int\", 1)",
        "    VarSetCapacity(buf, 16384, 0)",
        "    recv := DllCall(\"Ws2_32\\recv\", \"Ptr\", sock, \"Ptr\", &buf, \"Int\", 16384, \"Int\", 0)",
        "    resp := \"\"",
        "    if (recv > 0)",
        "        resp := StrGet(&buf, recv, \"UTF-8\")",
        "    DllCall(\"Ws2_32\\closesocket\", \"Ptr\", sock)",
        "    return resp",
        "}",
        "",
    ]


def ocr_engine_helpers() -> List[str]:
    """Generate AHK helper functions for OCR engine TCP communication."""
    return [
        "OcrEngine_Send(payload, server_port) {",
        "    static wsainit := 0",
        "    if (!wsainit) {",
        "        VarSetCapacity(wsaData, 32, 0)",
        "        if (DllCall(\"Ws2_32\\WSAStartup\", \"UShort\", 0x202, \"Ptr\", &wsaData) != 0)",
        "            return \"\"",
        "        wsainit := 1",
        "    }",
        "    sock := DllCall(\"Ws2_32\\socket\", \"Int\", 2, \"Int\", 1, \"Int\", 6, \"Ptr\")",
        "    if (sock = -1)",
        "        return \"\"",
        "    VarSetCapacity(addr, 16, 0)",
        "    NumPut(2, addr, 0, \"UShort\")",
        "    NumPut(DllCall(\"Ws2_32\\htons\", \"UShort\", server_port, \"UShort\"), addr, 2, \"UShort\")",
        "    NumPut(DllCall(\"Ws2_32\\inet_addr\", \"AStr\", \"127.0.0.1\", \"UInt\"), addr, 4, \"UInt\")",
        "    if (DllCall(\"Ws2_32\\connect\", \"Ptr\", sock, \"Ptr\", &addr, \"Int\", 16) != 0) {",
        "        DllCall(\"Ws2_32\\closesocket\", \"Ptr\", sock)",
        "        return \"\"",
        "    }",
        "    size := StrPut(payload, \"UTF-8\")",
        "    VarSetCapacity(sendbuf, size, 0)",
        "    StrPut(payload, &sendbuf, size, \"UTF-8\")",
        "    DllCall(\"Ws2_32\\send\", \"Ptr\", sock, \"Ptr\", &sendbuf, \"Int\", size - 1, \"Int\", 0)",
        "    DllCall(\"Ws2_32\\shutdown\", \"Ptr\", sock, \"Int\", 1)",
        "    ; Read response in chunks up to 65536 bytes",
        "    VarSetCapacity(buf, 65536, 0)",
        "    total := 0",
        "    resp := \"\"",
        "    Loop {",
        "        recv := DllCall(\"Ws2_32\\recv\", \"Ptr\", sock, \"Ptr\", &buf, \"Int\", 65536, \"Int\", 0)",
        "        if (recv <= 0)",
        "            break",
        "        resp .= StrGet(&buf, recv, \"UTF-8\")",
        "        total += recv",
        "    }",
        "    DllCall(\"Ws2_32\\closesocket\", \"Ptr\", sock)",
        "    return resp",
        "}",
        "",
        "OcrEngine_ParseText(json_resp) {",
        "    q := Chr(34)",
        "    needle := q . \"text\" . q",
        "    pos := InStr(json_resp, needle)",
        "    if (!pos)",
        "        return \"\"",
        "    pos := InStr(json_resp, \":\", false, pos)",
        "    if (!pos)",
        "        return \"\"",
        "    pos := InStr(json_resp, q, false, pos + 1)",
        "    if (!pos)",
        "        return \"\"",
        "    start := pos + 1",
        "    end := start",
        "    Loop {",
        "        end := InStr(json_resp, q, false, end)",
        "        if (!end)",
        "            break",
        "        if (SubStr(json_resp, end - 1, 1) != \"\\\")",
        "            break",
        "        end += 1",
        "    }",
        "    if (!end)",
        "        return \"\"",
        "    result := SubStr(json_resp, start, end - start)",
        "    StringReplace, result, result, \\\", \", All",
        "    StringReplace, result, result, \\n, `n, All",
        "    StringReplace, result, result, \\r, `r, All",
        "    StringReplace, result, result, \\t, `t, All",
        "    return result",
        "}",
        "",
        "OcrEngine_ParseField(json_resp, field_name) {",
        "    q := Chr(34)",
        "    needle := q . field_name . q",
        "    pos := InStr(json_resp, needle)",
        "    if (!pos)",
        "        return \"\"",
        "    pos := InStr(json_resp, \":\", false, pos)",
        "    if (!pos)",
        "        return \"\"",
        "    rest := LTrim(SubStr(json_resp, pos + 1))",
        "    if (SubStr(rest, 1, 1) = q) {",
        "        start := 2",
        "        end := InStr(rest, q, false, start)",
        "        if (!end)",
        "            return \"\"",
        "        return SubStr(rest, start, end - start)",
        "    }",
        "    end := 1",
        "    Loop {",
        "        ch := SubStr(rest, end, 1)",
        "        if (ch = \",\" or ch = \"}\" or ch = \" \" or ch = \"`n\")",
        "            break",
        "        end += 1",
        "    }",
        "    return SubStr(rest, 1, end - 1)",
        "}",
        "",
        "OcrEngine_IsSuccess(json_resp) {",
        "    q := Chr(34)",
        "    n1 := q . \"success\" . q . \": true\"",
        "    n2 := q . \"success\" . q . \":true\"",
        "    return InStr(json_resp, n1) or InStr(json_resp, n2)",
        "}",
        "",
        "OcrEngine_ParseCenter(json_resp, ByRef cx, ByRef cy) {",
        "    q := Chr(34)",
        "    n1 := q . \"match_box\" . q",
        "    n2 := q . \"center\" . q",
        "    pos := InStr(json_resp, n1)",
        "    if (!pos)",
        "        pos := InStr(json_resp, n2)",
        "    else",
        "        pos := InStr(json_resp, n2, false, pos)",
        "    if (!pos)",
        "        return 0",
        "    bracket := InStr(json_resp, \"[\", false, pos)",
        "    if (!bracket)",
        "        return 0",
        "    end_bracket := InStr(json_resp, \"]\", false, bracket)",
        "    if (!end_bracket)",
        "        return 0",
        "    coords := SubStr(json_resp, bracket + 1, end_bracket - bracket - 1)",
        "    StringSplit, parts, coords, `,",
        "    cx := Trim(parts1) + 0",
        "    cy := Trim(parts2) + 0",
        "    return 1",
        "}",
        "",
    ]


def ahk_expression(base: str, offset: int) -> str:
    if offset == 0:
        return base
    sign = "+" if offset > 0 else "-"
    return f"{base} {sign} {abs(offset)}"


def ahk_scaled_expression(base: str, offset: int, scale_variable: str = "") -> str:
    """Build a coordinate expression whose recorded offset follows the detected image scale."""
    if offset == 0:
        return base
    if not scale_variable:
        return ahk_expression(base, offset)
    sign = "+" if offset > 0 else "-"
    return f"{base} {sign} Round({abs(offset)} * {scale_variable})"


def ahk_quote(text: str) -> str:
    """Escape text for use inside AHK quoted strings."""
    return text.replace('"', '""')


def render_mouse_click(step: Dict[str, Any]) -> List[str]:
    button = step.get("button", "Left")
    x = step.get("x")
    y = step.get("y")
    count = step.get("count", 1)
    coordinate_scope = str(step.get("coordinate_scope") or "screen").lower()
    window = str(step.get("window") or "")
    window_exe = str(step.get("window_exe") or "")
    window_hwnd = int(step.get("window_hwnd") or 0)
    if coordinate_scope == "client" and x is not None and y is not None and (window or window_exe or window_hwnd):
        lines: List[str] = []
        if window_hwnd:
            lines.append(f'TargetHwnd := WinExist("ahk_id {window_hwnd}")')
        else:
            lines.append("TargetHwnd := 0")
        if window:
            lines.append("if !TargetHwnd")
            lines.append("{")
            lines.append(f'    TargetHwnd := WinExist("{ahk_quote(window)}")')
            lines.append("}")
        if window_exe:
            lines.append("if !TargetHwnd")
            lines.append("{")
            lines.append(f'    TargetHwnd := WinExist("ahk_exe {ahk_quote(window_exe)}")')
            lines.append("}")
        lines.append("if !TargetHwnd")
        lines.append("{")
        lines.append(f'    Log("foreground click failed: target window not found - {ahk_quote(window or window_exe)}")')
        lines.append('    SetRunResult("FAILED", "TARGET_WINDOW_NOT_FOUND", "녹화한 대상 창을 찾지 못했습니다.")')
        lines.append("    Return")
        lines.append("}")
        timeout_seconds = max(0.2, int(step.get("activate_timeout") or 1200) / 1000)
        lines.append("WinActivate, ahk_id %TargetHwnd%")
        lines.append(f"WinWaitActive, ahk_id %TargetHwnd%, , {timeout_seconds:.2f}")
        lines.append("if ErrorLevel")
        lines.append("{")
        lines.append('    Log("foreground click failed: target window did not activate")')
        lines.append('    SetRunResult("FAILED", "TARGET_WINDOW_INACTIVE", "녹화한 대상 창을 활성화하지 못했습니다.")')
        lines.append("    Return")
        lines.append("}")
        lines.append("VarSetCapacity(__recorded_point, 8, 0)")
        lines.append(f'NumPut({int(x)}, __recorded_point, 0, "Int")')
        lines.append(f'NumPut({int(y)}, __recorded_point, 4, "Int")')
        lines.append('DllCall("ClientToScreen", "ptr", TargetHwnd, "ptr", &__recorded_point)')
        lines.append('ClickX := NumGet(__recorded_point, 0, "Int")')
        lines.append('ClickY := NumGet(__recorded_point, 4, "Int")')
        lines.append("CoordMode, Mouse, Screen")
        lines.append(f"MouseClick, {button}, %ClickX%, %ClickY%, {count}")
        lines.append('SetLastClick(ClickX, ClickY, "foreground")')
        lines.append("CoordMode, Mouse, %MacroMouseCoordMode%")
        lines.append(f'Log("foreground client click: {ahk_quote(window_exe or window)} at " . ClickX . "," . ClickY)')
        sleep = step.get("sleep_after")
        if sleep:
            lines.append(f"Sleep, {sleep}")
        return lines
    args = ["MouseClick", button]
    if x is not None and y is not None:
        args.append(str(x))
        args.append(str(y))
    args.append(str(count))
    lines = [", ".join(args)]
    lines.append("CoordMode, Mouse, Screen")
    lines.append("MouseGetPos, __LastClickX, __LastClickY")
    lines.append('SetLastClick(__LastClickX, __LastClickY, "foreground")')
    lines.append("CoordMode, Mouse, %MacroMouseCoordMode%")
    sleep = step.get("sleep_after")
    if sleep:
        lines.append(f"Sleep, {sleep}")
    return lines


def render_inactive_click(step: Dict[str, Any]) -> List[str]:
    window = step.get("window", "A")
    window_exe = step.get("window_exe")
    control = step.get("control", "")
    target_control = str(step.get("target_control") or "")
    target_hwnd_text = str(step.get("target_hwnd") or "")
    button = step.get("button", "Left")
    clicks = step.get("clicks", 1)
    options = step.get("options", "NA")
    x = step.get("x")
    y = step.get("y")
    action_type = str(step.get("action_type") or "click").lower()
    method = str(step.get("method") or "controlclick").lower()
    direct_post = method == "direct_postmessage"
    handle_probe = method == "handle_probe"
    if method not in {"controlclick", "postmessage", "direct_postmessage", "handle_probe", "auto"}:
        method = "controlclick"
    if direct_post or handle_probe:
        method = "postmessage"
    if str(button).lower() in ("wheelup", "wheeldown") and method != "postmessage":
        method = "postmessage"
    retry_count = int(step.get("retry_count") or step.get("retries") or 0)
    retry_delay = int(step.get("retry_delay") or step.get("retry_interval") or 80)
    if retry_count < 0:
        retry_count = 0
    if retry_delay < 10:
        retry_delay = 10
    drag_to = step.get("drag_to")
    drag_click_after = bool(step.get("drag_click_after", False))
    lines = [f'TargetHwnd := WinExist("{ahk_quote(str(window))}")']
    if window_exe:
        lines.append("if !TargetHwnd")
        lines.append("{")
        lines.append(f'    TargetHwnd := WinExist("ahk_exe {ahk_quote(str(window_exe))}")')
        lines.append("}")
    lines.append("if !TargetHwnd")
    lines.append("{")
    lines.append(f'    Log("inactive click failed: window not found - {window}")')
    lines.append("    Return")
    lines.append("}")
    lines.append("WinGetClass, TargetClass, ahk_id %TargetHwnd%")
    lines.append("DirectPost := 0")
    lines.append("ManualChild := 0")
    lines.append("ClickHwnd := TargetHwnd")
    lines.append('if (TargetClass = "Chrome_WidgetWin_1" or TargetClass = "Chrome_WidgetWin_0" or TargetClass = "Chrome Legacy Window")')
    lines.append("{")
    lines.append("    ControlGet, RenderHwnd, Hwnd,, Chrome_RenderWidgetHostHWND1, ahk_id %TargetHwnd%")
    lines.append("    if (!RenderHwnd)")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, Chrome_RenderWidgetHostHWND2, ahk_id %TargetHwnd%")
    lines.append("    if (RenderHwnd)")
    lines.append("        ClickHwnd := RenderHwnd")
    lines.append("}")
    lines.append('if (InStr(TargetClass, "LDPlayer"))')
    lines.append("{")
    lines.append("    ControlGet, RenderHwnd, Hwnd,, RenderWindow1, ahk_id %TargetHwnd%")
    lines.append("    if (!RenderHwnd)")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, RenderWindow, ahk_id %TargetHwnd%")
    lines.append("    if (!RenderHwnd)")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, Qt5QWindowIcon1, ahk_id %TargetHwnd%")
    lines.append("    if (!RenderHwnd)")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, Qt5QWindowIcon, ahk_id %TargetHwnd%")
    lines.append("    if (RenderHwnd)")
    lines.append("        ClickHwnd := RenderHwnd")
    lines.append("}")
    if direct_post:
        lines.append("DirectPost := 1")
        lines.append("ClickHwnd := TargetHwnd")
        lines.append('Log("inactive click mode: direct top-level PostMessage")')
    if handle_probe:
        lines.append("ManualClickHwnd := 0")
        if target_control:
            lines.append(
                f'ControlGet, ManualClickHwnd, Hwnd,, {ahk_quote(target_control)}, ahk_id %TargetHwnd%'
            )
        try:
            saved_hwnd = int(target_hwnd_text, 0) if target_hwnd_text else 0
        except (TypeError, ValueError):
            saved_hwnd = 0
        if saved_hwnd:
            lines.append("if (!ManualClickHwnd)")
            lines.append("{")
            lines.append(f"    SavedClickHwnd := {saved_hwnd}")
            lines.append('    if (DllCall("IsWindow", "ptr", SavedClickHwnd) and DllCall("GetAncestor", "ptr", SavedClickHwnd, "uint", 2, "ptr") = TargetHwnd)')
            lines.append("        ManualClickHwnd := SavedClickHwnd")
            lines.append("}")
        lines.append("if (ManualClickHwnd)")
        lines.append("{")
        lines.append("    ClickHwnd := ManualClickHwnd")
        lines.append("    ManualChild := 1")
        lines.append('    Log("inactive click handle engine selected: " . ClickHwnd)')
        lines.append("}")
        lines.append("else")
        lines.append('    Log("inactive click handle engine fallback: saved child not found")')
    lines.append("ClickX := 0")
    lines.append("ClickY := 0")
    lines.append("ScreenX := 0")
    lines.append("ScreenY := 0")
    if x is not None and y is not None:
        lines.append(f"RawClickX := {int(x)}")
        lines.append(f"RawClickY := {int(y)}")
        lines.append("VarSetCapacity(_pt, 8, 0)")
        lines.append('NumPut(RawClickX, _pt, 0, "Int")')
        lines.append('NumPut(RawClickY, _pt, 4, "Int")')
        lines.append('DllCall("ClientToScreen", "ptr", TargetHwnd, "ptr", &_pt)')
        lines.append('ScreenX := NumGet(_pt, 0, "Int")')
        lines.append('ScreenY := NumGet(_pt, 4, "Int")')
        lines.append("if (!DirectPost && !ManualChild)")
        lines.append("{")
        lines.append('    __point_value := (ScreenY << 32) | (ScreenX & 0xFFFFFFFF)')
        lines.append('    __point_hwnd := DllCall("WindowFromPoint", "Int64", __point_value, "Ptr")')
        lines.append('    if (__point_hwnd and (__point_hwnd = TargetHwnd or DllCall("IsChild", "ptr", TargetHwnd, "ptr", __point_hwnd)))')
        lines.append("        ClickHwnd := __point_hwnd")
        lines.append("}")
        lines.append("if (DirectPost)")
        lines.append("    ClickHwnd := TargetHwnd")
        lines.append('DllCall("ScreenToClient", "ptr", ClickHwnd, "ptr", &_pt)')
        lines.append('ClickX := NumGet(_pt, 0, "Int")')
        lines.append('ClickY := NumGet(_pt, 4, "Int")')
    win_token = "ahk_id %TargetHwnd%"
    click_token = "ahk_id %ClickHwnd%"
    if action_type == "drag" and isinstance(drag_to, list) and len(drag_to) >= 2:
        end_x, end_y = drag_to[:2]
        lines.append(f"RawDragEndX := {int(end_x)}")
        lines.append(f"RawDragEndY := {int(end_y)}")
        lines.append("VarSetCapacity(_drag_end_pt, 8, 0)")
        lines.append('NumPut(RawDragEndX, _drag_end_pt, 0, "Int")')
        lines.append('NumPut(RawDragEndY, _drag_end_pt, 4, "Int")')
        lines.append('DllCall("ClientToScreen", "ptr", TargetHwnd, "ptr", &_drag_end_pt)')
        lines.append('DllCall("ScreenToClient", "ptr", ClickHwnd, "ptr", &_drag_end_pt)')
        lines.append('DragEndX := NumGet(_drag_end_pt, 0, "Int")')
        lines.append('DragEndY := NumGet(_drag_end_pt, 4, "Int")')
        if method == "postmessage":
            down_msg = "0x201" if button.lower() != "right" else "0x204"
            up_msg = "0x202" if button.lower() != "right" else "0x205"
            down_wparam = "1" if button.lower() != "right" else "2"
            lines.append("DragStartX := ClickX")
            lines.append("DragStartY := ClickY")
            lines.append("lParam := (DragStartY << 16) | (DragStartX & 0xFFFF)")
            lines.append(f"PostMessage, {down_msg}, {down_wparam}, %lParam%, , {click_token}")
            lines.append("Sleep, 30")
            lines.append("lParam := (DragEndY << 16) | (DragEndX & 0xFFFF)")
            lines.append(f"PostMessage, 0x200, {down_wparam}, %lParam%, , " + click_token)
            lines.append(f"PostMessage, {up_msg}, 0, %lParam%, , {click_token}")
            if drag_click_after:
                lines.append("Sleep, 40")
                lines.append(f"PostMessage, {down_msg}, {down_wparam}, %lParam%, , {click_token}")
                lines.append(f"PostMessage, {up_msg}, 0, %lParam%, , {click_token}")
        else:
            start_opts = f"{options} D".strip()
            end_opts = f"{options} U".strip()
            parts_down = [
                "x%ClickX% y%ClickY%",
                click_token,
                button,
                "1",
                start_opts,
            ]
            parts_up = [
                "x%DragEndX% y%DragEndY%",
                click_token,
                button,
                "1",
                end_opts,
            ]
            parts_down.insert(2, "")
            parts_up.insert(2, "")
            lines.append("ControlClick, " + ", ".join(parts_down))
            lines.append("Sleep, 40")
            lines.append("ControlClick, " + ", ".join(parts_up))
            if drag_click_after:
                lines.append("Sleep, 40")
                click_parts = [
                    "x%DragEndX% y%DragEndY%",
                    click_token,
                    button,
                    "1",
                    end_opts.replace(" U", "").strip(),
                ]
                click_parts.insert(2, "")
                lines.append("ControlClick, " + ", ".join(click_parts))
    else:
        if method == "postmessage":
            lines.append("lParam := (ClickY << 16) | (ClickX & 0xFFFF)")
            if str(button).lower() in ("wheelup", "wheeldown"):
                delta = 120 * int(clicks or 1)
                if str(button).lower() == "wheeldown":
                    delta = -delta
                lines.append("lParam := (ScreenY << 16) | (ScreenX & 0xFFFF)")
                lines.append(f"wParam := ({delta} << 16)")
                lines.append(f"PostMessage, 0x20A, %wParam%, %lParam%, , {click_token}")
            else:
                down_msg = "0x201"
                up_msg = "0x202"
                if str(button).lower() == "right":
                    down_msg = "0x204"
                    up_msg = "0x205"
                down_wparam = "2" if str(button).lower() == "right" else "1"
                lines.append("PostMessage, 0x200, 0, %lParam%, , " + click_token)
                lines.append(f"PostMessage, {down_msg}, {down_wparam}, %lParam%, , {click_token}")
                lines.append(f"PostMessage, {up_msg}, 0, %lParam%, , {click_token}")
                if direct_post:
                    lines.append('Log("inactive click direct post: mousemove/down/up sent")')
                if int(clicks or 1) > 1:
                    lines.append("Sleep, 30")
                    lines.append(f"PostMessage, {down_msg}, {down_wparam}, %lParam%, , {click_token}")
                    lines.append(f"PostMessage, {up_msg}, 0, %lParam%, , {click_token}")
        elif method == "controlclick":
            click_options = options
            control_or_pos = "x%ClickX% y%ClickY%" if x is not None and y is not None else str(control)
            parts = [
                control_or_pos,
                click_token,
                "",
                button,
                str(clicks),
                click_options,
            ]
            line = "ControlClick, " + ", ".join(parts)
            lines.append(line)
        else:
            lines.append(f'Log("inactive click auto: class=" . TargetClass . " retries={retry_count}")')
            lines.append(f"__max_try := {retry_count + 1}")
            lines.append('__prefer_post := InStr(TargetClass, "EVA_") or InStr(TargetClass, "Chrome_WidgetWin") or InStr(TargetClass, "HwndWrapper") or InStr(TargetClass, "Qt") or InStr(TargetClass, "LDPlayer")')
            lines.append("if (__prefer_post)")
            lines.append("    __max_try := 0")
            lines.append(f"__retry_delay := {retry_delay}")
            lines.append("__click_ok := 0")
            lines.append('__click_target := "ahk_id " . ClickHwnd')
            if x is not None and y is not None:
                lines.append('__click_control := "x" . ClickX . " y" . ClickY')
            else:
                lines.append(f'__click_control := "{ahk_quote(control)}"')
            lines.append(f'__click_button := "{ahk_quote(str(button))}"')
            lines.append(f"__click_count := {int(clicks or 1)}")
            lines.append(f'__click_opts := "{ahk_quote(str(options))}"')
            lines.append("if (__click_button = \"WheelUp\" or __click_button = \"WheelDown\")")
            lines.append("{")
            lines.append("    lParam := (ScreenY << 16) | (ScreenX & 0xFFFF)")
            lines.append("    __delta := 120 * __click_count")
            lines.append("    if (__click_button = \"WheelDown\")")
            lines.append("        __delta := -__delta")
            lines.append("    wParam := (__delta << 16)")
            lines.append("    PostMessage, 0x20A, %wParam%, %lParam%, , ahk_id %ClickHwnd%")
            lines.append('    Log("inactive click auto: wheel postmessage sent")')
            lines.append("    __click_ok := 1")
            lines.append("}")
            lines.append("else")
            lines.append("{")
            lines.append("    Loop, %__max_try%")
            lines.append("    {")
            lines.append("        __try_idx := A_Index")
            lines.append("        ErrorLevel := 0")
            lines.append("        ControlClick, %__click_control%, %__click_target%, , %__click_button%, %__click_count%, %__click_opts%")
            lines.append("        if (ErrorLevel = 0)")
            lines.append("        {")
            lines.append('            Log("inactive click auto: controlclick ok try=" . __try_idx)')
            lines.append("            __click_ok := 1")
            lines.append("            break")
            lines.append("        }")
            lines.append('        Log("inactive click auto: controlclick fail try=" . __try_idx)')
            lines.append("        if (A_Index < __max_try)")
            lines.append("            Sleep, %__retry_delay%")
            lines.append("    }")
            lines.append("    if (!__click_ok)")
            lines.append("    {")
            lines.append("        lParam := (ClickY << 16) | (ClickX & 0xFFFF)")
            lines.append("        __down := 0x201")
            lines.append("        __up := 0x202")
            lines.append("        if (__click_button = \"Right\")")
            lines.append("        {")
            lines.append("            __down := 0x204")
            lines.append("            __up := 0x205")
            lines.append("        }")
            lines.append('        __down_wparam := (__click_button = "Right") ? 2 : 1')
            lines.append("        PostMessage, 0x200, 0, %lParam%, , ahk_id %ClickHwnd%")
            lines.append("        PostMessage, %__down%, %__down_wparam%, %lParam%, , ahk_id %ClickHwnd%")
            lines.append("        PostMessage, %__up%, 0, %lParam%, , ahk_id %ClickHwnd%")
            lines.append("        if (__click_count > 1)")
            lines.append("        {")
            lines.append("            Sleep, 30")
            lines.append("            PostMessage, %__down%, %__down_wparam%, %lParam%, , ahk_id %ClickHwnd%")
            lines.append("            PostMessage, %__up%, 0, %lParam%, , ahk_id %ClickHwnd%")
            lines.append("        }")
            lines.append('        Log("inactive click auto: fallback postmessage sent")')
            lines.append("        __click_ok := 1")
            lines.append("    }")
            lines.append("}")
    if x is not None and y is not None:
        lines.append('SetLastClick(ScreenX, ScreenY, "inactive")')
    sleep = step.get("sleep_after")
    if sleep:
        lines.append(f"Sleep, {sleep}")
    return lines


def render_wait(step: Dict[str, Any]) -> List[str]:
    duration = step.get("duration", 250)
    return [f"Sleep, {duration}"]


def render_flow_control(step: Dict[str, Any], step_index: int) -> List[str]:
    repeat = int(step.get("repeat_count") or step.get("repeat") or 0)
    target_step = int(step.get("jump_to") or step.get("target_step") or 0)
    counter_key = str(step.get("counter_key") or f"flow_{step_index}")
    lines = [
        "if (!IsObject(FlowCounter))",
        "    FlowCounter := {}",
        f'__flow_key := "{ahk_quote(counter_key)}"',
        "if (!FlowCounter.HasKey(__flow_key))",
        "    FlowCounter[__flow_key] := 0",
        "FlowCounter[__flow_key] += 1",
        f'Log("flow_control key={counter_key} count=" . FlowCounter[__flow_key])',
    ]
    if target_step <= 0:
        lines.append("return")
        return lines
    if repeat <= 0:
        lines.append(f'Log("flow_control jump: {target_step}")')
        lines.append(f"Goto, Step{target_step}")
        return lines
    lines.append(f"if (FlowCounter[__flow_key] <= {repeat})")
    lines.append("{")
    lines.append(f'    Log("flow_control jump: {target_step}")')
    lines.append(f"    Goto, Step{target_step}")
    lines.append("}")
    return lines


def render_edge_conditions(
    step: Dict[str, Any],
    step_index: int,
    kind: str,
    indent: str = "",
) -> List[str]:
    raw_rules = step.get("edge_conditions") or []
    rules = [
        rule
        for rule in raw_rules
        if isinstance(rule, dict) and str(rule.get("kind") or "success") == kind
    ] if isinstance(raw_rules, list) else []
    if not rules:
        return []
    key = f"edge_{step_index}_{kind}"
    lines = [
        indent + "if (!IsObject(EdgeCounter))",
        indent + "    EdgeCounter := {}",
        indent + f'__edge_key := "{key}"',
        indent + "if (!EdgeCounter.HasKey(__edge_key))",
        indent + "    EdgeCounter[__edge_key] := 0",
        indent + "EdgeCounter[__edge_key] += 1",
    ]
    operator_map = {"==": "=", "=": "=", "!=": "!=", ">=": ">=", "<=": "<=", ">": ">", "<": "<"}
    for rule in rules:
        target = int(rule.get("target") or 0)
        if target <= 0:
            continue
        source = str(rule.get("source") or "edge_count")
        if source == "variable":
            variable = str(rule.get("variable") or "").strip()
            if not variable or variable[0].isdigit() or not variable.replace("_", "a").isalnum():
                continue
            expression = variable
        else:
            expression = "EdgeCounter[__edge_key]"
        operator = operator_map.get(str(rule.get("operator") or ">="), ">=")
        value = int(rule.get("value") or 0)
        delay = max(0, int(rule.get("delay") or 0))
        label = str(rule.get("label") or f"{expression} {operator} {value}")
        lines.append(indent + f"if ({expression} {operator} {value})")
        lines.append(indent + "{")
        lines.append(indent + f'    Log("edge condition: {ahk_quote(label)} -> {target}")')
        if bool(rule.get("reset_on_match")) and source != "variable":
            lines.append(indent + "    EdgeCounter[__edge_key] := 0")
        if delay:
            lines.append(indent + f"    Sleep, {delay}")
        lines.append(indent + f"    Goto, Step{target}")
        lines.append(indent + "}")
    return lines


def render_text_condition(step: Dict[str, Any], step_index: int) -> List[str]:
    source = str(step.get("source") or "ocr").strip().lower()
    mode = str(step.get("mode") or "contains").strip().lower()
    case_sensitive = bool(step.get("case_sensitive", False))
    normalize = bool(step.get("normalize", True))
    on_match = int(step.get("on_match", 0) or 0)
    on_no_match = int(step.get("on_no_match", 0) or 0)
    edge_success = int(step.get("on_success", 0) or 0)
    edge_fail = int(step.get("on_fail", 0) or 0)
    on_match_delay = int(step.get("on_match_delay", step.get("on_success_delay", 0)) or 0)
    on_no_match_delay = int(step.get("on_no_match_delay", step.get("on_fail_delay", 0)) or 0)
    if on_match <= 0 and edge_success > 0:
        on_match = edge_success
    if on_no_match <= 0 and edge_fail > 0:
        on_no_match = edge_fail
    if on_match_delay < 0:
        on_match_delay = 0
    if on_no_match_delay < 0:
        on_no_match_delay = 0

    needles_raw = step.get("needles")
    needles: List[str] = []
    if isinstance(needles_raw, list):
        needles = [str(item).strip() for item in needles_raw if str(item).strip()]
    elif isinstance(needles_raw, str) and needles_raw.strip():
        needles = [item.strip() for item in needles_raw.split(",") if item.strip()]
    needle = str(step.get("needle") or "").strip()
    if needle:
        needles.insert(0, needle)
    dedup: List[str] = []
    for item in needles:
        if item not in dedup:
            dedup.append(item)
    needles = dedup
    if not needles:
        return ['Log("text_condition skipped: no needles")']

    lines = [f"; text_condition step={step_index}", "__tc_src := \"\""]
    if source == "clipboard":
        lines.append("__tc_src := Clipboard")
    elif source == "table":
        lines.append("__tc_src := OCR_LastText")
    else:
        lines.append("__tc_src := OCR_LastText")
    if normalize:
        lines.append('__tc_src := StrReplace(__tc_src, " ", "")')
        lines.append('__tc_src := StrReplace(__tc_src, "`t", "")')
        lines.append('__tc_src := StrReplace(__tc_src, "`r", "")')
        lines.append('__tc_src := StrReplace(__tc_src, "`n", "")')
    lines.append("__tc_hay := __tc_src")
    if not case_sensitive:
        lines.append("StringLower, __tc_hay, __tc_hay")
    lines.append("__tc_match := 0")
    for raw in needles:
        token = raw
        if normalize:
            token = token.replace(" ", "").replace("\t", "").replace("\r", "").replace("\n", "")
        if not case_sensitive:
            token = token.lower()
        escaped = ahk_quote(token)
        if mode == "equals":
            lines.append(f'if (__tc_hay = "{escaped}")')
        else:
            lines.append(f'if (InStr(__tc_hay, "{escaped}"))')
        lines.append("{")
        lines.append("    __tc_match := 1")
        lines.append("}")
    lines.append("__tc_log_hay := __tc_hay")
    lines.append("if (StrLen(__tc_log_hay) > 80)")
    lines.append("    __tc_log_hay := SubStr(__tc_log_hay, 1, 80) . \"...\"")
    lines.append('__tc_log_hay := StrReplace(__tc_log_hay, "`r", "\\\\r")')
    lines.append('__tc_log_hay := StrReplace(__tc_log_hay, "`n", "\\\\n")')
    lines.append('Log("text_condition source=' + ahk_quote(source) + ' mode=' + ahk_quote(mode) + ' needles=' + ahk_quote(",".join(needles)) + ' hay=" . __tc_log_hay . " match=" . __tc_match)')
    lines.append("if (__tc_match)")
    lines.append("{")
    lines.extend(render_edge_conditions(step, step_index, "success", "    "))
    if on_match > 0:
        if on_match_delay > 0:
            lines.append(f"    Sleep, {on_match_delay}")
        lines.append(f"    Goto, Step{on_match}")
    lines.append("}")
    lines.append("else")
    lines.append("{")
    lines.extend(render_edge_conditions(step, step_index, "fail", "    "))
    if on_no_match > 0:
        if on_no_match_delay > 0:
            lines.append(f"    Sleep, {on_no_match_delay}")
        lines.append(f"    Goto, Step{on_no_match}")
    lines.append("}")
    return lines


def render_set_var(step: Dict[str, Any]) -> List[str]:
    name = str(step.get("name") or step.get("var") or "").strip()
    if not name:
        return ["; set_var skipped, no name"]
    value = step.get("value", "")
    if isinstance(value, bool):
        value_token = "1" if value else "0"
    elif isinstance(value, (int, float)):
        value_token = str(value)
    else:
        value_token = f'"{ahk_quote(str(value))}"'
    return [f"{name} := {value_token}"]


def render_calc_var(step: Dict[str, Any]) -> List[str]:
    name = str(step.get("name") or step.get("var") or "").strip()
    if not name:
        return ["; calc_var skipped, no name"]
    expr = str(step.get("expr") or "").strip()
    op = str(step.get("op") or "").strip()
    value = step.get("value", "")
    if expr:
        expr_token = expr.replace("$", "")
        return [f"{name} := {expr_token}"]
    if op and value != "":
        if isinstance(value, str):
            value_token = value.replace("$", "")
        else:
            value_token = str(value)
        return [f"{name} := {name} {op} {value_token}"]
    return ["; calc_var skipped, no expr"]


def table_helpers() -> List[str]:
    return [
        "TableStore := {}",
        "Table_Set(name, row, col, value) {",
        "    global TableStore",
        "    if (!TableStore.HasKey(name))",
        "        TableStore[name] := []",
        "    while (TableStore[name].MaxIndex() < row)",
        "        TableStore[name].Push([])",
        "    rowArr := TableStore[name][row]",
        "    if (!IsObject(rowArr))",
        "        rowArr := []",
        "    while (rowArr.MaxIndex() < col)",
        "        rowArr.Push(\"\")",
        "    rowArr[col] := value",
        "    TableStore[name][row] := rowArr",
        "}",
        "Table_Add(name, value) {",
        "    global TableStore",
        "    if (!TableStore.HasKey(name))",
        "        TableStore[name] := []",
        "    row := TableStore[name].MaxIndex() + 1",
        "    Table_Set(name, row, 1, value)",
        "}",
        "Table_Get(name, row, col) {",
        "    global TableStore",
        "    if (!TableStore.HasKey(name))",
        "        return \"\"",
        "    arr := TableStore[name]",
        "    if (row < 1 || row > arr.MaxIndex())",
        "        return \"\"",
        "    rowArr := arr[row]",
        "    if (!IsObject(rowArr))",
        "        return \"\"",
        "    if (col < 1 || col > rowArr.MaxIndex())",
        "        return \"\"",
        "    return rowArr[col]",
        "}",
        "Table_Clear(name) {",
        "    global TableStore",
        "    TableStore[name] := []",
        "}",
        "Table_ColToIndex(col) {",
        "    if (col = \"\")",
        "        return 0",
        "    if (col is integer)",
        "        return col + 0",
        "    col := Trim(col)",
        "    if (col = \"\")",
        "        return 0",
        "    StringUpper, col, col",
        "    total := 0",
        "    Loop, Parse, col",
        "    {",
        "        ch := Asc(A_LoopField)",
        "        if (ch < 65 || ch > 90)",
        "            continue",
        "        total := total * 26 + (ch - 64)",
        "    }",
        "    return total",
        "}",
        "TableSelection_Read(table, key, default) {",
        "    __sel_ini := A_ScriptDir . \"\\table_cursor.ini\"",
        "    if (!FileExist(__sel_ini))",
        "        return default",
        "    __sel_key := table . \"_\" . key",
        "    IniRead, __value, %__sel_ini%, selection, %__sel_key%, %default%",
        "    if (ErrorLevel)",
        "        return default",
        "    if (__value = \"ERROR\")",
        "        return default",
        "    return __value",
        "}",
        "TableCursor := {}",
        "TableCursor_Set(key, row, col) {",
        "    global TableCursor",
        "    TableCursor[key] := {\"row\": row, \"col\": col}",
        "}",
        "TableCursor_Init(key, row, col, force := 0) {",
        "    global TableCursor",
        "    if (force || !TableCursor.HasKey(key))",
        "        TableCursor[key] := {\"row\": row, \"col\": col}",
        "}",
        "TableCursor_Row(key) {",
        "    global TableCursor",
        "    return TableCursor[key].row",
        "}",
        "TableCursor_Col(key) {",
        "    global TableCursor",
        "    return TableCursor[key].col",
        "}",
        "TableCursor_Advance(key, rowStep, colStep) {",
        "    global TableCursor",
        "    TableCursor[key].row += rowStep",
        "    TableCursor[key].col += colStep",
        "    if (TableCursor[key].row < 1)",
        "        TableCursor[key].row := 1",
        "    if (TableCursor[key].col < 1)",
        "        TableCursor[key].col := 1",
        "}",
        "TableCursor_EnsureIni(path) {",
        "    if (!FileExist(path))",
        "        return",
        "    __first := \"\"",
        "    Loop, Read, %path%",
        "    {",
        "        __line := Trim(A_LoopReadLine)",
        "        if (SubStr(__line, 1, 1) = Chr(0xFEFF))",
        "            __line := SubStr(__line, 2)",
        "        else if (SubStr(__line, 1, 3) = \"ï»¿\")",
        "            __line := SubStr(__line, 4)",
        "        __line := Trim(__line)",
        "        if (__line = \"\" || SubStr(__line, 1, 1) = \";\")",
        "            continue",
        "        __first := __line",
        "        break",
        "    }",
        "    if (__first = \"\")",
        "        return",
        "    if (SubStr(__first, 1, 1) != \"[\") {",
        "        __bak := path . \".bad-\" . A_Now",
        "        FileMove, %path%, %__bak%, 1",
        "    }",
        "}",
        "",
    ]


def render_table_init(tables: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for table_name, rows in tables.items():
        safe_name = ahk_quote(table_name)
        if not isinstance(rows, list):
            continue
        for r_idx, row in enumerate(rows, start=1):
            if isinstance(row, list):
                for c_idx, value in enumerate(row, start=1):
                    safe_value = ahk_quote(str(value))
                    lines.append(f'Table_Set("{safe_name}", {r_idx}, {c_idx}, "{safe_value}")')
            else:
                safe_value = ahk_quote(str(row))
                lines.append(f'Table_Set("{safe_name}", {r_idx}, 1, "{safe_value}")')
    if lines:
        lines.append("")
    return lines


def render_table_store(step: Dict[str, Any]) -> List[str]:
    table = str(step.get("table") or step.get("name") or "default")
    source = str(step.get("source") or "").lower()
    value = step.get("value", "")
    row = int(step.get("row") or 0)
    col = step.get("col") or "A"
    col_idx = col_to_index(str(col))
    if source == "ocr_last":
        value_token = "OCR_LastText"
    else:
        if isinstance(value, bool):
            value_token = "1" if value else "0"
        elif isinstance(value, (int, float)):
            value_token = str(value)
        else:
            value_token = f'"{ahk_quote(str(value))}"'
    if row > 0 and col_idx > 0:
        return [f'Table_Set("{ahk_quote(table)}", {row}, {col_idx}, {value_token})']
    return [f'Table_Add("{ahk_quote(table)}", {value_token})']


def render_table_copy(step: Dict[str, Any]) -> List[str]:
    table = str(step.get("table") or "default")
    use_selected_row = bool(step.get("use_selected_row") or step.get("prompt_row"))
    use_selected_col = bool(step.get("use_selected_col") or step.get("prompt_col"))
    selection_key = str(step.get("selection_key") or _selection_key(table))
    computed_key = _selection_key(table)
    if not selection_key or selection_key.replace("_", "") == "":
        selection_key = computed_key
    elif selection_key != computed_key and selection_key.replace("_", "") == "":
        selection_key = computed_key
    if step.get("selection_key") and not use_selected_row and not use_selected_col:
        use_selected_row = True

    def row_expr(value, fallback):
        if value is None or value == "":
            return fallback
        if isinstance(value, (int, float)):
            return str(int(value))
        token = str(value).strip()
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            return token
        return token.replace("$", "")

    row_start = row_expr(step.get("row_start") or step.get("row") or step.get("index") or 1, "1")
    row_end = row_expr(step.get("row_end") or row_start, row_start)
    if step.get("col_start") or step.get("col_end"):
        cols = col_range(str(step.get("col_start") or "A"), str(step.get("col_end") or step.get("col_start") or "A"))
    else:
        cols = parse_col_list(str(step.get("cols") or "A"))
    if not cols:
        cols = [1]
    row_step = int(step.get("row_step") or 0)
    col_step = int(step.get("col_step") or 0)
    cursor_key = step.get("cursor_key") or f"{table}:{step.get('col_start') or 'A'}:{step.get('row_start') or 1}"
    persist_cursor = bool(step.get("cursor_persist", True)) if (row_step or col_step) else False

    if row_step or col_step:
        col_start_idx = cols[0]
        lines = ["__tbl_text := \"\""]
        lines.append(f'Log("table_copy flags row={1 if use_selected_row else 0} col={1 if use_selected_col else 0} key={selection_key}")')
        lines.extend(
            [
                f'__cursor_key := "{ahk_quote(str(cursor_key))}"',
                f"__row_start := {row_start}",
                f"__col_start := {col_start_idx}",
            ]
        )
        if use_selected_row or use_selected_col:
            lines.append("__row_sel := \"\"")
            lines.append("__col_sel := \"\"")
        if use_selected_row:
            lines.extend(
                [
                    f"__row_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"row\", \"\")",
                    "if (__row_sel = \"\")",
                    f"    __row_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"row\", \"\")",
                    "if (__row_sel != \"\")",
                    "    __row_start := __row_sel + 0",
                ]
            )
        if use_selected_col:
            lines.extend(
                [
                    f"__col_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"col\", \"\")",
                    "if (__col_sel = \"\")",
                    f"    __col_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"col\", \"\")",
                    "if (__col_sel != \"\")",
                    "    __col_start := __col_sel + 0",
                ]
            )
        if use_selected_row or use_selected_col:
            lines.extend(
                [
                    f'Log("table_copy selection key={selection_key} row_sel=" . __row_sel . " col_sel=" . __col_sel)',
                    f'Log("table_copy resolved row=" . __row_start . " col=" . __col_start)',
                ]
            )
        if persist_cursor:
            lines.extend(
                [
                    "__cursor_ini := A_ScriptDir . \"\\table_cursor.ini\"",
                    "IniRead, __row_start, %__cursor_ini%, cursor, %__cursor_key%_row, %__row_start%",
                    "IniRead, __col_start, %__cursor_ini%, cursor, %__cursor_key%_col, %__col_start%",
                ]
            )
        lines.extend(
            [
                "TableCursor_Init(__cursor_key, __row_start, __col_start)",
                "__row_start := TableCursor_Row(__cursor_key)",
                "__col_start := TableCursor_Col(__cursor_key)",
                "Clipboard := Table_Get(\"" + ahk_quote(table) + "\", __row_start, __col_start)",
                "ClipWait, 1",
                f"TableCursor_Advance(__cursor_key, {row_step}, {col_step})",
            ]
        )
        if persist_cursor:
            lines.extend(
                [
                    "IniWrite, % TableCursor_Row(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_row",
                    "IniWrite, % TableCursor_Col(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_col",
                ]
            )
        return lines

    lines = [
        "__tbl_text := \"\"",
        f'Log("table_copy flags row={1 if use_selected_row else 0} col={1 if use_selected_col else 0} key={selection_key}")',
        f"__row_start := {row_start}",
        f"__row_end := {row_end}",
        f"__col_start := {cols[0]}",
    ]
    if use_selected_row or use_selected_col:
        lines.extend(
            [
                "__row_sel := \"\"",
                "__col_sel := \"\"",
            ]
        )
    if use_selected_row:
        lines.extend(
            [
                f"__row_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"row\", \"\")",
                "if (__row_sel = \"\")",
                f"    __row_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"row\", \"\")",
                "if (__row_sel != \"\")",
                "    __row_start := __row_sel + 0",
                "if (__row_sel != \"\")",
                "    __row_end := __row_sel + 0",
            ]
        )
    if use_selected_col:
        lines.extend(
            [
                f"__col_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"col\", \"\")",
                "if (__col_sel = \"\")",
                f"    __col_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"col\", \"\")",
                "if (__col_sel != \"\")",
                "    __col_start := __col_sel + 0",
            ]
        )
    if use_selected_row or use_selected_col:
        lines.extend(
            [
                f'Log("table_copy selection key={selection_key} row_sel=" . __row_sel . " col_sel=" . __col_sel)',
                f'Log("table_copy resolved row=" . __row_start . " col=" . __col_start)',
            ]
        )

    lines.extend(
        [
        "Loop, % (__row_end - __row_start + 1)",
        "{",
        "    __r := __row_start + A_Index - 1",
        "    __line := \"\"",
        ]
    )
    for idx, col in enumerate(cols):
        col_expr = "__col_start" if use_selected_col and idx == 0 else (f"(__col_start + {idx})" if use_selected_col else str(col))
        if idx == 0:
            lines.append(f'    __line := Table_Get("{ahk_quote(table)}", __r, {col_expr})')
        else:
            lines.append(f'    __line .= "`t" . Table_Get("{ahk_quote(table)}", __r, {col_expr})')
    lines.extend(
        [
            "    if (A_Index = 1)",
            "        __tbl_text := __line",
            "    else",
            "        __tbl_text .= \"`n\" . __line",
            "}",
            "Clipboard := __tbl_text",
            "ClipWait, 1",
        ]
    )
    return lines


def render_table_paste(step: Dict[str, Any]) -> List[str]:
    table = str(step.get("table") or "default")
    mode = str(step.get("mode") or "active").lower()
    window = step.get("window") or "A"
    window_exe = step.get("window_exe")
    use_selected_row = bool(step.get("use_selected_row") or step.get("prompt_row"))
    use_selected_col = bool(step.get("use_selected_col") or step.get("prompt_col"))
    selection_key = str(step.get("selection_key") or _selection_key(table))
    computed_key = _selection_key(table)
    if not selection_key or selection_key.replace("_", "") == "":
        selection_key = computed_key
    elif selection_key != computed_key and selection_key.replace("_", "") == "":
        selection_key = computed_key
    if step.get("selection_key") and not use_selected_row and not use_selected_col:
        use_selected_row = True

    def row_expr(value, fallback):
        if value is None or value == "":
            return fallback
        if isinstance(value, (int, float)):
            return str(int(value))
        token = str(value).strip()
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            return token
        return token.replace("$", "")

    row_start = row_expr(step.get("row_start") or step.get("row") or step.get("index") or 1, "1")
    row_end = row_expr(step.get("row_end") or row_start, row_start)
    if step.get("col_start") or step.get("col_end"):
        cols = col_range(str(step.get("col_start") or "A"), str(step.get("col_end") or step.get("col_start") or "A"))
    else:
        cols = parse_col_list(str(step.get("cols") or "A"))
    if not cols:
        cols = [1]
    row_step = int(step.get("row_step") or 0)
    col_step = int(step.get("col_step") or 0)
    cursor_key = step.get("cursor_key") or f"{table}:{step.get('col_start') or 'A'}:{step.get('row_start') or 1}"
    persist_cursor = bool(step.get("cursor_persist", True)) if (row_step or col_step) else False
    lines = ["__tbl_text := \"\""]

    lines.append(f'Log("table_paste flags row={1 if use_selected_row else 0} col={1 if use_selected_col else 0} key={selection_key}")')
    if row_step or col_step:
        col_start_idx = cols[0]
        lines.extend(
            [
                f'__cursor_key := "{ahk_quote(str(cursor_key))}"',
                f"__row_start := {row_start}",
                f"__col_start := {col_start_idx}",
            ]
        )
        if use_selected_row or use_selected_col:
            lines.append("__row_sel := \"\"")
            lines.append("__col_sel := \"\"")
        if use_selected_row:
            lines.extend(
                [
                    f"__row_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"row\", \"\")",
                    f"if (__row_sel = \"\")",
                    f"    __row_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"row\", \"\")",
                    f"if (__row_sel != \"\")",
                    f"    __row_start := __row_sel + 0",
                    "if (__row_start < 1)",
                    "    __row_start := 1",
                ]
            )
        if use_selected_col:
            lines.extend(
                [
                    f"__col_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"col\", \"\")",
                    f"if (__col_sel = \"\")",
                    f"    __col_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"col\", \"\")",
                    f"if (__col_sel != \"\")",
                    f"    if __col_sel is integer",
                    f"        __col_start := __col_sel + 0",
                    f"    else",
                    f"        __col_start := Table_ColToIndex(__col_sel)",
                    "if (__col_start < 1)",
                    f"    __col_start := {col_start_idx}",
                ]
            )
        if use_selected_row or use_selected_col:
            lines.extend(
                [
                    f'Log("table_paste selection key={selection_key} row_sel=" . __row_sel . " col_sel=" . __col_sel)',
                    f'Log("table_paste resolved row=" . __row_start . " col=" . __col_start)',
                ]
            )
        if persist_cursor and not (use_selected_row or use_selected_col):
            lines.extend(
                [
                    "__cursor_ini := A_ScriptDir . \\\"\\\\table_cursor.ini\\\"",
                    "IniRead, __row_start, %__cursor_ini%, cursor, %__cursor_key%_row, %__row_start%",
                    "IniRead, __col_start, %__cursor_ini%, cursor, %__cursor_key%_col, %__col_start%",
                ]
            )
        lines.extend(
            [
                f"TableCursor_Init(__cursor_key, __row_start, __col_start, {1 if (use_selected_row or use_selected_col) else 0})",
                "__row_start := TableCursor_Row(__cursor_key)",
                "__row_end := __row_start",
                "__col_start := TableCursor_Col(__cursor_key)",
                "__col_end := __col_start",
                "Loop, % (__row_end - __row_start + 1)",
                "{",
                "    __r := __row_start + A_Index - 1",
                "    __line := Table_Get(\"" + ahk_quote(table) + "\", __r, __col_start)",
                "    if (A_Index = 1)",
                "        __tbl_text := __line",
                "    else",
                "        __tbl_text .= \"`n\" . __line",
                "}",
                "Clipboard := __tbl_text",
                "ClipWait, 1",
                f"TableCursor_Advance(__cursor_key, {row_step}, {col_step})",
            ]
        )
        if persist_cursor:
            lines.extend(
                [
                    "IniWrite, % TableCursor_Row(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_row",
                    "IniWrite, % TableCursor_Col(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_col",
                ]
            )
    else:
        lines.extend(
            [
                f"__row_start := {row_start}",
                f"__row_end := {row_end}",
            ]
        )
        if use_selected_row or use_selected_col:
            lines.append("__row_sel := \"\"")
            lines.append("__col_sel := \"\"")
        if use_selected_row:
            lines.extend(
                [
                    f"__row_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"row\", \"\")",
                    f"if (__row_sel = \"\")",
                    f"    __row_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"row\", \"\")",
                    f"if (__row_sel != \"\")",
                    f"    __row_start := __row_sel + 0",
                    "if (__row_start < 1)",
                    "    __row_start := 1",
                    "__row_end := __row_start",
                ]
            )
        col_start_idx = cols[0]
        if use_selected_col:
            lines.extend(
                [
                    f"__col_sel := TableSelection_Read(\"{ahk_quote(selection_key)}\", \"col\", \"\")",
                    f"if (__col_sel = \"\")",
                    f"    __col_sel := TableSelection_Read(\"{ahk_quote(table)}\", \"col\", \"\")",
                    f"if (__col_sel != \"\")",
                    f"    if __col_sel is integer",
                    f"        __col_start := __col_sel + 0",
                    f"    else",
                    f"        __col_start := Table_ColToIndex(__col_sel)",
                    "if (__col_start < 1)",
                    f"    __col_start := {col_start_idx}",
                    "__col_end := __col_start",
                ]
            )
            cols = [col_start_idx]
        else:
            lines.extend([f"__col_start := {col_start_idx}", f"__col_end := {cols[-1]}"])
        if use_selected_row or use_selected_col:
            lines.extend(
                [
                    f'Log("table_paste selection key={selection_key} row_sel=" . __row_sel . " col_sel=" . __col_sel)',
                    f'Log("table_paste resolved row=" . __row_start . " col=" . __col_start)',
                ]
            )
        lines.extend(
            [
                "Loop, % (__row_end - __row_start + 1)",
                "{",
                "    __r := __row_start + A_Index - 1",
                "    __line := \"\"",
            ]
        )
        if use_selected_col:
            lines.append(f'    __line := Table_Get("{ahk_quote(table)}", __r, __col_start)')
        else:
            for idx, col in enumerate(cols):
                if idx == 0:
                    lines.append(f'    __line := Table_Get("{ahk_quote(table)}", __r, {col})')
                else:
                    lines.append(f'    __line .= "`t" . Table_Get("{ahk_quote(table)}", __r, {col})')
        lines.extend(
            [
                "    if (A_Index = 1)",
                "        __tbl_text := __line",
                "    else",
                "        __tbl_text .= \"`n\" . __line",
                "}",
                "Clipboard := __tbl_text",
                "ClipWait, 1",
            ]
        )
    if mode == "inactive":
        lines.append(f'TargetHwnd := WinExist("{window}")')
        if window_exe:
            lines.append("if !TargetHwnd")
            lines.append("{")
            lines.append(f'    TargetHwnd := WinExist("ahk_exe {window_exe}")')
            lines.append("}")
        lines.append("if (TargetHwnd)")
        lines.append("{")
        lines.append("    ControlFocus,, ahk_id %TargetHwnd%")
        lines.append("    SendMessage, 0x302, 0, 0,, ahk_id %TargetHwnd%")
        lines.append("    ControlSend,, {Ctrl down}v{Ctrl up}, ahk_id %TargetHwnd%")
        lines.append("}")
        lines.append("else")
        lines.append("    SendInput, ^v")
    else:
        lines.append(f'TargetHwnd := WinExist("{window}")')
        if window_exe:
            lines.append("if !TargetHwnd")
            lines.append("{")
            lines.append(f'    TargetHwnd := WinExist("ahk_exe {window_exe}")')
            lines.append("}")
        lines.append("if (TargetHwnd)")
        lines.append("{")
        lines.append("    WinActivate, ahk_id %TargetHwnd%")
        lines.append("    WinWaitActive, ahk_id %TargetHwnd%,, 0.8")
        lines.append("    ControlFocus,, ahk_id %TargetHwnd%")
        lines.append("}")
        lines.append("SendInput, ^v")
    return lines

def render_table_excel_read(step: Dict[str, Any]) -> List[str]:
    table = str(step.get("table") or "default")
    mode = str(step.get("excel_mode") or "file")
    path = str(step.get("excel_path") or "")
    sheet = str(step.get("excel_sheet") or "")
    cell = str(step.get("excel_cell") or "")
    python_cmd = str(step.get("python") or '"%PythonExe%"')
    script_path = f"%A_ScriptDir%\\data_table_action.py"
    temp_file = "%A_ScriptDir%\\table_tmp.txt"
    def row_expr(value, fallback):
        if value is None or value == "":
            return fallback
        if isinstance(value, (int, float)):
            return str(int(value))
        token = str(value).strip()
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            return token
        return token.replace("$", "")

    row = row_expr(step.get("row") or step.get("index") or 1, "1")
    col_idx = col_to_index(str(step.get("col") or step.get("col_start") or "A")) or 1

    def cmd_quote(value: str) -> str:
        return value.replace('"', '""')

    parts = [python_cmd, f'"{script_path}"', "--read"]
    parts.append(f'--mode "{cmd_quote(mode)}"')
    if path:
        parts.append(f'--path "{cmd_quote(path)}"')
    if sheet:
        parts.append(f'--sheet "{cmd_quote(sheet)}"')
    if cell:
        parts.append(f'--cell "{cmd_quote(cell)}"')
    cmd = " ".join(parts)
    lines = [
        f'RunWait, %ComSpec% /c "{cmd} > ""{temp_file}""", , Hide',
        f"FileRead, __tbl_val, {temp_file}",
        f'Table_Set("{ahk_quote(table)}", {row}, {col_idx}, __tbl_val)',
    ]
    return lines


def render_table_excel_write(step: Dict[str, Any]) -> List[str]:
    table = str(step.get("table") or "default")
    def row_expr(value, fallback):
        if value is None or value == "":
            return fallback
        if isinstance(value, (int, float)):
            return str(int(value))
        token = str(value).strip()
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            return token
        return token.replace("$", "")

    row = row_expr(step.get("row") or step.get("index") or 1, "1")
    col_idx = col_to_index(str(step.get("col") or step.get("col_start") or "A")) or 1
    mode = str(step.get("excel_mode") or "file")
    path = str(step.get("excel_path") or "")
    sheet = str(step.get("excel_sheet") or "")
    cell = str(step.get("excel_cell") or "")
    python_cmd = str(step.get("python") or '"%PythonExe%"')
    script_path = f"%A_ScriptDir%\\data_table_action.py"
    temp_file = "%A_ScriptDir%\\table_tmp.txt"

    def cmd_quote(value: str) -> str:
        return value.replace('"', '""')

    parts = [python_cmd, f'"{script_path}"', "--write"]
    parts.append(f'--mode "{cmd_quote(mode)}"')
    if path:
        parts.append(f'--path "{cmd_quote(path)}"')
    if sheet:
        parts.append(f'--sheet "{cmd_quote(sheet)}"')
    if cell:
        parts.append(f'--cell "{cmd_quote(cell)}"')
    cmd = " ".join(parts)
    lines = [
        f'__tbl_val := Table_Get("{ahk_quote(table)}", {row}, {col_idx})',
        f'FileDelete, {temp_file}',
        f'FileAppend, %__tbl_val%, {temp_file}',
        f'RunWait, %ComSpec% /c "{cmd} --value-file ""{temp_file}""", , Hide',
    ]
    return lines

def render_coord_mode(step: Dict[str, Any]) -> List[str]:
    mode = step.get("mode", "Screen")
    return [f"CoordMode, Mouse, {mode}"]


def render_type_text(step: Dict[str, Any]) -> List[str]:
    text = str(step.get("text", ""))
    send_mode = str(step.get("send_mode", "raw")).lower()
    mode = str(step.get("mode", "active")).lower()
    if mode == "inactive":
        window = str(step.get("window") or "").strip()
        if not window:
            return ["; type_text inactive skipped, no window"]
        if send_mode in ("input", "sendinput"):
            lines = [f"ControlSend,, {text}, {window}"]
        elif send_mode in ("event", "send"):
            lines = [f"ControlSend,, {text}, {window}"]
        else:
            escaped = text.replace("%", "%%")
            lines = [f"ControlSend,, {escaped}, {window}"]
    elif send_mode in ("input", "sendinput"):
        lines = [f"SendInput, {text}"]
    elif send_mode in ("event", "send"):
        lines = [f"Send, {text}"]
    else:
        escaped = text.replace("%", "%%")
        lines = [f"SendRaw, {escaped}"]
    delay = step.get("delay")
    if delay:
        lines.append(f"Sleep, {delay}")
    return lines


def render_run(step: Dict[str, Any]) -> List[str]:
    command = step.get("command") or step.get("program")
    if not command:
        return ["; run_program skipped, no command provided"]
    return [f'Run, {command}']


def render_terminate(step: Dict[str, Any]) -> List[str]:
    target = step.get("process") or step.get("name")
    if not target:
        return ["; terminate_program skipped, no process name provided"]
    return [f'Process, Close, {target}']


def render_remote_notify(step: Dict[str, Any]) -> List[str]:
    title = ahk_quote(str(step.get("title") or "MacroRelay"))
    message = ahk_quote(str(step.get("message") or "동작이 완료되었습니다."))
    level = ahk_quote(str(step.get("level") or "success"))
    lines = [
        '__notify_script := A_ScriptDir . "\\remote_notify.py"',
        'if !FileExist(__notify_script)',
        '    __notify_script := A_ScriptDir . "\\..\\remote_notify.py"',
        f'__notify_message := "{message}"',
    ]
    if step.get("include_last_ocr"):
        lines.extend([
            'if (OCR_LastText != "")',
            '    __notify_message .= "`n`n" . OCR_LastText',
        ])
    lines.extend([
        '__notify_file := A_Temp . "\\macrorelay-notify-" . A_TickCount . ".txt"',
        'FileDelete, %__notify_file%',
        'FileAppend, %__notify_message%, %__notify_file%, UTF-8',
        f'__notify_cmd := """" . PythonExe . """ """ . __notify_script . """ --title ""{title}"" --level ""{level}"" --message-file """ . __notify_file . """"',
        'Log("remote notify: " . __notify_message)',
    ])
    lines.append('RunWait, %__notify_cmd%, , Hide' if step.get("wait_delivery") else 'Run, %__notify_cmd%, , Hide')
    if step.get("wait_delivery"):
        lines.append('FileDelete, %__notify_file%')
    return lines


def render_image_search(
    step: Dict[str, Any],
    assets: Dict[str, Dict[str, Any]],
    step_index: int,
) -> List[str]:
    alias = step.get("asset")
    found_var = f"__step_found_{step_index}"
    if not alias:
        return [
            f"{found_var} := 0",
            '; image_search skipped, no asset alias',
            'Log("image search configuration error: no asset selected")',
            'SetRunResult("FAILED", "IMAGE_NOT_CONFIGURED", "이미지 서치에 검색 이미지가 지정되지 않았습니다.")',
        ]
    asset_file = resolve_asset_filename(alias, assets)
    if not asset_file:
        safe_alias = ahk_quote(str(alias))
        return [
            f"{found_var} := 0",
            f"; image_search skipped, asset {alias} missing",
            f'Log("image search configuration error: missing asset - {safe_alias}")',
            f'SetRunResult("FAILED", "IMAGE_ASSET_MISSING", "이미지 자산을 찾을 수 없습니다: {safe_alias}")',
        ]
    image_path = rf"{asset_file}"
    lines: List[str] = [
        f"{found_var} := 0",
        f'ImagePath := A_ScriptDir . "\\assets\\{image_path}"',
        "if !FileExist(ImagePath)",
        "{",
        '    Log("이미지 파일 없음: " . ImagePath)',
        '    SetRunResult("FAILED", "IMAGE_FILE_MISSING", "이미지 파일을 찾을 수 없습니다: " . ImagePath)',
        '    MsgBox, 16, 매크로 오류, 이미지 파일을 찾을 수 없습니다: "%ImagePath%"., 1',
        "    Return",
        "}",
        "ImageSpec := ImagePath",
    ]
    variation = step.get("variation", 16)
    options = step.get("options", "")
    if isinstance(options, list):
        options = " ".join(str(item) for item in options)
    if not options:
        option_parts: List[str] = []
        trans = step.get("trans")
        if variation is not None:
            option_parts.append(f"*{variation}")
        if trans:
            option_parts.append(f"*Trans{trans}")
        options = " ".join(option_parts)
    region_mode = str(step.get("region_mode") or "screen").lower()
    if region_mode not in {"screen", "window", "client"}:
        region_mode = "screen"
    region_coords = str(step.get("region_coords") or "").lower()
    region_window = str(step.get("region_window") or "")
    region_window_exe = str(step.get("region_window_exe") or "")
    click_payload = step.get("click") if isinstance(step.get("click"), dict) else {}
    if not region_window and isinstance(click_payload, dict):
        region_window = str(click_payload.get("window") or "")
    if not region_window_exe and isinstance(click_payload, dict):
        region_window_exe = str(click_payload.get("window_exe") or "")
    region_prefix = f"__img_region_{step_index}"
    region_base_x = f"{region_prefix}_base_x"
    region_base_y = f"{region_prefix}_base_y"
    region_width = f"{region_prefix}_width"
    region_height = f"{region_prefix}_height"
    region_hwnd = f"{region_prefix}_hwnd"
    def is_zero_area(entry: Any) -> bool:
        if not isinstance(entry, list) or len(entry) < 4:
            return False
        try:
            left, top, right, bottom = (int(value) for value in entry[:4])
        except (TypeError, ValueError):
            return False
        return left == right or top == bottom

    regions_list = []
    regions = step.get("regions")
    if isinstance(regions, list):
        for entry in regions:
            if isinstance(entry, list) and len(entry) >= 4 and not is_zero_area(entry):
                regions_list.append(entry[:4])
    if not regions_list:
        region = step.get("region")
        if isinstance(region, dict):
            left = region.get("left", "VirtualLeft" if region_mode == "screen" else 0)
            top = region.get("top", "VirtualTop" if region_mode == "screen" else 0)
            right = region.get("right", "VirtualRight" if region_mode == "screen" else region_width)
            bottom = region.get("bottom", "VirtualBottom" if region_mode == "screen" else region_height)
        elif isinstance(region, list) and len(region) >= 4 and not is_zero_area(region):
            left, top, right, bottom = region[:4]
        else:
            if region_mode == "screen":
                left, top, right, bottom = "VirtualLeft", "VirtualTop", "VirtualRight", "VirtualBottom"
            else:
                left, top = 0, 0
                right, bottom = region_width, region_height
        regions_list.append([left, top, right, bottom])
    if bool(step.get("fallback_full_region")) and region_mode != "screen":
        dynamic_full_region = [0, 0, region_width, region_height]
        if dynamic_full_region not in regions_list:
            regions_list.append(dynamic_full_region)
    has_explicit_region = bool(step.get("regions")) or step.get("region") is not None
    if region_mode == "screen":
        region_coords = "screen"
    elif region_coords not in {"relative", "screen"}:
        region_coords = "screen" if has_explicit_region else "relative"
    if options:
        lines.append(f'ImageSpec := "{options} " . ImagePath')
    lines.extend(
        [
            "FoundImageW := 0",
            "FoundImageH := 0",
            "__image_hbm := LoadPicture(ImagePath)",
            "if (__image_hbm)",
            "{",
            "    VarSetCapacity(__image_bitmap, 32, 0)",
            '    DllCall("GetObject", "ptr", __image_hbm, "int", 32, "ptr", &__image_bitmap)',
            '    FoundImageW := NumGet(__image_bitmap, 4, "int")',
            '    FoundImageH := Abs(NumGet(__image_bitmap, 8, "int"))',
            '    DllCall("Gdi32\\DeleteObject", "ptr", __image_hbm)',
            "}",
            "SourceImageW := FoundImageW",
            "SourceImageH := FoundImageH",
            "FoundScaleX := 1.0",
            "FoundScaleY := 1.0",
        ]
    )
    region_window_text = region_window.replace('"', '""')
    region_window_exe_text = region_window_exe.replace('"', '""')
    lines.extend(
        [
            f'{region_prefix}Mode := "{region_mode}"',
            f'{region_prefix}Coords := "{region_coords}"',
            f"{region_prefix}UseBase := ({region_prefix}Coords = \"relative\")",
            f"{region_base_x} := 0",
            f"{region_base_y} := 0",
            f"{region_width} := A_ScreenWidth",
            f"{region_height} := A_ScreenHeight",
            f'{region_hwnd} := ""',
            f'if ({region_prefix}Mode = "window" or {region_prefix}Mode = "client")',
            "{",
            f'    if ("{region_window_text}" != "")',
            f'        {region_hwnd} := WinExist("{region_window_text}")',
            f'    if (!{region_hwnd} and "{region_window_exe_text}" != "")',
            f'        {region_hwnd} := WinExist("ahk_exe {region_window_exe_text}")',
            f"    if (!{region_hwnd})",
            f'        {region_hwnd} := WinExist("A")',
            f"    if ({region_hwnd})",
            "    {",
            f'        if ({region_prefix}Mode = "window")',
            "        {",
            f"            WinGetPos, {region_base_x}, {region_base_y}, {region_width}, {region_height}, ahk_id %{region_hwnd}%",
            "        }",
            "        else",
            "        {",
            f"            VarSetCapacity({region_prefix}_rect, 16, 0)",
            f'            DllCall("GetClientRect", "ptr", {region_hwnd}, "ptr", &{region_prefix}_rect)',
            f"            {region_width} := NumGet({region_prefix}_rect, 8, \"int\")",
            f"            {region_height} := NumGet({region_prefix}_rect, 12, \"int\")",
            f"            VarSetCapacity({region_prefix}_pt, 8, 0)",
            f"            NumPut(0, {region_prefix}_pt, 0, \"int\")",
            f"            NumPut(0, {region_prefix}_pt, 4, \"int\")",
            f'            DllCall("ClientToScreen", "ptr", {region_hwnd}, "ptr", &{region_prefix}_pt)',
            f"            {region_base_x} := NumGet({region_prefix}_pt, 0, \"int\")",
            f"            {region_base_y} := NumGet({region_prefix}_pt, 4, \"int\")",
            "        }",
            "    }",
            "}",
        ]
    )
    lines.append(
        f'Log("image search region mode: " . {region_prefix}Mode . " coords=" . {region_prefix}Coords . " base=" . {region_base_x} . "," . {region_base_y} . " size=" . {region_width} . "," . {region_height})'
    )
    timeout = step.get("timeout")
    poll_delay = max(10, int(step.get("poll_delay", 60) or 60))
    search_profile = str(step.get("search_profile") or "balanced").lower()
    if search_profile not in {"fast", "balanced", "precise"}:
        search_profile = "balanced"
    confidence = step.get("confidence")
    engine = str(step.get("engine") or "ahk").lower()
    if engine not in {"ahk", "opencv"}:
        engine = "ahk"

    def _expr(value: Any) -> str:
        return value if isinstance(value, str) else str(value)

    def emit_region_vars(idx, left, top, right, bottom, indent=""):
        left_expr = _expr(left)
        top_expr = _expr(top)
        right_expr = _expr(right)
        bottom_expr = _expr(bottom)
        left_var = f"{region_prefix}_left_{idx}"
        top_var = f"{region_prefix}_top_{idx}"
        right_var = f"{region_prefix}_right_{idx}"
        bottom_var = f"{region_prefix}_bottom_{idx}"
        base_expr = f"({region_prefix}UseBase ? {region_base_x} : 0)"
        base_expr_y = f"({region_prefix}UseBase ? {region_base_y} : 0)"
        lines.append(f"{indent}{left_var} := {base_expr} + ({left_expr})")
        lines.append(f"{indent}{top_var} := {base_expr_y} + ({top_expr})")
        lines.append(f"{indent}{right_var} := {base_expr} + ({right_expr})")
        lines.append(f"{indent}{bottom_var} := {base_expr_y} + ({bottom_expr})")
        # A recorded window may be resized between recording and playback.
        # Keep relative regions inside the *current* client area.  The old
        # behavior changed oversized relative values into screen coordinates,
        # which silently searched the top-left of the desktop.
        lines.append(f"{indent}if ({region_prefix}UseBase)")
        lines.append(f"{indent}{{")
        lines.append(f"{indent}    {left_var} := Max({region_base_x}, Min({left_var}, {region_base_x} + {region_width} - 1))")
        lines.append(f"{indent}    {top_var} := Max({region_base_y}, Min({top_var}, {region_base_y} + {region_height} - 1))")
        lines.append(f"{indent}    {right_var} := Max({region_base_x}, Min({right_var}, {region_base_x} + {region_width} - 1))")
        lines.append(f"{indent}    {bottom_var} := Max({region_base_y}, Min({bottom_var}, {region_base_y} + {region_height} - 1))")
        lines.append(f"{indent}    if ({right_var} <= {left_var} or {bottom_var} <= {top_var})")
        lines.append(f"{indent}    {{")
        lines.append(f"{indent}        {left_var} := {region_base_x}")
        lines.append(f"{indent}        {top_var} := {region_base_y}")
        lines.append(f"{indent}        {right_var} := {region_base_x} + {region_width} - 1")
        lines.append(f"{indent}        {bottom_var} := {region_base_y} + {region_height} - 1")
        lines.append(f"{indent}    }}")
        lines.append(f"{indent}}}")
        return left_var, top_var, right_var, bottom_var

    def emit_opencv_search(region_vars, indent=""):
        cmd = f'{indent}OpenCvCmd := """" . PythonExe . """ """ . OpenCvScript . """ --image """ . ImagePath . """"'
        lines.append(f"{indent}FileDelete, %OpenCvOut%")
        lines.append(cmd)
        for left_var, top_var, right_var, bottom_var in region_vars:
            lines.append(
                f'{indent}OpenCvCmd .= " --region " . {left_var} . "," . {top_var} . "," . {right_var} . "," . {bottom_var}'
            )
        lines.append(
            f'{indent}OpenCvCmd .= " --threshold " . OpenCvThreshold . " --profile " . OpenCvProfile . " --timeout " . OpenCvTimeout . " --poll " . OpenCvPoll . " --out """ . OpenCvOut . """"'
        )
        lines.append(f"{indent}RunWait, %OpenCvCmd%,, Hide")
        lines.append(f"{indent}OpenCvExit := ErrorLevel")
        lines.append(f'{indent}OpenCvResult := ""')
        lines.append(f"{indent}OpenCvResultSize := 0")
        lines.append(f"{indent}if (OpenCvExit = 0)")
        lines.append(f"{indent}{{")
        lines.append(f"{indent}    OpenCvResultDeadline := A_TickCount + OpenCvTimeout + 2000")
        lines.append(f"{indent}    Loop")
        lines.append(f"{indent}    {{")
        lines.append(f"{indent}        if FileExist(OpenCvOut)")
        lines.append(f"{indent}        {{")
        lines.append(f"{indent}            FileGetSize, OpenCvResultSize, %OpenCvOut%")
        lines.append(f"{indent}            if (OpenCvResultSize > 0)")
        lines.append(f"{indent}                break")
        lines.append(f"{indent}        }}")
        lines.append(f"{indent}        if (A_TickCount >= OpenCvResultDeadline)")
        lines.append(f"{indent}            break")
        lines.append(f"{indent}        Sleep, 20")
        lines.append(f"{indent}    }}")
        lines.append(f"{indent}}}")
        lines.append(f"{indent}if FileExist(OpenCvOut)")
        lines.append(f"{indent}    FileRead, OpenCvResult, %OpenCvOut%")
        lines.append(f"{indent}StringReplace, OpenCvResult, OpenCvResult, `r`n, , All")
        lines.append(f"{indent}StringReplace, OpenCvResult, OpenCvResult, `n, , All")
        lines.append(f"{indent}StringSplit, OpenCvParts, OpenCvResult, `,")
        lines.append(f'{indent}if (OpenCvResult != "")')
        lines.append(f"{indent}    FileDelete, %OpenCvOut%")
        lines.append(f'{indent}if (OpenCvParts1 = "ERROR")')
        lines.append(f"{indent}{{")
        lines.append(f"{indent}    OpenCvErrorCode := OpenCvParts2")
        lines.append(f"{indent}    OpenCvErrorDetail := OpenCvParts3")
        lines.append(f'{indent}    Log("opencv search error: code=" . OpenCvErrorCode . " exit=" . OpenCvExit . " detail=" . OpenCvErrorDetail)')
        lines.append(f"{indent}    ErrorLevel := 2")
        lines.append(f"{indent}}}")
        lines.append(f'{indent}else if (OpenCvExit != 0)')
        lines.append(f"{indent}{{")
        lines.append(f'{indent}    OpenCvErrorCode := "PROCESS_FAILED"')
        lines.append(f'{indent}    OpenCvErrorDetail := "exit=" . OpenCvExit')
        lines.append(f'{indent}    Log("opencv search error: " . OpenCvErrorDetail)')
        lines.append(f"{indent}    ErrorLevel := 2")
        lines.append(f"{indent}}}")
        lines.append(f'{indent}else if (OpenCvParts1 = "FOUND")')
        lines.append(f"{indent}{{")
        lines.append(f"{indent}    FoundX := OpenCvParts2")
        lines.append(f"{indent}    FoundY := OpenCvParts3")
        lines.append(f"{indent}    OpenCvBestScore := OpenCvParts4")
        lines.append(f"{indent}    FoundImageW := OpenCvParts5")
        lines.append(f"{indent}    FoundImageH := OpenCvParts6")
        lines.append(f"{indent}    FoundScaleX := (SourceImageW > 0 ? FoundImageW / SourceImageW : 1.0)")
        lines.append(f"{indent}    FoundScaleY := (SourceImageH > 0 ? FoundImageH / SourceImageH : 1.0)")
        lines.append(f"{indent}    ErrorLevel := 0")
        lines.append(f"{indent}}}")
        lines.append(f'{indent}else if (OpenCvParts1 = "NOTFOUND")')
        lines.append(f"{indent}{{")
        lines.append(f"{indent}    OpenCvBestScore := OpenCvParts2")
        lines.append(f"{indent}    ErrorLevel := 1")
        lines.append(f"{indent}}}")
        lines.append(f"{indent}else")
        lines.append(f"{indent}{{")
        lines.append(f'{indent}    OpenCvErrorCode := "OUTPUT_INVALID"')
        lines.append(f'{indent}    OpenCvErrorDetail := "result missing or invalid: " . OpenCvResult')
        lines.append(f'{indent}    Log("opencv search output invalid: " . OpenCvOut . " value=" . OpenCvResult)')
        lines.append(f"{indent}    ErrorLevel := 2")
        lines.append(f"{indent}}}")

    if engine == "opencv":
        lines.append(f'Log("image search start: {alias} | engine=opencv | " . ImagePath)')
        lines.append('OpenCvErrorCode := ""')
        lines.append('OpenCvErrorDetail := ""')
    else:
        lines.append(f'Log("image search start: {alias} | engine=ahk | " . ImageSpec)')
    # Never let a failed/invalid search reuse coordinates from an earlier
    # attempt. Empty coordinates are especially dangerous because AHK treats
    # them as the current mouse position in MouseClick.
    lines.append('FoundX := ""')
    lines.append('FoundY := ""')
    if engine == "opencv":
        threshold = 0.86
        if confidence is not None:
            try:
                threshold = max(0.5, min(0.99, float(confidence) / 100))
            except Exception:
                threshold = 0.86
        elif variation is not None:
            try:
                threshold = max(0.1, min(0.99, (100 - float(variation)) / 100))
            except Exception:
                threshold = 0.86
        lines.append('OpenCvScript := A_ScriptDir . "\\opencv_search.py"')
        lines.append('OpenCvProcessId := DllCall("GetCurrentProcessId")')
        lines.append(f'OpenCvOut := A_Temp . "\\MacroRelay_OpenCV_" . OpenCvProcessId . "_{step_index}.txt"')
        lines.append("if !FileExist(OpenCvScript)")
        lines.append("{")
        lines.append('    Log("opencv helper missing: " . OpenCvScript)')
        lines.append('    SetRunResult("FAILED", "OPENCV_HELPER_MISSING", "OpenCV 도우미 파일을 찾을 수 없습니다: " . OpenCvScript)')
        lines.append('    MsgBox, 16, macro error, OpenCV helper missing: "%OpenCvScript%"., 1')
        lines.append("    Return")
        lines.append("}")
        lines.append(f"OpenCvThreshold := {threshold}")
        lines.append(f'OpenCvProfile := "{search_profile}"')
        lines.append(f"OpenCvTimeout := {int(timeout or 0)}")
        lines.append(f"OpenCvPoll := {poll_delay}")
        lines.append('OpenCvBestScore := ""')
        lines.append('Log("opencv runtime: python=" . PythonExe . " helper=" . OpenCvScript)')
        opencv_region_vars = []
        if len(regions_list) > 1:
            lines.append(f'Log("image search regions: {len(regions_list)} · single process")')
        for idx, region_vals in enumerate(regions_list, start=1):
            left, top, right, bottom = region_vals
            left_var, top_var, right_var, bottom_var = emit_region_vars(idx, left, top, right, bottom)
            opencv_region_vars.append((left_var, top_var, right_var, bottom_var))
            label = "image search region" if len(regions_list) == 1 else f"image search region {idx}"
            lines.append(
                f'Log("{label}: " . {left_var} . ", " . {top_var} . ", " . {right_var} . ", " . {bottom_var})'
            )
        # Most recorded assets are searched at the exact size they were
        # captured.  AHK's in-process ImageSearch can resolve that common case
        # without paying Python/OpenCV process startup and multi-scale costs.
        # OpenCV remains the fallback for scaled or difficult matches.
        lines.append("OpenCvNativeHit := 0")
        for left_var, top_var, right_var, bottom_var in opencv_region_vars:
            lines.append("if (!OpenCvNativeHit)")
            lines.append("{")
            lines.append(
                f"    ImageSearch, FoundX, FoundY, %{left_var}%, %{top_var}%, %{right_var}%, %{bottom_var}%, %ImageSpec%"
            )
            lines.append("    if (ErrorLevel = 0)")
            lines.append("    {")
            lines.append("        FoundX += Floor(FoundImageW / 2)")
            lines.append("        FoundY += Floor(FoundImageH / 2)")
            lines.append("        OpenCvNativeHit := 1")
            lines.append("    }")
            lines.append("}")
        lines.append("if (OpenCvNativeHit)")
        lines.append("{")
        lines.append('    OpenCvBestScore := "native"')
        lines.append('    Log("image search fast-path: native exact-size hit")')
        lines.append("    ErrorLevel := 0")
        lines.append("}")
        lines.append("else")
        lines.append("{")
        emit_opencv_search(opencv_region_vars, "    ")
        lines.append("}")
        lines.append("OpenCvSearchStatus := ErrorLevel")
        lines.append('if (OpenCvBestScore != "")')
        lines.append('    Log("opencv best confidence: " . OpenCvBestScore . " threshold=" . OpenCvThreshold)')
        # Log() writes to a file and therefore changes AutoHotkey's global
        # ErrorLevel. Restore the actual OpenCV result before found/not-found
        # branching or an empty coordinate would click the current cursor.
        lines.append("ErrorLevel := OpenCvSearchStatus")
        lines.append('if (ErrorLevel = 0 and (FoundX = "" or FoundY = ""))')
        lines.append("{")
        lines.append('    Log("opencv returned success without coordinates; treating as not found")')
        lines.append("    ErrorLevel := 1")
        lines.append("}")
    else:
        if len(regions_list) == 1:
            left, top, right, bottom = regions_list[0]
            left_var, top_var, right_var, bottom_var = emit_region_vars(1, left, top, right, bottom)
            lines.append(
                f'Log("image search region: " . {left_var} . ", " . {top_var} . ", " . {right_var} . ", " . {bottom_var})'
            )
            search_line = (
                f"ImageSearch, FoundX, FoundY, %{left_var}%, %{top_var}%, %{right_var}%, %{bottom_var}%, %ImageSpec%"
            )
            if timeout:
                lines.append("StartTick := A_TickCount")
                lines.append("Loop")
                lines.append("{")
                lines.append(f"    {search_line}")
                lines.append("    if (ErrorLevel = 2)")
                lines.append("    {")
                lines.append('        Log("image search error (file): " . ImageSpec)')
                lines.append('        MsgBox, 16, 매크로 오류, AutoHotkey 이미지 서치가 이미지 파일을 열지 못했습니다: "%ImagePath%"., 1')
                lines.append("        Return")
                lines.append("    }")
                lines.append("    if (ErrorLevel = 0)")
                lines.append("        break")
                lines.append(f"    if (A_TickCount - StartTick > {timeout})")
                lines.append("    {")
                lines.append("        Break")
                lines.append("    }")
                lines.append(f"    Sleep, {poll_delay}")
                lines.append("}")
            else:
                lines.append(search_line)
        else:
            lines.append(f'Log("image search regions: {len(regions_list)}")')
            lines.append(f"{found_var} := 0")
            for idx, region_vals in enumerate(regions_list, start=1):
                left, top, right, bottom = region_vals
                lines.append(f"; region {idx}")
                left_var, top_var, right_var, bottom_var = emit_region_vars(idx, left, top, right, bottom)
                lines.append(
                    f'Log("image search region {idx}: " . {left_var} . ", " . {top_var} . ", " . {right_var} . ", " . {bottom_var})'
                )
                search_line = (
                    f"ImageSearch, FoundX, FoundY, %{left_var}%, %{top_var}%, %{right_var}%, %{bottom_var}%, %ImageSpec%"
                )
                lines.append(search_line)
                lines.append("if (ErrorLevel = 2)")
                lines.append("{")
                lines.append('    Log("image search error (file): " . ImageSpec)')
                lines.append('    MsgBox, 16, 매크로 오류, AutoHotkey 이미지 서치가 이미지 파일을 열지 못했습니다: "%ImagePath%"., 1')
                lines.append("    Return")
                lines.append("}")
                lines.append("if (ErrorLevel = 0)")
                lines.append(f"    {found_var} := 1")
                lines.append(f"if ({found_var})")
                lines.append(f"    goto __step_{step_index}_region_done")
            lines.append(f"__step_{step_index}_region_done:")

    if engine == "ahk":
        lines.extend(
            [
                "if (ErrorLevel = 0)",
                "{",
                "    FoundX += Floor(FoundImageW / 2)",
                "    FoundY += Floor(FoundImageH / 2)",
                "}",
            ]
        )
    lines.append("if (ErrorLevel = 2)")
    lines.append("{")
    if engine == "opencv":
        lines.append('    Log("opencv image search failed: " . OpenCvErrorCode . " | " . OpenCvErrorDetail)')
        lines.append('    SetRunResult("FAILED", OpenCvErrorCode, OpenCvErrorDetail)')
        lines.append('    if (OpenCvErrorCode = "IMPORT_FAILED")')
        lines.append('        MsgBox, 16, OpenCV 오류, OpenCV 구성요소를 불러오지 못했습니다.`n설정 > 구성요소 설치에서 OpenCV 설치가 완료됐는지 확인하세요.`n`n상세: %OpenCvErrorDetail%, 1')
        lines.append('    else if (OpenCvErrorCode = "IMAGE_MISSING" or OpenCvErrorCode = "IMAGE_DECODE")')
        lines.append('        MsgBox, 16, OpenCV 오류, 검색 이미지 파일을 열지 못했습니다: "%ImagePath%".`n`n상세: %OpenCvErrorDetail%, 1')
        lines.append('    else')
        lines.append('        MsgBox, 16, OpenCV 오류, OpenCV 이미지 서치 실행에 실패했습니다.`n로그에서 오류 코드를 확인하세요: %OpenCvErrorCode%`n`n상세: %OpenCvErrorDetail%, 1')
    else:
        lines.append('    Log("image search error (file): " . ImagePath)')
        lines.append('    SetRunResult("FAILED", "AHK_IMAGE_ERROR", "AutoHotkey가 이미지 파일을 열지 못했습니다: " . ImagePath)')
        lines.append('    MsgBox, 16, 매크로 오류, AutoHotkey 이미지 서치가 이미지 파일을 열지 못했습니다: "%ImagePath%"., 1')
    lines.append("    Return")
    lines.append("}")
    die_on_missing = step.get("abort_on_fail", True)
    on_fail = step.get("on_fail")
    lines.append("if ErrorLevel")
    if die_on_missing:
        lines.append("{")
        lines.append(f'    Log("화면에서 이미지 미탐지: {alias}")')
        if on_fail:
            lines.append(f'    Log("on_fail jump: {on_fail}")')
        else:
            lines.append(f'    SetRunResult("FAILED", "IMAGE_NOT_FOUND", "화면에서 이미지를 찾지 못했습니다: {ahk_quote(str(alias))}")')
            lines.append(f'    MsgBox, 16, 매크로 오류, 화면에서 이미지를 찾지 못했습니다: "{alias}"., 1')
            lines.append("    Return")
        lines.append("}")
    else:
        lines.append("{")
        lines.append(f'    Log("화면에서 이미지 미탐지(계속): {alias}")')
        lines.append(f'    Log("click skipped: image not found - {alias}")')
        lines.append(f'    SetRunResult("PARTIAL", "IMAGE_NOT_FOUND", "이미지를 찾지 못해 클릭을 건너뛰었습니다: {ahk_quote(str(alias))}")')
        lines.append("}")
    click_info = step.get("click")
    lines.append("else")
    lines.append("{")
    lines.append(f"    {found_var} := 1")
    lines.append(f'    Log("image found on screen: {alias} at " . FoundX . "," . FoundY)')
    lines.append('    Log("image detected scale: x=" . Round(FoundScaleX, 3) . " y=" . Round(FoundScaleY, 3))')
    if click_info:
        mode = str(click_info.get("mode", "active")).lower()
        offset_values = click_info.get("offset") if isinstance(click_info.get("offset"), list) else [0, 0]
        click_offset = bool(click_info.get("click_offset"))
        if "click_offset" not in click_info:
            click_offset = any(int(value or 0) for value in offset_values[:2])
        click_image = bool(click_info.get("click_image")) if "click_image" in click_info else not click_offset
        if mode != "inactive":
            act_window = click_info.get("window")
            act_exe = click_info.get("window_exe")
            if act_window or act_exe:
                lines.append(f'    ActiveHwnd := WinExist("{act_window or "A"}")')
                if act_exe:
                    lines.append("    if !ActiveHwnd")
                    lines.append("    {")
                    lines.append(f'        ActiveHwnd := WinExist("ahk_exe {act_exe}")')
                    lines.append("    }")
                lines.append("    if (ActiveHwnd)")
                lines.append("    {")
                lines.append("        WinActivate, ahk_id %ActiveHwnd%")
                lines.append("        WinWaitActive, ahk_id %ActiveHwnd%, , 0.5")
                lines.append("    }")
        use_mouse_coord_override = mode != "inactive"
        if use_mouse_coord_override:
            lines.append("    CoordMode, Mouse, Screen")
        def emit_click(info, offset_override=None):
            info_use = dict(info)
            if offset_override is not None:
                info_use["offset"] = offset_override
            if engine == "opencv":
                info_use["_offset_scale_x"] = "FoundScaleX"
                info_use["_offset_scale_y"] = "FoundScaleY"
            if mode == "inactive":
                lines.extend(f"    {line}" for line in render_inactive_click_from_hit(info_use))
            else:
                lines.extend(f"    {line}" for line in render_click_from_hit(info_use))
        effective_offset = (offset_values + [0, 0])[:2]
        if click_image:
            lines.append('    Log("image center click: enabled")')
            emit_click(click_info, [0, 0])
        if click_image and click_offset:
            between_click_delay = max(0, int(click_info.get("between_click_delay", 80) or 0))
            if between_click_delay:
                lines.append(f"    Sleep, {between_click_delay}")
        if click_offset:
            lines.append(
                f'    Log("image offset click: base x={int(effective_offset[0] or 0)} y={int(effective_offset[1] or 0)} scale=" . Round(FoundScaleX, 3) . "," . Round(FoundScaleY, 3))'
            )
            emit_click(click_info)
        if not click_image and not click_offset:
            lines.append('    Log("image click skipped: center and offset are both disabled")')
        else:
            total_points = int(click_image) + int(click_offset)
            lines.append(f'    Log("click executed: {alias} points={total_points}")')
        keys = click_info.get("keys")
        if keys:
            key_mode = str(click_info.get("key_mode") or ("inactive" if mode == "inactive" else "active")).lower()
            key_text = ahk_quote(str(keys))
            lines.append(f'    KeyPayload := "{key_text}"')
            lines.append('    if (SubStr(KeyPayload, 1, 1) != "{")')
            lines.append('        KeyPayload := "{Text}" . KeyPayload')
            if key_mode == "inactive":
                window = click_info.get("window", "A")
                window_exe = click_info.get("window_exe")
                lines.append(f'    KeyTargetHwnd := WinExist("{window}")')
                if window_exe:
                    lines.append("    if !KeyTargetHwnd")
                    lines.append("    {")
                    lines.append(f'        KeyTargetHwnd := WinExist("ahk_exe {window_exe}")')
                    lines.append("    }")
                lines.append("    if (KeyTargetHwnd)")
                lines.append("        ControlSend,, %KeyPayload%, ahk_id %KeyTargetHwnd%")
                lines.append("    else")
                lines.append("        SendInput, %KeyPayload%")
            else:
                lines.append("    SendInput, %KeyPayload%")
        sleep_after = step.get("sleep_after")
        if sleep_after:
            lines.append(f"    Sleep, {sleep_after}")
        if use_mouse_coord_override:
            lines.append("    CoordMode, Mouse, %MacroMouseCoordMode%")
    lines.append("}")
    return lines


def render_browser_action(step: Dict[str, Any], browser_fast: bool = False) -> List[str]:
    selector = str(step.get("selector") or "")
    if not selector:
        return ["; browser_action skipped, no selector"]
    title = str(step.get("title") or "")
    action = str(step.get("browser_action") or step.get("action_type") or "click")
    value = str(step.get("value") or "")
    port = int(step.get("port", 9222) or 9222)
    server_port = int(step.get("server_port", 9233) or 9233)
    timeout = int(step.get("timeout") or 2000)
    poll_delay = int(step.get("poll_delay") or 100)
    script_path = f"%A_ScriptDir%\\browser_action.py"
    python_cmd = str(step.get("python") or "").strip()
    if not python_cmd:
        python_cmd_part = '"%PythonExe%"'
    elif python_cmd.lower().endswith(".exe"):
        python_cmd_part = f'"{python_cmd}"'
    else:
        python_cmd_part = python_cmd
    payload = {
        "cmd": "action",
        "port": port,
        "title": title,
        "selector": selector,
        "action": action,
        "value": value,
        "timeout": timeout,
        "poll": poll_delay,
    }
    if step.get("prefer_active"):
        payload["prefer_active"] = True
    payload_text = ahk_quote(json.dumps(payload, ensure_ascii=False))
    server_cmd = f'{python_cmd_part} "{script_path}" --server --port {port} --server-port %BrowserServerPort%'
    lines: List[str] = []
    if not browser_fast and not step.get("no_server_check"):
        lines.extend(
            [
                "if (BrowserServerStarted != 1)",
                "{",
                f"    BrowserServerPort := {server_port}",
                f"    Run, {server_cmd}, , Hide",
                "    Sleep, 300",
                "    BrowserServerStarted := 1",
                "}",
            ]
        )
    lines.extend(
        [
             f'Log("browser_action start: {action}")',
             f'BrowserPayload := "{payload_text}"',
             "BrowserResp := BrowserAction_Send(BrowserPayload, BrowserServerPort)",
             "if (BrowserResp = \"\")",
             "{",
             "    Log(\"browser_action failed: no response\")",
             "}",
            "else if (InStr(BrowserResp, \"\"\"ok\"\":true\") or InStr(BrowserResp, \"\"\"ok\"\": true\"))",
            "{",
            "    ; ok",
            "}",
            "else",
            "{",
            "    Log(\"browser_action failed: \" . BrowserResp)",
            "}",
            f'Log("browser_action end: {action}")',
        ]
    )
    sleep = step.get("sleep_after")
    if sleep:
        lines.append(f"Sleep, {sleep}")
    return lines


def render_ocr(step: Dict[str, Any]) -> List[str]:
    mode = str(step.get("mode", "region")).lower()
    lang = str(step.get("lang", "eng+kor"))
    output_path = str(step.get("output_path", ""))
    output_format = str(step.get("output_format", "csv"))
    output_append = bool(step.get("output_append", True))
    excel_mode = str(step.get("excel_mode", "none")).lower()
    excel_path = str(step.get("excel_path", ""))
    excel_sheet = str(step.get("excel_sheet", ""))
    excel_cell = str(step.get("excel_cell", ""))
    title = str(step.get("title", ""))
    selector = str(step.get("selector", ""))
    port = int(step.get("port", 9222) or 9222)
    server_port = int(step.get("server_port", 9233) or 9233)
    prefer_active = bool(step.get("prefer_active", False))
    timeout = int(step.get("timeout", 2000) or 2000)
    poll_delay = int(step.get("poll_delay", 50) or 50)
    python_cmd = str(step.get("python") or "").strip()
    script_path = "ocr_action.py"
    selector_file = "ocr_selector.txt"
    title_file = "ocr_title.txt"
    table_name_file = "ocr_table.txt"

    def cmd_quote(value: str) -> str:
        return value.replace('"', '""')

    python_raw = python_cmd.strip().strip('"')
    if not python_raw:
        python_token = '"%PythonExe%"'
        python_assignment = "__python_path := PythonExe"
    else:
        python_is_path = python_raw.lower().endswith(".exe") or Path(python_raw).exists()
        if python_is_path:
            python_token = f'"{cmd_quote(python_raw)}"'
        else:
            python_token = python_raw
        python_assignment = f'__python_path := "{cmd_quote(python_raw)}"'
    parts = [python_token, f'"{script_path}"']
    parts.append(f'--mode "{cmd_quote(mode)}"')
    parts.append(f'--lang "{cmd_quote(lang)}"')
    if mode == "browser":
        if selector:
            parts.append(f'--selector-file "{selector_file}"')
        if title:
            parts.append(f'--title "{cmd_quote(title)}"')
        parts.append(f"--port {port}")
    else:
        region = step.get("region") or []
        if len(region) >= 4:
            parts.append(f"--left {int(region[0])} --top {int(region[1])} --right {int(region[2])} --bottom {int(region[3])}")
    if output_path:
        parts.append(f'--also-output "{cmd_quote(output_path)}"')
        parts.append(f'--also-format "{cmd_quote(output_format)}"')
        parts.append(f'--also-append {"1" if output_append else "0"}')
    if excel_mode and excel_mode != "none":
        parts.append(f'--excel-mode "{cmd_quote(excel_mode)}"')
        if excel_path:
            parts.append(f'--excel-path "{cmd_quote(excel_path)}"')
        if excel_sheet:
            parts.append(f'--excel-sheet "{cmd_quote(excel_sheet)}"')
        if excel_cell:
            parts.append(f'--excel-cell "{cmd_quote(excel_cell)}"')
    cmd = " ".join(parts)
    temp_file = "ocr_last.txt"
    err_file = "ocr_err.txt"
    cmd = f'{cmd} --output "{temp_file}" --output-format "txt" --append 0'
    ocr_cmd_var = "__ocr_cmd"
    browser_script = "browser_action.py"
    browser_cmd = ""
    browser_direct_cmd = ""
    table_update_helper = "%A_ScriptDir%\\table_update.py"
    ocr_path_var = "__ocr_path"
    ocr_err_var = "__ocr_err_path"
    lines = [
        'Log("ocr start")',
        '__script_dir := A_ScriptDir',
        f'__ocr_script := __script_dir . "\\{script_path}"',
        f'__browser_script := __script_dir . "\\{browser_script}"',
        f'__selector_path := __script_dir . "\\{selector_file}"',
        f'__selector_text := "{ahk_quote(selector)}"' if selector else '__selector_text := ""',
        f'__title_path := __script_dir . "\\{title_file}"',
        f'__title_text := "{ahk_quote(title)}"' if title else '__title_text := ""',
        "Log(\"ocr selector len: \" . StrLen(__selector_text))",
        "if (__selector_text != \"\")",
        "{",
        "    FileDelete, %__selector_path%",
        "    FileAppend, %__selector_text%, %__selector_path%",
        "    FileGetSize, __SelSize, %__selector_path%",
        "    Log(\"ocr selector file size: \" . __SelSize)",
        "}",
        "if (__title_text != \"\")",
        "{",
        "    FileDelete, %__title_path%",
        "    FileEncoding, UTF-8-RAW",
        "    FileAppend, %__title_text%, %__title_path%",
        "    FileEncoding, UTF-8",
        "}",
        f'{ocr_path_var} := __script_dir . "\\{temp_file}"',
        f'{ocr_err_var} := __script_dir . "\\{err_file}"',
        f"FileDelete, %{ocr_path_var}%",
        f"FileDelete, %{ocr_err_var}%",
        "__q := Chr(34)",
        "__ocr_file := FileOpen(" + ocr_path_var + ', "w")',
        "if (__ocr_file)",
        "{",
        "    __ocr_file.Close()",
        "}",
        "__ocr_err_file := FileOpen(" + ocr_err_var + ', "w")',
        "if (__ocr_err_file)",
        "{",
        "    __ocr_err_file.Close()",
        "}",
    ]
    lines.extend(
        [
            python_assignment,
            f'{ocr_cmd_var} := __q . __python_path . __q . " " . __q . __ocr_script . __q',
            f'{ocr_cmd_var} .= " --mode ""{cmd_quote(mode)}"""',
            f'{ocr_cmd_var} .= " --lang ""{cmd_quote(lang)}"""',
        ]
    )
    if mode == "browser":
        if selector:
            lines.append(f'{ocr_cmd_var} .= " --selector-file " . __q . __selector_path . __q')
        if title:
            lines.append(f'{ocr_cmd_var} .= " --title-file " . __q . __title_path . __q')
        lines.append(f'{ocr_cmd_var} .= " --port {port}"')
    else:
        region = step.get("region") or []
        if len(region) >= 4:
            lines.append(
                f'{ocr_cmd_var} .= " --left {int(region[0])} --top {int(region[1])} --right {int(region[2])} --bottom {int(region[3])}"'
            )
    if output_path:
        lines.append(f'{ocr_cmd_var} .= " --also-output ""{cmd_quote(output_path)}"""')
        lines.append(f'{ocr_cmd_var} .= " --also-format ""{cmd_quote(output_format)}"""')
        lines.append(f'{ocr_cmd_var} .= " --also-append {"1" if output_append else "0"}"')
    if excel_mode and excel_mode != "none":
        lines.append(f'{ocr_cmd_var} .= " --excel-mode ""{cmd_quote(excel_mode)}"""')
        if excel_path:
            lines.append(f'{ocr_cmd_var} .= " --excel-path ""{cmd_quote(excel_path)}"""')
        if excel_sheet:
            lines.append(f'{ocr_cmd_var} .= " --excel-sheet ""{cmd_quote(excel_sheet)}"""')
        if excel_cell:
            lines.append(f'{ocr_cmd_var} .= " --excel-cell ""{cmd_quote(excel_cell)}"""')
    lines.append(
        f'{ocr_cmd_var} .= " --output " . __q . {ocr_path_var} . __q . " --output-format ""txt"" --append 0"'
    )
    if mode == "browser" and selector:
        browser_payload = {
            "cmd": "action_to_file",
            "port": port,
            "title": title,
            "selector": selector,
            "action": "extract_text",
            "value": "",
            "timeout": timeout,
            "poll": poll_delay,
            "prefer_active": prefer_active,
            "output": temp_file,
        }
        payload_text = ahk_quote(json.dumps(browser_payload, ensure_ascii=False))
        server_cmd = f'{python_token} "%A_ScriptDir%\\{browser_script}" --server --port {port} --server-port %BrowserServerPort%'
        lines.extend(
            [
                'Log("ocr dom start")',
                "if (BrowserServerStarted != 1)",
                "{",
                f"    BrowserServerPort := {server_port}",
                "    BrowserAction_Send(\"{\"\"cmd\"\": \"\"shutdown\"\"}\", BrowserServerPort)",
                "    Sleep, 150",
                f"    Run, {server_cmd}, , Hide",
                "    Sleep, 300",
                "    BrowserServerStarted := 1",
                "}",
                f'BrowserPayload := "{payload_text}"',
                "BrowserResp := BrowserAction_Send(BrowserPayload, BrowserServerPort)",
                "__retry := 0",
                "while (__retry < 3 && (BrowserResp = \"\" || InStr(BrowserResp, \"unknown command\")))",
                "{",
                "    if (BrowserResp != \"\")",
                "        Log(\"ocr dom retry: \" . BrowserResp)",
                "    BrowserAction_Send(\"{\"\"cmd\"\": \"\"shutdown\"\"}\", BrowserServerPort)",
                "    Sleep, 120",
                "    BrowserServerPort := BrowserServerPort + 1",
                "    BrowserServerStarted := 0",
                f"    Run, {server_cmd}, , Hide",
                "    Sleep, 300",
                "    BrowserServerStarted := 1",
                "    BrowserResp := BrowserAction_Send(BrowserPayload, BrowserServerPort)",
                "    __retry += 1",
                "}",
                "if (BrowserResp = \"\")",
                "{",
                f"    FileAppend, no response, %{ocr_err_var}%",
                "}",
                "else if (InStr(BrowserResp, \"\"\"ok\"\":true\") or InStr(BrowserResp, \"\"\"ok\"\": true\"))",
                "{",
                "    ; ok",
                "}",
                "else",
                "{",
                f"    FileAppend, %BrowserResp%, %{ocr_err_var}%",
                "}",
                'Log("ocr dom end")',
                f"FileRead, __OcrErr, %{ocr_err_var}%",
                f"FileGetSize, __OcrSize, %{ocr_path_var}%",
                f"FileGetSize, __ErrSize, %{ocr_err_var}%",
                'Log("ocr file size: " . __OcrSize . ", err size: " . __ErrSize)',
                "if (__OcrErr)",
                "{",
                '    Log("ocr err: " . __OcrErr)',
                "}",
                f"if (!FileExist({ocr_path_var}))",
                "{",
                '    Log("ocr output missing")',
                f"    FileAppend,, %{ocr_path_var}%",
                "}",
                "if (__OcrErr or __OcrSize <= 3)",
                "{",
                f"    FileDelete, %{ocr_err_var}%",
                f'    RunWait, %{ocr_cmd_var}%, , Hide',
                '    Log("ocr fallback exit: " . ErrorLevel)',
                f"    if (!FileExist({ocr_path_var}))",
                "    {",
                '        Log("ocr output missing")',
                f"        FileAppend,, %{ocr_path_var}%",
                "    }",
                f"    FileRead, __OcrErr, %{ocr_err_var}%",
                f"    FileGetSize, __OcrSize, %{ocr_path_var}%",
                f"    FileGetSize, __ErrSize, %{ocr_err_var}%",
                '    Log("ocr fallback size: " . __OcrSize . ", err size: " . __ErrSize)',
                "    if (__OcrErr)",
                "    {",
                '        Log("ocr fallback err: " . __OcrErr)',
                "    }",
                "}",
            ]
        )
    else:
        lines.extend([f'RunWait, %{ocr_cmd_var}%, , Hide'])
    lines.extend(
        [
            f"FileRead, OCR_LastText, %{ocr_path_var}%",
            '__ocr_success := (OCR_LastText != "")',
            'Log("ocr text len: " . StrLen(OCR_LastText))',
            f"FileRead, __OcrErr, %{ocr_err_var}%",
            "if (__OcrErr)",
            "{",
            '    Log("ocr error: " . __OcrErr)',
            "}",
        ]
    )
    table_name = step.get("table")
    if table_name:
        table_row = step.get("table_row")
        table_col = step.get("table_col")
        table_col_index = col_to_index(str(table_col or "A")) or 1
        row_step = int(step.get("table_row_step") or 0)
        col_step = int(step.get("table_col_step") or 0)
        persist_cursor = bool(step.get("table_cursor_persist", False)) if (row_step or col_step) else False
        cursor_key = step.get("table_cursor_key") or f"{table_name}:A:{table_row or 1}"
        lines.extend(
            [
                '__table_file := A_ScriptDir . "\\data_tables.json"',
                f'__value_file := {ocr_path_var}',
                python_assignment,
                '__table_update := A_ScriptDir . "\\table_update.py"',
                f'__table_name := "{ahk_quote(str(table_name))}"',
                f'__table_name_path := __script_dir . "\\{table_name_file}"',
                f'__table_name_text := "{ahk_quote(str(table_name))}"',
                f'__table_err_path := __script_dir . "\\table_update_err.txt"',
                "if (__table_name_text != \"\")",
                "{",
                "    FileDelete, %__table_name_path%",
                "    FileEncoding, UTF-8-RAW",
                "    FileAppend, %__table_name_text%, %__table_name_path%",
                "    FileEncoding, UTF-8",
                "}",
            ]
        )
        if table_row and table_col:
            if row_step or col_step:
                lines.extend(
                    [
                        f'__cursor_key := "{ahk_quote(str(cursor_key))}"',
                        f"__row_start := {int(table_row)}",
                        f"__col_start := {table_col_index}",
                    ]
                )
                if persist_cursor:
                    lines.extend(
                        [
                            "__cursor_ini := A_ScriptDir . \"\\table_cursor.ini\"",
                            "IniRead, __row_start, %__cursor_ini%, cursor, %__cursor_key%_row, %__row_start%",
                            "IniRead, __col_start, %__cursor_ini%, cursor, %__cursor_key%_col, %__col_start%",
                        ]
                    )
                lines.extend(
                    [
                        "TableCursor_Init(__cursor_key, __row_start, __col_start)",
                        "__row_start := TableCursor_Row(__cursor_key)",
                        "__col_start := TableCursor_Col(__cursor_key)",
                        f'Table_Set("{ahk_quote(str(table_name))}", __row_start, __col_start, OCR_LastText)',
                        f"TableCursor_Advance(__cursor_key, {row_step}, {col_step})",
                    ]
                )
                if persist_cursor:
                    lines.extend(
                        [
                            "IniWrite, % TableCursor_Row(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_row",
                            "IniWrite, % TableCursor_Col(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_col",
                        ]
                    )
                lines.extend(
                    [
                        "if (FileExist(__table_update)) {",
                        "    FileDelete, %__table_err_path%",
                        '    __table_cmd := __q . __python_path . __q . " " . __q . __table_update . __q',
                        '    __table_cmd .= " --file " . __q . __table_file . __q',
                        '    __table_cmd .= " --table-file " . __q . __table_name_path . __q',
                        '    __table_cmd .= " --row " . __row_start . " --col " . __col_start',
                        '    __table_cmd .= " --value-file " . __q . __value_file . __q',
                        '    __table_cmd .= " 2> " . __q . __table_err_path . __q',
                        '    __table_cmd_file := A_ScriptDir . "\\table_update.cmd"',
                        "    FileDelete, %__table_cmd_file%",
                        "    FileEncoding, UTF-8-RAW",
                        "    FileAppend, @echo off`r`n, %__table_cmd_file%",
                        "    FileAppend, chcp 65001 > nul`r`n, %__table_cmd_file%",
                        "    FileAppend, %__table_cmd%, %__table_cmd_file%",
                        "    FileEncoding, UTF-8",
                        '    RunWait, %ComSpec% /c ""%__table_cmd_file%"", , Hide',
                        '    Log("ocr table_update exit: " . ErrorLevel)',
                        "    FileGetSize, __TableErrSize, %__table_err_path%",
                        "    if (__TableErrSize > 0)",
                        "    {",
                        "        FileRead, __TableErr, %__table_err_path%",
                        '        Log("ocr table_update err: " . __TableErr)',
                        "    }",
                        "}",
                    ]
                )
            else:
                lines.append(
                    f'Table_Set("{ahk_quote(str(table_name))}", {int(table_row)}, {table_col_index}, OCR_LastText)'
                )
                lines.extend(
                    [
                        "if (FileExist(__table_update)) {",
                        "    FileDelete, %__table_err_path%",
                        '    __table_cmd := __q . __python_path . __q . " " . __q . __table_update . __q',
                        '    __table_cmd .= " --file " . __q . __table_file . __q',
                        '    __table_cmd .= " --table-file " . __q . __table_name_path . __q',
                        f'    __table_cmd .= " --row {int(table_row)} --col {table_col_index}"',
                        '    __table_cmd .= " --value-file " . __q . __value_file . __q',
                        '    __table_cmd .= " 2> " . __q . __table_err_path . __q',
                        '    __table_cmd_file := A_ScriptDir . "\\table_update.cmd"',
                        "    FileDelete, %__table_cmd_file%",
                        "    FileEncoding, UTF-8-RAW",
                        "    FileAppend, @echo off`r`n, %__table_cmd_file%",
                        "    FileAppend, chcp 65001 > nul`r`n, %__table_cmd_file%",
                        "    FileAppend, %__table_cmd%, %__table_cmd_file%",
                        "    FileEncoding, UTF-8",
                        '    RunWait, %ComSpec% /c ""%__table_cmd_file%"", , Hide',
                        '    Log("ocr table_update exit: " . ErrorLevel)',
                        "    FileGetSize, __TableErrSize, %__table_err_path%",
                        "    if (__TableErrSize > 0)",
                        "    {",
                        "        FileRead, __TableErr, %__table_err_path%",
                        '        Log("ocr table_update err: " . __TableErr)',
                        "    }",
                        "}",
                    ]
                )
        else:
            lines.append(f'Table_Add("{ahk_quote(str(table_name))}", OCR_LastText)')
            lines.extend(
                [
                    "if (FileExist(__table_update)) {",
                    "    FileDelete, %__table_err_path%",
                    '    __table_cmd := __q . __python_path . __q . " " . __q . __table_update . __q',
                    '    __table_cmd .= " --file " . __q . __table_file . __q',
                    '    __table_cmd .= " --table-file " . __q . __table_name_path . __q',
                    '    __table_cmd .= " --append --value-file " . __q . __value_file . __q',
                    '    __table_cmd .= " 2> " . __q . __table_err_path . __q',
                    '    __table_cmd_file := A_ScriptDir . "\\table_update.cmd"',
                    "    FileDelete, %__table_cmd_file%",
                    "    FileEncoding, UTF-8-RAW",
                    "    FileAppend, @echo off`r`n, %__table_cmd_file%",
                    "    FileAppend, chcp 65001 > nul`r`n, %__table_cmd_file%",
                    "    FileAppend, %__table_cmd%, %__table_cmd_file%",
                    "    FileEncoding, UTF-8",
                    '    RunWait, %ComSpec% /c ""%__table_cmd_file%"", , Hide',
                    '    Log("ocr table_update exit: " . ErrorLevel)',
                    "    FileGetSize, __TableErrSize, %__table_err_path%",
                    "    if (__TableErrSize > 0)",
                    "    {",
                    "        FileRead, __TableErr, %__table_err_path%",
                    '        Log("ocr table_update err: " . __TableErr)',
                    "    }",
                    "}",
                ]
            )
    lines.append('Log("ocr end")')
    return lines


def render_ocr_engine(step: Dict[str, Any]) -> List[str]:
    """Render OCR step using the background OCR engine server.
    
    Falls back to legacy render_ocr() if the engine is not available.
    """
    ocr_action_type = str(step.get("ocr_action", "extract")).lower()
    mode = str(step.get("mode", "region")).lower()
    
    # Browser mode always uses legacy path
    if mode == "browser":
        return render_ocr(step)
    
    lang = str(step.get("lang", "eng+kor"))
    profile = str(step.get("profile", "auto"))
    expect_text = str(step.get("expect_text", ""))
    regex_pattern = str(step.get("regex", ""))
    whitelist = str(step.get("whitelist", ""))
    find_text = str(step.get("find_text", ""))
    match_mode = str(step.get("match_mode", "contains"))
    engine_preference = str(step.get("engine_preference", "auto"))
    capture_mode = str(step.get("capture_mode", "screen"))
    window_title = str(step.get("window_title", ""))
    coord_base = str(step.get("coord_base", "screen"))
    click_offset_x = int(step.get("click_offset_x", 0) or 0)
    click_offset_y = int(step.get("click_offset_y", 0) or 0)
    number_condition = str(step.get("number_condition", ""))
    number_value = float(step.get("number_value", 0) or 0)
    minimum_confidence = max(0, min(int(step.get("minimum_confidence", 35) or 0), 100))
    position_priority = str(step.get("position_priority", "top_left"))
    store_var = str(step.get("store_var", ""))
    
    region = step.get("region") or []
    region_json = json.dumps(list(region[:4]) if len(region) >= 4 else [0, 0, 0, 0])
    
    # Build JSON request payload
    payload_dict = {
        "cmd": "ocr",
        "region": list(region[:4]) if len(region) >= 4 else [0, 0, 0, 0],
        "capture_mode": capture_mode,
        "window_title": window_title,
        "window_hwnd": 0,
        "coord_base": coord_base,
        "lang": lang,
        "profile": profile,
        "expect_text": expect_text,
        "regex": regex_pattern,
        "whitelist": whitelist,
        "find_text": find_text if ocr_action_type in ("find_text", "find_click", "find_click_offset") else "",
        "match_mode": match_mode,
        "engine_preference": engine_preference,
        "ocr_action": ocr_action_type,
        "number_condition": number_condition if ocr_action_type == "number_condition" else "",
        "number_value": number_value if ocr_action_type in ("number_condition", "extract_number") else 0,
        "minimum_confidence": minimum_confidence / 100.0,
        "position_priority": position_priority,
        "debug": False,
    }
    payload_json = json.dumps(payload_dict, ensure_ascii=False)
    payload_ahk = ahk_quote(payload_json)
    
    lines: List[str] = [
        'Log("ocr_engine start")',
    ]
    
    # Start OCR engine server if not already running
    lines.extend([
        'if (OcrEngineStarted != 1) {',
        '    __ocr_engine_script := A_ScriptDir . "\\ocr_engine.py"',
        '    if (FileExist(__ocr_engine_script)) {',
        '        __ocr_start_cmd := __q . PythonExe . __q . " " . __q . __ocr_engine_script . __q . " --server --port " . OcrEnginePort . " --log-file"',
        '        Run, %__ocr_start_cmd%, , Hide',
        '        Sleep, 800',
        '        OcrEngineStarted := 1',
        '        Log("ocr engine started on port " . OcrEnginePort)',
        '    } else {',
        '        Log("ocr engine script not found, using legacy")',
        '    }',
        '}',
        '',
    ])
    
    # Send OCR request
    lines.extend([
        f'__ocr_payload := "{payload_ahk}"',
        '__ocr_resp := OcrEngine_Send(__ocr_payload, OcrEnginePort)',
        '',
        '; Check if engine responded',
        'if (__ocr_resp = "") {',
        '    Log("ocr engine no response, retry...")',
        '    Sleep, 500',
        '    __ocr_resp := OcrEngine_Send(__ocr_payload, OcrEnginePort)',
        '}',
        '',
    ])
    
    # Parse response or fallback to legacy
    lines.extend([
        'if (__ocr_resp = "") {',
        '    Log("ocr engine unavailable, falling back to legacy")',
        '    OcrEngineStarted := 0',
    ])
    # Inline legacy fallback
    legacy_lines = render_ocr(step)
    for ll in legacy_lines:
        lines.append('    ' + ll)
    lines.extend([
        '} else {',
        '    ; Parse engine response',
        '    OCR_LastText := OcrEngine_ParseText(__ocr_resp)',
        '    OCR_LastConfidence := OcrEngine_ParseField(__ocr_resp, "confidence")',
        '    OCR_LastEngine := OcrEngine_ParseField(__ocr_resp, "engine")',
        '    __ocr_success := OcrEngine_IsSuccess(__ocr_resp)',
        '    Log("ocr engine result: " . StrLen(OCR_LastText) . " chars, conf=" . OCR_LastConfidence . ", engine=" . OCR_LastEngine)',
    ])
    
    # Handle click actions
    if ocr_action_type in ("find_click", "find_click_offset"):
        lines.extend([
            '    if (__ocr_success) {',
            '        OcrEngine_ParseCenter(__ocr_resp, OCR_FoundX, OCR_FoundY)',
            f'        __click_x := OCR_FoundX + {click_offset_x}',
            f'        __click_y := OCR_FoundY + {click_offset_y}',
            '        Log("ocr click at: " . __click_x . "," . __click_y)',
            '        CoordMode, Mouse, Screen',
            '        Click, %__click_x%, %__click_y%',
            '        SetLastClick(__click_x, __click_y, "ocr")',
            '        CoordMode, Mouse, %MacroMouseCoordMode%',
            '    }',
        ])
    
    # Handle number extraction
    if ocr_action_type in ("extract_number", "number_condition"):
        lines.extend([
            '    OCR_LastNumber := OcrEngine_ParseField(__ocr_resp, "extracted_number")',
            '    if (OCR_LastNumber = "null" or OCR_LastNumber = "")',
            '        OCR_LastNumber := 0',
            '    OCR_LastNumber += 0  ; Force numeric',
            '    Log("ocr number: " . OCR_LastNumber)',
        ])
    
    # Handle variable storage
    if store_var:
        var_name = ahk_quote(store_var)
        lines.append(f'    {var_name} := OCR_LastText')
    
    lines.append('}')
    
    # Handle table storage (reuse existing pattern from render_ocr)
    table_name = step.get("table")
    if table_name:
        # Copy the table storage logic from render_ocr
        table_row = step.get("table_row")
        table_col = step.get("table_col")
        table_col_index = col_to_index(str(table_col or "A")) or 1
        row_step_val = int(step.get("table_row_step") or 0)
        col_step_val = int(step.get("table_col_step") or 0)
        if table_row and table_col:
            if row_step_val or col_step_val:
                persist_cursor = bool(step.get("table_cursor_persist", False))
                cursor_key = step.get("table_cursor_key") or f"{table_name}:A:{table_row or 1}"
                lines.extend([
                    f'__cursor_key := "{ahk_quote(str(cursor_key))}"',
                    f'__row_start := {int(table_row)}',
                    f'__col_start := {table_col_index}',
                ])
                if persist_cursor:
                    lines.extend([
                        '__cursor_ini := A_ScriptDir . "\\table_cursor.ini"',
                        'IniRead, __row_start, %__cursor_ini%, cursor, %__cursor_key%_row, %__row_start%',
                        'IniRead, __col_start, %__cursor_ini%, cursor, %__cursor_key%_col, %__col_start%',
                    ])
                lines.extend([
                    'TableCursor_Init(__cursor_key, __row_start, __col_start)',
                    '__row_start := TableCursor_Row(__cursor_key)',
                    '__col_start := TableCursor_Col(__cursor_key)',
                    f'Table_Set("{ahk_quote(str(table_name))}", __row_start, __col_start, OCR_LastText)',
                    f'TableCursor_Advance(__cursor_key, {row_step_val}, {col_step_val})',
                ])
                if persist_cursor:
                    lines.extend([
                        'IniWrite, % TableCursor_Row(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_row',
                        'IniWrite, % TableCursor_Col(__cursor_key), %__cursor_ini%, cursor, %__cursor_key%_col',
                    ])
            else:
                lines.append(f'Table_Set("{ahk_quote(str(table_name))}", {int(table_row)}, {table_col_index}, OCR_LastText)')
        else:
            lines.append(f'Table_Add("{ahk_quote(str(table_name))}", OCR_LastText)')
    
    # Handle output path (existing feature)
    output_path = str(step.get("output_path", ""))
    if output_path:
        output_format = str(step.get("output_format", "csv"))
        output_append = bool(step.get("output_append", True))
        # File output is handled by legacy path; for engine path write directly
        lines.extend([
            f'if (OCR_LastText != "") {{',
            f'    __out_path := "{ahk_quote(output_path)}"',
            f'    FileAppend, %OCR_LastText%`n, %__out_path%',
            '}',
        ])
    
    # Excel output
    excel_mode = str(step.get("excel_mode", "none")).lower()
    if excel_mode and excel_mode != "none":
        # Excel handling is complex; delegate to legacy for now
        pass
    
    lines.append('Log("ocr_engine end")')
    return lines


def resolve_asset_filename(alias: str, assets: Dict[str, Dict[str, Any]]) -> Optional[str]:
    record = assets.get(alias)
    if record:
        return Path(record["file"]).name
    direct = ASSET_DIR / alias
    if direct.exists() and direct.is_file():
        return direct.name
    for ext in (".png", ".jpg", ".jpeg", ".bmp"):
        candidate = ASSET_DIR / f"{alias}{ext}"
        if candidate.exists():
            return candidate.name
    return None


def render_click_from_hit(click_info: Dict[str, Any]) -> List[str]:
    click_type = click_info.get("type", "relative")
    button = click_info.get("button", "Left")
    count = click_info.get("count", 1)
    retry_count = int(click_info.get("retry_count", 1) or 1)
    retry_delay = int(click_info.get("retry_delay", 80) or 80)
    show_cursor = bool(click_info.get("show_cursor", True))
    if click_type == "absolute":
        x = click_info.get("x", 0)
        y = click_info.get("y", 0)
        x_expr = str(x)
        y_expr = str(y)
    else:
        offsets = click_info.get("offset", [0, 0])
        offset_x, offset_y = (offsets + [0, 0])[:2]
        scale_x = str(click_info.get("_offset_scale_x") or "")
        scale_y = str(click_info.get("_offset_scale_y") or "")
        x_expr = ahk_scaled_expression("FoundX", offset_x, scale_x)
        y_expr = ahk_scaled_expression("FoundY", offset_y, scale_y)
    lines = [
        f"ClickX := {x_expr}",
        f"ClickY := {y_expr}",
        f"RetryCount := {max(1, retry_count)}",
        f"RetryDelay := {max(0, retry_delay)}",
        "Loop, %RetryCount%",
        "{",
        "    Attempt := A_Index",
        f"    MouseClick, {button}, %ClickX%, %ClickY%, {count}",
    ]
    if show_cursor:
        lines.extend(
            [
                "    ToolTip, Click %ClickX%, %ClickY%",
                "    Sleep, 80",
                "    ToolTip",
            ]
        )
    lines.extend(
        [
            "    if (RetryDelay > 0 and Attempt < RetryCount)",
            "        Sleep, %RetryDelay%",
            "}",
            'SetLastClick(ClickX, ClickY, "image")',
        ]
    )
    return lines


def render_inactive_click_from_hit(click_info: Dict[str, Any]) -> List[str]:
    button = click_info.get("button", "Left")
    clicks = click_info.get("count", 1)
    window = click_info.get("window", "A")
    window_exe = click_info.get("window_exe")
    method = str(click_info.get("method", "auto")).lower()
    target_control = str(click_info.get("target_control") or "")
    target_hwnd_text = str(click_info.get("target_hwnd") or "")
    retry_count = int(click_info.get("retry_count", 2) or 2)
    retry_delay = int(click_info.get("retry_delay", 100) or 100)
    retry_post = bool(click_info.get("retry_post", False))
    show_cursor = bool(click_info.get("show_cursor", True))
    offsets = click_info.get("offset", [0, 0])
    offset_x, offset_y = (offsets + [0, 0])[:2]
    scale_x = str(click_info.get("_offset_scale_x") or "")
    scale_y = str(click_info.get("_offset_scale_y") or "")
    lines = []
    if offset_x or offset_y:
        lines.append(f"FoundClickX := {ahk_scaled_expression('FoundX', offset_x, scale_x)}")
        lines.append(f"FoundClickY := {ahk_scaled_expression('FoundY', offset_y, scale_y)}")
        x_expr = "FoundClickX"
        y_expr = "FoundClickY"
    else:
        x_expr = "FoundX"
        y_expr = "FoundY"
    lines.append(f'TargetHwnd := WinExist("{ahk_quote(str(window))}")')
    if window_exe:
        lines.append("if !TargetHwnd")
        lines.append("{")
        lines.append(f'    TargetHwnd := WinExist("ahk_exe {ahk_quote(str(window_exe))}")')
        lines.append("}")
    lines.append("if !TargetHwnd")
    lines.append("{")
    lines.append(f'    Log("inactive click failed: window not found - {window}")')
    lines.append("    Return")
    lines.append("}")
    lines.append("WinGetClass, TargetClass, ahk_id %TargetHwnd%")
    lines.append("UsePost := 0")
    lines.append("DirectPost := 0")
    lines.append("ManualChild := 0")
    lines.append('if (TargetClass = "Chrome_WidgetWin_1" or TargetClass = "Chrome_WidgetWin_0" or TargetClass = "Chrome Legacy Window")')
    lines.append("    UsePost := 1")
    lines.append('if (InStr(TargetClass, "HwndWrapper"))')
    lines.append("    UsePost := 1")
    lines.append('if (InStr(TargetClass, "EVA_") or InStr(TargetClass, "Qt") or InStr(TargetClass, "LDPlayer"))')
    lines.append("    UsePost := 1")
    lines.append(f'Log("inactive click target: window={window} exe={window_exe or ""}")')
    lines.append(f'Log("inactive click target class: " . TargetClass . " method: {method}")')
    lines.append("if (\"%s\" = \"postmessage\")" % method)
    lines.append("    UsePost := 1")
    lines.append("if (\"%s\" = \"direct_postmessage\")" % method)
    lines.append("{")
    lines.append("    UsePost := 1")
    lines.append("    DirectPost := 1")
    lines.append("}")
    lines.append("if (\"%s\" = \"handle_probe\")" % method)
    lines.append("    UsePost := 1")
    lines.append("if (\"%s\" = \"controlclick\")" % method)
    lines.append("    UsePost := 0")
    lines.append("ClickHwnd := TargetHwnd")
    lines.append("ClickLeft := 0")
    lines.append("ClickTop := 0")
    lines.append('if (InStr(TargetClass, "LDPlayer"))')
    lines.append("{")
    lines.append("    ControlGet, RenderHwnd, Hwnd,, RenderWindow1, ahk_id %TargetHwnd%")
    lines.append("    if (!RenderHwnd)")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, RenderWindow, ahk_id %TargetHwnd%")
    lines.append("    if (!RenderHwnd)")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, Qt5QWindowIcon1, ahk_id %TargetHwnd%")
    lines.append("    if (!RenderHwnd)")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, Qt5QWindowIcon, ahk_id %TargetHwnd%")
    lines.append("    if (RenderHwnd)")
    lines.append("        ClickHwnd := RenderHwnd")
    lines.append("}")
    lines.append("if (UsePost)")
    lines.append("{")
    lines.append('    if (TargetClass = "Chrome_WidgetWin_1" or TargetClass = "Chrome_WidgetWin_0" or TargetClass = "Chrome Legacy Window")')
    lines.append("    {")
    lines.append("        ControlGet, RenderHwnd, Hwnd,, Chrome_RenderWidgetHostHWND1, ahk_id %TargetHwnd%")
    lines.append("        if (!RenderHwnd)")
    lines.append("            ControlGet, RenderHwnd, Hwnd,, Chrome_RenderWidgetHostHWND2, ahk_id %TargetHwnd%")
    lines.append("        if (RenderHwnd)")
    lines.append("            ClickHwnd := RenderHwnd")
    lines.append("    }")
    lines.append("}")
    lines.append("if (DirectPost)")
    lines.append("{")
    lines.append("    ClickHwnd := TargetHwnd")
    lines.append('    Log("inactive click mode: direct top-level PostMessage")')
    lines.append("}")
    if method == "handle_probe":
        lines.append("ManualClickHwnd := 0")
        if target_control:
            lines.append(
                f'ControlGet, ManualClickHwnd, Hwnd,, {ahk_quote(target_control)}, ahk_id %TargetHwnd%'
            )
        try:
            saved_hwnd = int(target_hwnd_text, 0) if target_hwnd_text else 0
        except (TypeError, ValueError):
            saved_hwnd = 0
        if saved_hwnd:
            lines.append("if (!ManualClickHwnd)")
            lines.append("{")
            lines.append(f"    SavedClickHwnd := {saved_hwnd}")
            lines.append('    if (DllCall("IsWindow", "ptr", SavedClickHwnd) and DllCall("GetAncestor", "ptr", SavedClickHwnd, "uint", 2, "ptr") = TargetHwnd)')
            lines.append("        ManualClickHwnd := SavedClickHwnd")
            lines.append("}")
        lines.append("if (ManualClickHwnd)")
        lines.append("{")
        lines.append("    ClickHwnd := ManualClickHwnd")
        lines.append("    ManualChild := 1")
        lines.append('    Log("inactive click handle engine selected: " . ClickHwnd)')
        lines.append("}")
        lines.append("else")
        lines.append('    Log("inactive click handle engine fallback: saved child not found")')
    lines.append(f"ScreenX := {x_expr}")
    lines.append(f"ScreenY := {y_expr}")
    lines.append("if (!DirectPost && !ManualChild)")
    lines.append("{")
    lines.append('    PointValue := (ScreenY << 32) | (ScreenX & 0xFFFFFFFF)')
    lines.append('    PointHwnd := DllCall("WindowFromPoint", "Int64", PointValue, "Ptr")')
    lines.append('    if (PointHwnd and (PointHwnd = TargetHwnd or DllCall("IsChild", "ptr", TargetHwnd, "ptr", PointHwnd)))')
    lines.append("    {")
    lines.append("        ClickHwnd := PointHwnd")
    lines.append('        Log("inactive click child selected by point: " . ClickHwnd)')
    lines.append("    }")
    lines.append("}")
    lines.append("VarSetCapacity(pt, 8, 0)")
    lines.append(f'NumPut({x_expr}, pt, 0, "Int")')
    lines.append(f'NumPut({y_expr}, pt, 4, "Int")')
    lines.append('DllCall("ScreenToClient", "ptr", ClickHwnd, "ptr", &pt)')
    lines.append('ClickX := NumGet(pt, 0, "Int")')
    lines.append('ClickY := NumGet(pt, 4, "Int")')
    lines.append("VarSetCapacity(origin_pt, 8, 0)")
    lines.append('NumPut(0, origin_pt, 0, "Int")')
    lines.append('NumPut(0, origin_pt, 4, "Int")')
    lines.append('DllCall("ClientToScreen", "ptr", ClickHwnd, "ptr", &origin_pt)')
    lines.append('ClickLeft := NumGet(origin_pt, 0, "Int")')
    lines.append('ClickTop := NumGet(origin_pt, 4, "Int")')
    lines.append(f'Log("inactive click screen: " . {x_expr} . "," . {y_expr})')
    lines.append('Log("inactive click client: " . ClickX . "," . ClickY . " origin: " . ClickLeft . "," . ClickTop)')
    lines.append("Log(\"inactive click hwnd: \" . ClickHwnd)")
    lines.append(f"RetryCount := {max(1, retry_count)}")
    lines.append(f"RetryDelay := {max(0, retry_delay)}")
    lines.append(f"RetryPost := {1 if retry_post else 0}")
    lines.append("ClickOk := 0")
    lines.append(f'ClickButton := "{ahk_quote(str(button))}"')
    lines.append('DownMessage := (ClickButton = "Right") ? 0x204 : 0x201')
    lines.append('UpMessage := (ClickButton = "Right") ? 0x205 : 0x202')
    lines.append('DownWParam := (ClickButton = "Right") ? 2 : 1')
    lines.append("Loop, %RetryCount%")
    lines.append("{")
    lines.append("    Attempt := A_Index")
    lines.append("    if (UsePost)")
    lines.append("    {")
    lines.append("        lParam := (ClickY << 16) | (ClickX & 0xFFFF)")
    lines.append("        PostMessage, 0x200, 0, %lParam%, , ahk_id %ClickHwnd%")
    lines.append("        if (!DirectPost)")
    lines.append("            Sleep, 10")
    lines.append("        PostMessage, %DownMessage%, %DownWParam%, %lParam%, , ahk_id %ClickHwnd%")
    lines.append("        PostMessage, %UpMessage%, 0, %lParam%, , ahk_id %ClickHwnd%")
    lines.append("        if (DirectPost)")
    lines.append('            Log("inactive click direct post: mousemove/down/up sent")')
    lines.append("        ClickOk := 1")
    lines.append("        if (!RetryPost)")
    lines.append("            break")
    lines.append("    }")
    lines.append("    else")
    lines.append("    {")
    options = "NA"
    line = f"ControlClick, x%ClickX% y%ClickY%, ahk_id %ClickHwnd%, , {button}, {clicks}, {options}"
    lines.append("        ErrorLevel := 0")
    lines.append(f"        {line}")
    lines.append("        if (!ErrorLevel)")
    lines.append("            ClickOk := 1")
    lines.append("        else")
    lines.append('            Log("inactive click error: " . ErrorLevel)')
    lines.append("    }")
    if show_cursor:
        lines.append("    ToolTip, Click %ClickX%, %ClickY%")
        lines.append("    Sleep, 80")
        lines.append("    ToolTip")
    lines.append("    if (ClickOk)")
    lines.append("        break")
    lines.append("    if (RetryDelay > 0)")
    lines.append("        Sleep, %RetryDelay%")
    lines.append("}")
    lines.append("if (!ClickOk)")
    lines.append("{")
    lines.append('    Log("inactive click failed after retries, trying UIA")')
    lines.append('    UiaOut := A_Temp . "\\uia_click.txt"')
    lines.append('    UiaCmd := """" . PythonExe . """" . " ""%A_ScriptDir%\\uia_click.py"" --x " . ScreenX . " --y " . ScreenY . " --hwnd " . ClickHwnd')
    lines.append('    RunWait, %ComSpec% /c %UiaCmd% > "%UiaOut%" 2>&1, , Hide')
    lines.append("    if (FileExist(UiaOut))")
    lines.append("    {")
    lines.append("        FileRead, UiaResp, %UiaOut%")
    lines.append('        Log("inactive click uia result: " . UiaResp)')
    lines.append("        FileDelete, %UiaOut%")
    lines.append("    }")
    lines.append("}")
    lines.append('SetLastClick(ScreenX, ScreenY, "image-inactive")')
    return lines


def render_step(
    step: Dict[str, Any],
    assets: Dict[str, Dict[str, Any]],
    step_index: int,
    browser_fast: bool = False,
) -> List[str]:
    action = step.get("action")
    if action == "mouse_click":
        return render_mouse_click(step)
    if action == "inactive_click":
        return render_inactive_click(step)
    if action == "wait":
        return render_wait(step)
    if action == "flow_control":
        return render_flow_control(step, step_index)
    if action == "coord_mode":
        return render_coord_mode(step)
    if action == "type_text":
        return render_type_text(step)
    if action == "browser_action":
        return render_browser_action(step, browser_fast)
    if action == "ocr":
        if step.get("ocr_action") and step.get("ocr_action") != "extract":
            return render_ocr_engine(step)
        if step.get("engine_preference") and step.get("engine_preference") != "tesseract":
            return render_ocr_engine(step)
        return render_ocr(step)
    if action == "table_store":
        return render_table_store(step)
    if action == "table_copy":
        return render_table_copy(step)
    if action == "table_paste":
        return render_table_paste(step)
    if action == "table_excel_read":
        return render_table_excel_read(step)
    if action == "table_excel_write":
        return render_table_excel_write(step)
    if action == "set_var":
        return render_set_var(step)
    if action == "calc_var":
        return render_calc_var(step)
    if action == "run_program":
        return render_run(step)
    if action == "terminate_program":
        return render_terminate(step)
    if action == "remote_notify":
        return render_remote_notify(step)
    if action == "call_submacro":
        # call_submacro steps are expanded before rendering.
        return ["; call_submacro expanded"]
    if action == "text_condition":
        return render_text_condition(step, step_index)
    if action == "image_search":
        return render_image_search(step, assets, step_index)
    return [f"; unknown action {action!r}"]


def _expand_macro_steps(
    steps: List[Dict[str, Any]],
    active_stack: Optional[set[str]] = None,
    depth: int = 0,
    max_depth: int = 20,
) -> List[Dict[str, Any]]:
    if active_stack is None:
        active_stack = set()
    if depth >= max_depth:
        return []
    expanded: List[Dict[str, Any]] = []
    for raw_step in steps:
        step = dict(raw_step or {})
        if step.get("action") != "call_submacro":
            expanded.append(step)
            continue
        submacro_name = str(
            step.get("macro") or step.get("name") or step.get("target") or ""
        ).strip()
        if not submacro_name:
            continue
        stack_key = slugify(submacro_name)
        if stack_key in active_stack:
            continue
        submacro_file = macro_path(submacro_name)
        if not submacro_file.exists():
            continue
        try:
            submacro = load_json_file(submacro_file)
        except Exception:
            continue
        child_steps = submacro.get("steps", [])
        if not isinstance(child_steps, list):
            continue
        active_stack.add(stack_key)
        expanded.extend(_expand_macro_steps(child_steps, active_stack, depth + 1, max_depth))
        active_stack.discard(stack_key)
    return expanded


def render_macro_script(
    macro: Dict[str, Any],
    assets: Dict[str, Dict[str, Any]],
    browser_fast: bool = False,
) -> str:
    lines = build_macro_header(macro)
    steps = _expand_macro_steps(list(macro.get("steps", [])))
    total_steps = len(steps)
    start_step = int(macro.get("graph_start_step", 0) or 0)
    end_step = int(macro.get("graph_end_step", 0) or 0)
    if start_step < 1 or start_step > total_steps:
        start_step = 0
    if end_step < 1 or end_step > total_steps:
        end_step = 0
    if any(step.get("action") == "browser_action" for step in steps) or any(
        step.get("action") == "ocr" and str(step.get("mode", "")).lower() == "browser" for step in steps
    ):
        lines.append("BrowserServerStarted := 0")
        lines.append("BrowserServerPort := 9233")
        lines.extend(browser_action_helpers())
        lines.append("")
    has_ocr = any(step.get("action") == "ocr" for step in steps)
    if has_ocr:
        lines.append("OcrEngineStarted := 0")
        lines.append("OcrEnginePort := 9234")
        lines.extend(ocr_engine_helpers())
        lines.append("")
    if any(
        step.get("action") in {"table_store", "table_copy", "table_paste", "table_excel_read", "table_excel_write"}
        for step in steps
    ) or any(step.get("action") == "ocr" and step.get("table") for step in steps):
        lines.append("OCR_LastText := \"\"")
        lines.extend(table_helpers())
        lines.append("TableCursor_EnsureIni(A_ScriptDir . \"\\table_cursor.ini\")")
        referenced_tables = {
            str(step.get("table"))
            for step in steps
            if step.get("table")
        }
        table_data = {
            name: rows
            for name, rows in load_data_tables().items()
            if name in referenced_tables
        }
        if table_data:
            lines.extend(render_table_init(table_data))
    if has_ocr:
        # Initialize OCR result variables
        if 'OCR_LastText := ""' not in '\n'.join(lines):
            lines.append('OCR_LastText := ""')
        lines.append('OCR_LastConfidence := 0')
        lines.append('OCR_LastEngine := ""')
        lines.append('OCR_FoundX := 0')
        lines.append('OCR_FoundY := 0')
        lines.append('OCR_LastNumber := 0')
        lines.append('')
    if start_step:
        lines.append(f"Goto, Step{start_step}")
        lines.append("")
    for count, step in enumerate(steps, start=1):
        label = step.get("label") or step.get("action")
        action = step.get("action")
        repeat = int(step.get("repeat", 1) or 1)
        on_success = step.get("on_success")
        on_fail = step.get("on_fail")
        stop_on_success = bool(step.get("stop_on_success", False))
        on_success_delay = int(step.get("on_success_delay", 0) or 0)
        on_fail_delay = int(step.get("on_fail_delay", 0) or 0)
        if action == "flow_control":
            repeat = 1
            on_success = None
            on_fail = None
            on_success_delay = 0
            on_fail_delay = 0
        if not isinstance(on_success, int):
            on_success = None
        if not isinstance(on_fail, int):
            on_fail = None
        if on_success and (on_success < 1 or on_success > total_steps):
            on_success = None
        if on_fail and (on_fail < 1 or on_fail > total_steps):
            on_fail = None
        if on_success_delay < 0:
            on_success_delay = 0
        if on_fail_delay < 0:
            on_fail_delay = 0

        lines.append(f"; Step {count}: {label}")
        lines.append(f"Step{count}:")
        lines.append(f"SetRunProgress({count})")
        lines.append(f'Log("step start: {count} | {ahk_quote(str(label))}")')
        if repeat > 1:
            lines.append(f"if (__rep{count} = \"\")")
            lines.append(f"    __rep{count} := 0")
            lines.append(f"__rep{count} += 1")

        lines.extend(render_step(step, assets, count, browser_fast))
        if end_step and count == end_step:
            lines.append("Return")
            lines.append("")
            continue

        if action == "text_condition":
            lines.append("")
            continue

        if action in {"image_search", "ocr"}:
            found_var = f"__step_found_{count}" if action == "image_search" else "__ocr_success"
            lines.append(f"if ({found_var})")
            lines.append("{")
            if repeat > 1:
                lines.append(f"    if (__rep{count} < {repeat})")
                lines.append("    {")
                lines.append(f"        Goto, Step{count}")
                lines.append("    }")
                lines.append(f"    __rep{count} := 0")
            lines.extend(render_edge_conditions(step, count, "success", "    "))
            if on_success:
                if on_success_delay > 0:
                    lines.append(f"    Sleep, {on_success_delay}")
                lines.append(f"    Goto, Step{on_success}")
            elif stop_on_success:
                lines.append("    Return")
            elif count >= total_steps:
                lines.append("    Return")
            lines.append("}")
            lines.append("else")
            lines.append("{")
            if repeat > 1:
                lines.append(f"    if (__rep{count} < {repeat})")
                lines.append("    {")
                lines.append(f"        Goto, Step{count}")
                lines.append("    }")
                lines.append(f"    __rep{count} := 0")
            lines.extend(render_edge_conditions(step, count, "fail", "    "))
            if on_fail:
                if on_fail_delay > 0:
                    lines.append(f"    Sleep, {on_fail_delay}")
                lines.append(f"    Goto, Step{on_fail}")
            elif count >= total_steps:
                lines.append("    Return")
            lines.append("}")
        elif repeat > 1:
            lines.append(f"if (__rep{count} < {repeat})")
            lines.append("{")
            lines.append(f"    Goto, Step{count}")
            lines.append("}")
            lines.append(f"__rep{count} := 0")
            lines.extend(render_edge_conditions(step, count, "success"))
            if on_success:
                if on_success_delay > 0:
                    lines.append(f"Sleep, {on_success_delay}")
                lines.append(f"Goto, Step{on_success}")
            elif count >= total_steps:
                lines.append("Return")
        elif on_success:
            lines.extend(render_edge_conditions(step, count, "success"))
            if on_success_delay > 0:
                lines.append(f"Sleep, {on_success_delay}")
            lines.append(f"Goto, Step{on_success}")
        elif action != "flow_control":
            lines.extend(render_edge_conditions(step, count, "success"))
            if count >= total_steps:
                lines.append("Return")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def prepare_macro_for_runtime(macro: Dict[str, Any], runtime_mode: str = "auto") -> Dict[str, Any]:
    """Create an export-only macro variant for the selected portable runtime."""
    mode = str(runtime_mode or "auto").strip().casefold()
    if mode not in {"auto", "ahk", "python"}:
        raise ValueError(f"지원하지 않는 내보내기 실행 구성입니다: {runtime_mode}")
    prepared = copy.deepcopy(macro)
    steps = _expand_macro_steps(list(prepared.get("steps") or []))
    if mode == "ahk":
        python_actions = {
            str(step.get("action") or "")
            for step in steps
            if isinstance(step, dict)
            and str(step.get("action") or "") in {"browser_action", "ocr", "table_excel_read", "table_excel_write"}
        }
        if python_actions:
            labels = {
                "browser_action": "브라우저 자동화",
                "ocr": "OCR",
                "table_excel_read": "Excel 읽기",
                "table_excel_write": "Excel 쓰기",
            }
            detail = ", ".join(labels.get(action, action) for action in sorted(python_actions))
            raise ValueError(
                "AutoHotkey 전용으로 내보낼 수 없는 Python 필수 단계가 있습니다: " + detail
            )
    for step in steps:
        if not isinstance(step, dict) or step.get("action") != "image_search":
            continue
        if mode == "ahk":
            step["engine"] = "ahk"
        elif mode == "python":
            step["engine"] = "opencv"
    prepared["steps"] = steps
    return prepared


def export_macro(
    name: str,
    *,
    output: Optional[Path],
    stdout: bool,
    force: bool,
    browser_fast: bool = False,
    runtime_mode: str = "auto",
) -> None:
    path = macro_path(name)
    if not path.exists():
        raise FileNotFoundError(f"macro {name} not found")
    macro = prepare_macro_for_runtime(load_json_file(path), runtime_mode)
    script = render_macro_script(macro, read_assets(), browser_fast)
    if stdout:
        print(script)
        return
    target = output or (EXPORT_DIR / f"{path.stem}.ahk")
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (use --force to overwrite)")
    export_macro_payload(macro, target, browser_fast, rendered_script=script)
    print(f"exported {target}")


def export_macro_payload(
    macro: Dict[str, Any],
    target: Path,
    browser_fast: bool = False,
    *,
    rendered_script: str | None = None,
) -> Path:
    """Export an in-memory macro, used by isolated step tests and smart automation previews."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    script = rendered_script if rendered_script is not None else render_macro_script(macro, read_assets(), browser_fast)
    target.write_text(script, encoding="utf-8-sig")
    expanded_steps = list(macro.get("steps", []))
    macro_for_export = dict(macro)
    macro_for_export["steps"] = expanded_steps
    copy_assets_for_macro(macro_for_export, target.parent)
    if any(step.get("action") == "browser_action" for step in expanded_steps) or any(
        step.get("action") == "ocr" and str(step.get("mode", "")).lower() == "browser"
        for step in expanded_steps
    ):
        browser_helper = BASE_DIR / "browser_action.py"
        if browser_helper.exists():
            shutil.copy2(browser_helper, target.parent / browser_helper.name)
    if any(step.get("action") == "ocr" for step in expanded_steps):
        ocr_helper = BASE_DIR / "ocr_action.py"
        if ocr_helper.exists():
            shutil.copy2(ocr_helper, target.parent / ocr_helper.name)
        for helper_name in (
            "ocr_engine.py",
            "ocr_capture.py",
            "ocr_preprocess.py",
            "ocr_paddle.py",
            "ocr_tesseract.py",
            "ocr_postprocess.py",
        ):
            helper = BASE_DIR / helper_name
            if helper.exists():
                shutil.copy2(helper, target.parent / helper.name)
        tessdata = BASE_DIR / "tessdata"
        if tessdata.is_dir():
            shutil.copytree(tessdata, target.parent / "tessdata", dirs_exist_ok=True)
    if any(
        step.get("action") == "image_search"
        and str(step.get("engine") or "ahk").lower() == "opencv"
        for step in expanded_steps
    ):
        opencv_helper = BASE_DIR / "opencv_search.py"
        if opencv_helper.exists():
            shutil.copy2(opencv_helper, target.parent / opencv_helper.name)
    if any(step.get("action") == "ocr" and step.get("table") for step in expanded_steps):
        table_update = BASE_DIR / "table_update.py"
        if table_update.exists():
            shutil.copy2(table_update, target.parent / table_update.name)
    if any(step.get("action") in {"table_excel_read", "table_excel_write"} for step in expanded_steps):
        table_helper = BASE_DIR / "data_table_action.py"
        if table_helper.exists():
            shutil.copy2(table_helper, target.parent / table_helper.name)
    if any(step.get("action") == "remote_notify" for step in expanded_steps):
        for helper_name in ("remote_notify.py", "remote_common.py"):
            helper = BASE_DIR / helper_name
            if helper.exists():
                shutil.copy2(helper, target.parent / helper.name)
    return target


def describe_macro(name: str) -> None:
    path = macro_path(name)
    if not path.exists():
        raise FileNotFoundError(f"macro {name} not found")
    payload = load_json_file(path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def copy_assets_for_macro(macro: Dict[str, Any], destination: Path) -> None:
    assets = read_assets()
    aliases = set()
    for step in macro.get("steps", []):
        if step.get("action") == "image_search":
            alias = step.get("asset")
            if alias:
                aliases.add(alias)
    if not aliases:
        return
    target_dir = destination / "assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    for alias in aliases:
        record = assets.get(alias)
        if not record:
            continue
        source = BASE_DIR / record["file"]
        if not source.exists():
            continue
        shutil.copy2(source, target_dir / source.name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoHotkey macro builder")
    parser.add_argument("--version", action="version", version="macro_tool 0.1")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    new_parser = subparsers.add_parser("new", help="create an empty macro definition")
    new_parser.add_argument("name")
    new_parser.add_argument("--description", default="", help="short description")
    new_parser.add_argument("--coord-mode", default="Screen", help="CoordMode for mouse operations")

    list_parser = subparsers.add_parser("list", help="show saved macros")

    describe_parser = subparsers.add_parser("describe", help="dump macro JSON")
    describe_parser.add_argument("name")

    add_parser = subparsers.add_parser("add-step", help="append a step to a macro")
    add_parser.add_argument("name")
    add_parser.add_argument("--action", required=True, choices=ACTIONS)
    add_parser.add_argument("--label", help="friendly name for this step")
    add_parser.add_argument("--param", action="append", help="key=value step parameter")
    add_parser.add_argument("--json", help="JSON string defining the step payload")

    export_parser = subparsers.add_parser("export", help="render AHK script")
    export_parser.add_argument("name")
    export_parser.add_argument("--output", type=Path, help="path to write the AHK file")
    export_parser.add_argument("--stdout", action="store_true", help="print to stdout instead")
    export_parser.add_argument("--force", action="store_true")
    export_parser.add_argument("--browser-fast", action="store_true")

    asset_parser = subparsers.add_parser("asset", help="manage image assets")
    asset_sub = asset_parser.add_subparsers(dest="asset_cmd", required=True)
    add_asset = asset_sub.add_parser("add", help="copy an image into the asset folder")
    add_asset.add_argument("source", type=Path)
    add_asset.add_argument("--alias", help="name to use when referencing the asset")
    add_asset.add_argument("--force", action="store_true")

    remove_asset = asset_sub.add_parser("remove", help="delete an asset")
    remove_asset.add_argument("alias")

    asset_sub.add_parser("list", help="show registered assets")
    return parser


def main() -> None:
    ensure_environment()
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        if sys.stdin.isatty():
            input("Press Enter to exit...")
        return
    args = parser.parse_args()

    if args.cmd == "new":
        target = create_macro(args.name, args.description, args.coord_mode)
        print(f"created {target}")
        return

    if args.cmd == "list":
        for macro_file in list_macros():
            data = load_json_file(macro_file)
            print(f"{macro_file.stem}: {data.get('description', '')}")
        return

    if args.cmd == "describe":
        describe_macro(args.name)
        return

    if args.cmd == "add-step":
        payload: Dict[str, Any]
        if args.json:
            payload = json.loads(args.json)
        else:
            payload = parse_param_list(args.param)
        path = macro_path(args.name)
        if not path.exists():
            raise FileNotFoundError(f"macro {args.name} not found")
        add_step_to_macro(path, args.action, payload, args.label)
        print(f"appended {args.action} to {path.stem}")
        return

    if args.cmd == "export":
        export_macro(
            args.name,
            output=args.output,
            stdout=args.stdout,
            force=args.force,
            browser_fast=args.browser_fast,
        )
        return

    if args.cmd == "asset":
        index = read_assets()
        if args.asset_cmd == "add":
            alias = register_asset(args.source, args.alias, args.force)
            print(f"asset registered as {alias}")
            return
        if args.asset_cmd == "remove":
            remove_asset(args.alias)
            print(f"asset {args.alias} removed")
            return
        if args.asset_cmd == "list":
            for key, meta in index.items():
                print(f"{key}: {meta['file']} (size={meta['size']} bytes)")
            return

    parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
